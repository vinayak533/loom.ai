"""Edit / regenerate / switch-branch, for whichever graph is asking.

The Chat/Code socket and the specialist socket both offer these three actions
and both need them to mean exactly the same thing, but they check out different
graphs — `app.agent.runner` and `app.agents.runner`. So the runner module is a
parameter here rather than an import, and the logic exists once.

What "the same thing" has to mean is the part worth being careful about. An
edit is not a redraw: it rewinds the *checkpoint*, which is what the model
reads on its next turn, so a conversation resumed from an edited point has
genuinely never seen the turns that were cut. See `app/branching.py` for how
the replaced turns are kept.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app import branching
from app import events as ev
from app.db import repository
from app.emitter import Emitter

log = logging.getLogger(__name__)


class Runner(Protocol):
    """The two methods this module needs from whichever runner owns the graph."""

    async def get_state(self, session_id: str) -> dict[str, Any]: ...
    async def set_messages(
        self, session_id: str, messages: list[dict[str, Any]]
    ) -> None: ...


async def emit_branches(session_id: str, emitter: Emitter) -> None:
    """Tell the client which turns have alternate versions.

    Best-effort: a session whose switchers fail to load is still a usable
    session, and failing the connect over it would be a much worse trade.
    """
    try:
        emitter.emit(ev.branches(await branching.summary(session_id)))
    except Exception:  # noqa: BLE001
        log.debug("Branch summary failed for %s", session_id, exc_info=True)


async def prepare_rerun(
    runner: Any, session_id: str, frame: dict, kind: str, emitter: Emitter
) -> tuple[str, int, int, list[str]] | None:
    """Rewind the conversation to a user turn, ready for it to run again.

    Returns ``(text, turn_index, version, file_ids)``, or None when the request
    cannot be honoured — in which case the reason has already been sent to the
    client.

    The ordering is the correctness argument, so it is worth stating plainly:

      1. read the live conversation out of the checkpoint;
      2. record everything from the target turn onwards as a branch, so the
         answers about to be replaced stay reachable;
      3. truncate the checkpoint to *before* that turn.

    Step 3 is what makes an edit real. The next `run_turn` builds its history
    from the checkpoint, so the model resumes from the edited point rather than
    from a transcript that merely looks edited.
    """
    state = await runner.get_state(session_id)
    messages = list(state.get("messages") or [])
    positions = repository.user_turn_positions(messages)
    if not positions:
        emitter.emit(ev.error("There is nothing to re-run yet."))
        return None

    if kind == "regenerate":
        turn_index = len(positions) - 1
        text = branching.turn_text(messages[positions[turn_index]])
        if not text:
            emitter.emit(ev.error("That turn has no text to re-run."))
            return None
    else:
        try:
            turn_index = int(frame.get("turn_index"))
        except (TypeError, ValueError):
            emitter.emit(ev.error("Malformed edit reference."))
            return None
        text = (frame.get("content") or "").strip()
        if not text:
            emitter.emit(ev.error("An edited message cannot be empty."))
            return None
        if turn_index < 0 or turn_index >= len(positions):
            emitter.emit(
                ev.error("That message is no longer part of this conversation.")
            )
            return None

    cut = positions[turn_index]
    user_id = state.get("user_id") or None
    version = await branching.snapshot_original(
        session_id, messages, turn_index, user_id
    )
    # Branches recorded *deeper* than this turn describe a conversation that is
    # about to be replaced wholesale; their switchers would point at turns that
    # no longer exist.
    await repository.drop_branches_from(session_id, turn_index + 1)
    await runner.set_messages(session_id, messages[:cut])

    return text, turn_index, version, list(frame.get("file_ids") or [])


async def switch_branch(
    runner: Any, session_id: str, frame: dict, emitter: Emitter
) -> bool:
    """Make a stored branch the live conversation. True when it happened."""
    try:
        turn_index = int(frame.get("turn_index"))
        version = int(frame.get("version"))
    except (TypeError, ValueError):
        emitter.emit(ev.error("Malformed branch reference."))
        return False

    state = await runner.get_state(session_id)
    restored = await branching.restore(
        session_id, list(state.get("messages") or []), turn_index, version
    )
    if restored is None:
        emitter.emit(ev.error("That version is no longer part of this conversation."))
        await emit_branches(session_id, emitter)
        return False

    await runner.set_messages(session_id, restored)
    emitter.emit(ev.history_replaced("branch"))
    await emit_branches(session_id, emitter)
    return True


async def settle_branches(
    runner: Any,
    session_id: str,
    emitter: Emitter,
    branch: tuple[int, int] | None,
) -> None:
    """Record what a finished turn did to the branch structure, then publish it.

    Two jobs, both needed on every turn:

      * an edit or regenerate has a version waiting to be filled in — the reply
        only came into existence during the run that has just ended;
      * an *ordinary* turn appended to whichever branch is live, so every active
        snapshot is now short by those messages. Skipping that is what would
        make switching away and back silently truncate the conversation to
        where it stood when the fork happened.
    """
    try:
        state = await runner.get_state(session_id)
        messages = list(state.get("messages") or [])
        if branch is not None:
            turn_index, version = branch
            await branching.record_branch(
                session_id,
                messages,
                turn_index,
                version,
                state.get("user_id") or None,
            )
        else:
            await branching.sync_active(session_id, messages)
        await emit_branches(session_id, emitter)
    except Exception:  # noqa: BLE001 - never fail a completed turn over this
        log.warning("Branch bookkeeping failed for %s", session_id, exc_info=True)
