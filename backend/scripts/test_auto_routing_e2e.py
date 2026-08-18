"""Auto routing through the real graph and checkpointer.

    python -m scripts.test_auto_routing_e2e

Runs real turns against real models (no sandbox, so no E2B needed) and asserts
that routing behaves across checkpoint boundaries:

  * auto mode routes a simple question to the fast model
  * an attached image reroutes the same session to the vision model
  * switching auto -> manual -> auto mid-session keeps the thread intact
  * `routing_mode` / `routing_hint` land in state for usage logging
"""

from __future__ import annotations

import asyncio
import base64
import struct
import sys
import uuid
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.agent import runner  # noqa: E402
from app.emitter import Emitter, registry  # noqa: E402
from app.llm_router import AUTO_MODEL_ID, auto_pool_available  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def red_png(w: int = 96, h: int = 96) -> bytes:
    raw = b"".join(b"\x00" + bytes((220, 30, 30)) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


IMAGE_BLOCK = {
    "type": "image",
    "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(red_png()).decode(),
    },
}


async def collect(emitter: Emitter, events: list[dict]) -> None:
    while True:
        e = await emitter.queue.get()
        if e is None:
            return
        events.append(e)


class Check:
    def __init__(self) -> None:
        self.failures = 0

    def __call__(self, label: str, ok: bool, detail: str = "") -> None:
        self.failures += not ok
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  {mark} {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


async def main() -> int:
    if not auto_pool_available():
        print(f"{RED}OPENCODE_API_KEY is not set — nothing to test.{RESET}")
        return 1

    check = Check()
    session_id = str(uuid.uuid4())
    await runner.startup()

    emitter = Emitter()
    registry.register(emitter)
    events: list[dict] = []
    reader = asyncio.create_task(collect(emitter, events))

    try:
        # --- 1. auto + simple question -> fast model ---------------------
        print("\n1. auto mode, simple question")
        final = await runner.run_turn(
            session_id,
            "What is the capital of France? Answer in one word.",
            emitter,
            model_id=AUTO_MODEL_ID,
        )
        check("routing_mode is auto", final.get("routing_mode") == "auto")
        check(
            "routed to the fast model",
            final.get("model_id") == "deepseek_v4_flash",
            f"got {final.get('model_id')}",
        )
        check(
            "hint recorded for usage logging",
            final.get("routing_hint") == "fast_simple",
            f"got {final.get('routing_hint')!r}",
        )

        # --- 2. same session, image attached -> vision model -------------
        print("\n2. same session, image attached")
        state = await runner.get_state(session_id)
        history = list(state["messages"])
        history.append(
            {
                "role": "user",
                "content": [IMAGE_BLOCK, {"type": "text", "text": "What colour is this?"}],
            }
        )
        # Inject the attachment directly: load_content_blocks needs an upload
        # round-trip, and what is under test is the routing, not storage.
        graph = runner.get_graph()
        config = {
            "configurable": {"thread_id": session_id, "emitter_id": emitter.id},
            "recursion_limit": 40,
        }
        final = await graph.ainvoke(
            {
                "session_id": session_id,
                "model_id": final["model_id"],
                "routing_mode": "auto",
                "routing_hint": "",
                "messages": history,
                "pending": [],
                "tool_results": [],
                "iterations": 0,
                "stop_reason": "",
                "usage": state.get("usage") or {},
            },
            config=config,
        )
        check(
            "rerouted to the vision model",
            final.get("model_id") == "qwen3_7_plus",
            f"got {final.get('model_id')}",
        )
        check("hint is visual_structural", final.get("routing_hint") == "visual_structural")
        # The turn may span several messages (the model can call tools before
        # answering), so scan every assistant turn it produced, not just the
        # last message in the list.
        answer = " ".join(
            b.get("text", "")
            for m in final["messages"][len(history) - 1 :]
            if m.get("role") == "assistant" and isinstance(m.get("content"), list)
            for b in m["content"]
            if b.get("type") == "text"
        )
        check("image actually reached the model", "red" in answer.lower(), answer[:70])

        routed = [e for e in events if e["type"] == "model_changed"]
        check(
            "a routing notice was emitted",
            any(e.get("routing_mode") == "auto" for e in routed),
            f"{len(routed)} model_changed event(s)",
        )
        check(
            "notice carries a human reason",
            any(e.get("reason") for e in routed),
            next((e.get("reason") for e in routed if e.get("reason")), ""),
        )

        # --- 3. switch to manual mid-session -----------------------------
        print("\n3. switch auto -> manual mid-session")
        before = len((await runner.get_state(session_id))["messages"])
        final = await runner.run_turn(
            session_id,
            "In one word, what colour did I just show you?",
            emitter,
            model_id="grok-4-5",
        )
        check("routing_mode is manual", final.get("routing_mode") == "manual")
        check("model is the manual pick", final.get("model_id") == "grok-4-5")
        check("hint cleared in manual mode", not final.get("routing_hint"))
        check(
            "checkpoint history intact and growing",
            len(final["messages"]) > before,
            f"{before} -> {len(final['messages'])} messages",
        )

        # --- 4. switch back to auto --------------------------------------
        print("\n4. switch manual -> auto")
        final = await runner.run_turn(
            session_id, "Thanks! Anything else I should know?", emitter,
            model_id=AUTO_MODEL_ID,
        )
        check("routing_mode is auto again", final.get("routing_mode") == "auto")
        check("a hint was produced", bool(final.get("routing_hint")))
        check(
            "conversation still coherent",
            len(final["messages"]) >= 8,
            f"{len(final['messages'])} messages",
        )
        check(
            "usage totals carried across the switches",
            (final.get("usage") or {}).get("input_tokens", 0) > 0,
            str(final.get("usage")),
        )
    finally:
        emitter.close()
        await reader
        registry.unregister(emitter.id)
        await runner.shutdown()

    print(
        f"\n{GREEN}all checks passed{RESET}"
        if not check.failures
        else f"\n{RED}{check.failures} check(s) failed{RESET}"
    )
    return 1 if check.failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
