"""Tool surcharges: derived from the real configuration, and only when earned.

    python scripts/test_tool_pricing.py

Covers the two pricing defects found in the audit. Neither vendor is called.

Image generation
----------------
The surcharge used to be a flat 30 credits while `STABILITY_MODEL` was a free
config value. `core` really is 30 credits ($0.03), so it looked right — but
switching to `ultra` ($0.08) would have gone on charging 30 forever. The
surcharge is now derived from the configured model, and a model with no
published price is refused rather than guessed at.

read_url
--------
`read_url` carries Jina's per-call price, but it falls back to the built-in
extractor when Jina has no key or fails. That fallback was still being charged
Jina's surcharge — a vendor bill for a call no vendor received.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import credits  # noqa: E402
from app.agents import graph as agents_graph  # noqa: E402
from app.agents.tools import imagery, research  # noqa: E402
from app.agents.tools.base import ToolContext, ToolResult  # noqa: E402
from app.config import get_settings  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def with_model(model: str):
    """Point the settings at a Stability model, as an env change would."""
    settings = get_settings()
    object.__setattr__(settings, "stability_model", model)


async def main() -> int:
    settings = get_settings()
    original_model = settings.stability_model
    ok = True

    # --- 1. the surcharge follows the configured model --------------------
    print("\n1. Image surcharge is derived, not hardcoded")
    # Stability publishes 1 credit = $0.01; our credit is $0.001, so their
    # per-image credit count x10 is what a user should be charged.
    expected = {"core": 30.0, "sd3.5-large": 65.0, "ultra": 80.0}
    for model, want in expected.items():
        with_model(model)
        got = credits.surcharge_for("generate_image")
        ok &= check(
            f"`{model}` charges {want:.0f} credits",
            abs(got - want) < 0.001,
            f"got {got}",
        )

    with_model("core")
    ok &= check(
        "the old flat constant is gone from the table",
        "generate_image" not in credits.TOOL_SURCHARGE,
        "TOOL_SURCHARGE no longer carries an image price",
    )

    # --- 2. an unpriced model fails loudly --------------------------------
    print("\n2. An unpriced model is refused, not guessed")
    with_model("some-future-model")
    try:
        credits.surcharge_for("generate_image")
        ok &= check("surcharge_for raises for an unknown model", False, "it returned")
    except credits.UnknownImagePricing as exc:
        ok &= check(
            "surcharge_for raises for an unknown model",
            "some-future-model" in str(exc) and "platform.stability.ai" in str(exc),
            "and the message names the fix",
        )

    problems = credits.validate_pricing()
    ok &= check(
        "the startup check reports it",
        any("some-future-model" in p for p in problems),
        f"{len(problems)} problem(s) found",
    )

    # The tool must refuse *before* the approval gate and before any spend.
    ctx = ToolContext(session_id="s", agent_id="graphic_poster_creator",
                      user_id=None, call_id="c")
    result = await imagery.generate_image(
        ctx, {"prompt": "a lighthouse", "aspect_ratio": "1:1"}
    )
    ok &= check(
        "generate_image refuses rather than rendering at an unknown price",
        not result.success and result.meta.get("pricing_error") is True,
        result.output.split("\n")[0][:70] + "…",
    )

    with_model(original_model)
    ok &= check(
        "the tables agree for the configured model",
        credits.validate_pricing() == [],
        f"STABILITY_MODEL={original_model}",
    )

    # --- 3. drift between the two tables is caught ------------------------
    print("\n3. Adding an endpoint without a price is caught at startup")
    imagery._STABILITY_ENDPOINTS["sd4-huge"] = "https://example.invalid/sd4"
    try:
        problems = credits.validate_pricing()
        ok &= check(
            "an unpriced but callable model is reported",
            any("sd4-huge" in p and "charged nothing" in p for p in problems),
            problems[0] if problems else "nothing reported",
        )
    finally:
        del imagery._STABILITY_ENDPOINTS["sd4-huge"]

    # --- 4. read_url only bills when Jina served it -----------------------
    print("\n4. read_url bills the fallback at nothing")

    async def fake_builtin(url):
        return type("X", (), {"ok": True, "title": "T", "text": "body " * 200,
                              "error": ""})()

    import app.learn.ingest as ingest
    real_from_url = ingest.from_url
    ingest.from_url = fake_builtin
    real_jina = research._jina
    try:
        # Jina configured but failing -> the built-in extractor serves it.
        async def broken_jina(url):
            raise RuntimeError("jina is down")

        research._jina = broken_jina
        fallback = await research.read_url(ctx, {"url": "https://example.com"})
        ok &= check(
            "a fallback read is marked not billable",
            fallback.success and fallback.meta.get("billable") is False,
            f"via={fallback.meta.get('via')}",
        )

        # Jina answering -> the paid path really was used.
        async def working_jina(url):
            return "Title", "real jina markdown " * 50

        research._jina = working_jina
        if settings.jina_enabled:
            paid = await research.read_url(ctx, {"url": "https://example.com"})
            ok &= check(
                "a real Jina read is billable",
                paid.meta.get("billable") is True,
                f"via={paid.meta.get('via')}",
            )
        else:
            print("      (skipped the paid-path check — JINA_AI_READER_API_KEY is unset)")
    finally:
        ingest.from_url = real_from_url
        research._jina = real_jina

    # --- 5. the graph honours the flag ------------------------------------
    print("\n5. The graph does not charge an unbilled call")
    charged: list[str] = []

    async def fake_charge_tool(user_id, *, session_id, agent_id, tool, multiplier=1.0):
        charged.append(tool)
        return credits.TOOL_SURCHARGE.get(tool, 0.0)

    async def fake_run(name, ctx, args, allowed):
        return ToolResult("page text", meta=dict(args.get("_meta") or {}))

    real_charge = agents_graph.charge_tool
    real_run = agents_graph.run_agent_tool
    real_fire = agents_graph.repository.fire
    agents_graph.charge_tool = fake_charge_tool
    agents_graph.run_agent_tool = fake_run
    agents_graph.repository.fire = lambda *a, **k: None
    try:
        state = {"session_id": "s", "agent_id": "research_fact_checker",
                 "user_id": "", "model_id": "qwen3_7_plus"}
        await agents_graph._execute_call(
            state,
            {"id": "1", "name": "read_url",
             "input": {"_meta": {"via": "builtin-extractor", "billable": False}}},
            None,
        )
        ok &= check(
            "the built-in extractor path is not charged",
            charged == [],
            f"charged: {charged or 'nothing'}",
        )
        await agents_graph._execute_call(
            state,
            {"id": "2", "name": "read_url",
             "input": {"_meta": {"via": "jina-reader", "billable": True}}},
            None,
        )
        ok &= check(
            "the Jina path is charged",
            charged == ["read_url"],
            f"charged: {charged}",
        )
        await agents_graph._execute_call(
            state,
            {"id": "3", "name": "search_web", "input": {"_meta": {}}},
            None,
        )
        ok &= check(
            "a tool with no flag still bills by default",
            charged == ["read_url", "search_web"],
            f"charged: {charged}",
        )
    finally:
        agents_graph.charge_tool = real_charge
        agents_graph.run_agent_tool = real_run
        agents_graph.repository.fire = real_fire
        with_model(original_model)

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} — {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
