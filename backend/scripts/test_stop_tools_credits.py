"""The two stop cases that are easy to get wrong.

1. **Stopping mid-tool-call.** The failure this guards against is an orphaned
   `tool_use` — a call the model asked for that never got a `tool_result`. The
   next request on that session is then rejected outright by the provider, so
   the session is dead and the only symptom is an error on the *following*
   turn, a long way from the stop that caused it.

2. **Billing a stopped turn.** A stop must charge for the output the provider
   actually produced and no more. Both directions matter: billing the full
   intended response would overcharge, and billing nothing would make Stop a
   way to use the product for free.

Needs the backend running on :8000.

    .venv/Scripts/python -m scripts.test_stop_tools_credits
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

WS = "ws://127.0.0.1:8000/ws/{sid}?section=code"
_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


async def test_stop_mid_tool() -> None:
    print("\n[1] Stopping during a tool batch leaves no orphaned tool_use")
    sid = str(uuid.uuid4())

    async with websockets.connect(WS.format(sid=sid), max_size=None) as ws:
        # connect
        async with asyncio.timeout(30):
            while json.loads(await ws.recv())["type"] != "connected":
                pass

        await ws.send(json.dumps({
            "type": "user_message",
            "content": (
                "Create five files a.txt, b.txt, c.txt, d.txt and e.txt, each "
                "containing one sentence about a different planet. Use one "
                "tool call per file, then read each one back."
            ),
            "file_ids": [],
        }))

        # Stop as soon as a tool call actually starts — that is the window the
        # orphan bug lives in.
        started = 0
        stopped_at = None
        frames: list[dict] = []
        async with asyncio.timeout(180):
            while True:
                frame = json.loads(await ws.recv())
                frames.append(frame)
                if frame["type"] == "tool_call_start":
                    started += 1
                    if started == 1 and stopped_at is None:
                        stopped_at = frame["call_id"]
                        await ws.send(json.dumps({"type": "cancel"}))
                if frame["type"] == "agent_done":
                    break

        done = next(
            (f for f in reversed(frames) if f["type"] == "agent_done"), None
        )
        check("at least one tool call ran before the stop", started >= 1,
              f"{started} started")
        check(
            "the turn closed cleanly",
            bool(done) and done.get("reason") in ("cancelled", "end_turn"),
            f"reason={done.get('reason') if done else None}",
        )

        starts = {f["call_id"] for f in frames if f["type"] == "tool_call_start"}
        results = {f["call_id"] for f in frames if f["type"] == "tool_call_result"}
        check(
            "every tool card the UI opened was also closed",
            starts == results,
            f"{len(starts)} started / {len(results)} resolved",
        )

    # The half that actually matters: what is in the conversation the model
    # will be handed next.
    from app.agent import runner  # noqa: PLC0415

    await runner.startup()
    try:
        messages = (await runner.get_state(sid)).get("messages") or []
        uses, results = set(), set()
        for message in messages:
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    uses.add(block.get("id"))
                elif block.get("type") == "tool_result":
                    results.add(block.get("tool_use_id"))
        check(
            "every tool_use in the checkpoint has a matching tool_result",
            uses == results,
            f"{len(uses)} calls, {len(results)} results"
            + (f", orphaned: {sorted(uses - results)}" if uses - results else ""),
        )
        check(
            "the conversation ends on an assistant turn",
            bool(messages) and messages[-1].get("role") == "assistant",
            f"last role={messages[-1].get('role') if messages else None}",
        )

        # And the strongest proof available: send another turn. A conversation
        # with an orphaned tool_use is rejected by the provider, so this
        # succeeding is the real end-to-end verdict.
        async with websockets.connect(WS.format(sid=sid), max_size=None) as ws:
            async with asyncio.timeout(30):
                while json.loads(await ws.recv())["type"] != "connected":
                    pass
            await ws.send(json.dumps({
                "type": "user_message",
                "content": "In one short sentence: what did you manage to do?",
                "file_ids": [],
            }))
            reply, errored = "", None
            async with asyncio.timeout(120):
                while True:
                    frame = json.loads(await ws.recv())
                    if frame["type"] == "agent_token":
                        reply += frame["content"]
                    elif frame["type"] == "error":
                        errored = frame["message"]
                    elif frame["type"] == "agent_done":
                        break
        check(
            "the session is still usable after the stop",
            len(reply.strip()) > 0 and errored is None,
            errored or f"replied {len(reply)} chars",
        )
    finally:
        await runner.shutdown()


async def test_stop_billing() -> None:
    print("\n[2] A stopped turn is billed for what it generated, and no more")

    from app.credits import get_balance  # noqa: PLC0415
    from app.db.supabase_client import get_client  # noqa: PLC0415

    sid = str(uuid.uuid4())
    before = (await get_balance(None)).balance

    generated = 0
    async with websockets.connect(
        WS.format(sid=sid).replace("section=code", "section=chat"), max_size=None
    ) as ws:
        async with asyncio.timeout(30):
            while json.loads(await ws.recv())["type"] != "connected":
                pass
        await ws.send(json.dumps({
            "type": "user_message",
            "content": (
                "Write a 4000 word essay on the history of the bicycle. "
                "Be exhaustive."
            ),
            "file_ids": [],
        }))
        # Reasoning counts. The chat default is a thinking model, so most of
        # what a stopped turn has produced by the time you press the button is
        # `agent_thinking_delta`, not `agent_token` — and the estimator bills
        # both. Waiting only on visible text meant the stop never fired, which
        # is exactly the asymmetry this check exists to catch.
        text = ""
        thinking = ""
        async with asyncio.timeout(240):
            while True:
                frame = json.loads(await ws.recv())
                if frame["type"] == "agent_token":
                    text += frame["content"]
                elif frame["type"] == "agent_thinking_delta":
                    thinking += frame["content"]
                elif frame["type"] == "agent_done":
                    break
                if generated == 0 and len(text) + len(thinking) > 500:
                    generated = len(text) + len(thinking)
                    await ws.send(json.dumps({"type": "cancel"}))

    # Give the ledger write a moment; it is fired, not awaited.
    await asyncio.sleep(2.0)
    after = (await get_balance(None)).balance
    charged = float(before) - float(after)

    produced = text + thinking
    check("the stop happened partway through a long answer",
          generated > 0 and len(produced) < 4000 * 5,
          f"{len(text)} chars of text + {len(thinking)} of reasoning")
    check(
        "the stopped turn was charged something",
        charged > 0,
        f"{charged:.4f} credits",
    )

    client = get_client()
    rows = (
        client.table("token_usage")
        .select("input_tokens,output_tokens,cost_estimate")
        .eq("session_id", sid)
        .execute()
    ).data or []
    billed_out = sum(r["output_tokens"] for r in rows)
    # The estimator is CHARS_PER_TOKEN = 4, so this is the number it should
    # have arrived at for the text that was actually streamed.
    expected = max(1, (len(produced) + 3) // 4)
    check(
        "output tokens billed match the text actually streamed",
        abs(billed_out - expected) <= max(20, expected * 0.35),
        f"billed {billed_out}, streamed ~{expected}",
    )
    check(
        "the turn was not billed for the essay it never wrote",
        # 4000 words is ~5300 tokens. Anything near that means the full
        # intended response was charged for.
        billed_out < 3000,
        f"billed {billed_out} output tokens",
    )


async def main() -> None:
    print("=" * 68)
    print("Stop: tool batches and billing")
    print("=" * 68)
    await test_stop_mid_tool()
    await test_stop_billing()
    print("\n" + "=" * 68)
    if _failures:
        print(f"{len(_failures)} check(s) failed:")
        for name in _failures:
            print(f"  - {name}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
