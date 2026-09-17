"""A 402 that names an affordable ceiling retries the same model. Offline.

    python scripts/test_budget_trim.py

No provider is called: `call_model` is replaced, so what is asserted is the
router's decision, not OpenRouter's balance on the day the test runs.

The bug this pins down
----------------------
OpenRouter prices a request by its `max_tokens` *ceiling* rather than by what
it produces, and refuses once the account balance cannot cover that ceiling:

    402  You requested up to 8000 tokens, but can only afford 5870

`openrouter_max_tokens` is one number for the whole provider, but
affordability is per-token-price. At 8000 it is comfortable for
`llama-4-scout` and unaffordable for `nemotron-3-ultra-550b-a55b`, which is
550B parameters and priced accordingly — so that model answered 402 to
everything, "reply with PONG" included. It was listed as available, offered in
the selector, and silently swapped for another model every single time it was
chosen. Verified against the live API before the fix: 402 at 8000, fine at
4000.

The refusal already carries its own remedy. Retrying at the named ceiling
turns a model that never worked into one that does.

What is asserted
----------------
1. The ceiling is parsed out of the refusal.
2. A refusal with no number, or an absurdly small one, is not acted on.
3. The retry goes to the *same* model, at the ceiling it named.
4. It costs no fallback attempt — asking the same model the same question is
   not a fallback, and must not consume the budget for real ones.
5. One trim per model: a second 402 falls back instead of looping, because a
   balance that is still dropping will not be caught by chasing it.
6. Other retryable statuses are untouched — a 429 still changes model.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENCODE_API_KEY"):
    os.environ.setdefault(_k, "test-key-not-real")

import app.llm_router as R  # noqa: E402
from app.llm_router import NormalizedMessage, affordable_max_tokens  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []

AFFORD_MSG = (
    "Error code: 402 - {'error': {'message': 'This request requires more "
    "credits, or fewer max_tokens. You requested up to 8000 tokens, but can "
    "only afford 5870.'}}"
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def http_error(status: int, message: str) -> Exception:
    class _Resp:
        status_code = status

    exc = Exception(message)
    exc.status_code = status  # type: ignore[attr-defined]
    exc.response = _Resp()  # type: ignore[attr-defined]
    return exc


class Router:
    """Stands in for `call_model`, recording (model, max_tokens) per attempt."""

    def __init__(self, fail_with, fail_times=1):
        self.calls: list[tuple[str, int | None]] = []
        self.fail_with = fail_with
        self.fail_times = fail_times

    async def call_model(self, model_id, *, messages, tools, system, stream, max_tokens):
        self.calls.append((model_id, max_tokens))
        if len(self.calls) <= self.fail_times:
            raise self.fail_with(len(self.calls))
        return NormalizedMessage(
            content=[{"type": "text", "text": "PONG"}],
            usage={"input_tokens": 5, "output_tokens": 1},
            stop_reason="end_turn",
            model_name=model_id,
        )


async def run(router, model="nemotron-3", max_tokens=8000):
    real = R.call_model
    R.call_model = router.call_model
    try:
        return await R.complete_with_fallback(
            model, messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=max_tokens, section="test",
        )
    finally:
        R.call_model = real


async def main() -> int:
    ok = True

    print("\n1. The refusal is read for its own answer")
    ok &= check("the ceiling is parsed", affordable_max_tokens(Exception(AFFORD_MSG)) == 5870)
    ok &= check(
        "a 402 with no number is not acted on",
        affordable_max_tokens(Exception("402 out of credit")) is None,
    )
    ok &= check(
        "an unusably small ceiling is declined",
        affordable_max_tokens(Exception("can only afford 12")) is None,
        "a ceiling of ~nothing buys a truncated answer and a second failure",
    )

    print("\n2. The retry is the same model at the affordable ceiling")
    router = Router(lambda n: http_error(402, AFFORD_MSG), fail_times=1)
    message, resolved = await run(router)
    ok &= check("the same model answered", resolved == "nemotron-3", resolved)
    ok &= check("two attempts, no more", len(router.calls) == 2, str(router.calls))
    ok &= check(
        "the first asked for the configured ceiling",
        router.calls[0] == ("nemotron-3", 8000),
        str(router.calls[0]),
    )
    ok &= check(
        "the second asked for what it could afford",
        router.calls[1] == ("nemotron-3", 5870),
        str(router.calls[1]),
    )

    print("\n3. One trim per model, then a real fallback")
    # Always 402, and the affordable figure keeps dropping — a balance being
    # spent by something else.
    def dropping(n):
        return http_error(402, AFFORD_MSG.replace("5870", str(6000 - n * 500)))

    router2 = Router(dropping, fail_times=99)
    try:
        await run(router2)
    except Exception:
        pass
    models = [m for m, _ in router2.calls]
    ok &= check(
        "the first model was retried exactly once",
        models.count("nemotron-3") == 2,
        f"attempts: {models}",
    )
    ok &= check(
        "then other models were tried",
        len(set(models)) > 1,
        f"models: {sorted(set(models))}",
    )
    ok &= check(
        "and the fallback budget was not spent on the trim",
        len(set(models)) == 1 + R.MAX_FALLBACK_ATTEMPTS,
        f"{len(set(models))} models tried on a budget of "
        f"{R.MAX_FALLBACK_ATTEMPTS} fallbacks, over {len(models)} attempts",
    )

    first_try: dict[str, int | None] = {}
    for _model, _mt in router2.calls:
        first_try.setdefault(_model, _mt)
    ok &= check(
        "every model starts from the configured ceiling",
        set(first_try.values()) == {8000},
        f"first attempt per model: {first_try} — a trim is a fact about one "
        f"model's price and must not follow the request onto the next",
    )

    print("\n4. Other retryable failures are unaffected")
    router3 = Router(lambda n: http_error(429, "rate limit"), fail_times=1)
    _msg, resolved3 = await run(router3)
    ok &= check(
        "a 429 changes model as before",
        resolved3 != "nemotron-3",
        f"answered by {resolved3}",
    )

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
