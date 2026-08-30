"""The Agentic Loop websocket: `/ws/agent/{session_id}`.

A separate route from `/ws/{session_id}` on purpose. The Chat/Code socket owns
sandbox previews, file trees and the general agent loop; this one owns a
specialist's toolset, its approval gate and its credit meter. Sharing the route
would have meant one handler branching on a query parameter through every step,
which is how two behaviours end up quietly entangled.

What *is* shared is everything worth sharing: the emitter, the writer and its
frame coalescing, the rate limiter, the model registry, and the entire event
vocabulary in `app/events.py`. The frontend runs the same reducer against both.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import cancel
from app import events as ev
from app.agents import approvals, registry, runner
from app.agents.tool_registry import tool_public_meta
from app.agent.llm import generate_title
from app.api.auth import resolve_user
from app.api.ownership import owns_row
from app.api.ratelimit import RateLimiter
from app.api import rerun
from app.api.ws import _writer  # the same drain-and-coalesce loop Chat/Code use
from app.config import get_settings
from app.credits import (
    InsufficientCredits,
    ensure_can_start,
    get_balance,
    prime_balance,
)
from app.db import repository
from app.emitter import Emitter, registry as emitter_registry
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

log = logging.getLogger(__name__)
router = APIRouter()

_limiter = RateLimiter(get_settings().rate_limit_messages_per_minute)


@router.websocket("/ws/agent/{session_id}")
async def specialist_socket(
    websocket: WebSocket,
    session_id: str,
    agent: str | None = None,
    token: str | None = None,
):
    settings = get_settings()

    # Overlapped for the same reason as the Chat/Code socket: two Supabase
    # round trips that do not depend on each other were being paid one after
    # the other, ~305 ms before the socket was even accepted.
    user_task = asyncio.create_task(resolve_user(token))
    row_task = asyncio.create_task(repository.get_session(session_id))
    try:
        user_id = await user_task
    except Exception:  # noqa: BLE001 - HTTPException from resolve_user
        row_task.cancel()
        await websocket.close(code=4401, reason="Unauthorized")
        return

    # Same rule as the Chat/Code socket: knowing who you are is not the same as
    # being entitled to this session's transcript and its continuation.
    row = await row_task
    if not owns_row(user_id, row):
        await websocket.close(code=4403, reason="Forbidden")
        return

    await websocket.accept()

    emitter = Emitter()
    emitter_registry.register(emitter, session_id)
    writer = asyncio.create_task(_writer(websocket, emitter))
    run_task: asyncio.Task | None = None

    emitter.emit(ev.connected(session_id))

    asyncio.create_task(prime_balance(user_id))

    # --- resolve which specialist owns this session -----------------------
    # The session row is authoritative. The query parameter only seeds a *new*
    # session: letting it override an existing one would continue a transcript
    # written under one persona using another's tools.
    # `row` is the one already read for the ownership check — re-reading it
    # here was a second ~153 ms round trip on every connect.
    stored_agent = (row or {}).get("agent_id")
    agent_id = stored_agent or (agent or "").strip()

    if not registry.is_agent(agent_id):
        emitter.emit(
            ev.error(
                f"`{agent_id or '(none)'}` is not one of the ten specialists. "
                "Open this session from the Agents gallery."
            )
        )
        await asyncio.sleep(0.1)  # let the writer flush before the close
        emitter.close()
        emitter_registry.unregister(emitter.id)
        writer.cancel()
        await websocket.close(code=4404, reason="Unknown agent")
        return

    definition = registry.get_agent(agent_id)
    # Same stale-model guard as the Chat/Code socket: a session pinned to a
    # retired model is reassigned to the default and told so, instead of
    # raising at dispatch on its next turn.
    current_model, model_notice = resolve_stored_model((row or {}).get("model_id"))
    if row is not None and model_notice:
        await repository.touch_session(session_id, model_id=current_model)
    if row is None:
        # Awaited, not fired: `messages` has a foreign key onto this row and
        # the message writes during a run are fired rather than awaited, so the
        # row must exist before the user can send anything.
        await repository.create_session(
            session_id, user_id, model_id=current_model, agent_id=agent_id
        )
    elif not stored_agent:
        # A session created through the generic REST route before the agent was
        # known. Pin it now so a reconnect resolves without the query parameter.
        await repository.touch_session(session_id, agent_id=agent_id)

    balance = await get_balance(user_id)
    emitter.emit(
        ev.agent_meta(
            agent_id=definition.id,
            name=definition.name,
            role=definition.role,
            icon=definition.icon,
            accent=definition.accent,
            tools=[tool_public_meta(t) for t in definition.tools],
            credits={
                "balance": round(balance.balance, 3),
                "enabled": settings.credits_enabled,
            },
        )
    )
    _emit_model(emitter, current_model)
    asyncio.create_task(rerun.emit_branches(session_id, emitter))

    try:
        while True:
            frame = await websocket.receive_json()
            kind = frame.get("type")

            if kind == "ping":
                emitter.emit({"type": "pong"})
                continue

            if kind == "cancel":
                # Cooperative — see app/cancel.py. The specialist loop keeps
                # its partial answer, closes any tool call it had not started,
                # and bills the tokens actually produced.
                if run_task and not run_task.done():
                    cancel.request_stop(session_id)
                continue

            if kind == "approval_resolve":
                # Answered on the *reader* task, never inside the run: the run
                # is suspended awaiting this, so handling it anywhere else
                # would deadlock the socket against itself.
                approval_id = str(frame.get("approval_id") or "")
                decision = str(frame.get("decision") or "rejected")
                parameters = frame.get("parameters") or {}
                if not approvals.resolve(approval_id, decision, parameters):
                    emitter.emit(
                        ev.error(
                            "That approval is no longer waiting — it was already "
                            "answered, or its run has ended."
                        )
                    )
                continue

            if kind == "set_model":
                mid = (frame.get("model_id") or "").strip()
                if mid == AUTO_MODEL_ID or mid in MODEL_REGISTRY:
                    current_model = mid
                    await repository.touch_session(session_id, model_id=mid)
                    _emit_model(emitter, mid)
                else:
                    emitter.emit(ev.error(f"Unknown model `{mid}`."))
                continue

            if kind in ("edit_message", "regenerate"):
                if run_task and not run_task.done():
                    emitter.emit(
                        ev.error("This agent is still working — stop it first.")
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
                        agent_id,
                        new_text,
                        file_ids,
                        emitter,
                        current_model,
                        user_id,
                        title_it=False,
                        branch=(turn_index, version),
                    )
                )
                continue

            if kind == "switch_branch":
                if run_task and not run_task.done():
                    emitter.emit(
                        ev.error("This agent is still working — stop it first.")
                    )
                    continue
                await rerun.switch_branch(runner, session_id, frame, emitter)
                continue

            if kind != "user_message":
                emitter.emit(ev.error(f"Unknown client frame `{kind}`."))
                continue

            if run_task and not run_task.done():
                emitter.emit(ev.error("This agent is still working — cancel it first."))
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

            text = (frame.get("content") or "").strip()
            if not text:
                emitter.emit(ev.error("Empty message."))
                continue

            # The meter, before a single token is spent. Every one of the ten
            # agents passes through here; there is no exempt path.
            try:
                await ensure_can_start(user_id, agent_id)
            except InsufficientCredits as exc:
                emitter.emit(ev.error(str(exc)))
                emitter.emit(
                    ev.credits(exc.balance, 0.0, enabled=settings.credits_enabled)
                )
                emitter.emit(ev.agent_done(0, "insufficient_credits"))
                continue

            run_task = asyncio.create_task(
                _run(
                    session_id,
                    agent_id,
                    text,
                    frame.get("file_ids") or [],
                    emitter,
                    current_model,
                    user_id,
                )
            )

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("Specialist socket failed for session %s", session_id)
    finally:
        if run_task and not run_task.done():
            run_task.cancel()
        emitter.close()
        emitter_registry.unregister(emitter.id)
        writer.cancel()
        await repository.touch_session(session_id, status="idle")


def _emit_model(
    emitter: Emitter, model_id: str, extra_note: str | None = None
) -> None:
    """Announce the active model. Same semantics as the Chat/Code socket's,
    including ``extra_note`` for a session whose stored model has been retired."""
    if model_id == AUTO_MODEL_ID:
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
                    else "OPENCODE_API_KEY is not set — Auto is falling back to "
                    "the section defaults instead of routing by task."
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
            "This model cannot use tools. The specialist will answer from its "
            "persona alone, without any of its tools."
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


async def _run(
    session_id: str,
    agent_id: str,
    text: str,
    file_ids: list[str],
    emitter: Emitter,
    model_id: str,
    user_id: str | None,
    title_it: bool = True,
    branch: tuple[int, int] | None = None,
) -> None:
    """One specialist turn.

    ``title_it`` and ``branch`` carry the same meanings as on the Chat/Code
    side: a re-run must not rename the session, and an edit or regenerate has a
    branch version waiting for the reply this run is about to produce.
    """
    # A stop asked for while nothing was running must not land on this turn.
    cancel.clear(session_id)

    # Read per turn, not captured at connect, so moving a session into a
    # project takes effect on its next message. Fired ahead of the writes below
    # so it is in flight while they are queued rather than after them.
    row_task = asyncio.create_task(repository.get_session(session_id))

    repository.fire(repository.add_message(session_id, "user", text))
    repository.fire(repository.touch_session(session_id, status="running"))
    if title_it:
        asyncio.create_task(_maybe_title(session_id, agent_id, text))

    try:
        final = await runner.run_turn(
            session_id,
            agent_id,
            text,
            emitter,
            file_ids,
            model_id=model_id,
            user_id=user_id,
            project_id=((await row_task) or {}).get("project_id"),
        )
        reason = final.get("stop_reason") or "end_turn"
        if reason != "max_iterations":
            emitter.emit(ev.agent_done(int(final.get("iterations") or 0), reason))
    except asyncio.CancelledError:
        emitter.emit(ev.error("Run cancelled."))
        emitter.emit(ev.agent_done(0, "cancelled"))
        raise
    except Exception as exc:  # noqa: BLE001 - reported, connection stays open
        log.exception("Specialist run failed for session %s", session_id)
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
        repository.fire(repository.touch_session(session_id, status="idle"))
        cancel.clear(session_id)
        asyncio.create_task(
            rerun.settle_branches(runner, session_id, emitter, branch)
        )


async def _maybe_title(session_id: str, agent_id: str, text: str) -> None:
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
