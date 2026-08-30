"""Where the wait actually goes, measured rather than guessed.

Three passes, because "slow" has three different fixes:

  1. **App layer** — everything between the user's Enter and the first byte
     sent to a provider: the credit gate, the checkpoint read, attachment
     loading, auto classification. Measured directly, with no model involved.
  2. **Provider** — time-to-first-token and total generation time per model,
     for one short prompt and one longer one. This is the number that decides
     whether the default model is the problem.
  3. **End to end** — a real websocket turn against the running backend,
     timestamping every frame, so the two numbers above can be checked against
     what a user experiences.

Run passes selectively; the provider pass costs real credits.

    python -m scripts.profile_latency --app
    python -m scripts.profile_latency --provider
    python -m scripts.profile_latency --e2e            # backend must be up
    python -m scripts.profile_latency --all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

SHORT = "Reply with exactly the word: ok"
LONGER = "In three sentences, explain what a websocket is."


def ms(t: float) -> str:
    return f"{t * 1000:8.1f} ms"


def row(label: str, seconds: float, note: str = "") -> None:
    print(f"  {label:<34} {ms(seconds)}   {note}")


# ---------------------------------------------------------------------------
# 1. app layer
# ---------------------------------------------------------------------------


async def app_pass(repeats: int = 3) -> None:
    from app.agent import runner
    from app.agent.task_classifier import classify_task
    from app.credits import ensure_can_start
    from app.files import load_content_blocks
    from app.llm_router import auto_pool_available, model_for_hint

    print("\n=== 1. App layer (no model call) ===")
    await runner.startup()

    session_id = str(uuid.uuid4())
    user_id = None

    async def timed(fn, n=repeats):
        samples = []
        for _ in range(n):
            t0 = time.perf_counter()
            await fn()
            samples.append(time.perf_counter() - t0)
        return samples

    s = await timed(lambda: ensure_can_start(user_id, "chat"))
    row("credits.ensure_can_start", statistics.median(s), f"n={repeats} first={ms(s[0])}")

    s = await timed(lambda: runner.get_state(session_id))
    row("runner.get_state (checkpoint)", statistics.median(s), f"n={repeats}")

    s = await timed(lambda: load_content_blocks(session_id, []))
    row("files.load_content_blocks([])", statistics.median(s), "no attachments")

    state = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": SHORT}]}],
        "routing_mode": "auto",
    }
    t0 = time.perf_counter()
    for _ in range(100):
        classify_task(dict(state))
    row("classify_task x100", time.perf_counter() - t0, "auto routing, pure CPU")

    t0 = time.perf_counter()
    for _ in range(100):
        auto_pool_available()
        model_for_hint("fast_simple")
    row("auto_pool_available x100", time.perf_counter() - t0)

    await runner.shutdown()


# ---------------------------------------------------------------------------
# 2. providers
# ---------------------------------------------------------------------------


async def one_call(model_id: str, prompt: str, with_tools: bool) -> dict:
    from app.agent.prompts import SYSTEM_PROMPT
    from app.llm_router import model_meta, stream_with_fallback
    from app.tools.schemas import TOOLS

    meta = model_meta(model_id)
    tools = TOOLS if (with_tools and meta["supports_tools"]) else []
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]

    t0 = time.perf_counter()
    first: float | None = None
    first_visible: float | None = None
    chars = 0
    usage: dict = {}
    async for se in stream_with_fallback(
        model_id, messages=messages, tools=tools, system=SYSTEM_PROMPT, section="probe"
    ):
        now = time.perf_counter() - t0
        if first is None:
            first = now
        if se.kind in ("text_delta", "thinking_delta"):
            if first_visible is None:
                first_visible = now
            chars += len(se.content or "")
        if se.kind == "done" and se.message is not None:
            usage = se.message.usage
    total = time.perf_counter() - t0
    return {
        "ttfe": first or total,
        "ttft": first_visible or total,
        "total": total,
        "chars": chars,
        "usage": usage,
    }


async def provider_pass(models: list[str] | None, with_tools: bool) -> None:
    from app.llm_router import MODEL_REGISTRY, display_name, is_available

    ids = models or [m for m in MODEL_REGISTRY if is_available(m)]
    print(f"\n=== 2. Providers (tools={'on' if with_tools else 'off'}) ===")
    print(f"  {'model':<20} {'ttft':>10} {'total':>10}  prompt")
    results: dict[str, dict] = {}
    for mid in ids:
        for label, prompt in (("short", SHORT), ("longer", LONGER)):
            try:
                r = await one_call(mid, prompt, with_tools)
            except Exception as exc:  # noqa: BLE001
                print(f"  {mid:<20} {'FAILED':>10}  {type(exc).__name__}: {exc}")
                continue
            print(
                f"  {mid:<20} {ms(r['ttft']):>10} {ms(r['total']):>10}  "
                f"{label} · {r['chars']} chars · {display_name(mid)}"
            )
            results.setdefault(mid, {})[label] = r
    if results:
        ranked = sorted(results.items(), key=lambda kv: kv[1].get("short", {}).get("ttft", 9e9))
        print("\n  Fastest first token (short prompt):")
        for mid, r in ranked:
            if "short" in r:
                print(f"    {mid:<20} {ms(r['short']['ttft'])}")


# ---------------------------------------------------------------------------
# 3. end to end
# ---------------------------------------------------------------------------


async def e2e_pass(base: str, prompt: str, section: str = "chat") -> None:
    import websockets

    session_id = str(uuid.uuid4())
    url = f"{base}/ws/{session_id}?section={section}"
    print(f"\n=== 3. End to end ({section}) ===\n  {url}")

    t_connect = time.perf_counter()
    async with websockets.connect(url, max_size=None) as ws:
        connected_at = None
        marks: list[tuple[str, float]] = []
        # drain the connect burst
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if frame.get("type") == "connected":
                connected_at = time.perf_counter() - t_connect
                break
        row("ws connect -> connected", connected_at or 0.0)

        # settle: let model_changed / preview frames land
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=0.6)
        except asyncio.TimeoutError:
            pass

        t0 = time.perf_counter()
        await ws.send(json.dumps({"type": "user_message", "content": prompt}))
        seen: set[str] = set()
        chars = 0
        while True:
            try:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
            except asyncio.TimeoutError:
                marks.append(("TIMEOUT", time.perf_counter() - t0))
                break
            kind = frame.get("type")
            now = time.perf_counter() - t0
            if kind in ("agent_token", "agent_thinking_delta"):
                chars += len(frame.get("content") or "")
            if kind not in seen:
                seen.add(kind)
                marks.append((kind, now))
            if kind == "agent_done":
                marks.append(("agent_done", now))
                break
            if kind == "error":
                print(f"  ERROR frame: {frame}")

        print(f"  send -> first frame of each type ({chars} streamed chars):")
        for kind, at in marks:
            row(f"  {kind}", at)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--app", action="store_true")
    p.add_argument("--provider", action="store_true")
    p.add_argument("--e2e", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--models", default="", help="comma-separated model ids")
    p.add_argument("--tools", action="store_true", help="send the tool schemas too")
    p.add_argument("--base", default="ws://127.0.0.1:8000")
    p.add_argument("--prompt", default=SHORT)
    args = p.parse_args()

    if not any([args.app, args.provider, args.e2e, args.all]):
        args.all = True

    async def go():
        if args.app or args.all:
            await app_pass()
        if args.provider or args.all:
            ids = [m.strip() for m in args.models.split(",") if m.strip()] or None
            await provider_pass(ids, args.tools)
        if args.e2e or args.all:
            await e2e_pass(args.base, args.prompt)

    asyncio.run(go())


if __name__ == "__main__":
    main()
