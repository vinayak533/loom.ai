"""Does each account's balance still agree with its own history?

    .venv/Scripts/python -m scripts.reconcile_credits            # report
    .venv/Scripts/python -m scripts.reconcile_credits --repair   # and fix

Why this exists
---------------
A balance is meant to be the sum of its ledger — that is the invariant the
whole module is built around — but the two live in different rows and are
written by different round trips, so they can drift. Four ways:

  * a crash between the ledger append and the balance update. The append now
    goes first precisely so that this leaves a movement with no balance, which
    can be finished, rather than a balance with no movement, which cannot be
    explained;
  * a degraded window whose buffered movements were replayed while some were
    already applied, or not replayed at all because the process died with a
    spill file on disk;
  * the first-sight account creation race, which wrote one "Opening balance"
    row per concurrent caller. Here it is the *ledger* that is wrong, and
    `--repair` refuses those accounts rather than crediting them for grants
    they never received;
  * a manual correction made against one and not the other.

This reads both, per account, and reports `spent - Σ debits` and
`balance - (granted - spent)`. With `--repair` it rewrites the balance columns
from the ledger — recomputed, never patched by a delta, so running it twice
changes nothing the first run did not.

Reading is always safe. `--repair` writes, prints exactly what it changed, and
names anything it deliberately did not touch.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.db.supabase_client import enabled as supabase_enabled, get_client  # noqa: E402

#: Rounding noise, not drift. Every amount is stored to 4 decimal places and
#: summed in float, so an account with thousands of movements can be a
#: ten-thousandth out without anything being wrong.
TOLERANCE = 0.005

#: One page of ledger rows. PostgREST caps a response, so a busy account needs
#: paging rather than one unbounded select.
PAGE = 1000


def _fetch_all(client, table: str, columns: str) -> list[dict]:
    """Every row of ``table``, paged, ordered so the pages do not overlap."""
    rows: list[dict] = []
    start = 0
    while True:
        res = (
            client.table(table)
            .select(columns)
            .order("created_at", desc=False)
            .range(start, start + PAGE - 1)
            .execute()
        )
        page = getattr(res, "data", None) or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        start += PAGE


def main() -> int:
    repair = "--repair" in sys.argv

    if not supabase_enabled():
        print(
            "Supabase is not configured, so there is no durable ledger to "
            "reconcile. Set SUPABASE_URL and SUPABASE_SERVICE_KEY in "
            "backend/.env."
        )
        return 1

    client = get_client()
    accounts = _fetch_all(client, "user_credits", "user_id,balance,granted,spent")

    # `balance_applied` arrives with `schema.sql`; a database that has not had
    # `scripts/apply_schema.py` run against it does not have it. The sums are
    # the substance of this report and they do not need the column, so its
    # absence costs one section of detail rather than the whole run.
    columns = "id,user_id,amount,kind,reason,balance_applied,created_at"
    has_applied = True
    try:
        ledger = _fetch_all(client, "credit_ledger", columns)
    except Exception as exc:  # noqa: BLE001
        if "balance_applied" not in str(exc):
            raise
        has_applied = False
        print(
            "Note: `credit_ledger.balance_applied` is missing from this "
            "database, so half-written movements cannot be identified "
            "individually — only the sums below. Run "
            "`python -m scripts.apply_schema` to add it.\n"
        )
        ledger = _fetch_all(
            client, "credit_ledger", "id,user_id,amount,kind,reason,created_at"
        )

    print(f"{len(accounts)} account(s), {len(ledger)} ledger row(s).\n")

    debits: dict[str, float] = defaultdict(float)
    grants: dict[str, float] = defaultdict(float)
    unapplied: dict[str, list[dict]] = defaultdict(list)
    openings: dict[str, list[dict]] = defaultdict(list)
    orphaned: list[dict] = []
    known = {a["user_id"] for a in accounts}

    for row in ledger:
        user = row.get("user_id") or ""
        amount = float(row.get("amount") or 0)
        if user not in known:
            orphaned.append(row)
        if amount < 0:
            debits[user] += -amount
        else:
            grants[user] += amount
        # An account is opened once. More than one opening row is the
        # first-sight creation race (CR-1) — concurrent callers each inserting
        # their own grant — and it means the *ledger* is the wrong side of any
        # drift it causes, which `--repair` has to know about before it
        # rewrites a balance from it.
        if row.get("kind") == "grant" and row.get("reason") == "Opening balance":
            openings[user].append(row)
        # `false` means the balance had not moved when the row was written. It
        # is normally flipped a moment later; one that is still false is either
        # a crash between the two writes or a mark that failed, and either way
        # it is where to look first.
        if row.get("balance_applied") is False:
            unapplied[user].append(row)

    problems = 0
    repaired = 0
    skipped = 0

    header = f"{'account':<38} {'spent':>12} {'Σ debits':>12} {'drift':>10}"
    print(header)
    print("-" * len(header))

    for account in sorted(accounts, key=lambda a: a.get("user_id") or ""):
        user = account["user_id"]
        spent = float(account.get("spent") or 0)
        granted = float(account.get("granted") or 0)
        balance = float(account.get("balance") or 0)

        spent_drift = spent - debits[user]
        granted_drift = granted - grants[user]
        # The balance is the one the user sees, and it is the sum of everything
        # rather than a third independent number.
        balance_drift = balance - (grants[user] - debits[user])

        duplicated = len(openings[user]) > 1

        flags = []
        if abs(spent_drift) > TOLERANCE:
            flags.append(f"spent off by {spent_drift:+.4f}")
        if abs(granted_drift) > TOLERANCE:
            flags.append(f"granted off by {granted_drift:+.4f}")
        if abs(balance_drift) > TOLERANCE:
            flags.append(f"balance off by {balance_drift:+.4f}")
        if unapplied[user]:
            flags.append(f"{len(unapplied[user])} row(s) not marked applied")
        if duplicated:
            flags.append(
                f"{len(openings[user])} 'Opening balance' rows — the account "
                "creation race wrote one per concurrent caller"
            )

        marker = "  " if not flags else "! "
        print(
            f"{marker}{user:<36} {spent:>12.4f} {debits[user]:>12.4f} "
            f"{spent_drift:>+10.4f}"
        )
        for flag in flags:
            print(f"      {flag}")
        for row in unapplied[user][:5]:
            print(
                f"      unapplied: {row.get('created_at')} "
                f"{float(row.get('amount') or 0):+.4f} {row.get('reason')!r}"
            )
        if len(unapplied[user]) > 5:
            print(f"      ... and {len(unapplied[user]) - 5} more")

        for row in openings[user][1:]:
            print(f"      duplicate opening: {row.get('created_at')} {row['id']}")

        if not flags:
            continue
        problems += 1

        if repair and duplicated:
            # Refused, not skipped quietly. `--repair` rewrites the balance
            # from the ledger, and here the ledger is the corrupted side: two
            # of these opening grants never should have existed, so recomputing
            # would hand this account the duplicates as real credit. Deleting
            # them is a judgement call about someone's balance and is not one
            # this script makes on its own.
            skipped += 1
            print(
                "      NOT repaired: the duplicate opening rows above have to "
                "be resolved first, or repair would credit this account for "
                "grants it never received. Decide which single opening row "
                "stands, delete the others, then re-run."
            )
        elif repair:
            # Recomputed from the ledger, never patched by a delta: recomputing
            # is idempotent, so running this twice is a no-op, whereas applying
            # a difference twice doubles it.
            patch = {
                "granted": round(grants[user], 4),
                "spent": round(debits[user], 4),
                "balance": round(grants[user] - debits[user], 4),
            }
            client.table("user_credits").update(patch).eq("user_id", user).execute()
            if unapplied[user] and has_applied:
                client.table("credit_ledger").update(
                    {"balance_applied": True}
                ).in_("id", [r["id"] for r in unapplied[user]]).execute()
            repaired += 1
            print(
                f"      repaired -> balance {patch['balance']}, "
                f"granted {patch['granted']}, spent {patch['spent']}"
            )

    if orphaned:
        print(
            f"\n{len(orphaned)} ledger row(s) belong to an account with no "
            "`user_credits` row. Those movements are real and are not counted "
            "by any balance:"
        )
        for row in orphaned[:10]:
            print(
                f"  {row.get('user_id')} {row.get('created_at')} "
                f"{float(row.get('amount') or 0):+.4f} {row.get('reason')!r}"
            )
        if len(orphaned) > 10:
            print(f"  ... and {len(orphaned) - 10} more")

    print()
    if not problems:
        print("Every account agrees with its ledger.")
    elif repair:
        print(
            f"{problems} account(s) had drifted; {repaired} repaired from the "
            f"ledger, {skipped} left alone pending a decision."
        )
    else:
        print(
            f"{problems} account(s) have drifted. Re-run with --repair to "
            "rewrite their balances from the ledger."
        )
    # A drift that is merely reported is still a finding, and one that --repair
    # deliberately declined to touch is still outstanding, so a monitoring
    # caller can key off the exit code.
    return 0 if not problems or (repair and not skipped) else 2


if __name__ == "__main__":
    raise SystemExit(main())
