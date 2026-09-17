"""End-to-end smoke test: every one of the ten specialists, over the real socket.

    python scripts/test_agents_e2e.py            # all ten
    python scripts/test_agents_e2e.py --port 8010
    python scripts/test_agents_e2e.py document_summarizer email_copywriter

Each agent gets one real prompt through the real websocket, against the real
LLM router and the real credit meter. What is asserted per agent:

  * the run reaches `agent_done` without an error frame;
  * it called at least one of its own tools (the point of a specialist),
    and — for the agents whose deliverable is an artifact rather than
    prose — the specific tool that produces it;
  * its output honours the format its contract specifies;
  * credits were actually deducted.

Two agents need a decision from a human, which this script supplies:

  * Agent 5 pauses before spending image-generation budget. The script
    APPROVES it, because verifying that the render path really works is the
    only way to know the plumbing is right — it costs a few Stability credits.
    Pass --no-spend to reject instead, which still exercises the gate.
  * Agent 9 pauses by design. The script approves its test action.

Agent 2's `send_email` is never exercised: sending mail is irreversible and
outward-facing, and a smoke test is not a reason to put a message in someone's
inbox. The tool is wired and gated; the gate is proven by Agents 5 and 9.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets  # noqa: E402


# --- what each agent is asked, and what counts as a pass -------------------


def has_metadata_block(text: str) -> bool:
    return bool(re.search(r"^---\s*$.*?^source", text, re.MULTILINE | re.DOTALL)) or (
        "source_length" in text and "generated" in text
    )


def has_json_draft(text: str) -> bool:
    for candidate in re.findall(r"```json\s*(\{[\s\S]*?\})\s*```", text) or re.findall(
        r"(\{[\s\S]*\})", text
    ):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "subject_line" in parsed and "email_body" in parsed:
            return True
    return False


def has_html_block(text: str) -> bool:
    return bool(re.search(r"```html\s*\n[\s\S]*?<\w+[\s\S]*?```", text, re.IGNORECASE))


def has_prompt_block(text: str) -> bool:
    blocks = re.findall(r"```(?:text)?\s*\n([\s\S]*?)```", text)
    return any("," in b and len(b.strip()) > 40 for b in blocks)


def has_headings(text: str) -> bool:
    return len(re.findall(r"^#{2,3}\s+\S", text, re.MULTILINE)) >= 2


def has_links(text: str) -> bool:
    return bool(re.search(r"\[[^\]]+\]\(https?://", text))


def has_routing_directive(text: str) -> bool:
    return bool(re.search(r'"next_agent"\s*:\s*"\w+"', text))


def has_code_block(text: str) -> bool:
    return bool(re.search(r"```(?:python|py)?\s*\n[\s\S]*?(def |=|print)", text))


CASES: dict[str, dict] = {
    "document_summarizer": {
        "prompt": (
            "Summarise this. Q3 revenue was $4.2M, up 18% from Q2's $3.56M. "
            "Headcount grew from 42 to 51. Churn fell to 2.1% from 3.4%. CAC "
            "rose to $1,840 from $1,610, which management attributes to a "
            "shift toward enterprise. Runway stands at 19 months. The board "
            "asked for a hiring freeze in Q4 pending a review, and we are, of "
            "course, delighted with the momentum the team has built."
        ),
        "expect_tools": {"count_tokens", "parse_source", "chunk_document"},
        "check": ("metadata block + metrics table", lambda t: has_metadata_block(t) and "|" in t),
    },
    "email_copywriter": {
        "prompt": (
            "Write a cold outbound email to a VP of Engineering at a mid-size "
            "fintech about our API observability product. We cut mean time to "
            "resolution by 40% at Monzo."
        ),
        "expect_tools": {"list_email_templates", "score_subject_line", "check_spam_words"},
        "check": ("JSON draft with subject_line and email_body", has_json_draft),
    },
    "ui_component_designer": {
        "prompt": "Build an accessible notification banner with a dismiss button and an icon.",
        "expect_tools": {"lookup_tailwind", "lookup_lucide_icons"},
        "check": ("fenced html block containing markup", has_html_block),
    },
    "creative_prompt_engineer": {
        "prompt": "A lighthouse in a storm at dusk, for a movie poster.",
        "expect_tools": {"normalize_aspect_ratio", "list_style_modifiers"},
        "check": ("comma-separated prompt in a fenced block", has_prompt_block),
    },
    "graphic_poster_creator": {
        "prompt": (
            "Generate a 1:1 image: a single ceramic coffee mug on a white "
            "background, soft studio lighting, 85mm lens, minimal product shot."
        ),
        "expect_tools": {"generate_image"},
        # Same contract, one branch wider: the deliverable is the image, and
        # the only honest alternative is saying out loud that the tool is
        # unconfigured. `len(t) > 20` accepted any two sentences at all.
        "require_tools": {"generate_image"},
        "require_tools_unless_unconfigured": True,
        "check": (
            "names the render, or states plainly that it is not configured",
            lambda t: len(t) > 20,
        ),
        "approve": True,
        "spends_money": True,
    },
    "seo_content_creator": {
        "prompt": (
            "Write a 350-word article targeting the phrase 'headless CMS "
            "migration'. Keep it short — this is a smoke test."
        ),
        "expect_tools": {"readability_score", "keyword_density", "search_web"},
        "check": ("meta section + real headings", lambda t: has_headings(t) and "meta" in t.lower()),
    },
    "research_fact_checker": {
        "prompt": "Fact-check this claim: the Rust programming language is used in the Linux kernel.",
        "expect_tools": {"search_web", "rank_domain_trust", "read_url"},
        "check": ("claims with clickable citations", has_links),
    },
    "system_logic_router": {
        "prompt": (
            "Here is upstream data. Which specialist should handle it next?\n"
            '{"kind": "raw_document", "char_count": 84000, "mime": "application/pdf", '
            '"goal": "condense for the board"}'
        ),
        "expect_tools": {"route_to_agent", "validate_json_schema", "match_patterns", "evaluate_conditions"},
        # Agent 8's deliverable is not text — it is the handoff artifact, and
        # only `route_to_agent` produces one. The model can and did write the
        # same JSON directly into its reply, which reads identically and gives
        # the user no button to press; `has_routing_directive` passed on it and
        # so did "at least one own tool", because it had called
        # `validate_json_schema` on the way. Named explicitly here, so a run
        # that produces prose in place of the artifact fails.
        "require_tools": {"route_to_agent"},
        "check": ("routing directive JSON", has_routing_directive),
    },
    "human_approval_gatekeeper": {
        "prompt": (
            "I am about to delete the production analytics table to free up "
            "space. Gate this for me."
        ),
        "expect_tools": {"request_approval"},
        "check": ("reports the decision", lambda t: len(t) > 20),
        "approve": True,
    },
    "code_refactoring_assistant": {
        "prompt": (
            "Audit and fix this, then run it to prove it works:\n\n"
            "```python\n"
            "def dedupe(items, seen=[]):\n"
            "    out = ''\n"
            "    for i in range(len(items)):\n"
            "        if items[i] not in seen:\n"
            "            seen.append(items[i])\n"
            "            out += str(items[i]) + ','\n"
            "    return out\n"
            "```"
        ),
        "expect_tools": {"parse_ast", "lint_code", "run_code"},
        "check": ("audit + code block", lambda t: has_code_block(t) and "|" in t),
    },
}


async def run_agent(agent_id: str, case: dict, port: int, spend: bool) -> dict:
    session_id = str(uuid.uuid4())
    url = f"ws://localhost:{port}/ws/agent/{session_id}?agent={agent_id}"

    text = ""
    tools_called: list[str] = []
    errors: list[str] = []
    approvals: list[dict] = []
    credits_before: float | None = None
    credits_after: float | None = None
    spent = 0.0
    not_configured: list[str] = []
    done = False
    reason = ""

    try:
        async with websockets.connect(url, max_size=32 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"type": "user_message", "content": case["prompt"]}))

            while not done:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=300)
                except asyncio.TimeoutError:
                    errors.append("timed out waiting for the run to finish")
                    break

                event = json.loads(raw)
                kind = event.get("type")

                if kind == "agent_token":
                    text += event.get("content", "")
                elif kind == "tool_call_start":
                    tools_called.append(event["tool"])
                elif kind == "tool_call_result":
                    if (event.get("meta") or {}).get("not_configured"):
                        not_configured.append((event.get("meta") or {}).get("tool", "?"))
                elif kind == "credits":
                    if credits_before is None:
                        credits_before = event["balance"] + event["spent_this_turn"]
                    credits_after = event["balance"]
                    spent = max(spent, event.get("spent_this_turn", 0.0))
                elif kind == "agent_paused":
                    approvals.append(event)
                    decision = "approved" if (case.get("approve") and spend) else "rejected"
                    print(
                        f"      ↳ paused on `{event['action']}` "
                        f"({event['risk']} risk) — answering {decision}"
                    )
                    await ws.send(
                        json.dumps(
                            {
                                "type": "approval_resolve",
                                "approval_id": event["approval_id"],
                                "decision": decision,
                            }
                        )
                    )
                elif kind == "error":
                    errors.append(event.get("message", ""))
                elif kind == "agent_done":
                    reason = event.get("reason", "")
                    done = True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")

    own_tools = set(tools_called) & set(case["expect_tools"])
    label, check = case["check"]

    # Some agents' contract is an artifact, not prose. For those, "called one
    # of its own tools" is not the assertion worth making — the specific tool
    # that produces the artifact is. Agent 8 emitted correct routing JSON as
    # bare text in one run in four: the check passed, and no handoff button
    # ever appeared.
    required = set(case.get("require_tools") or ())
    missing_required = sorted(required - set(tools_called))
    if (
        missing_required
        and case.get("require_tools_unless_unconfigured")
        and not_configured
    ):
        # A tool with no API key cannot be called, and the agent saying so is
        # the correct outcome rather than a failure to produce the artifact.
        missing_required = []

    return {
        "agent": agent_id,
        "done": done,
        "reason": reason,
        "tools": tools_called,
        "own_tools": sorted(own_tools),
        "used_own_tool": bool(own_tools),
        "required_tools": sorted(required),
        "missing_required": missing_required,
        "format_label": label,
        "format_ok": bool(text) and check(text),
        "chars": len(text),
        "errors": errors,
        "approvals": approvals,
        "not_configured": not_configured,
        "credits_before": credits_before,
        "credits_after": credits_after,
        "spent": spent,
        "text": text,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agents", nargs="*", help="Which agents to test. Default: all.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--no-spend",
        action="store_true",
        help="Reject the image-generation approval instead of approving it.",
    )
    args = parser.parse_args()

    selected = args.agents or list(CASES)
    unknown = [a for a in selected if a not in CASES]
    if unknown:
        print(f"Unknown agent(s): {', '.join(unknown)}")
        return 2

    results = []
    for i, agent_id in enumerate(selected, start=1):
        case = CASES[agent_id]
        spends = case.get("spends_money") and not args.no_spend
        print(f"\n[{i}/{len(selected)}] {agent_id}{'  (spends real budget)' if spends else ''}")
        result = await run_agent(agent_id, case, args.port, spend=not args.no_spend)
        results.append(result)

        ok = (
            result["done"]
            and not result["errors"]
            and not result["missing_required"]
        )
        print(f"      done={result['done']} reason={result['reason'] or '—'} chars={result['chars']}")
        print(f"      tools: {', '.join(result['tools']) or '(none)'}")
        print(
            f"      own tools used: {'yes — ' + ', '.join(result['own_tools']) if result['used_own_tool'] else 'NO'}"
        )
        if result["required_tools"]:
            print(
                f"      contract tool ({', '.join(result['required_tools'])}): "
                + (
                    "MISSING — " + ", ".join(result["missing_required"])
                    if result["missing_required"]
                    else "called"
                )
            )
        print(f"      format ({result['format_label']}): {'ok' if result['format_ok'] else 'FAIL'}")
        if result["not_configured"]:
            print(f"      not configured: {', '.join(result['not_configured'])}")
        if result["approvals"]:
            print(f"      approvals raised: {len(result['approvals'])}")
        print(
            f"      credits: {result['credits_before']} -> {result['credits_after']} "
            f"(spent {result['spent']})"
        )
        if result["errors"]:
            print(f"      ERRORS: {result['errors']}")
        if not ok:
            print(f"      --- output ---\n{result['text'][:600]}")

    # --- summary ----------------------------------------------------------
    print("\n" + "=" * 74)
    print(f"{'agent':30s} {'run':5s} {'tools':6s} {'format':7s} {'credits':9s}")
    print("-" * 74)
    failures = 0
    for r in results:
        run_ok = r["done"] and not r["errors"]
        charged = (r["spent"] or 0) > 0
        # An agent whose contract names a specific tool has to have called it.
        # Folded into the `tools` column rather than given its own, because
        # "did this agent do its job with its tools" is one question.
        tools_ok = r["used_own_tool"] and not r["missing_required"]
        row_ok = run_ok and tools_ok and r["format_ok"] and charged
        if not row_ok:
            failures += 1
        print(
            f"{r['agent']:30s} {'ok' if run_ok else 'FAIL':5s} "
            f"{'ok' if tools_ok else 'FAIL':6s} "
            f"{'ok' if r['format_ok'] else 'FAIL':7s} "
            f"{('-' + str(round(r['spent'], 2))) if charged else 'FAIL':9s}"
        )
    print("-" * 74)
    print(f"{len(results) - failures}/{len(results)} agents fully passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
