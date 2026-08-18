"""Exercise every agent tool that does not need a network call or a human.

Run it after touching anything in `app/agents/tools/`:

    python scripts/test_agent_tools.py

It asserts the behaviours the agents' system prompts are written against — a
readability score that is actually computed, a validator that actually rejects,
a dispatcher that actually refuses a tool the agent does not have. The tools
that need an external key or an approval click are covered by the end-to-end
smoke test instead; this one is meant to be runnable offline in a second.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# The Windows console defaults to cp1252, which cannot encode the characters a
# report like this naturally reaches for. Reconfigure rather than restricting
# the output to ASCII.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover - not a real stream
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.agents import registry  # noqa: E402
from app.agents.tool_registry import HANDLERS, SCHEMAS, run  # noqa: E402
from app.agents.tools.base import ToolContext  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  ok    {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL  {label}  {detail}")


def ctx(agent_id: str = "document_summarizer") -> ToolContext:
    return ToolContext(
        session_id="test-session",
        agent_id=agent_id,
        user_id=None,
        call_id="test-call",
        model_id="grok-4-5",
    )


async def main() -> int:
    print("\n— registry —")
    check("ten agents registered", len(registry.AGENTS) == 10, str(len(registry.AGENTS)))
    check(
        "every declared tool has a schema",
        not [t for a in registry.ordered_agents() for t in a.tools if t not in SCHEMAS],
    )
    check("schemas and handlers agree", set(SCHEMAS) == set(HANDLERS))
    check(
        "every agent has a non-empty persona",
        all(len(a.system_prompt()) > 400 for a in registry.ordered_agents()),
    )

    print("\n— dispatch boundary —")
    denied = await run("generate_image", ctx("ui_component_designer"), {}, ("lookup_tailwind",))
    check(
        "an agent cannot call a tool it does not have",
        not denied.success and "not one of your tools" in denied.output,
        denied.output[:80],
    )

    print("\n— agent 1: documents —")
    from app.agents.tools import documents

    r = await documents.chunk_document(ctx(), {"text": "para one.\n\n" * 400, "chunk_size": 400})
    check("chunk_document splits", r.success and r.meta["chunks"] > 1, str(r.meta))
    r = await documents.count_tokens(ctx(), {"text": "hello world " * 50, "model_id": "llama-4-scout"})
    check(
        "count_tokens uses a real tokenizer",
        r.success and r.meta["tokens"] > 50 and "tiktoken" in r.meta["tokenizer"],
        str(r.meta.get("tokenizer")),
    )
    r = await documents.parse_source(ctx(), {"text": "  Hello   world.\n\n\n\nSecond para. "})
    check("parse_source normalises text", r.success and "Second para." in r.output)
    r = await documents.parse_source(ctx(), {})
    check("parse_source needs an input", not r.success)

    print("\n— agent 2: email —")
    from app.agents.tools import email

    r = await email.list_email_templates(ctx("email_copywriter"), {"audience": "cold_outbound"})
    check("templates return structure", r.success and "cold_outbound" in r.output)
    r = await email.check_spam_words(
        ctx("email_copywriter"),
        {"subject": "ACT NOW - 100% FREE!!!", "body": "Guaranteed risk free cash bonus."},
    )
    check(
        "spam checker flags real triggers",
        r.success and r.meta["severity"] == "high" and len(r.meta["flagged"]) >= 4,
        str(r.meta.get("severity")),
    )
    r = await email.check_spam_words(ctx("email_copywriter"), {"body": "Freedom of movement."})
    check(
        "spam checker respects word boundaries ('freedom' is not 'free')",
        r.meta["severity"] == "clean",
        str(r.meta.get("flagged")),
    )
    a = await email.score_subject_line(ctx("email_copywriter"), {"subject": "Your Q3 report is ready"})
    b = await email.score_subject_line(
        ctx("email_copywriter"), {"subject": "FREE!!! ACT NOW BEFORE THIS AMAZING OFFER EXPIRES FOREVER"}
    )
    check(
        "subject scoring separates good from bad",
        a.meta["score"] > b.meta["score"] + 30,
        f"{a.meta['score']} vs {b.meta['score']}",
    )
    # 57 characters: past the mobile cutoff (41), inside the desktop one (60).
    check(
        "a 57-char subject truncates on mobile but not desktop",
        b.meta["truncates_on_mobile"] and not b.meta["truncates_on_desktop"],
        str(b.meta["length"]),
    )
    long_subject = await email.score_subject_line(
        ctx("email_copywriter"),
        {"subject": "A subject line that is comfortably longer than sixty characters in total"},
    )
    check("a 72-char subject truncates on desktop too", long_subject.meta["truncates_on_desktop"])

    print("\n— agent 3: frontend —")
    from app.agents.tools import frontend

    r = await frontend.lookup_lucide_icons(ctx("ui_component_designer"), {"query": "delete"})
    check("icon search finds Trash2", r.success and "Trash2" in r.output, r.output[:80])
    r = await frontend.lookup_lucide_icons(
        ctx("ui_component_designer"), {"names": ["Mail", "DefinitelyNotAnIcon"]}
    )
    check(
        "icon validation rejects a fake name",
        '"name": "DefinitelyNotAnIcon",\n   "exists": false' in r.output.replace("  ", " ")
        or '"exists": false' in r.output,
        r.output[:120],
    )
    check("registry icons are all real", all(frontend.icon_exists(a.icon) for a in registry.ordered_agents()))
    r = await frontend.lookup_tailwind(
        ctx("ui_component_designer"), {"classes": ["flex", "text-ink-muted", "!!bogus"]}
    )
    check("tailwind validation runs", r.success and "validated" in r.output)
    check(
        "project tokens were read from tailwind.config.ts",
        '"available": true' in r.output.lower(),
        r.output[:160],
    )

    print("\n— agent 4: imagery —")
    from app.agents.tools import imagery

    for raw, want in [("a poster", "2:3"), ("16x9", "16:9"), ("1080x1920", "9:16"), ("square", "1:1")]:
        r = await imagery.normalize_aspect_ratio(ctx("creative_prompt_engineer"), {"value": raw})
        check(f"aspect ratio '{raw}' → {want}", r.success and r.meta["aspect_ratio"] == want, str(r.meta.get("aspect_ratio")))
    r = await imagery.normalize_aspect_ratio(ctx("creative_prompt_engineer"), {"value": "banana"})
    check("unreadable ratio is refused", not r.success)
    r = await imagery.list_style_modifiers(ctx("creative_prompt_engineer"), {"category": "lighting"})
    check("style modifiers return terms", r.success and "Rembrandt" in r.output)

    print("\n— agents 6/7: research —")
    from app.agents.tools import research

    r = await research.readability_score(
        ctx("seo_content_creator"),
        {"text": "The cat sat on the mat. It was a warm day. The sun was bright. Birds sang."},
    )
    simple = r.meta["flesch_reading_ease"]
    r = await research.readability_score(
        ctx("seo_content_creator"),
        {
            "text": (
                "The epistemological ramifications of phenomenological "
                "interpretation necessitate a fundamental reconsideration of "
                "hermeneutic methodologies, particularly insofar as such "
                "reconsiderations implicate the constitutive relationship "
                "between consciousness and intentionality."
            )
        },
    )
    complex_score = r.meta["flesch_reading_ease"]
    check(
        "readability separates simple from academic prose",
        simple > 70 > complex_score,
        f"{simple} vs {complex_score}",
    )
    r = await research.keyword_density(
        ctx("seo_content_creator"),
        {"text": "content marketing is great. content marketing works. " * 5 + "other words here " * 40,
         "keywords": ["content marketing", "absent phrase"]},
    )
    rows = {k["keyword"]: k for k in r.meta["keywords"]}
    check("keyword density counts n-grams", rows["content marketing"]["occurrences"] == 10, str(rows["content marketing"]))
    check("absent keyword reported absent", rows["absent phrase"]["verdict"] == "absent")

    r = await research.rank_domain_trust(
        ctx("research_fact_checker"),
        {"urls": ["https://nature.com/x", "https://reddit.com/r/y", "https://cdc.gov/z",
                  "https://docs.python.org/3/", "https://bestdeals123.xyz/q"]},
    )
    tiers = {e["domain"]: e["tier"] for e in r.meta["ranked"]}
    check("nature.com is high", tiers["nature.com"] == "high", str(tiers))
    check("reddit is medium (aggregator)", tiers["reddit.com"] == "medium", str(tiers))
    check("a .gov is high", tiers["cdc.gov"] == "high", str(tiers))
    check("docs.python.org inherits python.org", tiers["docs.python.org"] == "high", str(tiers))
    check("a bulk TLD is low", tiers["bestdeals123.xyz"] == "low", str(tiers))
    check("the caveat travels with the result", "never as the reason a claim is true" in r.output)

    print("\n— agent 8: routing —")
    from app.agents.tools import routing

    schema = {
        "type": "object",
        "required": ["name", "count"],
        "properties": {
            "name": {"type": "string", "minLength": 2},
            "count": {"type": "integer", "minimum": 1},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
    r = await routing.validate_json_schema(
        ctx("system_logic_router"), {"data": {"name": "ok", "count": 3, "tags": ["a"]}, "schema": schema}
    )
    check("valid payload passes", r.meta["valid"], str(r.meta.get("errors")))
    r = await routing.validate_json_schema(
        ctx("system_logic_router"), {"data": {"name": "x", "count": 0, "tags": [1]}, "schema": schema}
    )
    check("invalid payload fails with 3 errors", not r.meta["valid"] and r.meta["error_count"] == 3, str(r.meta.get("errors")))
    r = await routing.validate_json_schema(
        ctx("system_logic_router"), {"data": '{"name":"ok","count":2}', "schema": schema}
    )
    check("a stringified payload is accepted", r.meta["valid"], str(r.meta.get("errors")))

    r = await routing.match_patterns(
        ctx("system_logic_router"),
        {"text": "Mail me at a@b.com or see https://x.dev\n```py\nprint(1)\n```"},
    )
    matched = {m["pattern"] for m in r.meta["matched"]}
    check("pattern matcher finds email/url/code", {"email_address", "url", "code_fence"} <= matched, str(matched))

    rules = [
        {"field": "kind", "operator": "equals", "value": "email", "outcome": "email_copywriter"},
        {"field": "body", "operator": "length_greater_than", "value": 5000, "outcome": "document_summarizer"},
        {"default": True, "outcome": "system_logic_router"},
    ]
    r = await routing.evaluate_conditions(ctx("system_logic_router"), {"data": {"kind": "email"}, "rules": rules})
    check("first matching rule wins", r.meta["outcome"] == "email_copywriter", str(r.meta.get("outcome")))
    r = await routing.evaluate_conditions(ctx("system_logic_router"), {"data": {"kind": "other", "body": "x" * 6000}, "rules": rules})
    check("second rule matches on length", r.meta["outcome"] == "document_summarizer", str(r.meta.get("outcome")))
    r = await routing.evaluate_conditions(ctx("system_logic_router"), {"data": {"kind": "other"}, "rules": rules})
    check("default outcome applies", r.meta["outcome"] == "system_logic_router", str(r.meta.get("outcome")))
    r = await routing.evaluate_conditions(
        ctx("system_logic_router"),
        {"data": {"a": {"b": [{"c": 9}]}}, "rules": [{"field": "a.b[0].c", "operator": "greater_than", "value": 5, "outcome": "hit"}]},
    )
    check("dotted paths with indexes resolve", r.meta["outcome"] == "hit", str(r.meta.get("trace")))

    r = await routing.route_to_agent(ctx("system_logic_router"), {"next_agent": "nope", "reason": "x"})
    check("routing to an unknown agent is refused", not r.success)
    r = await routing.route_to_agent(ctx("system_logic_router"), {"next_agent": "system_logic_router", "reason": "x"})
    check("routing to itself is refused", not r.success)
    r = await routing.route_to_agent(
        ctx("system_logic_router"), {"next_agent": "email_copywriter", "reason": "It is a draft request."}
    )
    check("a valid route emits a handoff artifact", r.success and r.meta["artifact"]["kind"] == "handoff", str(r.meta)[:120])

    print("\n— agent 9: approvals —")
    from app.agents import approvals

    decision = await approvals.request(
        ctx("human_approval_gatekeeper"), action="test", summary="s", parameters={"a": 1}
    )
    check(
        "with no socket to ask, an approval is REFUSED",
        not decision.approved and decision.decision == "disconnected",
        decision.decision,
    )

    print("\n— agent 10: code —")
    from app.agents.tools import code as code_tools

    source = (
        "def f(items, seen=[]):\n"
        "    out = ''\n"
        "    for i in range(len(items)):\n"
        "        out += str(items[i])\n"
        "        try:\n"
        "            seen.append(i)\n"
        "        except:\n"
        "            pass\n"
        "    return out\n"
    )
    r = await code_tools.parse_ast(ctx("code_refactoring_assistant"), {"code": source})
    kinds = {f["kind"] for f in r.meta["findings"]}
    check("AST finds the mutable default", "mutable_default" in kinds, str(kinds))
    check("AST finds the bare except", "bare_except" in kinds, str(kinds))
    check("AST finds O(n^2) string building", "quadratic_string_build" in kinds, str(kinds))
    check("AST finds range(len(x))", "range_len" in kinds, str(kinds))
    check("AST reports complexity", r.meta["totals"]["max_complexity"] >= 3, str(r.meta["totals"]))
    r = await code_tools.parse_ast(ctx("code_refactoring_assistant"), {"code": "def broken(:\n  pass"})
    check("a syntax error is reported with a line", not r.success and r.meta["syntax_error"]["line"] == 1, str(r.meta))
    r = await code_tools.parse_ast(ctx("code_refactoring_assistant"), {"code": "x=1", "language": "javascript"})
    check("AST refuses non-Python honestly", not r.success and "Python only" in r.output)

    print(f"\n{'=' * 60}\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for f in FAILED:
            print(f"  FAILED: {f}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
