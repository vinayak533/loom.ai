"""Live check: a multi-turn conversation on a thinking-mode OpenCode model.

The bug this covers: several OpenCode models run in thinking mode and reject a
follow-up call whose assistant turn arrives without the `reasoning_content`
they produced —

    400 ... The `reasoning_content` in the thinking mode must be passed back
    to the API

— which killed any conversation on `deepseek_v4_flash` the moment it reasoned
once. The fix carries reasoning through checkpointed state as a `reasoning`
block (see `REASONING_BLOCK` in `app.llm_router`) and hands it back to the
providers that asked for it, while stripping it from the ones that did not.

So this script does the one thing the unit of the bug needs: *two* turns, with
the first turn's real response fed into the second. Run with:

    .venv/Scripts/python -m scripts.test_reasoning_roundtrip
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.llm_router import (  # noqa: E402
    REASONING_BLOCK,
    call_model,
    display_name,
    is_available,
)

#: The one that failed in the wild, plus the rest of the Auto pool — they are
#: the models a real session is routed between without being asked.
MODELS = ["deepseek_v4_flash", "minimax_m2_7", "mimo_v2_5", "qwen3_7_plus"]

#: A conversation that only makes sense if turn one is actually present.
TURN_ONE = "Pick a number between 1 and 100 and say only that number."
TURN_TWO = "What number did you just pick? Answer with the number alone."


async def one_turn(model_id: str, messages: list[dict]) -> dict:
    """Run a streamed turn and return the assistant message, as the graph does."""
    stream = call_model(model_id, messages, tools=[], system=None, stream=True)
    async for event in stream:
        if event.kind == "done":
            return {"role": "assistant", "content": event.message.content}
    raise AssertionError("stream ended without a `done` event")


def text_of(message: dict) -> str:
    return " ".join(
        b.get("text", "")
        for b in message["content"]
        if b.get("type") == "text"
    ).strip()


async def check(model_id: str) -> bool:
    name = display_name(model_id)
    if not is_available(model_id):
        print(f"  SKIP  {name}: no key")
        return True

    messages: list[dict] = [{"role": "user", "content": TURN_ONE}]
    try:
        first = await one_turn(model_id, messages)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {name}: turn 1 raised {exc}")
        return False

    reasoned = any(b.get("type") == REASONING_BLOCK for b in first["content"])
    messages.append(first)
    messages.append({"role": "user", "content": TURN_TWO})

    try:
        second = await one_turn(model_id, messages)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {name}: turn 2 raised {exc}")
        return False

    print(
        f"  ok    {name}: "
        f"turn 1 {'reasoned, ' if reasoned else ''}said {text_of(first)[:40]!r}; "
        f"turn 2 said {text_of(second)[:40]!r}"
    )
    return True


async def check_crossover(first_id: str, second_id: str) -> bool:
    """Reason on one provider, then continue on another.

    This is not hypothetical: Auto reclassifies every iteration, so a thread
    that reasons on an OpenCode model and then turns into file edits continues
    on a different provider carrying those reasoning blocks. A provider receives
    `messages` verbatim and would reject a block type it does not define.
    """
    names = f"{display_name(first_id)} -> {display_name(second_id)}"
    if not (is_available(first_id) and is_available(second_id)):
        print(f"  SKIP  {names}: no key")
        return True

    messages: list[dict] = [{"role": "user", "content": TURN_ONE}]
    try:
        first = await one_turn(first_id, messages)
        if not any(b.get("type") == REASONING_BLOCK for b in first["content"]):
            print(f"  note  {names}: turn 1 produced no reasoning to carry over")
        messages.append(first)
        messages.append({"role": "user", "content": TURN_TWO})
        second = await one_turn(second_id, messages)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {names}: {exc}")
        return False

    print(f"  ok    {names}: continued, said {text_of(second)[:40]!r}")
    return True


async def main() -> int:
    print("Multi-turn round trip on thinking-mode models\n")
    results = [await check(mid) for mid in MODELS]

    print("\nSwitching provider mid-conversation (what Auto does)\n")
    results.append(await check_crossover("deepseek_v4_flash", "grok-4-5"))
    results.append(await check_crossover("deepseek_v4_flash", "llama-4-scout"))

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
