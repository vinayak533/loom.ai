"""Quality evals for the ten specialists — transcripts for a human to grade.

    python scripts/eval_agents.py --label before            # all ten
    python scripts/eval_agents.py --label after --prompts 2
    python scripts/eval_agents.py --label after research_fact_checker

`test_agents_e2e.py` asks whether each agent *works*: it reached `agent_done`,
called its tools, honoured its output contract, was charged. That is a
pass/fail question and a harness can answer it. This script asks a different
question — whether the output is the work of a senior specialist or of a
competent default — and a harness cannot answer that, so it does not try.
What it does is make the comparison possible:

  * a **fixed set of prompts per agent**, chosen to expose judgment rather
    than formatting: a document with a spun statistic, a cold email with no
    proof point, a keyword whose SERP wants a different page shape than the
    user asked for, a claim that is true in one form and false in another,
    a rule set with a malformed rule, a snippet with a defect no linter
    reports;
  * a **rubric per agent** stating what a fifteen-year specialist would do
    with that prompt that a generic assistant would not;
  * every run written to `data/evals/<label>/<agent>.md` with the prompt, the
    tools it called, the credits it spent, the rubric, and the full output —
    so two labels (`before` the prompt rewrite, `after`) can be read side by
    side and graded by a person.

`--label` is the whole point: run it once on the old prompts, once on the
new, and grade both. The script prints a grade sheet skeleton at the end for
that purpose. It spends real credits, over the real socket, against the real
providers, exactly as the e2e test does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets  # noqa: E402

OUT_ROOT = Path(__file__).resolve().parents[1] / "data" / "evals"


# --- the fixed prompt set ---------------------------------------------------
#
# Every entry is (prompt, what-a-senior-would-do). The second half is the
# rubric the grader reads beside the output. Prompts are deliberately the
# kind where a competent-but-generic answer and a senior one diverge.

EVALS: dict[str, dict] = {
    "document_summarizer": {
        "rubric": [
            "Names what the document is trying to make the reader conclude, then reports what it shows.",
            "Flags the spun figure: a percentage with no base, a growth rate with no comparison period, a selectively chosen window.",
            "Separates stated claims from evidenced findings; does not promote 'management believes' to a finding.",
            "Caveats name what a competent reader would expect and cannot find (the cost beside the benefit, the denominator).",
            "Every retained number keeps its unit, period and base. Nothing is recomputed silently.",
        ],
        "prompts": [
            (
                "Summarise this update for the board.\n\n"
                "Platform engagement is up 240% since we changed the metric definition in "
                "March. Daily active users reached 18,400 in the best week of Q2. Gross "
                "margin improved to 61% (from 58%) after we reclassified support costs as "
                "G&A. Net revenue retention is strong. Churn in the SMB segment was 4.1% "
                "monthly; enterprise churn is not broken out this quarter. We closed 3 of "
                "the 5 enterprise deals we forecast, with the remaining 2 'highly likely' "
                "in Q3. Cash at quarter end was $6.1M against a monthly burn that management "
                "expects to bring down. The team is energised and the roadmap is the "
                "strongest it has ever been."
            ),
            (
                "Compress this incident write-up into a one-page brief, keeping every "
                "number.\n\n"
                "At 09:14 UTC a deploy of api-gateway v2.31 raised p99 latency from 210ms to "
                "4.8s for 37 minutes. 12% of requests in that window returned 502. The "
                "rollback completed at 09:51. Root cause: a connection-pool size change from "
                "64 to 8 committed as part of an unrelated config cleanup, reviewed by one "
                "engineer. Customer impact: 41 support tickets, two enterprise accounts "
                "escalated. Action items: add a pool-size alert (owner: SRE, due 14 days); "
                "require two reviewers on config changes (owner: platform lead, no date); "
                "consider canary deploys (no owner). The on-call engineer noticed the issue "
                "from a customer tweet before the alert fired, because the latency alert "
                "threshold is 5s."
            ),
        ],
    },
    "email_copywriter": {
        "rubric": [
            "One ask, in the last line, phrased so 'yes' costs the reader one sentence.",
            "Opens with the reader's situation, not the sender's company.",
            "Does not invent a proof point; where the brief lacks one, asks for it in a bracketed note rather than fabricating a customer or a number.",
            "Spam flags are judged, not obeyed blindly: a kept flag has a stated reason.",
            "Under the length limit for the audience; subject promises exactly what the body delivers.",
        ],
        "prompts": [
            (
                "Cold outbound to a Head of Data at a 200-person e-commerce company. We sell "
                "a data-quality monitoring tool. I don't have a customer reference I can "
                "name yet. Make it compelling."
            ),
            (
                "Warm follow-up. I met Priya at the SaaStr booth last Tuesday, she runs "
                "RevOps at a Series B fintech, we talked about her CRM being a mess and I "
                "said I'd send over how we helped a similar company. We cut a client's "
                "lead-routing time from 2 days to 20 minutes."
            ),
        ],
    },
    "ui_component_designer": {
        "rubric": [
            "Designs the non-happy states (empty, loading, error, overflow) or says which it left out and why.",
            "Handles long content: a thirty-character plan name, a long email, does not break the layout.",
            "Native semantics: real buttons, labelled inputs, a heading structure; no div soup.",
            "States the contrast pairing chosen and keeps focus visible.",
            "Notes explain the trade-offs (why buttons not links, why the grid collapses where it does).",
        ],
        "prompts": [
            (
                "A pricing section with three tiers (Free, Team, Enterprise). Team is the "
                "recommended one. Enterprise has no price, just 'Contact sales'. It has to "
                "work on a phone."
            ),
            (
                "A data table row for a users admin page: avatar, name, email, role "
                "(select), status badge, and a kebab menu. Some emails are very long."
            ),
        ],
    },
    "creative_prompt_engineer": {
        "rubric": [
            "Picks one idea when the brief contains two, and says which it dropped.",
            "Lens and light are chosen for the subject with real values, and the reasoning is stated.",
            "Negative prompt is specific to this subject's failure modes.",
            "Tells the user what the model will not do reliably (legible text, exact counts) and what to do instead.",
            "Prompt is tight and front-loaded; no trailing 'masterpiece, 8k' filler.",
        ],
        "prompts": [
            (
                "A poster for a jazz festival, with the dates 'July 12-14' on it in big "
                "letters, showing a saxophonist on stage and also the city skyline at "
                "night and a crowd of exactly twelve people."
            ),
            (
                "Product shot of a matte black ceramic mug for our online store. Square."
            ),
        ],
    },
    "graphic_poster_creator": {
        "rubric": [
            "Settles purpose, format and hierarchy before spending; picks the aspect ratio from the deliverable.",
            "Plans text and logo as overlays and leaves space, rather than asking the model to render them.",
            "One render then an honest art-director evaluation; a second render is justified or not requested.",
            "Reports the real returned parameters (model, seed, dimensions).",
            "If unconfigured, says so and stops — no placeholder.",
        ],
        "prompts": [
            (
                "Make a hero banner for our coffee brand's website. It needs our name "
                "'Ridgeline Roasters' across it. Warm, morning light."
            ),
        ],
        "approve": True,
    },
    "seo_content_creator": {
        "rubric": [
            "Reads the SERP for intent and page shape; says so when the ranking shape differs from what the user asked for.",
            "Headings are the sub-questions people ask; each section answers its heading in the first two sentences.",
            "No filler openers; specifics and practitioner caveats; asks for real experience rather than inventing it.",
            "Treats density as a diagnostic; fixes by rewriting, not by inserting the phrase.",
            "Reports measured numbers only, and says what they do and do not tell you about ranking.",
        ],
        "prompts": [
            (
                "Write a 600-word blog post targeting the keyword 'best project "
                "management software'. Include the keyword at least 10 times."
            ),
            (
                "800 words on 'postgres connection pooling' for a developer audience. "
                "Target phrases: 'connection pooling', 'pgbouncer'."
            ),
        ],
    },
    "research_fact_checker": {
        "rubric": [
            "Pins the claim's specific form (number, date, scope) and notices when it is true in one form and false in another.",
            "Traces to the origin: identifies when many outlets are one source.",
            "Overrides the domain-trust heuristic with a stated reason when the page merits it.",
            "Distinguishes 'no evidence found' from 'evidence against'; uses UNVERIFIABLE when that is the honest answer.",
            "States confidence and what would change the verdict.",
        ],
        "prompts": [
            (
                "Fact-check: 'Python is the most used programming language in the world "
                "according to GitHub's 2024 Octoverse report.'"
            ),
            (
                "Verify: 'The EU AI Act bans all use of facial recognition by police.'"
            ),
        ],
    },
    "system_logic_router": {
        "rubric": [
            "Treats a rule that could not be evaluated as a defect in the rules, reported from the trace, not as a non-match.",
            "States the deciding signal in one line.",
            "When two specialists fit, routes to the one whose work comes first and names the second hop.",
            "The route is emitted through the tool call, never as bare text.",
            "Does not route to a default to look decisive.",
        ],
        "prompts": [
            (
                "Route this. Rules: [{\"field\": \"kind\", \"operator\": \"equals\", "
                "\"value\": \"email\", \"outcome\": \"email_copywriter\"}, "
                "{\"outcome\": \"document_summarizer\"}, {\"field\": \"kind\", "
                "\"operator\": \"equals\", \"value\": \"code\"}]. Payload: "
                "{\"kind\": \"pdf\", \"pages\": 40, \"ask\": \"turn this into a launch email\"}"
            ),
            (
                "Here's what came in: a customer pasted a 3,000-word competitor review "
                "and asked 'is any of this actually true, and can you write a rebuttal "
                "blog post?'. Who handles it?"
            ),
        ],
    },
    "human_approval_gatekeeper": {
        "rubric": [
            "Classifies on reversibility, blast radius, cost, external visibility and data sensitivity — and says which drive the rating.",
            "Puts exact parameters in the request (table, row count, recipients, amount).",
            "Splits a bundled action into separate decisions in execution order.",
            "Offers a safer variant (dry run, backup, smaller batch) and recommends one option with a reason.",
            "Lets a genuinely low-risk action through without ceremony.",
        ],
        "prompts": [
            (
                "About to run: DELETE FROM events WHERE created_at < now() - interval "
                "'90 days' on production (roughly 41M rows), then email all 1,200 "
                "customers that their old data was removed. Gate it."
            ),
            (
                "I want to rename a local git branch from 'feature/x' to 'feature/y'. "
                "Does this need approval?"
            ),
        ],
        "approve": True,
    },
    "code_refactoring_assistant": {
        "rubric": [
            "If the lint came back degraded, the audit's first line says the style/correctness rules did not run.",
            "Finds the defects no linter reports (shared mutable default, swallowed exception, wrong data structure, O(n²) on a large input) and names them by production consequence.",
            "Separates fixes from refactors and says which changes alter behaviour.",
            "Tests assert on the cases that used to break, not only the happy path.",
            "Scope discipline: lists what it saw but did not change.",
        ],
        "prompts": [
            (
                "Audit and fix this, then prove it:\n\n"
                "```python\n"
                "import json\n"
                "\n"
                "def load_users(path, cache={}):\n"
                "    if path in cache:\n"
                "        return cache[path]\n"
                "    try:\n"
                "        data = json.load(open(path))\n"
                "    except Exception:\n"
                "        data = []\n"
                "    users = []\n"
                "    for u in data:\n"
                "        if u['id'] not in [x['id'] for x in users]:\n"
                "            users.append(u)\n"
                "    cache[path] = users\n"
                "    return users\n"
                "\n"
                "def report(users):\n"
                "    out = ''\n"
                "    for u in users:\n"
                "        out += u['name'] + ',' + str(u.get('age')) + '\\n'\n"
                "    return out\n"
                "```"
            ),
            (
                "This is slow on big inputs. Make it fast without changing what it "
                "returns.\n\n"
                "```python\n"
                "def common(a, b):\n"
                "    result = []\n"
                "    for x in a:\n"
                "        if x in b and x not in result:\n"
                "            result.append(x)\n"
                "    return result\n"
                "```"
            ),
        ],
    },
}


async def run_one(
    agent_id: str, prompt: str, port: int, approve: bool, spend: bool, model: str = ""
) -> dict:
    session_id = str(uuid.uuid4())
    url = f"ws://localhost:{port}/ws/agent/{session_id}?agent={agent_id}"
    text = ""
    tools: list[str] = []
    tool_notes: list[str] = []
    errors: list[str] = []
    spent = 0.0
    reason = ""
    started = datetime.now(timezone.utc)
    try:
        async with websockets.connect(url, max_size=32 * 1024 * 1024) as ws:
            if model:
                # Same frame the model picker sends; the session is pinned to
                # it before the first message goes out.
                await ws.send(json.dumps({"type": "set_model", "model_id": model}))
            await ws.send(json.dumps({"type": "user_message", "content": prompt}))
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=420)
                except asyncio.TimeoutError:
                    errors.append("timed out")
                    break
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "agent_token":
                    text += event.get("content", "")
                elif kind == "tool_call_start":
                    tools.append(event["tool"])
                elif kind == "tool_call_result":
                    meta = event.get("meta") or {}
                    if meta.get("degraded"):
                        tool_notes.append(f"{event.get('call_id', '?')}: degraded")
                    if meta.get("not_configured"):
                        tool_notes.append("not configured: " + str(meta.get("tool", "?")))
                elif kind == "credits":
                    spent = max(spent, float(event.get("spent_this_turn") or 0.0))
                elif kind == "agent_paused":
                    decision = "approved" if (approve and spend) else "rejected"
                    await ws.send(json.dumps({
                        "type": "approval_resolve",
                        "approval_id": event["approval_id"],
                        "decision": decision,
                    }))
                    tool_notes.append(f"paused on {event['action']} -> {decision}")
                elif kind == "error":
                    errors.append(event.get("message", ""))
                elif kind == "agent_done":
                    reason = event.get("reason", "")
                    break
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")
    return {
        "prompt": prompt,
        "text": text,
        "tools": tools,
        "notes": tool_notes,
        "errors": errors,
        "spent": spent,
        "reason": reason,
        "seconds": (datetime.now(timezone.utc) - started).total_seconds(),
    }


def write_transcript(out_dir: Path, agent_id: str, rubric: list[str], runs: list[dict]) -> Path:
    path = out_dir / f"{agent_id}.md"
    lines = [f"# {agent_id}", ""]
    lines.append("## Rubric — what a senior specialist does here that a generic one does not")
    lines.append("")
    for item in rubric:
        lines.append(f"- [ ] {item}")
    lines.append("")
    for i, run in enumerate(runs, start=1):
        lines.append(f"## Prompt {i}")
        lines.append("")
        lines.append("```text")
        lines.append(run["prompt"])
        lines.append("```")
        lines.append("")
        lines.append(
            f"- tools: {', '.join(run['tools']) or '(none)'}"
        )
        lines.append(f"- credits spent: {run['spent']:.2f} · {run['seconds']:.0f}s · ended: {run['reason'] or '—'}")
        if run["notes"]:
            lines.append(f"- notes: {'; '.join(run['notes'])}")
        if run["errors"]:
            lines.append(f"- ERRORS: {'; '.join(run['errors'])}")
        lines.append("")
        lines.append("### Output")
        lines.append("")
        lines.append(run["text"].strip() or "(no text)")
        lines.append("")
        lines.append("### Grade (1-5, and why)")
        lines.append("")
        lines.append("_ /5 — ")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agents", nargs="*", help="Which agents. Default: all ten.")
    parser.add_argument("--label", required=True, help="e.g. before, after")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--prompts", type=int, default=0, help="Max prompts per agent (0 = all).")
    parser.add_argument("--no-spend", action="store_true", help="Reject spend approvals.")
    parser.add_argument(
        "--model", default="",
        help="Run every agent on this model id (default: the section default). Use the "
             "same value for both labels, or the comparison measures the model.",
    )
    args = parser.parse_args()

    selected = args.agents or list(EVALS)
    unknown = [a for a in selected if a not in EVALS]
    if unknown:
        print(f"Unknown agent(s): {', '.join(unknown)}")
        return 2

    out_dir = OUT_ROOT / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Writing transcripts to {out_dir}" + (f" (model: {args.model})" if args.model else ""))

    summary: list[tuple[str, int, float, int]] = []
    total_spent = 0.0
    for agent_id in selected:
        spec = EVALS[agent_id]
        prompts = spec["prompts"][: args.prompts] if args.prompts else spec["prompts"]
        runs = []
        print(f"\n{agent_id}")
        for i, prompt in enumerate(prompts, start=1):
            print(f"  prompt {i}/{len(prompts)} …", end="", flush=True)
            run = await run_one(
                agent_id, prompt, args.port, bool(spec.get("approve")), not args.no_spend,
                model=args.model,
            )
            runs.append(run)
            total_spent += run["spent"]
            status = "ERR" if run["errors"] else "ok"
            print(f" {status} {len(run['text'])} chars, {run['spent']:.1f} cr, tools: {', '.join(run['tools']) or '-'}")
        path = write_transcript(out_dir, agent_id, spec["rubric"], runs)
        summary.append((agent_id, len(runs), sum(r["spent"] for r in runs), sum(1 for r in runs if r["errors"])))
        print(f"  -> {path.name}")

    sheet = out_dir / "GRADES.md"
    lines = [f"# Grade sheet — {args.label}" + (f" (model: {args.model})" if args.model else ""), "",
             "| agent | runs | credits | errors | grade /5 | notes |", "|---|---|---|---|---|---|"]
    for agent_id, n, spent, errs in summary:
        lines.append(f"| {agent_id} | {n} | {spent:.1f} | {errs} |  |  |")
    lines.append("")
    lines.append(f"Total credits: {total_spent:.1f}. Grade each transcript against its rubric; the "
                 "same prompts under another label are the comparison.")
    sheet.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nTotal credits spent: {total_spent:.1f}. Grade sheet: {sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
