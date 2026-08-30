"""Real calls to the models added or changed in this pass. Spends real tokens.

    python scripts/test_live_models.py                # every changed model
    python scripts/test_live_models.py gpt-oss-120b   # just one

Everything goes through `app.llm_router.call_model`, not a hand-rolled client,
so this proves the *wiring* — registry id -> settings slug -> adapter -> wire
id — and not merely that the provider is up. Its companion,
`scripts/test_model_fallback.py`, covers the fallback logic offline.

A model with no key configured is skipped, not failed: this is meant to be
runnable on a deployment that only has some providers.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.llm_router as R  # noqa: E402
from app.llm_router import ModelCallError, ModelUnavailableError  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
)

#: The models this pass touched. Everything else in the registry was already
#: verified and is not re-spent on here.
TARGETS = ("gpt-oss-120b", "mimo_v2_5_pro", "qwen3_7_plus")

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
}

_failures = 0
_skipped = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"    [{mark}] {label}" + (f" {DIM}({detail}){RESET}" if detail else ""))


async def collect(model_id: str, **kw):
    """Drain a streamed call; return (text, final NormalizedMessage)."""
    text = ""
    final = None
    async for se in R.call_model(model_id, stream=True, **kw):
        if se.kind == "text_delta":
            text += se.content or ""
        elif se.kind == "done":
            final = se.message
    return text, final


async def probe(model_id: str) -> None:
    global _skipped
    meta = R.MODEL_REGISTRY[model_id]
    wire = R._resolve(meta, "model_name")
    print(f"\n{model_id}  {DIM}-> {wire} via {meta['provider']}{RESET}")

    if not R.is_available(model_id):
        _skipped += 1
        print(f"    {YELLOW}SKIP{RESET} {meta['required_key'].upper()} is not set")
        return

    # --- 1. plain completion --------------------------------------------
    try:
        text, final = await collect(
            model_id,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Reply with exactly the word PONG and nothing else."}
            ]}],
            tools=[],
            system="You follow instructions literally.",
        )
        check("streamed a chat completion", bool(text.strip()), repr(text.strip()[:60]))
        check(
            "the provider echoed the expected wire id back",
            final is not None and final.model_name == wire,
            getattr(final, "model_name", "None"),
        )
        check(
            "usage was reported (so the call can be billed)",
            final is not None and final.usage.get("output_tokens", 0) > 0,
            str(getattr(final, "usage", {})),
        )
    except (ModelUnavailableError, ModelCallError) as exc:
        check("streamed a chat completion", False, str(exc)[:200])
        return

    # --- 2. tool calling, only where the registry claims it --------------
    if not meta["supports_tools"]:
        print(f"    {DIM}(registry says no tool support — not tested){RESET}")
        return
    try:
        _, final = await collect(
            model_id,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "What is the weather in Paris? Use the tool."}
            ]}],
            tools=[WEATHER_TOOL],
            system="Use the provided tool when it fits the question.",
        )
        blocks = final.content if final else []
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        check(
            "`supports_tools: True` is true — it emitted a real tool_use block",
            bool(calls),
            str(calls[0])[:120] if calls else f"stop_reason={getattr(final, 'stop_reason', '?')}",
        )
        if calls:
            check(
                "with the right tool and parseable arguments",
                calls[0].get("name") == "get_weather"
                and isinstance(calls[0].get("input"), dict),
                f"{calls[0].get('name')} {calls[0].get('input')}",
            )
    except (ModelUnavailableError, ModelCallError) as exc:
        check("tool calling", False, str(exc)[:200])


async def main() -> int:
    targets = sys.argv[1:] or list(TARGETS)
    unknown = [t for t in targets if t not in R.MODEL_REGISTRY]
    if unknown:
        print(f"{RED}Not in the registry: {', '.join(unknown)}{RESET}")
        return 1

    print("Live model verification — this spends real tokens")
    for model_id in targets:
        await probe(model_id)

    print()
    if _failures:
        print(f"{RED}{_failures} check(s) failed.{RESET}")
        return 1
    note = f" ({_skipped} model(s) skipped for missing keys)" if _skipped else ""
    print(f"{GREEN}All checks passed{RESET}{note}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
