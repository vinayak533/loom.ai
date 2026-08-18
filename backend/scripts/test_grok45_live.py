"""Real end-to-end calls to Grok 4.5. Requires a working XAI_API_KEY.

This is the half of the Grok 4.5 verification that `test_grok45_wiring.py`
deliberately cannot cover: whether api.x.ai actually accepts the body this
project builds. It spends real tokens — a few hundred, so cents at most.

    python scripts/test_grok45_live.py

Three things are checked, in order of what breaks first when a model id is
wrong: the id resolves at all, a plain chat turn streams back, and a
tool-calling turn comes back as a real tool_use block.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402
from app.llm_router import (  # noqa: E402
    MODEL_REGISTRY,
    ModelCallError,
    ModelUnavailableError,
    _resolve,
    call_model,
    estimate_cost,
    is_available,
)

MODEL_ID = "grok-4-5"
results: list[bool] = []


def check(label: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))


WEATHER_TOOL = [{
    "name": "get_weather",
    "description": "Get the current temperature for a city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
}]


async def collect(**kw):
    events = []
    async for event in call_model(MODEL_ID, **kw):
        events.append(event)
    return events


async def main() -> int:
    settings = get_settings()
    wire_id = _resolve(MODEL_REGISTRY[MODEL_ID], "model_name")

    print(f"Model id `{MODEL_ID}` -> wire id `{wire_id}` at {settings.xai_base_url}\n")

    if not is_available(MODEL_ID):
        print("XAI_API_KEY is not set in backend/.env — nothing to test against.")
        print("This script is the only part of the Grok 4.5 verification that")
        print("needs it; scripts/test_grok45_wiring.py covers the rest offline.")
        return 2

    print("1. Plain chat turn")
    try:
        events = await collect(
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Reply with exactly the word: pong"},
            ]}],
            tools=[],
            system="You are terse.",
            stream=True,
            max_tokens=64,
        )
    except (ModelUnavailableError, ModelCallError) as exc:
        # The failure mode a wrong model id produces, called out explicitly
        # because it is the whole reason this script exists.
        print(f"  [FAIL] the call was rejected: {exc}")
        print("\n  A 404 / 'model not found' here means the wire id is wrong.")
        print(f"  Check XAI_MODEL_GROK_45 (currently `{wire_id}`) against")
        print("  https://docs.x.ai/developers/models")
        return 1

    text = "".join(e.content or "" for e in events if e.kind == "text_delta")
    final = events[-1].message
    check("the call succeeded and streamed text", bool(text.strip()), repr(text[:60]))
    check("xAI echoed the model back", bool(final.model_name), final.model_name)
    check("token usage came back", final.usage.get("input_tokens", 0) > 0, str(final.usage))
    print(f"         estimated cost: ${estimate_cost(MODEL_ID, final.usage):.6f}")

    print("\n2. Tool-calling turn")
    try:
        events = await collect(
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "What is the temperature in Paris right now?"},
            ]}],
            tools=WEATHER_TOOL,
            system="Use the supplied tool when it applies. Do not guess.",
            stream=True,
            max_tokens=512,
        )
    except (ModelUnavailableError, ModelCallError) as exc:
        check("tool-calling turn completed", False, str(exc))
        return 1

    final = events[-1].message
    uses = [b for b in final.content if b.get("type") == "tool_use"]
    check("the model asked to call the tool", bool(uses),
          str([u.get("name") for u in uses]))
    if uses:
        check("arguments arrived as a parsed object",
              isinstance(uses[0].get("input"), dict), str(uses[0].get("input")))
        check("stop_reason is tool_use", final.stop_reason == "tool_use", final.stop_reason)
    else:
        print("         NOTE: the model answered without calling the tool. That is a")
        print("         prompt-sensitivity result, not proof that tool calling is")
        print("         unsupported — rerun before concluding anything.")

    print(f"\n{sum(results)}/{len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
