"""The streaming websocket: `/ws/{session_id}`.

One connection = one session. Two tasks run concurrently:

  * a **writer** that drains the session's `Emitter` queue onto the socket;
  * a **reader** that accepts `user_message` / `cancel` / `ping` frames, and
    — in the Code section — `terminal_command` frames from the terminal panel.

The agent run happens in its own task so `cancel` can interrupt it mid-tool.
A terminal command runs in its own task too, so the user can run `ls` while
the agent is thinking; it shares the sandbox, not the run.

The full event contract lives in `app/events.py`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app import autocommit, cancel
from app import memory
from app.api import rerun
from app import events as ev
from app.agent import runner
from app.agent.llm import generate_title
from app.api.auth import resolve_user
from app.api.ownership import owns_row
from app.credits import InsufficientCredits, ensure_can_start, prime_balance
from app.api.ratelimit import RateLimiter
from app.config import get_settings
from app.security import (
    reject_foreign_origin,
    websocket_client_key,
    websocket_token,
)
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
    section_default_model,
)
from app.tools.impl import run_terminal_command
from app.tools.preview import preview_manager
from app.tools.sandbox import sandbox_manager

log = logging.getLogger(__name__)
router = APIRouter()

#: The longest command the terminal panel accepts. Anything a person types by
#: hand is far shorter; a frame past this is a script pasted in by mistake.
MAX_TERMINAL_COMMAND_CHARS = 4_000

_limiter = RateLimiter(get_settings().rate_limit_messages_per_minute)
#: Connections per client address. The message limiter above is keyed by
#: session id, which the client mints, so on its own it bounded nothing a
#: determined caller could not sidestep with a fresh id per message.
_connect_limiter = RateLimiter(60)


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

    # CORS does not cover websockets. Without this any page could open a
    # visitor's session from their browser.
    if await reject_foreign_origin(websocket):
        return
    allowed, retry_after = _connect_limiter.check(websocket_client_key(websocket))
    if not allowed:
        await websocket.close(code=4429, reason=f"Too many connections; retry in {retry_after}s")
        return
    # The token rides in a subprotocol rather than the URL, so it stays out of
    # access logs; `?token=` is still read for older clients.
    token, subprotocol = websocket_token(websocket, token)

    # Two independent round trips — verifying the token against Supabase Auth,
    # and reading the session row — that used to run one after the other, for
    # ~305 ms of dead time before the socket was even accepted. Neither needs
    # the other's answer: the row is fetched by id, and only the *comparison*
    # below needs both. Overlapping them costs one round trip instead of two.
    user_task = asyncio.create_task(resolve_user(token))
    row_task = asyncio.create_task(repository.get_session(session_id))
    try:
        user_id = await user_task
    except Exception:  # noqa: BLE001 - HTTPException from resolve_user
        row_task.cancel()
        await websocket.close(code=4401, reason="Unauthorized")
        return

    # Authenticating the caller says who they are, not what they may open. The
    # session id arrives in the path, so without this anyone could attach to a
    # stranger's conversation and both read its replay and continue it. An id
    # with no row yet is allowed through: this handler is where a new Chat/Code
    # session is created, so the connect necessarily precedes the row.
    row = await row_task
    if not owns_row(user_id, row):
        await websocket.close(code=4403, reason="Forbidden")
        return

    await websocket.accept(subprotocol=subprotocol)

    emitter = Emitter()
    # Bound to the session as well as registered by id: the live preview emits
    # long after the run that started it has finished, and has to reach
    # whichever socket the session is on at the time.
    registry.register(emitter, session_id)
    writer = asyncio.create_task(_writer(websocket, emitter))
    run_task: asyncio.Task | None = None
    # The user's own shell command, when one is running. One at a time per
    # socket — the panel is a single terminal, and two commands interleaving
    # their output in it would be unreadable.
    terminal_task: asyncio.Task | None = None

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
    # Warm the credit snapshot while the user is still reading the screen, so
    # the first turn's gate is answered from memory instead of paying a
    # Supabase round trip at the exact moment latency is most visible.
    asyncio.create_task(prime_balance(user_id))

    live = preview_manager.status(session_id)
    if live.get("running"):
        emitter.emit(
            ev.preview_ready(live["url"], live["port"], live.get("command") or "")
        )
    else:
        emitter.emit(ev.preview_stopped(None, "absent"))

    # Resolve the session's model so a reconnect honours the saved selection.
    # `row` is the one already read for the ownership check above — re-reading
    # it here was a second ~153 ms Supabase round trip on every connect.
    # A session stored against a model that has since been retired is switched
    # to the default here rather than being allowed to fail at dispatch on its
    # next turn. The new value is persisted so the swap happens once.
    # A brand-new session has no stored model, so it opens on the default for
    # the section that opened this socket — `section_default_model` is the one
    # place that mapping lives. An existing session's stored pick is honoured
    # and only reassigned when it has gone stale.
    current_model, model_notice = resolve_stored_model(
        row.get("model_id") if row else None,
        default=section_default_model(section),
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
        fresh = await repository.create_session(
            session_id, user_id, model_id=current_model, section=section
        )
        if not fresh.get("persisted", True):
            # The insert failed, twice, and every `messages` and `token_usage`
            # write for the rest of this session will fail its foreign key —
            # each one logged at warning and swallowed, so the run itself works
            # and nothing tells the user until the conversation is missing from
            # their history. Say it now, while the composer is still empty and
            # the choice of whether to type is still theirs.
            emitter.emit(
                ev.notice(
                    "This conversation is not being saved — the database "
                    "would not accept the session. You can carry on, but "
                    "reloading will lose it."
                )
            )
    _emit_model(emitter, current_model)
    if model_notice:
        emitter.emit(ev.notice(model_notice))

    # Which turns carry a `‹ 1/2 ›` switcher. Sent on every connect, because
    # the transcript is replayed from the checkpoint on every connect and the
    # switchers are drawn onto it — a reload without this shows an edited
    # conversation with no way back to what it replaced.
    asyncio.create_task(rerun.emit_branches(session_id, emitter))

    try:
        while True:
            frame = await websocket.receive_json()
            kind = frame.get("type")

            if kind == "ping":
                emitter.emit({"type": "pong"})
                continue

            if kind == "cancel":
                # Cooperative, not an interrupt. `run_task.cancel()` tore the
                # node down mid-stream, which threw away the partial answer the
                # user was already reading, billed nothing for tokens the
                # provider had produced, and — when it landed during a tool
                # batch — left a `tool_use` block with no matching result. The
                # flag lets the graph finish the turn properly instead; see
                # app/cancel.py and app/turnstop.py.
                if run_task and not run_task.done():
                    cancel.request_stop(session_id)
                continue

            if kind == "terminal_command":
                # A command the user typed into the terminal panel. It goes
                # to the same sandbox the agent's tools use, through
                # `sandbox_manager.get` — same lazy creation, same keepalive,
                # same idle reaper — so nothing here is a second lifecycle.
                # It is *not* gated on `run_task`: reading the tree or the
                # logs while the agent works is the normal use, and the
                # sandbox runs commands concurrently. What it is gated on is
                # itself — one command at a time.
                command = str(frame.get("command") or "").strip()
                if not command:
                    emitter.emit(ev.error("Empty command."))
                    continue
                if len(command) > MAX_TERMINAL_COMMAND_CHARS:
                    emitter.emit(ev.error("That command is too long for the terminal."))
                    continue
                if terminal_task and not terminal_task.done():
                    emitter.emit(
                        ev.error("A terminal command is still running — wait for it to finish.")
                    )
                    continue
                terminal_task = asyncio.create_task(
                    run_terminal_command(
                        session_id, command, f"term_{uuid.uuid4().hex[:12]}", emitter
                    )
                )
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

            if kind == "edit_message" or kind == "regenerate":
                # Both are the same operation with a different starting point:
                # rewind the conversation to a user turn, then run it again.
                # Edit supplies new text for that turn; regenerate reuses what
                # is already there.
                if run_task and not run_task.done():
                    emitter.emit(
                        ev.error("The agent is still working — stop it first.")
                    )
                    continue

                allowed, retry_after = _limiter.check(session_id)
                if not allowed:
                    emitter.emit(
                        ev.error(
                            f"Rate limit reached "
                            f"({settings.rate_limit_messages_per_minute}/min). "
                            f"Try again in {retry_after}s."
                        )
                    )
                    continue

                prepared = await rerun.prepare_rerun(
                    runner, session_id, frame, kind, emitter
                )
                if prepared is None:
                    continue
                new_text, turn_index, version, file_ids = prepared

                emitter.emit(ev.history_replaced(kind, turn_index, new_text))
                run_task = asyncio.create_task(
                    _run(
                        session_id,
                        new_text,
                        file_ids,
                        emitter,
                        current_model,
                        user_id,
                        section,
                        # A re-run must not rename the session: the title was
                        # generated from the *first* message and editing turn
                        # five has nothing to say about it.
                        title_it=False,
                        branch=(turn_index, version),
                    )
                )
                continue

            if kind == "switch_branch":
                if run_task and not run_task.done():
                    emitter.emit(
                        ev.error("The agent is still working — stop it first.")
                    )
                    continue
                await rerun.switch_branch(runner, session_id, frame, emitter)
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
                    section,
                )
            )

    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # A client that vanishes mid-stream reaches the two tasks by different
        # routes, and only one of them is called `WebSocketDisconnect`. The
        # writer notices first: its next `send` fails with `OSError`, which
        # Starlette turns into `application_state = DISCONNECTED` plus a
        # `WebSocketDisconnect(1006)` that the writer swallows. The reader is
        # parked in `receive_json`, which checks that same state on the way in
        # and raises a bare `RuntimeError("...Need to call accept first")`
        # instead — the wrong name for what happened, and nothing a caller can
        # match on.
        #
        # So the state is what gets asked, not the exception type: a socket
        # that is no longer connected has disconnected, however it was
        # reported. Every browser refresh during a run was logging this with a
        # full traceback at ERROR, which is the level real failures use.
        # A RuntimeError raised while the socket is still up is still a bug and
        # is still logged as one.
        if websocket.application_state is WebSocketState.CONNECTED:
            log.exception("Websocket loop failed for session %s", session_id)
    except Exception:  # noqa: BLE001
        log.exception("Websocket loop failed for session %s", session_id)
    finally:
        if run_task and not run_task.done():
            run_task.cancel()
        if terminal_task and not terminal_task.done():
            terminal_task.cancel()
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
    section: str = "chat",
    title_it: bool = True,
    branch: tuple[int, int] | None = None,
) -> None:
    """One turn, start to finish.

    ``title_it`` is False for a re-run: the session was named from its opening
    message and editing a later turn says nothing about what the conversation
    is called.

    ``branch`` is ``(turn_index, version)`` when this turn is an edit or a
    regenerate. The answer it produces is recorded as that version once the run
    ends, which is what puts the new reply behind the ``2/2`` half of the
    switcher.
    """
    # Both of these are two Supabase round trips standing between the user
    # hitting enter and the first token of the model call. Nothing in the run
    # reads them back, so they go out in the background.
    # Refuse a turn that cannot pay for its first model call, before anything
    # is written or any provider is contacted. Chat and Code went unmetered
    # entirely until this existed, while the UI showed a balance the whole time.
    # Any stop asked for while nothing was running would otherwise land on this
    # turn and end it before its first token.
    cancel.clear(session_id)

    # Which project this session is in, read per turn rather than captured at
    # connect: moving a session into a project has to take effect on its very
    # next message, and a value cached at connect would still be the old one.
    # Started before the credit check so the two round trips overlap instead of
    # queueing — both sit between the user hitting enter and the first token.
    row_task = asyncio.create_task(repository.get_session(session_id))

    try:
        await ensure_can_start(user_id, section)
    except InsufficientCredits as exc:
        row_task.cancel()
        emitter.emit(ev.error(str(exc)))
        emitter.emit(ev.agent_done(0, "insufficient_credits"))
        return

    project_id = ((await row_task) or {}).get("project_id")

    repository.fire(repository.add_message(session_id, "user", text))
    repository.fire(
        repository.touch_session(
            session_id,
            status="running",
            sandbox_id=sandbox_manager.sandbox_id_for(session_id),
        )
    )
    if title_it:
        asyncio.create_task(_maybe_title(session_id, text))

    try:
        final = await runner.run_turn(
            session_id,
            text,
            emitter,
            file_ids,
            model_id=model_id,
            user_id=user_id,
            section=section,
            project_id=project_id,
        )
        reason = final.get("stop_reason") or "end_turn"
        # The end-of-turn commit and push, for a Code repository that opted
        # in. Before `agent_done` on purpose: that frame re-arms the composer,
        # and the commit belongs to the turn the user is reading, not to the
        # gap after it. A stopped or failed turn is left uncommitted — what it
        # wrote is half of something, and recording it is not what "auto" was
        # meant to mean. `commit_turn` never raises; see its docstring.
        if section == "code" and reason == "end_turn":
            await autocommit.commit_turn(session_id, user_id, emitter)
        if reason != "max_iterations":
            emitter.emit(ev.agent_done(int(final.get("iterations") or 0), reason))
        # Learn from the exchange, after the user has their answer and off the
        # critical path. Deliberately not awaited: it is a second model call,
        # and nothing the user is waiting for depends on it. A turn the user
        # stopped is skipped — a cut-off exchange is the worst possible source
        # of a fact meant to persist.
        if reason not in ("cancelled", "error"):
            repository.fire(
                memory.extract(user_id, session_id, text, _assistant_text(final))
            )
    except asyncio.CancelledError:
        emitter.emit(ev.error("Run cancelled."))
        emitter.emit(ev.agent_done(0, "cancelled"))
        raise
    except Exception as exc:  # noqa: BLE001 - reported, connection stays open
        log.exception("Agent run failed for session %s", session_id)
        emitter.emit(ev.error(f"{type(exc).__name__}: {exc}"))
        emitter.emit(ev.agent_done(0, "error"))
    finally:
        # Fired, not awaited. The client has already been sent `agent_done` by
        # this point, so it believes the turn is over and its composer is live
        # again — but the socket's "is a run in flight" guard is
        # `run_task.done()`, and awaiting a Supabase round trip here kept that
        # task alive for ~150 ms *after* the user was told to go ahead. Send a
        # second message inside that window and it came back "The agent is
        # still working - cancel it first", for a turn that had finished.
        # Nothing reads this write, so it does not belong on that path.
        repository.fire(
            repository.touch_session(
                session_id,
                status="idle",
                sandbox_id=sandbox_manager.sandbox_id_for(session_id),
            )
        )
        # The turn is over either way — completed, stopped or failed — so the
        # flag must not survive into the next one.
        cancel.clear(session_id)
        # Branch bookkeeping, after the run rather than during it: the answer
        # this turn produced is part of the branch it belongs to, and it does
        # not exist until now.
        asyncio.create_task(
            rerun.settle_branches(runner, session_id, emitter, branch)
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


def _assistant_text(final: dict) -> str:
    """Plain text of the reply this turn produced, for memory extraction.

    Reads the last assistant turn out of the finished graph state. Tool calls
    and thinking blocks are skipped: what is worth remembering is what the user
    was told, not how it was arrived at.
    """
    for message in reversed(list(final.get("messages") or [])):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        parts = [
            str(block.get("text") or "")
            for block in (content or [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()
    return ""
