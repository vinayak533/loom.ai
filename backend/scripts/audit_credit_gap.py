"""Bound the credit meter's fail-open window. READ ONLY.

    python scripts/audit_credit_gap.py

Context
-------
The meter used to treat a failed read of `user_credits` as "no row yet" and
return the opening grant, so while the credit tables did not exist every turn
re-read a full balance and nothing was ever debited. The fix latches a degraded
flag instead (see `app/credits.py`). This script answers the question the fix
does not: how long was that window, and what ran inside it.

Method
------
`token_usage` is the independent witness. It has been written since long before
credits existed, by a different code path (`repository.record_usage`), and it
carries the real cost of every model call. So:

  window opens   the first model call after the credit meter shipped
  window closes  the first row in `credit_ledger` — the first movement the
                 durable store actually accepted

Every `token_usage` row in between is a call that really happened and really
cost money while the meter was returning the opening grant.

Nothing is written. Nobody is charged. The point is to turn an open question
into a bounded number.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db.supabase_client import enabled as supabase_enabled, get_client  # noqa: E402


def parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def rows(client, table: str, columns: str = "*", order: str = "created_at") -> list[dict]:
    """Page through a table. Supabase caps a single select at 1000 rows."""
    out: list[dict] = []
    page = 0
    while True:
        res = (
            client.table(table)
            .select(columns)
            .order(order)
            .range(page * 1000, page * 1000 + 999)
            .execute()
        )
        batch = getattr(res, "data", None) or []
        out.extend(batch)
        if len(batch) < 1000:
            return out
        page += 1


def main() -> int:
    settings = get_settings()
    if not supabase_enabled():
        print("Supabase is not configured — the durable tables are not reachable.")
        return 1

    client = get_client()

    try:
        usage = rows(client, "token_usage")
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read token_usage: {exc}")
        return 1

    try:
        ledger = rows(client, "credit_ledger")
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read credit_ledger: {exc}")
        print("If this says the relation does not exist, the window is still open.")
        return 1

    try:
        credits_rows = rows(client, "user_credits", order="created_at")
    except Exception:  # noqa: BLE001
        credits_rows = []

    print("=" * 74)
    print("CREDIT METER FAIL-OPEN WINDOW — blast radius")
    print("=" * 74)

    if not ledger:
        print("\ncredit_ledger is EMPTY. Nothing has ever been durably metered:")
        print(f"  {len(usage)} model calls in token_usage are all unmetered.")
        return 0

    first_ledger = parse(ledger[0].get("created_at"))
    print(f"\nLedger rows           {len(ledger)}")
    print(f"First ledger movement {first_ledger}  <- the window CLOSES here")
    print(f"  {ledger[0].get('kind')}: {ledger[0].get('amount')} — {ledger[0].get('reason')}")
    last_ledger = parse(ledger[-1].get("created_at"))
    print(f"Last ledger movement  {last_ledger}")

    for row in credits_rows:
        print(
            f"\nAccount `{row.get('user_id')}` created {row.get('created_at')}: "
            f"balance {row.get('balance')}, granted {row.get('granted')}, "
            f"spent {row.get('spent')}"
        )

    # --- who was ever in scope -------------------------------------------
    # Only the Agentic Loop section meters credits: `app/agent/graph.py`
    # (Chat and Code) records `token_usage` but never calls `charge_llm`, by
    # design. Counting its calls as "unmetered" would invent an exposure that
    # was never in scope, so the window is measured over agent sessions only —
    # `sessions.agent_id` is null for every Chat and Code row.
    try:
        sessions = rows(client, "sessions", "id,agent_id,created_at")
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read sessions: {exc}")
        return 1

    agent_sessions = {s["id"]: s.get("agent_id") for s in sessions if s.get("agent_id")}
    print(
        f"\nSessions: {len(sessions)} total, {len(agent_sessions)} belong to a "
        "specialist agent (the only ones the meter charges)."
    )

    in_scope = [u for u in usage if u.get("session_id") in agent_sessions]
    out_of_scope = len(usage) - len(in_scope)
    print(
        f"token_usage: {len(usage)} rows, {len(in_scope)} from agent sessions, "
        f"{out_of_scope} from Chat/Code (never metered, by design)."
    )

    # --- the window -------------------------------------------------------
    print("\n" + "-" * 74)
    print("Agent-session model calls BEFORE the first ledger movement")
    print("-" * 74)

    before = [
        u for u in in_scope if (parse(u.get("created_at")) or first_ledger) < first_ledger
    ]
    after = [
        u for u in in_scope if (parse(u.get("created_at")) or first_ledger) >= first_ledger
    ]

    if not before:
        print("\nNone. The very first model call was already metered — no gap.")
    else:
        opens = parse(before[0].get("created_at"))
        span = first_ledger - opens if opens else None
        cost = sum(float(u.get("cost_estimate") or 0) for u in before)
        tokens_in = sum(int(u.get("input_tokens") or 0) for u in before)
        tokens_out = sum(int(u.get("output_tokens") or 0) for u in before)
        credits = cost / max(settings.credit_usd, 1e-9)

        print(f"\nWindow opened  {opens}")
        print(f"Window closed  {first_ledger}")
        print(f"Duration       {span}")
        print(f"\nUnmetered model calls   {len(before)}")
        print(f"Tokens                  {tokens_in:,} in · {tokens_out:,} out")
        print(f"Real provider cost      ${cost:.4f}")
        print(f"Would have been         {credits:.1f} credits")

        by_day: dict[str, int] = defaultdict(int)
        by_model: dict[str, list] = defaultdict(lambda: [0, 0.0])
        by_agent: dict[str, list] = defaultdict(lambda: [0, 0.0])
        for u in before:
            when = parse(u.get("created_at"))
            by_day[when.date().isoformat() if when else "?"] += 1
            entry = by_model[u.get("model_id") or u.get("model") or "?"]
            entry[0] += 1
            entry[1] += float(u.get("cost_estimate") or 0)
            who = by_agent[agent_sessions.get(u.get("session_id")) or "?"]
            who[0] += 1
            who[1] += float(u.get("cost_estimate") or 0)

        print("\n  By day:")
        for day, count in sorted(by_day.items()):
            print(f"    {day}  {count} calls")
        print("\n  By model:")
        for model, (count, spend) in sorted(by_model.items(), key=lambda x: -x[1][1]):
            print(f"    {model:<28} {count:>4} calls  ${spend:.4f}")
        print("\n  By agent:")
        for who, (count, spend) in sorted(by_agent.items(), key=lambda x: -x[1][1]):
            print(f"    {who:<28} {count:>4} calls  ${spend:.4f}")

        print("\n  Every call in the window, in order:")
        for u in before:
            print(
                f"    {parse(u.get('created_at'))}  "
                f"{(agent_sessions.get(u.get('session_id')) or '?'):<24} "
                f"{(u.get('model_id') or '?'):<18} ${float(u.get('cost_estimate') or 0):.5f}"
            )

    print("\n" + "-" * 74)
    print("After the window (metered)")
    print("-" * 74)
    cost_after = sum(float(u.get("cost_estimate") or 0) for u in after)
    print(f"  {len(after)} model calls · ${cost_after:.4f} · "
          f"{cost_after / max(settings.credit_usd, 1e-9):.1f} credits' worth")

    debits = [row for row in ledger if float(row.get("amount") or 0) < 0]
    grants = [row for row in ledger if float(row.get("amount") or 0) > 0]
    debited = -sum(float(row.get("amount") or 0) for row in debits)
    print(f"  Ledger over the same period: {len(debits)} debits totalling "
          f"{debited:.1f} credits, {len(grants)} grants")

    llm_debits = -sum(
        float(row.get("amount") or 0) for row in debits if row.get("kind") == "llm"
    )
    tool_debits = -sum(
        float(row.get("amount") or 0) for row in debits if row.get("kind") == "tool"
    )
    print(f"    of which {llm_debits:.1f} credits LLM, {tool_debits:.1f} credits tool surcharge")

    # Tool surcharges have no token_usage counterpart, so this only reconciles
    # the LLM half — a mismatch here means the meter and the usage log disagree
    # about the same calls, which is a different bug from the one above.
    drift = llm_debits - (cost_after / max(settings.credit_usd, 1e-9))
    print(f"\n  LLM debits vs token_usage over the metered period: {drift:+.2f} credits")

    # --- per session ------------------------------------------------------
    # A whole-period total hides *which* turns went unmetered. The interesting
    # failure is a session that shows model calls in `token_usage` and no
    # matching `llm` debit: that is a turn the in-process fallback charged and
    # a restart then threw away.
    print("\n" + "-" * 74)
    print("Per agent session: recorded usage vs. what the ledger kept")
    print("-" * 74)
    print(f"  {'session':<38} {'agent':<24} {'calls':>5} {'usage cr':>9} "
          f"{'ledger cr':>10} {'gap':>9}")

    usage_by_session: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for u in in_scope:
        entry = usage_by_session[u.get("session_id")]
        entry[0] += 1
        entry[1] += float(u.get("cost_estimate") or 0)

    ledger_by_session: dict[str, float] = defaultdict(float)
    for entry in ledger:
        if entry.get("kind") == "llm":
            ledger_by_session[str(entry.get("session_id"))] += -float(entry.get("amount") or 0)

    unmetered_credits = 0.0
    unmetered_calls = 0
    for session_id, (calls, cost) in sorted(
        usage_by_session.items(), key=lambda x: -x[1][1]
    ):
        want = cost / max(settings.credit_usd, 1e-9)
        got = ledger_by_session.get(str(session_id), 0.0)
        gap = want - got
        if gap > 0.5:
            unmetered_credits += gap
            unmetered_calls += calls if got == 0 else 0
        flag = "  <- nothing kept" if got == 0 else ""
        print(
            f"  {str(session_id):<38} {(agent_sessions.get(session_id) or '?'):<24} "
            f"{calls:>5} {want:>9.1f} {got:>10.1f} {gap:>9.1f}{flag}"
        )

    print(f"\n  Usage recorded but not held in the ledger: {unmetered_credits:.1f} credits "
          f"(~${unmetered_credits * settings.credit_usd:.2f}) across {unmetered_calls} calls "
          "in sessions with no ledger entry at all.")
    print("  Sessions with a partial gap were metered while the store was "
          "degraded for part of the run.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
