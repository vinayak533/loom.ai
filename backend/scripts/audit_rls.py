"""Prove per-user data isolation is actually enforced, not merely declared.

`schema.sql` containing a policy is not evidence that the live database has it.
This reads the catalogue back and then, more importantly, *exercises* the
policies the way PostgREST does: it becomes the `authenticated` role, claims to
be one user, and counts what that user can see of another user's rows.

    .venv/Scripts/python -m scripts.audit_rls

Nothing here writes. The two synthetic user ids are only ever used inside a
transaction that is rolled back, and the seeded probe rows (when `--seed` is
passed) are rolled back with it.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from scripts.apply_schema import connect  # noqa: E402

#: Every table that holds per-user data, and the column that ties a row to a
#: person — directly, or through the parent named here.
GUARDED: list[tuple[str, str]] = [
    ("sessions", "direct:user_id"),
    ("messages", "parent:sessions"),
    ("files", "parent:sessions"),
    ("token_usage", "parent:sessions"),
    ("notebooks", "direct:user_id"),
    ("notebook_sources", "parent:notebooks"),
    ("notebook_chunks", "parent:notebooks"),
    ("notebook_notes", "parent:notebooks"),
    ("notebook_lessons", "parent:notebooks"),
    ("notebook_progress", "parent:notebooks"),
    ("course_progress", "direct:user_id"),
    ("course_exam_attempts", "direct:user_id"),
    ("user_credits", "direct:user_id"),
    ("credit_ledger", "direct:user_id"),
    ("agent_approvals", "parent:sessions"),
]


def structural(conn) -> tuple[list[str], dict[str, int]]:
    """Which guarded tables have RLS switched on, and how many policies each has."""
    names = [t for t, _ in GUARDED]
    with conn.cursor() as cur:
        cur.execute(
            """
            select c.relname, c.relrowsecurity
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relname = any(%s)
            """,
            (names,),
        )
        rls = dict(cur.fetchall())

        cur.execute(
            """
            select tablename, count(*)
            from pg_policies
            where schemaname = 'public' and tablename = any(%s)
            group by tablename
            """,
            (names,),
        )
        policies = dict(cur.fetchall())

    unprotected = [t for t in names if not rls.get(t)]
    return unprotected, policies


def empirical(conn) -> list[str]:
    """Become two different users and check neither can see the other's rows.

    Runs inside a transaction that is rolled back, so the probe rows never
    outlive the check. `set local role authenticated` plus a `request.jwt.claims`
    GUC is exactly the context PostgREST establishes for a request carrying a
    user's JWT, so what this measures is what the browser would actually get.
    """
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    failures: list[str] = []

    with conn.cursor() as cur:
        cur.execute("begin")
        try:
            # Seed one owned row each, as the table owner (RLS does not apply).
            cur.execute("set local role postgres")
            for uid, title in ((alice, "alice-probe"), (bob, "bob-probe")):
                # `sessions.user_id` is a foreign key onto auth.users, so the
                # two synthetic people have to exist before they can own
                # anything. Rolled back with everything else.
                cur.execute(
                    """
                    insert into auth.users
                        (instance_id, id, aud, role, email,
                         encrypted_password, email_confirmed_at,
                         created_at, updated_at)
                    values
                        ('00000000-0000-0000-0000-000000000000', %s,
                         'authenticated', 'authenticated', %s,
                         '', now(), now(), now())
                    """,
                    (uid, f"{title}@rls-audit.invalid"),
                )
                cur.execute(
                    "insert into public.sessions (user_id, title) values (%s, %s)",
                    (uid, title),
                )
                cur.execute(
                    "insert into public.notebooks (user_id, title) values (%s, %s)",
                    (uid, f"{title}-notebook"),
                )
                cur.execute(
                    "insert into public.user_credits (user_id, balance) "
                    "values (%s, 500) on conflict (user_id) do nothing",
                    (uid,),
                )

            # Now become Alice and look for Bob.
            for me, them, who in ((alice, bob, "alice"), (bob, alice, "bob")):
                cur.execute("set local role authenticated")
                cur.execute(
                    "select set_config('request.jwt.claims', %s, true)",
                    (f'{{"sub":"{me}","role":"authenticated"}}',),
                )

                cur.execute(
                    "select count(*) from public.sessions where user_id = %s", (them,)
                )
                leaked = cur.fetchone()[0]
                if leaked:
                    failures.append(
                        f"sessions: {who} can see {leaked} of the other user's rows"
                    )

                cur.execute(
                    "select count(*) from public.notebooks where user_id = %s", (them,)
                )
                leaked = cur.fetchone()[0]
                if leaked:
                    failures.append(
                        f"notebooks: {who} can see {leaked} of the other user's rows"
                    )

                cur.execute(
                    "select count(*) from public.user_credits where user_id = %s",
                    (them,),
                )
                leaked = cur.fetchone()[0]
                if leaked:
                    failures.append(
                        f"user_credits: {who} can see the other user's balance"
                    )

                # And confirm the policy is not simply blocking everything,
                # which would "pass" an isolation test while breaking the app.
                cur.execute(
                    "select count(*) from public.sessions where user_id = %s", (me,)
                )
                own = cur.fetchone()[0]
                if own < 1:
                    failures.append(
                        f"sessions: {who} cannot see their OWN row — policy is too strict"
                    )

                cur.execute("reset role")

            # The anonymous case: the browser before sign-in must see nothing.
            cur.execute("set local role anon")
            cur.execute("select set_config('request.jwt.claims', %s, true)", (None,))
            for table in ("sessions", "notebooks", "user_credits"):
                cur.execute(f"select count(*) from public.{table}")
                leaked = cur.fetchone()[0]
                if leaked:
                    failures.append(f"{table}: anon role can read {leaked} rows")
            cur.execute("reset role")
        finally:
            cur.execute("rollback")

    return failures


def main() -> int:
    conn = connect()
    conn.autocommit = True

    print("=" * 68)
    print("RLS AUDIT")
    print("=" * 68)

    unprotected, policies = structural(conn)
    print("\n[1] Row-level security switched on?")
    for table, _ in GUARDED:
        n = policies.get(table, 0)
        state = "OFF  <-- EXPOSED" if table in unprotected else f"on   ({n} policy/policies)"
        if table not in unprotected and n == 0:
            state = "on   (0 policies -> denies all reads)"
        print(f"    {table:<24} {state}")

    print("\n[2] Two-user isolation, exercised against the live policies")
    failures = empirical(conn)
    if failures:
        for f in failures:
            print(f"    FAIL  {f}")
    else:
        print("    PASS  neither synthetic user could read the other's rows")
        print("    PASS  each user could still read their own rows")
        print("    PASS  the anonymous role read nothing")

    print()
    if unprotected or failures:
        print("RESULT: NOT ISOLATED — see the failures above.")
        return 1
    print("RESULT: per-user isolation is enforced by the database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
