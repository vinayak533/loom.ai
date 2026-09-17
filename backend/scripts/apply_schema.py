"""Apply `schema.sql` to the live database, then prove it landed.

Supabase's REST surface cannot run DDL — PostgREST speaks tables and RPCs, not
`create table` — so this needs a real Postgres connection. Set one in
`backend/.env`:

    SUPABASE_DB_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres

(Supabase dashboard → Project Settings → Database → Connection string → URI.
`POSTGRES_CHECKPOINT_URL` is used as a fallback, since it points at the same
database.) Then:

    .venv/Scripts/python -m scripts.apply_schema          # apply, then verify
    .venv/Scripts/python -m scripts.apply_schema --check  # verify only

`schema.sql` is idempotent by construction — every statement is
`create ... if not exists` or `alter table ... add column if not exists` — so
applying it twice is a no-op, and applying it to a database that has drifted
brings only the missing pieces.

The verification half exists because "the script ran" is not the same claim as
"the columns are there": this reads the live catalogue back and names anything
still missing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

SCHEMA = Path(__file__).resolve().parents[1] / "schema.sql"

#: What must exist afterwards, as (table, column) — `None` for "the table
#: itself". Kept here rather than parsed out of the SQL so that a statement
#: silently dropped from `schema.sql` shows up as a failure rather than as a
#: shorter checklist.
EXPECTED: list[tuple[str, str | None]] = [
    ("sessions", None),
    ("sessions", "is_pinned"),
    ("sessions", "is_archived"),
    ("sessions", "pinned_at"),
    ("sessions", "description"),
    ("sessions", "user_id"),
    ("messages", None),
    ("files", None),
    ("token_usage", None),
    ("token_usage", "routing_mode"),
    ("notebooks", None),
    ("notebook_sources", None),
    ("notebook_chunks", None),
    ("notebook_notes", None),
    ("notebook_lessons", None),
    ("notebook_progress", None),
    ("course_progress", None),
    ("course_progress", "user_id"),
    ("course_exam_attempts", None),
    ("course_exam_attempts", "user_id"),
    # The Agentic Loop. `sessions.agent_id` is what keeps the ten specialists'
    # conversations out of the Chat and Code history lists, and the credit
    # tables are what the meter needs to persist a balance at all — without
    # them every turn re-reads the opening grant, which is a silently free
    # product rather than a metered one.
    ("sessions", "agent_id"),
    ("user_credits", None),
    ("user_credits", "balance"),
    ("credit_ledger", None),
    ("credit_ledger", "agent_id"),
    ("credit_ledger", "balance_applied"),
    ("agent_approvals", None),
    ("agent_approvals", "decision"),
    # Message actions. `message_branches` is what makes an edit non-destructive
    # — without it, editing an earlier message overwrites the replies that
    # followed it and the "1/2" switcher has nothing to switch to.
    ("message_branches", None),
    ("message_branches", "turn_index"),
    ("message_branches", "version"),
    ("message_branches", "is_active"),
    ("message_feedback", None),
    ("message_feedback", "rating"),
    # Per-account preferences. The default model has to follow the account
    # rather than the browser, which is the whole reason this table exists.
    ("user_preferences", None),
    ("user_preferences", "default_model_id"),
    # Projects and memory. `sessions.project_id` is what makes a project a
    # container rather than a label — without it a project can hold
    # instructions but nothing to apply them to. The two preference columns
    # are the custom-instruction boxes; `user_memories` is what the account
    # has learned.
    ("projects", None),
    ("projects", "instructions"),
    ("project_files", None),
    ("project_files", "content"),
    ("sessions", "project_id"),
    ("user_memories", None),
    ("user_memories", "content"),
    ("user_preferences", "about_you"),
    ("user_preferences", "response_style"),
    ("user_preferences", "memory_enabled"),
    # Artifacts. `artifact_key` + `version` is the identity that makes an edit
    # add a row rather than overwrite one, which is what stops a user tidying
    # up a draft from destroying what the model actually wrote.
    ("artifacts", None),
    ("artifacts", "artifact_key"),
    ("artifacts", "version"),
    ("artifacts", "created_by"),
]


def _dsn() -> dict | str | None:
    """The connection, from discrete parts or a URI — in that order.

    Discrete parts first because a Postgres password is allowed to contain
    `@`, `+` and `=`, all of which have to be percent-encoded inside a URI and
    silently produce an authentication failure when they are not. Passing them
    as keywords sidesteps the encoding entirely.
    """
    host = os.environ.get("SUPABASE_DB_HOST", "").strip()
    password = os.environ.get("SUPABASE_DB_PASSWORD", "").strip()
    if host and password:
        return {
            "host": host,
            "port": int(os.environ.get("SUPABASE_DB_PORT", "5432")),
            "dbname": os.environ.get("SUPABASE_DB_NAME", "postgres"),
            "user": os.environ.get("SUPABASE_DB_USER", "postgres"),
            "password": password,
            "sslmode": os.environ.get("SUPABASE_DB_SSLMODE", "require"),
        }
    url = (
        os.environ.get("SUPABASE_DB_URL")
        or os.environ.get("POSTGRES_CHECKPOINT_URL")
        or ""
    ).strip()
    return url or None


def connect():
    dsn = _dsn()
    if not dsn:
        sys.exit(
            "No database connection configured. Set SUPABASE_DB_HOST + "
            "SUPABASE_DB_PASSWORD (or SUPABASE_DB_URL) in backend/.env — "
            "Supabase dashboard -> Project Settings -> Database. Nothing else "
            "here can run DDL."
        )
    try:
        import psycopg2  # noqa: PLC0415
    except ModuleNotFoundError:
        sys.exit(
            "psycopg2 is not installed. Run:\n"
            "    .venv/Scripts/python -m pip install psycopg2-binary"
        )
    if isinstance(dsn, dict):
        return psycopg2.connect(connect_timeout=20, **dsn)
    return psycopg2.connect(dsn, connect_timeout=20)


def verify(conn) -> list[str]:
    """Return the list of missing tables/columns. Empty means we are current."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select table_name, column_name
            from information_schema.columns
            where table_schema = 'public'
            """
        )
        live: dict[str, set[str]] = {}
        for table, column in cur.fetchall():
            live.setdefault(table, set()).add(column)

    missing: list[str] = []
    for table, column in EXPECTED:
        if table not in live:
            missing.append(f"table `{table}`")
        elif column and column not in live[table]:
            missing.append(f"`{table}.{column}`")
    return missing


def main() -> int:
    check_only = "--check" in sys.argv
    conn = connect()
    conn.autocommit = True

    if not check_only:
        sql = SCHEMA.read_text(encoding="utf-8")
        print(f"Applying {SCHEMA.name} ({len(sql):,} bytes)…")
        # psycopg2 sends this over the simple query protocol, so the whole file
        # — dollar-quoted function bodies and all — goes in one round trip.
        with conn.cursor() as cur:
            cur.execute(sql)
        print("Applied.\n")

    missing = verify(conn)
    if missing:
        print(f"MISSING ({len(missing)}):")
        for item in missing:
            print(f"  - {item}")
        return 1

    print(f"Verified: all {len(EXPECTED)} expected tables/columns are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
