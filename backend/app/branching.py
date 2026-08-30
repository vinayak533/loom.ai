"""Editing an earlier message without losing what it already said.

The rule this enforces is the one the user asked for and the one every other
assistant follows: **an edit forks, it never overwrites**. Send a different
version of your third message and the conversation re-runs from there, but the
replies the original produced stay reachable behind a `‹ 1/2 ›` control on that
message.

How a branch is stored is written up at length on `public.message_branches` in
schema.sql. The short version, because everything below depends on it:

    a branch is the whole conversation from turn N onwards, stored as one row

The live conversation stays exactly where it was — `AgentState["messages"]`
inside the LangGraph checkpoint — and remains the single thing the model is
ever shown. This module only ever does two things to it: cut it at a turn, and
splice a stored suffix back on. That is what makes the feature honest rather
than cosmetic. Switching branches does not merely redraw the transcript; it
puts the model back in the state that branch left it in, so the next turn
continues the branch you are actually looking at.

Turn numbering is by *user turn* (see `repository.user_turn_positions`), never
by position in the messages array, because a `tool_result` carrier has
``role == "user"`` too and would otherwise shift every pointer the moment a
turn used a tool.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import repository

log = logging.getLogger(__name__)


def turn_text(message: dict) -> str:
    """The prose of one user turn, for labelling its version in the switcher."""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    parts = [
        str(block.get("text") or "")
        for block in (content or [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(parts).strip()


def cut_at(messages: list[dict], turn_index: int) -> int | None:
    """Where turn ``turn_index`` starts in ``messages``, or None if it doesn't.

    None rather than an exception: a stale browser tab can ask to edit a turn
    that a branch switch has since removed, and that is a thing to decline
    politely, not a server error.
    """
    positions = repository.user_turn_positions(messages)
    if turn_index < 0 or turn_index >= len(positions):
        return None
    return positions[turn_index]


async def snapshot_original(
    session_id: str,
    messages: list[dict],
    turn_index: int,
    user_id: str | None,
) -> int:
    """Make sure the conversation as it stands is recorded as a version.

    Called immediately before an edit. The first edit at a turn has to preserve
    two things — what was there (version 1) and what is about to replace it
    (version 2) — and only the first of those is anyone's job but this
    function's. On the second and later edits the earlier versions are already
    stored and this is a no-op.

    Returns the version number the *new* branch should be given.
    """
    cut = cut_at(messages, turn_index)
    if cut is None:
        return 1

    existing = await repository.branch_versions(session_id, turn_index)
    if not existing:
        await repository.save_branch(
            session_id,
            turn_index,
            version=1,
            messages=messages[cut:],
            label=turn_text(messages[cut]),
            user_id=user_id,
        )
        return 2

    # Re-record the live version before leaving it. It has almost certainly
    # grown since it was written — every ordinary turn taken while it was the
    # active branch appended to the live conversation and not to the stored
    # snapshot — and a branch you cannot return to intact is not a branch.
    active = next((row for row in existing if row.get("is_active")), None)
    if active:
        await repository.save_branch(
            session_id,
            turn_index,
            version=int(active["version"]),
            messages=messages[cut:],
            label=active.get("label") or turn_text(messages[cut]),
            user_id=user_id,
        )
    return max(int(row["version"]) for row in existing) + 1


async def record_branch(
    session_id: str,
    messages: list[dict],
    turn_index: int,
    version: int,
    user_id: str | None,
) -> None:
    """Store the conversation from ``turn_index`` on as ``version``, and
    make it the active one."""
    cut = cut_at(messages, turn_index)
    if cut is None:
        return
    await repository.save_branch(
        session_id,
        turn_index,
        version=version,
        messages=messages[cut:],
        label=turn_text(messages[cut]),
        user_id=user_id,
    )
    await repository.set_active_branch(session_id, turn_index, version)


async def sync_active(session_id: str, messages: list[dict]) -> None:
    """Bring every active snapshot back in step with the live conversation.

    Run at the end of each turn. Without it a branched session drifts: the
    snapshot for turn 2 was written when the thread ended at turn 3, and after
    two more ordinary exchanges it no longer describes the branch it names — so
    switching away and back would silently truncate the conversation to where
    it stood when the fork happened.

    Only sessions that have actually been branched pay for this, and a session
    that never edits anything makes exactly one indexed lookup that returns
    nothing.
    """
    rows = await repository.list_branches(session_id)
    if not rows:
        return
    positions = repository.user_turn_positions(messages)
    for row in rows:
        if not row.get("is_active"):
            continue
        turn_index = int(row["turn_index"])
        if turn_index >= len(positions):
            # The live conversation no longer reaches this turn. That is not a
            # state an ordinary turn can produce — only a restore to a shorter
            # branch can — and the restore path writes the snapshots itself.
            continue
        cut = positions[turn_index]
        await repository.save_branch(
            session_id,
            turn_index,
            version=int(row["version"]),
            messages=messages[cut:],
            label=row.get("label"),
            user_id=row.get("user_id"),
        )


async def restore(
    session_id: str, messages: list[dict], turn_index: int, version: int
) -> list[dict] | None:
    """The conversation with ``version`` spliced in at ``turn_index``.

    Returns the new message list for the caller to write into the checkpoint,
    or None when the branch or the turn is gone. The caller does the writing
    because this module deliberately knows nothing about graphs — the Chat/Code
    loop and the specialist loop have different ones, and both use this.
    """
    cut = cut_at(messages, turn_index)
    if cut is None:
        return None
    row = await repository.get_branch(session_id, turn_index, version)
    if row is None:
        return None
    suffix = row.get("messages") or []
    if not isinstance(suffix, list):
        return None

    # The live suffix is written back to whichever version it belongs to before
    # it is replaced — the same "do not lose the branch you are leaving" rule
    # `snapshot_original` applies, and the reason switching back and forth any
    # number of times is lossless.
    existing = await repository.branch_versions(session_id, turn_index)
    active = next((r for r in existing if r.get("is_active")), None)
    if active and int(active["version"]) != version:
        await repository.save_branch(
            session_id,
            turn_index,
            version=int(active["version"]),
            messages=messages[cut:],
            label=active.get("label"),
        )

    await repository.set_active_branch(session_id, turn_index, version)
    # Branches recorded *deeper* than this turn describe a conversation that
    # this restore has just replaced wholesale. They cannot be reached from the
    # thread any more, so they are dropped rather than left to be offered by a
    # switcher whose own turn no longer exists.
    await repository.drop_branches_from(session_id, turn_index + 1)
    return list(messages[:cut]) + list(suffix)


async def summary(session_id: str) -> list[dict[str, Any]]:
    """What the browser needs to draw the switchers: one entry per branched turn.

    Message payloads are deliberately not included. They are tens of kilobytes
    apiece and are only needed at the moment someone actually switches, which
    is a separate request.
    """
    rows = await repository.list_branches(session_id)
    by_turn: dict[int, list[dict]] = {}
    for row in rows:
        by_turn.setdefault(int(row["turn_index"]), []).append(row)

    out: list[dict[str, Any]] = []
    for turn_index in sorted(by_turn):
        versions = sorted(by_turn[turn_index], key=lambda r: int(r["version"]))
        active = next(
            (int(r["version"]) for r in versions if r.get("is_active")),
            int(versions[-1]["version"]),
        )
        out.append(
            {
                "turn_index": turn_index,
                "active": active,
                "versions": [
                    {
                        "version": int(r["version"]),
                        "label": r.get("label") or "",
                        "created_at": r.get("created_at"),
                    }
                    for r in versions
                ],
            }
        )
    return out
