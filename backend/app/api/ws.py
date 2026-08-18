"""The streaming websocket: `/ws/{session_id}`.

One connection = one session. Two tasks run concurrently:

  * a **writer** that drains the session's `Emitter` queue onto the socket;
  * a **reader** that accepts `user_message` / `cancel` / `ping` frames.

The agent run happens in its own task so `cancel` can interrupt it mid-tool.

The full event contract lives in `app/events.py`.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import events as ev
from app.agent import runner
from app.agent.llm import generate_title
from app.api.auth import resolve_user
from app.api.ownership import owns_session
from app.credits import InsufficientCredits, ensure_can_start
from app.api.ratelimit import RateLimiter
from app.config import get_settings
from app.db import repository
from app.emitter import Emitter, registry
from app.llm_router import (
    AUTO_MODEL_ID,
    MODEL_REGISTRY,
    ModelUnavailableError,
    auto_pool_available,
    display_name,
    is_available,
    model_meta,
    resolve_stored_model,
)
from app.tools.preview import preview_manager
from app.tools.sandbox import sandbox_manager

log = logging.getLogger(__name__)
router = APIRouter()

_limiter = RateLimiter(get_settings().rate_limit_messages_per_minute)


@router.websocket("/ws/{session_id}")
async def agent_socket(
    websocket: WebSocket,
    session_id: str,
    token: str | None = None,
    section: str | None = None,
):
    """`section` is 'chat' or 'code' — which surface opened this socket.

    It matters because this is where a Chat/Code session row is actually
    created: the browser mints the id locally and connects, so the insert below
    is the only place the originating section is still known. Without it both
    sections' sessions land untagged and each section's history returns the
    other's — the session-bleed bug.
    """
    settings = get_settings()
    section = section if section in ("chat", "code") else "chat"

    try:
        user_id = await resolve_user(token)
    except Exception:  # noqa: BLE001 - HTTPException from resolve_user
        await websocket.close(code=4401, reason="Unauthorized")
        return

    # Authenticating the caller says who they are, not what they may open. The
    # session id arrives in the path, so without this anyone could attach to a
    # stranger's conversation and both read its replay and continue it. An id
    # with no row yet is allowed through: this handler is where a new Chat/Code
    # session is created, so the connect necessarily precedes the row.
    if not await owns_session(user_id, session_id):
        await websocket.close(code=4403, reason="Forbidden")
        return

    await websocket.accept()

    emitter = Emitter()
    # Bound to the session as well as registered by id: the live preview emits
    # long after the run that started it has finished, and has to reach
    # whichever socket the session is on at the time.
    registry.register(emitter, session_id)
    writer = asyncio.create_task(_writer(websocket, emitter))
    run_task: asyncio.Task | None = None

    # Tell the browser it is live before touching the database — the UI gates
    # its composer on this event and has no reason to wait on Supabase.
    emitter.emit(ev.connected(session_id))

    # Reconcile the browser's preview state with ours, in both directions.
    #
    # `preview_ready` fires once, at start, so without the first branch a page
    # reload would show an empty Preview panel over a perfectly live server.
    # The second branch matters just as much and is easier to miss: this
    # registry is process-local, so a backend restart loses it while the client
    # keeps showing a preview whose Stop button now does nothing. Saying "there
    # is no preview here" on every connect makes the client's state a function
    # of the server's rather than of its own history.
    live = preview_manager.status(session_id)
    if live.get("running"):
        emitter.emit(
            ev.preview_ready(live["url"], live["port"], live.get("command") or "")
        )
    else:
        emitter.emit(ev.preview_stopped(None, "absent"))

    # Resolve the session's model so a reconnect honours the saved selection.
    row = await repository.get_session(session_id)
    # A session stored against a model that has since been retired is switched
    # to the default here rather than being allowed to fail at dispatch on its
    # next turn. The new value is persisted so the swap happens once.
    current_model, model_notice = resolve_stored_model(
        row.get("model_id") if row else None
    )
    if row is not None and model_notice:
        await repository.touch_session(session_id, model_id=current_model)
    if row is None:
        # Only new sessions need the insert. Upserting on every reconnect cost
        # a round trip here and, because the row carries the default title,
        # also overwrote whatever `_maybe_title` had named the session.
        #
        # This one *is* awaited: `messages` has a foreign key onto `sessions`,
        # and the message writes during a run are fired rather than awaited, so
        # the row has to exist before the user can send anything. It happens
        # once per session, while the composer is still empty.
        await repository.create_session(
            session_id, user_id, model_id=current_model, section=section
        )
    _emit_model(emitter, current_model)
    if model_notice:
        emitter.emit(ev.notice(model_notice))

    try:
        while True:
            frame = await websocket.receive_json()
            kind = frame.get("type")

            if kind == "ping":
                emitter.emit({"type": "pong"})
                continue

            if kind == "cancel":
                if run_task and not run_task.done():
                    run_task.cancel()
                continue

            if kind == "set_model":
                mid = (frame.get("model_id") or "").strip()
                # The `auto` sentinel is stored exactly like a model id, so the
                # routing-mode preference persists through the same column and
                # the same reconnect path as a manual pick.
                if mid == AUTO_MODEL_ID or mid in MODEL_REGISTRY:
                    current_model = mid
                    await repository.touch_session(session_id, model_id=mid)
                    _emit_model(emitter, mid)
                else:
                    emitter.emit(ev.error(f"Unknown model `{mid}`."))
                continue

            if kind != "user_message":
                emitter.emit(ev.error(f"Unknown client frame `{kind}`."))
                continue

            if run_task and not run_task.done():
                emitter.emit(
                    ev.error("The agent is still working — cancel it first.")
                )
                continue

            allowed, retry_after = _limiter.check(session_id)
            if not allowed:
                emitter.emit(
                    ev.error(
                        f"Rate limit reached ({settings.rate_limit_messages_per_minute}"
                        f"/min). Try again in {retry_after}s."
                    )
                )
                continue

            text = (frame.get("content") or "").strip()
            if not text:
                emitter.emit(ev.error("Empty message."))
                continue

            run_model = current_model
            run_task = asyncio.create_task(
                _run(
                    session_id,
                    text,
                    frame.get("file_ids") or [],
                    emitter,
                    run_model,
                    user_id,
                )
            )

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("Websocket loop failed for session %s", session_id)
    finally:
        if run_task and not run_task.done():
            run_task.cancel()
        emitter.close()
        registry.unregister(emitter.id)
        writer.cancel()
        await repository.touch_session(session_id, status="idle")


def _emit_model(
    emitter: Emitter, model_id: str, extra_note: str | None = None
) -> None:
    """Announce the active model to the client (used on connect + switch).

    ``extra_note`` carries the "your old model is gone" message from
    :func:`resolve_stored_model`. It rides on the same `note` field the client
    already renders next to a model switch, so a reopened session explains
    itself in one line instead of needing a second event type.
    """
    if model_id == AUTO_MODEL_ID:
        # No model is chosen yet in auto mode — the first turn's classifier
        # decides, and the graph announces the concrete model it picked.
        ready = auto_pool_available()
        emitter.emit(
            ev.model_changed(
                model_id=AUTO_MODEL_ID,
                name="Auto",
                supports_tools=True,
                available=ready,
                note=(
                    ""
                    if ready
                    else "OPENCODE_API_KEY is not set — Auto is falling back "
                    "to the section defaults instead of routing by task."
                ),
                routing_mode="auto",
            )
        )
        return
    try:
        meta = model_meta(model_id)
    except ModelUnavailableError:
        emitter.emit(
            ev.model_changed(
                model_id=model_id, name=model_id, supports_tools=False,
                available=False, note="This model is not configured.",
            )
        )
        return
    note = extra_note or ""
    if not note and not is_available(model_id):
        note = "This model's API key is not set — add it to backend/.env."
    elif not note and not meta["supports_tools"]:
        note = (
            "This model does not support tool use. The agent can answer and "
            "write prose, but cannot run shell commands or edit files while "
            "it is selected."
        )
    emitter.emit(
        ev.model_changed(
            model_id=model_id,
            name=display_name(model_id),
            supports_tools=meta["supports_tools"],
            available=is_available(model_id),
            note=note,
        )
    )


#: Events whose payload is a fragment of a longer string, and which the client
#: reducer handles by appending `content` to what it already has. Two adjacent
#: frames of one of these types are therefore interchangeable with a single
#: frame holding both fragments.
_MERGEABLE = {"agent_token", "agent_thinking_delta", "tool_output_chunk"}


def _mergeable_with(a: dict, b: dict) -> bool:
    if a.get("type") != b.get("type") or a.get("type") not in _MERGEABLE:
        return False
    # Terminal output is keyed by which call and which stream it came from;
    # merging across either would put the wrong text in the wrong place.
    return a.get("call_id") == b.get("call_id") and a.get("stream") == b.get("stream")


def _coalesce(batch: list[dict]) -> list[dict]:
    """Collapse runs of adjacent fragment events into one frame each."""
    out: list[dict] = []
    for event in batch:
        if out and _mergeable_with(out[-1], event):
            prev = out[-1]
            # Copy on first merge so the emitted event dict is never mutated
            # in place (it may still be referenced by the producer).
            if prev.get("_merged"):
                prev["content"] += event.get("content", "")
                continue
            merged = dict(prev)
            merged["content"] = prev.get("content", "") + event.get("content", "")
            merged["_merged"] = True
            out[-1] = merged
            continue
        out.append(event)
    for event in out:
        event.pop("_merged", None)
    return out


async def _writer(websocket: WebSocket, emitter: Emitter) -> None:
    """Drain the emitter queue onto the socket, batching only when behind.

    Streaming a long answer one `send_json` per token means one JSON encode and
    one websocket frame per token. This drains whatever has *already* piled up
    and merges adjacent fragments before sending, so a burst becomes a handful
    of frames instead of hundreds. Nothing is ever held back waiting for more:
    when the queue is empty — the normal case for a slow, steady stream — the
    first event goes out immediately and nothing changes.
    """
    try:
        while True:
            event = await emitter.queue.get()
            if event is None:
                return
            batch = [event]
            closed = False
            while True:
                try:
                    nxt = emitter.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    closed = True
                    break
                batch.append(nxt)
            for frame in _coalesce(batch):
                await websocket.send_json(frame)
            if closed:
                return
    except (WebSocketDisconnect, RuntimeError):
        return
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.debug("Writer task ended", exc_info=True)


async def _run(
    session_id: str,
    text: str,
    file_ids: list[str],
    emitter: Emitter,
    model_id: str,
    user_id: str | None = None,
) -> None:
    # Both of these are two Supabase round trips standing between the user
    # hitting enter and the first token of the model call. Nothing in the run
    # reads them back, so they go out in the background.
    # Refuse a turn that cannot pay for its first model call, before anything
    # is written or any provider is contacted. Chat and Code went unmetered
    # entirely until this existed, while the UI showed a balance the whole time.
    try:
        await ensure_can_start(user_id, "chat")
    except InsufficientCredits as exc:
        emitter.emit(ev.error(str(exc)))
        emitter.emit(ev.agent_done(0, "insufficient_credits"))
        return

    repository.fire(repository.add_message(session_id, "user", text))
    repository.fire(
        repository.touch_session(
            session_id,
            status="running",
            sandbox_id=sandbox_manager.sandbox_id_for(session_id),
        )
    )
    asyncio.create_task(_maybe_title(session_id, text))

    try:
        final = await runner.run_turn(
            session_id, text, emitter, file_ids, model_id=model_id, user_id=user_id
        )
        reason = final.get("stop_reason") or "end_turn"
        if reason != "max_iterations":
            emitter.emit(ev.agent_done(int(final.get("iterations") or 0), reason))
    except asyncio.CancelledError:
        emitter.emit(ev.error("Run cancelled."))
        emitter.emit(ev.agent_done(0, "cancelled"))
        raise
    except Exception as exc:  # noqa: BLE001 - reported, connection stays open
        log.exception("Agent run failed for session %s", session_id)
        emitter.emit(ev.error(f"{type(exc).__name__}: {exc}"))
        emitter.emit(ev.agent_done(0, "error"))
    finally:
        await repository.touch_session(
            session_id,
            status="idle",
            sandbox_id=sandbox_manager.sandbox_id_for(session_id),
        )


async def _maybe_title(session_id: str, text: str) -> None:
    """Name the session from its first message, using the cheap model."""
    try:
        state = await runner.get_state(session_id)
        if len(state.get("messages") or []) > 1:
            return
        title = await generate_title(text)
        if title:
            await repository.touch_session(session_id, title=title)
    except Exception:  # noqa: BLE001
        log.debug("Title generation skipped", exc_info=True)
