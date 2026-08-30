"""Credit meter degradation and recovery, proven against a fake store.

    python scripts/test_credit_degradation.py

The real Supabase is never touched. A stand-in client holds two dicts and can
be told to start failing and to start working again, which is the only way to
exercise the interesting part: what happens to a charge that lands while the
store is down.

This is the regression test for a real incident. The meter used to latch a
boolean on the first error and never clear it, so one transient failure moved
every later charge into memory for the life of the process — and a restart
threw that away. 20 calls and ~910 credits of real usage were lost in an
11m42s window that way. Every scenario below is a way that could happen again.

Scenarios
---------
1. healthy            charges go straight to the durable store
2. outage             charges keep being taken, buffered in memory and on disk
3. no free ride       the balance still moves while degraded
4. recovery           the store comes back; the buffer lands in the ledger,
                      once, with the balance moved by exactly the right amount
5. restart mid-outage a new process replays the spill file and flushes it
6. half-written       balance written but ledger row lost — the flush must not
                      move the balance a second time
7. seeding            a degraded account does not get a fresh opening grant
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import credits  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


# ---------------------------------------------------------------------------
# A Supabase stand-in with a switch
# ---------------------------------------------------------------------------


class Down(Exception):
    """What the fake store raises while it is 'unreachable'."""


class FakeTable:
    def __init__(self, store: "FakeStore", name: str) -> None:
        self.store, self.name = store, name
        self._filters: dict[str, object] = {}
        self._payload = None
        self._op = None

    # -- query building (only the shapes credits.py actually uses) ---------
    def select(self, *_a, **_kw):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def upsert(self, payload):
        self._op, self._payload = "upsert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def limit(self, *_a):
        return self

    def order(self, *_a, **_kw):
        return self

    def range(self, *_a):
        return self

    # -- execution ---------------------------------------------------------
    def execute(self):
        if self.store.down:
            self.store.refused += 1
            raise Down("the fake store is down")
        self.store.calls += 1
        rows = self.store.data[self.name]

        if self._op == "select":
            out = [
                r for r in rows.values()
                if all(r.get(k) == v for k, v in self._filters.items())
            ]
            return type("Res", (), {"data": out})()

        payloads = self._payload if isinstance(self._payload, list) else [self._payload]

        if self._op in {"insert", "upsert"}:
            for row in payloads:
                key = row.get("user_id") if self.name == "user_credits" else row.get("id")
                if self._op == "insert" and key in rows:
                    raise Down(f"duplicate key {key}")
                rows[key] = dict(row)
            return type("Res", (), {"data": payloads})()

        if self._op == "update":
            touched = []
            for key, row in rows.items():
                if all(row.get(k) == v for k, v in self._filters.items()):
                    row.update(payloads[0])
                    touched.append(row)
            return type("Res", (), {"data": touched})()

        raise AssertionError(f"unsupported op {self._op}")


class FakeStore:
    def __init__(self) -> None:
        self.data = {"user_credits": {}, "credit_ledger": {}}
        self.down = False
        self.calls = 0
        self.refused = 0

    def table(self, name: str) -> FakeTable:
        return FakeTable(self, name)

    # -- introspection for assertions --------------------------------------
    def balance(self, account="anonymous") -> float:
        row = self.data["user_credits"].get(account)
        return float(row["balance"]) if row else 0.0

    def spent(self, account="anonymous") -> float:
        row = self.data["user_credits"].get(account)
        return float(row["spent"]) if row else 0.0

    def ledger(self) -> list[dict]:
        return sorted(
            self.data["credit_ledger"].values(), key=lambda r: r["created_at"]
        )

    def debits(self) -> float:
        return -sum(float(r["amount"]) for r in self.ledger() if float(r["amount"]) < 0)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def reset(store: FakeStore, spill: str) -> None:
    """Put the module back to a clean boot with the fake store attached."""
    credits.get_client = lambda: store
    credits.supabase_enabled = lambda: True
    credits._DEGRADED_SINCE = None
    credits._DEGRADED_REASON = ""
    credits._NEXT_PROBE = 0.0
    credits._PROBE_FAILURES = 0
    credits._PENDING.clear()
    credits._LOCAL.clear()
    credits._LAST_KNOWN.clear()
    credits.get_settings.cache_clear()
    os.environ["CREDIT_SPILL_PATH"] = spill
    os.environ["CREDIT_STORE_RETRY_SECONDS"] = "0"
    os.environ["CREDIT_STORE_RETRY_MAX_SECONDS"] = "0"


async def charge(amount_usd: float, reason: str = "test call") -> float:
    """One LLM debit, in the same shape the graph issues them."""
    return await credits.charge_llm(
        None, session_id="s1", agent_id="research_fact_checker",
        model_id="qwen3_7_plus", cost_usd=amount_usd,
    )


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="credit-degrade-")
    spill = os.path.join(tmp, "credit_spill.jsonl")
    store = FakeStore()
    reset(store, spill)
    settings = credits.get_settings()
    opening = settings.credit_starting_balance

    ok = True

    # --- 1. healthy -------------------------------------------------------
    print("\n1. Healthy store")
    await credits.get_balance(None)          # opens the account
    await charge(0.010)                      # 10 credits
    await charge(0.005)                      # 5 credits
    ok &= check(
        "charges reach the durable ledger",
        len(store.ledger()) == 3,            # opening grant + 2 debits
        f"{len(store.ledger())} ledger rows",
    )
    ok &= check(
        "balance is correct",
        abs(store.balance() - (opening - 15)) < 0.001,
        f"{store.balance()} (expected {opening - 15})",
    )
    ok &= check("nothing is buffered", not credits._PENDING)
    ok &= check("no spill file while healthy", not os.path.exists(spill))

    # --- 2. outage --------------------------------------------------------
    print("\n2. The store goes down mid-session")
    store.down = True
    ledger_at_outage = len(store.ledger())
    balance_at_outage = store.balance()

    await charge(0.020)                      # 20 credits
    await charge(0.030)                      # 30 credits
    await charge(0.007)                      # 7 credits

    ok &= check("the meter reports itself degraded", credits.degraded())
    ok &= check(
        "charges were buffered, not dropped",
        len(credits._PENDING) == 3,
        f"{len(credits._PENDING)} buffered",
    )
    ok &= check(
        "the buffer holds the right amount",
        abs(sum(abs(float(r["amount"])) for r in credits._PENDING) - 57) < 0.001,
        f"{sum(abs(float(r['amount'])) for r in credits._PENDING)} credits",
    )
    ok &= check(
        "the spill file exists on disk",
        os.path.exists(spill),
        f"{sum(1 for _ in open(spill, encoding='utf-8'))} lines",
    )
    ok &= check(
        "the durable store was untouched during the outage",
        len(store.ledger()) == ledger_at_outage and store.balance() == balance_at_outage,
    )

    status = credits.store_status()
    ok &= check(
        "the status endpoint says degraded, with the backlog",
        status["durable"] is False and status["buffered_movements"] == 3,
        f"buffered_credits={status.get('buffered_credits')}",
    )

    # --- 3. still charging ------------------------------------------------
    print("\n3. Degraded is not free")
    live = await credits.get_balance(None)
    ok &= check(
        "the in-process balance moved by the degraded charges",
        abs(live.balance - (balance_at_outage - 57)) < 0.001,
        f"{live.balance} (expected {balance_at_outage - 57})",
    )

    # --- 4. recovery ------------------------------------------------------
    print("\n4. The store comes back")
    store.down = False
    credits._NEXT_PROBE = 0.0                # the retry deadline has passed
    await charge(0.001)                      # ordinary traffic triggers recovery

    ok &= check("the meter is durable again", not credits.degraded())
    ok &= check("the buffer is empty", not credits._PENDING, f"{len(credits._PENDING)} left")
    ok &= check("the spill file is gone", not os.path.exists(spill))
    ok &= check(
        "every buffered movement reached the ledger",
        len(store.ledger()) == ledger_at_outage + 4,
        f"{len(store.ledger())} rows, expected {ledger_at_outage + 4}",
    )
    expected = opening - 15 - 57 - 1
    ok &= check(
        "the balance matches what was actually spent",
        abs(store.balance() - expected) < 0.001,
        f"{store.balance()} (expected {expected})",
    )
    ok &= check(
        "the ledger and the balance agree",
        abs((opening - store.debits()) - store.balance()) < 0.001,
        f"opening {opening} − debits {store.debits():.1f} = {opening - store.debits():.1f} "
        f"vs balance {store.balance():.1f}",
    )

    # --- 5. restart mid-outage --------------------------------------------
    print("\n5. A restart in the middle of an outage")
    store2 = FakeStore()
    spill2 = os.path.join(tmp, "spill2.jsonl")
    reset(store2, spill2)
    await credits.get_balance(None)
    store2.down = True
    await charge(0.040)                      # 40 credits, buffered
    await charge(0.020)                      # 20 credits, buffered
    ok &= check("two movements are buffered", len(credits._PENDING) == 2)

    # The process dies here. Everything in memory is gone; the file is not.
    buffered_before = list(credits._PENDING)
    credits._PENDING.clear()
    credits._LOCAL.clear()
    credits._DEGRADED_SINCE = None
    ok &= check(
        "the spill file survived the 'restart'",
        os.path.exists(spill2),
        f"{len(buffered_before)} movements on disk",
    )

    recovered = credits.load_spill()
    ok &= check(
        "startup replays them",
        recovered == 2 and len(credits._PENDING) == 2,
        f"load_spill() returned {recovered}",
    )
    ok &= check(
        "and starts degraded so they get flushed",
        credits.degraded(),
    )

    store2.down = False
    credits._NEXT_PROBE = 0.0
    await credits.get_balance(None)          # any traffic drives the recovery

    ok &= check(
        "the recovered movements land in the durable ledger",
        len(credits._PENDING) == 0 and store2.debits() == 60,
        f"debits now {store2.debits()}",
    )
    ok &= check(
        "the balance reflects them exactly once",
        abs(store2.balance() - (opening - 60)) < 0.001,
        f"{store2.balance()} (expected {opening - 60})",
    )

    # --- 6. the half-written movement -------------------------------------
    print("\n6. Balance written, ledger row lost")
    store3 = FakeStore()
    spill3 = os.path.join(tmp, "spill3.jsonl")
    reset(store3, spill3)
    await credits.get_balance(None)

    # Fail only the ledger insert, which is the second half of a debit.
    real_table = store3.table

    def selective(name: str):
        if name == "credit_ledger":
            raise Down("ledger is unreachable")
        return real_table(name)

    store3.table = selective
    await charge(0.025)                      # 25 credits: balance moves, row does not
    store3.table = real_table

    ok &= check(
        "the balance already moved durably",
        abs(store3.balance() - (opening - 25)) < 0.001,
        f"{store3.balance()}",
    )
    ok &= check(
        "the orphaned row is buffered, marked as already applied",
        len(credits._PENDING) == 1 and credits._PENDING[0]["_applied"] is True,
    )

    credits._NEXT_PROBE = 0.0
    await credits.get_balance(None)          # recover and flush

    ok &= check(
        "the flush writes the missing ledger row",
        store3.debits() == 25,
        f"debits {store3.debits()}",
    )
    ok &= check(
        "and does NOT charge the balance twice",
        abs(store3.balance() - (opening - 25)) < 0.001,
        f"{store3.balance()} (expected {opening - 25}, double-charge would be "
        f"{opening - 50})",
    )

    # --- 7. seeding -------------------------------------------------------
    print("\n7. A degraded account does not get a fresh grant")
    store4 = FakeStore()
    spill4 = os.path.join(tmp, "spill4.jsonl")
    reset(store4, spill4)
    await credits.get_balance(None)
    await charge(0.900)                      # burn it down to 100
    near_empty = store4.balance()
    store4.down = True
    credits._LOCAL.clear()                   # nothing cached; only _LAST_KNOWN
    degraded_view = await credits.get_balance(None)
    ok &= check(
        "the in-memory balance starts from the real one, not the opening grant",
        abs(degraded_view.balance - near_empty) < 0.001,
        f"{degraded_view.balance} (real {near_empty}, opening grant would be {opening})",
    )

    # --- 8. recovery under concurrent traffic -----------------------------
    # The graph runs read-only tools in parallel, so several charges really do
    # land at once. Recovery is a read-modify-write on the same balance row
    # they are writing, so it has to take the same lock they do — and resolving
    # durability while already holding that lock deadlocks the whole meter.
    # This is the shape that catches both mistakes.
    print("\n8. Recovery while charges are landing concurrently")
    store5 = FakeStore()
    spill5 = os.path.join(tmp, "spill5.jsonl")
    reset(store5, spill5)
    await credits.get_balance(None)

    store5.down = True
    await asyncio.gather(*(charge(0.010) for _ in range(6)))   # 60 buffered
    ok &= check("six concurrent charges all buffered", len(credits._PENDING) == 6,
                f"{len(credits._PENDING)} buffered")

    store5.down = False
    credits._NEXT_PROBE = 0.0
    try:
        # Every one of these can trigger the recovery; only one may win, and
        # none may hang. A deadlock shows up here as a timeout, not a wrong
        # number, which is why this is wrapped rather than merely awaited.
        await asyncio.wait_for(
            asyncio.gather(*(charge(0.005) for _ in range(6))), timeout=10
        )
        ok &= check("recovery under concurrent load does not deadlock", True)
    except asyncio.TimeoutError:
        ok &= check("recovery under concurrent load does not deadlock", False,
                    "timed out — the flush and a charge are fighting over _LOCK")

    ok &= check("the meter recovered", not credits.degraded())
    ok &= check(
        "no movement was lost or double-counted",
        abs(store5.debits() - 90) < 0.001,
        f"debits {store5.debits()} (60 buffered + 30 live)",
    )
    ok &= check(
        "and the balance agrees with the ledger",
        abs(store5.balance() - (opening - 90)) < 0.001,
        f"{store5.balance()} (expected {opening - 90})",
    )

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} — {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
