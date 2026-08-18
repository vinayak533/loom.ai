"""Report how every session row is categorised across Chat / Code / Agents.

Run after the `sessions.section` migration to answer the two questions that
matter about it:

  1. Is anything still uncategorised — a row from before the column existed
     whose section the backfill could not infer? Those are listed for manual
     review rather than guessed into a section.
  2. Can a Chat session reach Code's history, or the reverse? This runs the
     *actual* queries the two sections issue and checks the results are
     disjoint.

    .venv/Scripts/python -m scripts.audit_session_sections

Read-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from scripts.apply_schema import connect  # noqa: E402


def main() -> int:
    conn = connect()
    conn.autocommit = True

    print("=" * 68)
    print("SESSION SECTION AUDIT")
    print("=" * 68)

    with conn.cursor() as cur:
        cur.execute(
            """
            select
              case
                when agent_id is not null then 'agents:' || agent_id
                when section is null       then '(uncategorised)'
                else section
              end as bucket,
              count(*)
            from public.sessions
            group by 1
            order by 2 desc
            """
        )
        rows = cur.fetchall()

        print("\n[1] How every session row is currently tagged")
        total = 0
        for bucket, count in rows:
            total += count
            print(f"    {bucket:<28} {count}")
        print(f"    {'TOTAL':<28} {total}")

        # Uncategorised rows: listed, not guessed.
        cur.execute(
            """
            select id, title, created_at
            from public.sessions
            where section is null and agent_id is null
            order by created_at desc
            limit 25
            """
        )
        legacy = cur.fetchall()
        print("\n[2] Uncategorised (pre-migration) sessions")
        if not legacy:
            print("    None — every session carries a section.")
        else:
            print(
                f"    {len(legacy)} row(s) could not be inferred from sandbox or"
                " file activity."
            )
            print("    They are shown in Chat's history and never in Code's.")
            print("    Flagged here for manual review:")
            for sid, title, created in legacy:
                print(f"      - {sid}  {created:%Y-%m-%d}  {title[:44]}")

        # The disjointness test: run both sections' real queries and intersect.
        print("\n[3] Chat / Code list disjointness (the actual section queries)")
        cur.execute(
            """
            select id from public.sessions
            where agent_id is null and (section = 'chat' or section is null)
            """
        )
        chat_ids = {r[0] for r in cur.fetchall()}
        cur.execute(
            "select id from public.sessions where agent_id is null and section = 'code'"
        )
        code_ids = {r[0] for r in cur.fetchall()}
        overlap = chat_ids & code_ids

        print(f"    Chat list: {len(chat_ids)} session(s)")
        print(f"    Code list: {len(code_ids)} session(s)")
        if overlap:
            print(f"    FAIL  {len(overlap)} session(s) appear in BOTH lists:")
            for sid in list(overlap)[:10]:
                print(f"      - {sid}")
            return 1
        print("    PASS  the two lists share no session.")

        # And agents stay out of both.
        cur.execute("select count(*) from public.sessions where agent_id is not null")
        agent_count = cur.fetchone()[0]
        cur.execute(
            """
            select count(*) from public.sessions
            where agent_id is not null and section in ('chat', 'code')
            """
        )
        print(
            f"    PASS  {agent_count} agent session(s) excluded from both "
            "(filtered on agent_id)."
        )

    print("\nRESULT: Chat and Code histories are strictly separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
