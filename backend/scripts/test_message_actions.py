"""End-to-end proof for stop, edit-and-branch, and regenerate.

Drives the real `/ws/{session_id}` socket against the running backend — a real
model call, a real checkpoint, real Supabase rows. Nothing here is mocked,
because the things most likely to be wrong are precisely the ones a mock would
paper over: whether `aupdate_state` really replaces the message list, whether a
stopped stream leaves valid history behind, and whether the credit ledger sees
the partial turn.

Run the backend first, then:

    .venv/Scripts/python -m scripts.test_message_actions
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

WS = "ws://127.0.0.1:8000/ws/{sid}?section=chat"

PASS = "  PASS"
FAIL = "  FAIL"
_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{PASS if ok else FAIL}  {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


async def drain(ws, until: set[str], timeout: float = 90.0) -> list[dict]:
    """Collect frames until one of ``until`` arrives (inclusive)."""
    out: list[dict] = []
    async with asyncio.timeout(timeout):
        while True:
            frame = json.loads(await ws.recv())
            out.append(frame)
            if frame.get("type") in until:
                return out


async def drain_turn(ws, timeout: float = 120.0) -> list[dict]:
    """Collect one whole turn: everything up to `agent_done`, plus the
    `branches` frame that follows it.

    Branch bookkeeping is deliberately done *after* the turn closes — the reply
    a new version holds does not exist until the run has finished — so
    `agent_done` alone is not the end of the exchange. Stopping there leaves a
    `branches` frame in the queue, which the next drain then mistakes for its
    own and returns before a single token has arrived.
    """
    out: list[dict] = []
    done = False
    async with asyncio.timeout(timeout):
        while True:
            frame = json.loads(await ws.recv())
            out.append(frame)
            if frame.get("type") == "agent_done":
                done = True
            elif frame.get("type") == "branches" and done:
                return out


def text_of(frames: list[dict]) -> str:
    return "".join(f.get("content", "") for f in frames if f["type"] == "agent_token")


def last(frames: list[dict], kind: str) -> dict | None:
    for frame in reversed(frames):
        if frame.get("type") == kind:
            return frame
    return None


# ---------------------------------------------------------------------------


async def test_stop_preserves_partial() -> str:
    """Stop a long answer mid-stream; the partial text must survive a reload."""
    print("\n[1] Stop mid-stream keeps the partial answer")
    sid = str(uuid.uuid4())

    async with websockets.connect(WS.format(sid=sid), max_size=None) as ws:
        await drain(ws, {"connected"})
        await ws.send(
            json.dumps(
                {
                    "type": "user_message",
                    "content": (
                        "Count slowly from 1 to 120, one number per line, "
                        "with a short note after each."
                    ),
                    "file_ids": [],
                }
            )
        )

        # Let a real answer start, then stop it.
        streamed: list[dict] = []
        async with asyncio.timeout(90):
            while True:
                frame = json.loads(await ws.recv())
                streamed.append(frame)
                if frame["type"] == "agent_token" and len(text_of(streamed)) > 60:
                    break
                if frame["type"] == "agent_done":
                    break

        before = text_of(streamed)
        check("the model streamed something before the stop", len(before) > 0,
              f"{len(before)} chars")

        await ws.send(json.dumps({"type": "cancel"}))
        tail = await drain(ws, {"agent_done"})
        done = last(tail, "agent_done")
        streamed += tail

        check(
            "the turn ends with reason 'cancelled'",
            bool(done) and done.get("reason") == "cancelled",
            f"reason={done.get('reason') if done else 'no agent_done'}",
        )
        check(
            "no 'Run cancelled.' error is raised any more",
            not any(
                f.get("type") == "error" and "cancelled" in f.get("message", "").lower()
                for f in tail
            ),
        )
        usage = last(streamed, "usage")
        check(
            "usage was reported for the tokens actually generated",
            bool(usage) and usage.get("output_tokens", 0) > 0,
            f"output_tokens={usage.get('output_tokens') if usage else None}",
        )

    # The real test: reconnect and read the transcript back from the checkpoint.
    from app.agent import runner  # noqa: PLC0415

    await runner.startup()
    try:
        state = await runner.get_state(sid)
        messages = state.get("messages") or []
        assistant = [m for m in messages if m.get("role") == "assistant"]
        saved = "".join(
            b.get("text", "")
            for m in assistant
            for b in (m.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
        check(
            "the partial answer is in the checkpoint, not discarded",
            len(saved) > 40 and saved.startswith(text_of(streamed)[:40]),
            f"{len(saved)} chars saved",
        )
        check(
            "the stored turn says it was stopped",
            "Stopped by the user" in saved,
        )
        check(
            "the conversation ends on a complete user/assistant pair",
            bool(messages)
            and messages[0].get("role") == "user"
            and messages[-1].get("role") == "assistant",
        )
    finally:
        await runner.shutdown()

    return sid


async def test_edit_branches() -> None:
    """Edit turn 0; both the original and the edit must be reachable."""
    print("\n[2] Editing a message forks rather than overwrites")
    sid = str(uuid.uuid4())

    async with websockets.connect(WS.format(sid=sid), max_size=None) as ws:
        await drain(ws, {"connected"})

        await ws.send(json.dumps({
            "type": "user_message",
            "content": "Name exactly one primary colour. One word only.",
            "file_ids": [],
        }))
        first = await drain_turn(ws)
        original_answer = text_of(first)
        check("the original turn answered", len(original_answer) > 0,
              repr(original_answer[:50]))

        # Edit that same turn.
        await ws.send(json.dumps({
            "type": "edit_message",
            "turn_index": 0,
            "content": "Name exactly one European capital city. One word only.",
            "file_ids": [],
        }))
        edited = await drain_turn(ws)
        edited_answer = text_of(edited)
        check("the edited turn answered", len(edited_answer) > 0,
              repr(edited_answer[:50]))
        check(
            "the edit produced a different answer",
            edited_answer.strip() != original_answer.strip(),
        )
        check(
            "the client was told the history was replaced",
            any(f.get("type") == "history_replaced" for f in edited),
        )

        # The switcher payload.
        branches = last(edited, "branches")
        if not check("a 'branches' frame arrived", bool(branches)):
            return
        entries = branches.get("branches") or []
        entry = next((e for e in entries if e["turn_index"] == 0), None)
        if not check("turn 0 is recorded as branched", bool(entry)):
            return
        check(
            "turn 0 has two versions (1/2)",
            len(entry["versions"]) == 2,
            f"versions={[v['version'] for v in entry['versions']]}",
        )
        check("the edit is the active version", entry["active"] == 2,
              f"active={entry['active']}")
        labels = [v["label"] for v in entry["versions"]]
        check(
            "version 1 still carries the original wording",
            any("primary colour" in (l or "") for l in labels),
            f"labels={labels}",
        )

        # Switch back to the original and confirm the model state follows.
        await ws.send(json.dumps({
            "type": "switch_branch", "turn_index": 0, "version": 1,
        }))
        switched = await drain(ws, {"branches"}, timeout=30)
        check(
            "switching back is announced as a history replacement",
            any(f.get("type") == "history_replaced" for f in switched),
        )
        back = last(switched, "branches")
        entry = next(
            (e for e in (back.get("branches") or []) if e["turn_index"] == 0), None
        )
        check("version 1 is active again", bool(entry) and entry["active"] == 1,
              f"active={entry['active'] if entry else None}")

    from app.agent import runner  # noqa: PLC0415

    await runner.startup()
    try:
        state = await runner.get_state(sid)
        messages = state.get("messages") or []
        first_user = next((m for m in messages if m.get("role") == "user"), {})
        wording = "".join(
            b.get("text", "")
            for b in (first_user.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
        check(
            "the restored branch is what the MODEL sees, not just the screen",
            "primary colour" in wording,
            repr(wording[:60]),
        )
        answers = "".join(
            b.get("text", "")
            for m in messages if m.get("role") == "assistant"
            for b in (m.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
        check(
            "the edited branch's reply is not in the restored conversation",
            edited_answer.strip()[:30] not in answers,
        )
    finally:
        await runner.shutdown()


async def test_regenerate() -> None:
    """Regenerate re-runs the same question and keeps the old answer."""
    print("\n[3] Regenerate re-runs the last turn and keeps the old reply")
    sid = str(uuid.uuid4())

    async with websockets.connect(WS.format(sid=sid), max_size=None) as ws:
        await drain(ws, {"connected"})
        await ws.send(json.dumps({
            "type": "user_message",
            "content": "Invent a two-word name for a coffee shop. Name only.",
            "file_ids": [],
        }))
        first = await drain_turn(ws)
        first_answer = text_of(first)
        check("the first answer arrived", len(first_answer) > 0,
              repr(first_answer[:40]))

        await ws.send(json.dumps({"type": "regenerate"}))
        again = await drain_turn(ws)
        second_answer = text_of(again)
        check("the regenerated answer arrived", len(second_answer) > 0,
              repr(second_answer[:40]))

        branches = last(again, "branches")
        entry = next(
            (e for e in (branches.get("branches") or []) if e["turn_index"] == 0),
            None,
        ) if branches else None
        check(
            "the previous answer is kept as version 1",
            bool(entry) and len(entry["versions"]) == 2,
            f"versions={[v['version'] for v in entry['versions']] if entry else None}",
        )

    from app.agent import runner  # noqa: PLC0415

    await runner.startup()
    try:
        state = await runner.get_state(sid)
        users = [
            m for m in (state.get("messages") or [])
            if m.get("role") == "user"
            and any(
                isinstance(b, dict) and b.get("type") == "text"
                for b in (m.get("content") or [])
            )
        ]
        check(
            "regenerating did not duplicate the user's question",
            len(users) == 1,
            f"{len(users)} user turns in the checkpoint",
        )
    finally:
        await runner.shutdown()


async def main() -> None:
    print("=" * 68)
    print("Message actions — stop, edit/branch, regenerate")
    print("=" * 68)
    await test_stop_preserves_partial()
    await test_edit_branches()
    await test_regenerate()

    print("\n" + "=" * 68)
    if _failures:
        print(f"{len(_failures)} check(s) failed:")
        for name in _failures:
            print(f"  - {name}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
