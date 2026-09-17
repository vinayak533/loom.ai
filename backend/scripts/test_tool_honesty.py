"""Four tools that used to answer confidently about nothing.

    .venv/Scripts/python scripts/test_tool_honesty.py

Each of these is the same class of bug: a tool that could not do its job and
said so in neither its output nor its metadata, so the agent read a degraded or
malformed run as a clean result and reported it as fact.

  TL-1  `lint_code` for Python ended its linter chain on `py_compile`, which
        only parses. Neither ruff nor pyflakes ships in the E2B image, so every
        Python lint landed there and returned `clean: true` with no `degraded`
        flag — an unused import, an undefined name, a bare except, all "clean".
  TL-2  `evaluate_conditions` treated a malformed rule as a rule that did not
        match. Every argument getter has a default, so `{"foo": 1}` evaluated
        against the whole payload, came out False, and joined the trace looking
        exactly like a legitimate non-match.
  TL-4  `normalize_aspect_ratio` accepted `-4:3` as `4:3`. The ratio regex did
        not capture the sign, so `search` skipped it — while `0:0` and garbage
        were correctly refused.
  TL-5  `check_spam_words` counted "100% FREE" twice, once for `100% free` and
        once for `free`. That count drives the severity threshold, so nesting
        alone could push an ordinary email to `severity: high`.

No sandbox, no network, no database.
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

from app.agents.tools import code as code_tools  # noqa: E402
from app.agents.tools import email as email_tools  # noqa: E402
from app.agents.tools import imagery as imagery_tools  # noqa: E402
from app.agents.tools import routing as routing_tools  # noqa: E402
from app.agents.tools.base import ToolContext  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


CTX = ToolContext(
    session_id="sess-honesty", agent_id="t", user_id=None, call_id="c1"
)


# ---------------------------------------------------------------------------
# TL-1
# ---------------------------------------------------------------------------

#: Compiles cleanly and is nonetheless full of things a linter exists to find:
#: an unused import, an undefined name, a bare except. `py_compile` calls this
#: clean, which is why it can no longer be the last word.
DIRTY_BUT_VALID = """
import os
import json


def handler(payload):
    try:
        return jsson.loads(payload)
    except:
        return None
"""


async def tl1_lint_never_claims_a_check_it_did_not_run() -> bool:
    print("\nTL-1  lint_code never reports a clean it did not earn")
    ok = True

    chain = [name for name, _cmd in code_tools.LANGUAGES["python"]["lint"]]
    ok &= check(
        "py_compile is no longer in the Python linter chain",
        "compileall" not in chain and "py_compile" not in chain,
        f"chain: {chain}",
    )
    ok &= check(
        "a real linter still is",
        chain[:1] == ["ruff"],
        f"chain: {chain}",
    )

    # With no sandbox reachable, the tool must take the honest fallback.
    async def _no_sandbox(_session_id):
        raise code_tools.SandboxUnavailable("no sandbox in this test")

    saved = code_tools.sandbox_manager.get
    code_tools.sandbox_manager.get = _no_sandbox
    try:
        result = await code_tools.lint_code(
            CTX, {"code": DIRTY_BUT_VALID, "language": "python"}
        )
    finally:
        code_tools.sandbox_manager.get = saved

    ok &= check(
        "the degraded run is flagged as degraded",
        result.meta.get("degraded") is True,
        f"meta={result.meta}",
    )
    ok &= check(
        "and says in words that it was a syntax check only",
        "syntax check only" in result.output,
    )
    ok &= check(
        "and tells the agent to disclose that lint rules did not run",
        "NOT run" in result.output,
        result.output[:120],
    )
    return ok


# ---------------------------------------------------------------------------
# TL-2
# ---------------------------------------------------------------------------


async def tl2_malformed_rules_are_errors_not_non_matches() -> bool:
    print("\nTL-2  evaluate_conditions refuses to score rules it cannot read")
    ok = True
    data = {"plan": "pro", "seats": 12}

    # (a) every rule malformed: not a non-match, a tool error.
    result = await routing_tools.evaluate_conditions(
        CTX, {"data": data, "rules": [{"foo": 1}, {"bar": 2}]}
    )
    ok &= check("the tool reports failure", result.success is False)
    ok &= check(
        "and says explicitly that this is not a non-match",
        "not a non-match" in result.output,
        result.output.splitlines()[0][:100],
    )
    ok &= check(
        "it never claims no rule matched",
        "No rule matched" not in result.output,
    )
    ok &= check("nothing was evaluated", result.meta.get("rules_evaluated") == 0)
    ok &= check(
        "and the trace names the shape a rule should have",
        any("expected an object with" in str(t.get("error", "")) for t in result.meta["trace"]),
    )

    # (b) one good rule beside one malformed: a real answer, plus a warning.
    result = await routing_tools.evaluate_conditions(
        CTX,
        {
            "data": data,
            "rules": [
                {"foo": 1},
                {"field": "plan", "operator": "equals", "value": "pro", "outcome": "upgrade"},
            ],
        },
    )
    ok &= check("a partial run still succeeds", result.success is True)
    ok &= check("and finds the match", result.meta.get("outcome") == "upgrade")
    ok &= check(
        "while saying how many rules were actually evaluable",
        result.meta.get("rules_evaluated") == 1
        and result.meta.get("rules_given") == 2
        and "1 of 2 rules were evaluable" in result.output,
        result.output.splitlines()[0][:120],
    )

    # (c) a genuine non-match is still a genuine non-match.
    result = await routing_tools.evaluate_conditions(
        CTX,
        {
            "data": data,
            "rules": [
                {"field": "plan", "operator": "equals", "value": "free", "outcome": "nudge"}
            ],
        },
    )
    ok &= check("a real non-match still succeeds", result.success is True)
    ok &= check("and says so", "No rule matched" in result.output)
    ok &= check("having evaluated the rule", result.meta.get("rules_evaluated") == 1)

    # (d) a bare default rule is still a rule.
    result = await routing_tools.evaluate_conditions(
        CTX,
        {
            "data": data,
            "rules": [
                {"field": "plan", "operator": "equals", "value": "free", "outcome": "nudge"},
                {"default": True, "outcome": "leave_alone"},
            ],
        },
    )
    ok &= check(
        "a bare {default, outcome} rule is accepted and used",
        result.success is True and result.meta.get("outcome") == "leave_alone",
        f"outcome={result.meta.get('outcome')}, "
        f"evaluated={result.meta.get('rules_evaluated')}",
    )
    return ok


# ---------------------------------------------------------------------------
# TL-4
# ---------------------------------------------------------------------------


async def tl4_negative_ratios_are_refused() -> bool:
    print("\nTL-4  normalize_aspect_ratio refuses a side that cannot exist")
    ok = True
    for bad in ("-4:3", "4:-3", "-4:-3", "0:0", "-1920x1080"):
        result = await imagery_tools.normalize_aspect_ratio(CTX, {"value": bad})
        ok &= check(
            f"`{bad}` is refused",
            result.success is False,
            f"got aspect_ratio={result.meta.get('aspect_ratio')}",
        )
    for good, expected in (("4:3", "4:3"), ("1920x1080", "16:9"), ("poster", "2:3")):
        result = await imagery_tools.normalize_aspect_ratio(CTX, {"value": good})
        ok &= check(
            f"`{good}` still resolves to {expected}",
            result.success is True and result.meta.get("aspect_ratio") == expected,
            f"got {result.meta.get('aspect_ratio')}",
        )
    return ok


# ---------------------------------------------------------------------------
# TL-5
# ---------------------------------------------------------------------------


async def tl5_overlapping_phrases_count_once() -> bool:
    print("\nTL-5  check_spam_words counts an occurrence once")
    ok = True

    hits = email_tools._find_spam("100% FREE")
    phrases = [h["phrase"] for h in hits]
    ok &= check(
        "one occurrence produces one hit",
        len(hits) == 1,
        f"hits: {phrases}",
    )
    ok &= check(
        "and it is the longest phrase, not the fragment",
        phrases == ["100% free"],
        f"hits: {phrases}",
    )

    hits = email_tools._find_spam("Risk free and 100% free — free!")
    phrases = sorted(h["phrase"] for h in hits)
    ok &= check(
        "nested phrases each claim their own span",
        phrases == ["100% free", "free", "risk free"],
        f"hits: {phrases}",
    )
    ok &= check(
        "and the bare `free` is counted once, not three times",
        next(h["occurrences"] for h in hits if h["phrase"] == "free") == 1,
        f"{[(h['phrase'], h['occurrences']) for h in hits]}",
    )

    # The count is what drives severity, so the fix has to move the verdict.
    result = await email_tools.check_spam_words(
        CTX, {"subject": "100% FREE", "body": "Risk free, no obligation."}
    )
    ok &= check(
        "severity is judged on distinct occurrences",
        result.meta["severity"] in ("low", "medium"),
        f"severity={result.meta['severity']}, "
        f"flagged={[h['phrase'] for h in result.meta['flagged']]}",
    )

    # And a genuinely spammy email is still caught.
    result = await email_tools.check_spam_words(
        CTX,
        {
            "subject": "ACT NOW",
            "body": "Click here to buy now and earn money — limited time, urgent!!!!",
        },
    )
    ok &= check(
        "a genuinely spammy email is still high severity",
        result.meta["severity"] == "high",
        f"severity={result.meta['severity']}, "
        f"{len(result.meta['flagged'])} phrases",
    )
    return ok


async def main() -> int:
    ok = True
    ok &= await tl1_lint_never_claims_a_check_it_did_not_run()
    ok &= await tl2_malformed_rules_are_errors_not_non_matches()
    ok &= await tl4_negative_ratios_are_refused()
    ok &= await tl5_overlapping_phrases_count_once()

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
