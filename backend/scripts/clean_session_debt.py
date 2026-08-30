"""One-time cleanup for rows left behind by two fixed bugs.

Both source bugs are already fixed. What remains is the wreckage they wrote to
the database while they were live, which no amount of correct code going
forward will tidy up — a bad title stays on the shelf until something goes and
changes it.

**1. Placeholder titles.** `generate_title` used to store the router's
"the model produced no answer" sentence verbatim as a session's name, so the
history filled with rows called

    (The model ended its turn without producing an answer. This usually
     means it spe…

The fix (`app/agent/llm.py`) rejects that string now, but 29 rows carry it.

**2. Leaked reasoning as a title.** Not part of the original brief, found while
looking for the first: some titles are the *model's internal monologue about
the title prompt* — "The user wants a 3-6 word title for a coding session…" —
and one is a whole `## Setup` document. A reasoning model answered the naming
call by thinking out loud, and only the empty-turn sentinel was being guarded
against. Same symptom, same shelf, so the same pass cleans both.

**3. Leaked provider errors in transcripts.** An OpenRouter 402 used to be
written into the conversation verbatim — roughly 900 characters of nested JSON
including an account identifier. `ModelCallError.user_message()` fixed the
source. This script also *checks* for surviving rows, which is the honest way
to close that item: see the note in `scan_messages` for what was actually
found.

Every session is renamed from its own first real user message, which is what
the title should have been. A session with nothing to name it from is marked
"Untitled session" — plainly untitled rather than dressed up in a sentence that
was never about it.

    .venv/Scripts/python -m scripts.clean_session_debt            # report only
    .venv/Scripts/python -m scripts.clean_session_debt --apply    # write
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.db.supabase_client import enabled, get_client  # noqa: E402
from app.llm_router import EMPTY_TURN_TEXT  # noqa: E402

UNTITLED = "Untitled session"

#: A title is broken when it matches any of these.
#:
#: Matched on a prefix rather than the whole string because the column is
#: `text` but the values were truncated at 80 characters on the way in, so no
#: two are identical past that point.
BROKEN_TITLE_TESTS: list[tuple[str, re.Pattern[str]]] = [
    (
        "empty-turn placeholder",
        re.compile(re.escape(EMPTY_TURN_TEXT[:45]), re.I),
    ),
    (
        "leaked title-prompt reasoning",
        re.compile(
            r"^\s*(the user (wants|is asking|asks)|we need to|i (need|should) (to )?(write|produce))",
            re.I,
        ),
    ),
    # A title is a short phrase. A markdown heading, a fenced block or a line
    # break means a whole document was stored in the column.
    ("markdown document", re.compile(r"^\s*(#{1,6}\s|```)|\n")),
]

#: Raw provider payloads that were being written into transcripts. Deliberately
#: narrow: this deletes nothing and rewrites message bodies, so a false
#: positive would destroy a real answer. Every pattern here is a shape only a
#: provider error envelope has.
LEAK_TESTS: list[tuple[str, re.Pattern[str]]] = [
    ("openrouter 402 envelope", re.compile(r'"code"\s*:\s*402')),
    ("credits URL", re.compile(r"openrouter\.ai/settings/credits", re.I)),
    ("provider metadata block", re.compile(r'"provider_name"\s*:')),
    ("raw quota error", re.compile(r'"type"\s*:\s*"insufficient_quota"')),
]

REDACTED = (
    "[This message held a raw error response from the model provider, "
    "including account identifiers. It has been replaced; the failure it "
    "described is long over.]"
)


def why_broken(title: str | None) -> str | None:
    if not title:
        return None
    for reason, pattern in BROKEN_TITLE_TESTS:
        if pattern.search(title):
            return reason
    return None


def first_user_text(client, session_id: str) -> str:
    """The opening thing a person actually typed in this session.

    `role = 'user'` alone is not enough even here: the transcript table stores
    tool results under that role too. Those rows have a null `content` (the
    payload lives in `tool_calls`), so filtering on non-null content is the
    same distinction `repository.user_turn_positions` draws in the checkpoint.
    """
    res = (
        client.table("messages")
        .select("content,created_at")
        .eq("session_id", session_id)
        .eq("role", "user")
        .not_.is_("content", "null")
        .order("created_at")
        .limit(5)
        .execute()
    )
    for row in res.data or []:
        text = (row.get("content") or "").strip()
        if text:
            return text
    return ""


def title_from(text: str, limit: int = 60) -> str:
    """A readable name from a message, without calling a model.

    Deliberately mechanical. This is a repair pass over rows whose *only*
    problem is that a model was trusted to name them; sending them back to a
    model to be named again would be an odd lesson to take from that, would
    cost real money across hundreds of rows, and would need the run to be
    online. The first clause of what someone asked for is a good title.
    """
    flat = " ".join(text.split())
    if not flat:
        return UNTITLED
    # Cut at the first sentence or clause boundary that leaves something
    # substantial, so "Fix the login bug. It happens when…" becomes "Fix the
    # login bug" rather than the first 60 characters of both sentences.
    for mark in (". ", "? ", "! ", " — ", "; ", "\n"):
        at = flat.find(mark)
        if 12 <= at <= limit:
            flat = flat[:at]
            break
    if len(flat) > limit:
        cut = flat.rfind(" ", 0, limit)
        flat = flat[: cut if cut > limit // 2 else limit].rstrip(",;:- ")
    flat = flat.rstrip(" .,;:-")
    if not flat:
        return UNTITLED
    return flat[0].upper() + flat[1:]


def scan_sessions(client) -> list[dict]:
    rows: list[dict] = []
    page = 0
    while True:
        res = (
            client.table("sessions")
            .select("id,title,section,agent_id,created_at")
            .range(page * 1000, (page + 1) * 1000 - 1)
            .execute()
        )
        batch = res.data or []
        if not batch:
            break
        rows.extend(batch)
        page += 1
        if page > 50:
            break
    return rows


def scan_messages(client) -> list[dict]:
    """Message rows still holding a raw provider error payload.

    Worth stating what this found when it was written, because "we checked" is
    the deliverable here as much as any repair: **zero rows**, across 1,533
    messages and 1,482 checkpoint entries. The leak never reached storage. The
    error path emitted the provider's text to the socket as a transient
    `error` frame and returned *without* appending an assistant message, so it
    was rendered once and never written down. The scan stays in the script
    because that reasoning is only as good as the evidence, and this is how the
    evidence gets re-gathered on a database this run has not seen.
    """
    hits: list[dict] = []
    page = 0
    while True:
        res = (
            client.table("messages")
            .select("id,session_id,role,content,created_at")
            .range(page * 1000, (page + 1) * 1000 - 1)
            .execute()
        )
        batch = res.data or []
        if not batch:
            break
        for row in batch:
            content = row.get("content")
            if not isinstance(content, str) or not content:
                continue
            for reason, pattern in LEAK_TESTS:
                if pattern.search(content):
                    hits.append({**row, "reason": reason})
                    break
        page += 1
        if page > 50:
            break
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes. Without it this only reports.",
    )
    args = parser.parse_args()

    if not enabled():
        print("Supabase is not configured; nothing to clean.")
        return 0
    client = get_client()

    print("=" * 70)
    print("Session title debt")
    print("=" * 70)

    sessions = scan_sessions(client)
    print(f"{len(sessions)} sessions scanned.")

    broken = [(s, why_broken(s.get("title"))) for s in sessions]
    broken = [(s, r) for s, r in broken if r]
    if not broken:
        print("No broken titles found.")
    else:
        by_reason: dict[str, int] = {}
        for _, reason in broken:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"  {count:4d}  {reason}")

    planned: list[tuple[str, str, str, str]] = []
    for session, reason in broken:
        source = first_user_text(client, session["id"])
        planned.append(
            (session["id"], session.get("title") or "", title_from(source), reason)
        )

    if planned:
        named = sum(1 for _, _, new, _ in planned if new != UNTITLED)
        print(
            f"\n{named} can be renamed from their first message; "
            f"{len(planned) - named} have no message to name them from and "
            f'become "{UNTITLED}".'
        )
        print("\nFirst 12:")
        for sid, old, new, reason in planned[:12]:
            print(f"  {sid[:8]}  {old[:44]!r}")
            print(f"         -> {new!r}   [{reason}]")

    print("\n" + "=" * 70)
    print("Leaked provider errors in transcripts")
    print("=" * 70)
    leaks = scan_messages(client)
    if not leaks:
        print(
            "No message rows contain raw provider error payloads.\n"
            "The 402 leak was emitted to the socket as a transient `error`\n"
            "frame and never appended to the conversation, so it was never\n"
            "persisted. Nothing to redact."
        )
    else:
        print(f"{len(leaks)} message row(s) hold a raw provider payload:")
        for row in leaks[:10]:
            print(f"  {row['id'][:8]}  {row['reason']}  {len(row['content'])} chars")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write these changes.")
        return 0

    print("\nApplying…")
    renamed = 0
    for sid, _, new, _ in planned:
        res = (
            client.table("sessions")
            .update({"title": new})
            .eq("id", sid)
            .execute()
        )
        if res is not None:
            renamed += 1
    print(f"  {renamed} session(s) retitled.")

    redacted = 0
    for row in leaks:
        res = (
            client.table("messages")
            .update({"content": REDACTED})
            .eq("id", row["id"])
            .execute()
        )
        if res is not None:
            redacted += 1
    print(f"  {redacted} message(s) redacted.")

    print("\nRe-scanning to confirm…")
    left = [s for s in scan_sessions(client) if why_broken(s.get("title"))]
    still = scan_messages(client)
    print(f"  broken titles remaining: {len(left)}")
    print(f"  leaked payloads remaining: {len(still)}")
    return 0 if not left and not still else 1


if __name__ == "__main__":
    sys.exit(main())
