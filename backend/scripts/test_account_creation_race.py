"""Three callers meet a brand-new account at once. One grant, not three.

    .venv/Scripts/python scripts/test_account_creation_race.py

The bug
-------
A new user's first moments fire three balance reads concurrently — the header's
`GET /api/credits`, the socket connect, and `ensure_can_start` for the first
turn. All three found no row, and all three created one.

Creation went through `upsert(row)`, which PostgREST sends as
`ON CONFLICT DO UPDATE`. So the losers did not lose: each one wrote `balance`
and `spent` back to their opening values. A charge debited in the middle of
that window was erased — the account had spent the credits and the row said it
had not — and each caller appended its own "Opening balance" row, so the ledger
then claimed three grants for one account and stopped agreeing with `spent`.

What is asserted
----------------
1. Three concurrent first-sight reads produce exactly one INSERT.
2. Exactly one "Opening balance" ledger row is written.
3. A debit that lands mid-race survives it — no later caller writes `spent`
   back to zero.
4. The insert is sent as ON CONFLICT DO NOTHING, so a caller that loses the
   race across *processes* (where the in-process lock cannot help) overwrites
   nothing.
5. The ordinary paths still work: an existing account is read, not recreated,
   and a genuinely first-sight account is still granted its opening balance.

No database is touched. `get_client` is replaced with a fake PostgREST that
enforces the primary key on `user_credits` the way Postgres does, and every
call is made to take a real `await` inside the critical section so the race is
actually exercised rather than merely described.
"""

from __future__ import annotations

import asyncio
import sys
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
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


# ---------------------------------------------------------------------------
# a fake PostgREST that keeps the promises the real one keeps
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _Table:
    """One table's worth of the query builder, executed against a dict store.

    Only the fragment `app/credits.py` uses is implemented. `upsert` is the
    interesting one: it honours `ignore_duplicates` exactly as PostgREST does —
    `ON CONFLICT DO NOTHING`, returning the rows it actually inserted, which is
    the signal the fix reads to decide whether it owns the opening grant.
    """

    def __init__(self, store: "_Store", name: str) -> None:
        self.store, self.name = store, name
        self._filter: tuple[str, object] | None = None
        self._op: tuple[str, object] | None = None

    # -- query shape --
    def select(self, *_cols):
        self._op = ("select", None)
        return self

    def insert(self, row):
        self._op = ("insert", row)
        return self

    def upsert(self, row, ignore_duplicates: bool = False, **_kw):
        self._op = ("upsert", (row, ignore_duplicates))
        return self

    def update(self, patch):
        self._op = ("update", patch)
        return self

    def eq(self, column, value):
        self._filter = (column, value)
        return self

    def limit(self, _n):
        return self

    def order(self, *_a, **_kw):
        return self

    # -- execution --
    def execute(self):
        kind, payload = self._op
        rows = self.store.rows(self.name)

        if kind == "select":
            found = [r for r in rows if self._matches(r)]
            self.store.log.append((self.name, "select", len(found)))
            return _Result([dict(r) for r in found])

        if kind in ("insert", "upsert"):
            row, ignore = (payload, False) if kind == "insert" else payload
            if self.name == "user_credits":
                key = row["user_id"]
                if any(r["user_id"] == key for r in rows):
                    self.store.log.append((self.name, "conflict", key))
                    if ignore:
                        # ON CONFLICT DO NOTHING: nothing written, no rows back.
                        return _Result([])
                    # ON CONFLICT DO UPDATE — the old behaviour, kept so the
                    # test can prove the difference rather than assume it.
                    target = next(r for r in rows if r["user_id"] == key)
                    target.update(row)
                    self.store.log.append((self.name, "overwrote", key))
                    return _Result([dict(target)])
            rows.append(dict(row))
            self.store.log.append((self.name, "insert", row.get("user_id")))
            return _Result([dict(row)])

        if kind == "update":
            touched = [r for r in rows if self._matches(r)]
            for r in touched:
                r.update(payload)
            self.store.log.append((self.name, "update", len(touched)))
            return _Result([dict(r) for r in touched])

        raise AssertionError(f"unhandled op {kind}")

    def _matches(self, row: dict) -> bool:
        if self._filter is None:
            return True
        column, value = self._filter
        return row.get(column) == value


class _Store:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {"user_credits": [], "credit_ledger": []}
        self.log: list[tuple] = []

    def rows(self, name: str) -> list[dict]:
        return self.tables.setdefault(name, [])

    def table(self, name: str) -> _Table:
        return _Table(self, name)

    # -- readouts the assertions use --
    def opening_rows(self) -> list[dict]:
        return [
            r for r in self.rows("credit_ledger")
            if r.get("reason") == "Opening balance"
        ]

    def inserts(self, table: str) -> int:
        return sum(1 for t, op, _ in self.log if t == table and op == "insert")

    def overwrites(self) -> int:
        return sum(1 for _t, op, _ in self.log if op == "overwrote")


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


class _Env:
    """Point `credits` at the fake store, and put it back afterwards."""

    def __init__(self, store: _Store) -> None:
        self.store = store

    def __enter__(self):
        self.saved = {
            "get_client": credits.get_client,
            "durable": credits._durable,
            "to_thread": asyncio.to_thread,
        }
        credits.get_client = lambda: self.store

        async def _durable():
            return True

        credits._durable = _durable

        # The real `_safe` runs the synchronous client call on a worker thread,
        # which is where the interleaving comes from. `to_thread` is kept, but
        # yielded through so every store call really does suspend — a race the
        # scheduler never gets a chance to run is not a race.
        real_to_thread = self.saved["to_thread"]

        async def _yielding(fn, *a, **kw):
            await asyncio.sleep(0)
            result = await real_to_thread(fn, *a, **kw)
            await asyncio.sleep(0)
            return result

        asyncio.to_thread = _yielding

        credits._LOCAL.clear()
        credits._LAST_KNOWN.clear()
        return self

    def __exit__(self, *_exc):
        credits.get_client = self.saved["get_client"]
        credits._durable = self.saved["durable"]
        asyncio.to_thread = self.saved["to_thread"]
        credits._LOCAL.clear()
        credits._LAST_KNOWN.clear()
        return False


async def main() -> int:
    ok = True
    opening = credits.get_settings().credit_starting_balance

    # -- 1. the race itself -------------------------------------------------
    print("\n1. Three callers meet a brand-new account at once")
    store = _Store()
    with _Env(store):
        balances = await asyncio.gather(
            credits.get_balance("racer"),
            credits.get_balance("racer"),
            credits.get_balance("racer"),
        )

    ok &= check(
        "exactly one account row exists",
        len(store.rows("user_credits")) == 1,
        f"{len(store.rows('user_credits'))} rows",
    )
    ok &= check(
        "exactly one INSERT was attempted",
        store.inserts("user_credits") == 1,
        f"{store.inserts('user_credits')} inserts",
    )
    ok &= check(
        "exactly one Opening balance ledger row",
        len(store.opening_rows()) == 1,
        f"{len(store.opening_rows())} grant rows",
    )
    ok &= check(
        "nothing was overwritten on conflict",
        store.overwrites() == 0,
        f"{store.overwrites()} overwrites",
    )
    ok &= check(
        "all three callers see the same balance",
        len({b.balance for b in balances}) == 1
        and balances[0].balance == opening,
        f"balances={[b.balance for b in balances]}",
    )

    # -- 2. a charge landing mid-race is not erased -------------------------
    print("\n2. A charge that lands mid-race survives it")
    store = _Store()
    charge = 12.5
    with _Env(store):
        async def _charge_once():
            # Let the first reader get as far as its own first store call, then
            # debit — so the debit lands inside the window the other callers
            # are still creating the account in.
            await credits.get_balance("spender")
            await credits._debit(
                "spender", None, None, "llm", charge, "a turn that ran",
            )

        await asyncio.gather(
            credits.get_balance("spender"),
            _charge_once(),
            credits.get_balance("spender"),
            credits.get_balance("spender"),
        )
        final = await credits.get_balance("spender")

    ok &= check(
        "spent records the charge",
        abs(final.spent - charge) < 1e-6,
        f"spent={final.spent}, expected {charge}",
    )
    ok &= check(
        "balance is the opening grant minus the charge",
        abs(final.balance - (opening - charge)) < 1e-6,
        f"balance={final.balance}, expected {opening - charge}",
    )
    ok &= check(
        "still only one Opening balance row",
        len(store.opening_rows()) == 1,
        f"{len(store.opening_rows())} grant rows",
    )
    debits = [
        r for r in store.rows("credit_ledger") if float(r.get("amount", 0)) < 0
    ]
    ok &= check(
        "the ledger and the spent column agree",
        abs(sum(-float(r["amount"]) for r in debits) - final.spent) < 1e-6,
        f"ledger debits={sum(-float(r['amount']) for r in debits)}, "
        f"spent={final.spent}",
    )

    # -- 3. the cross-process half: DO NOTHING, not DO UPDATE ---------------
    print("\n3. A caller that loses the race in another process overwrites nothing")
    store = _Store()
    with _Env(store):
        # Seed the row as if another process created it and spent against it,
        # then let this process take its first-sight path against it. The
        # in-process lock cannot help here — only ON CONFLICT DO NOTHING can.
        store.rows("user_credits").append(
            {
                "user_id": "elsewhere",
                "balance": opening - 40,
                "granted": opening,
                "spent": 40,
            }
        )

        async def _first_sight():
            # Force the creation path by making the initial read miss, exactly
            # as it would if the other process's INSERT landed a moment later.
            calls = {"n": 0}
            real_table = store.table

            def _table(name):
                builder = real_table(name)
                if name != "user_credits":
                    return builder
                real_execute = builder.execute

                def execute():
                    result = real_execute()
                    calls["n"] += 1
                    if calls["n"] == 1 and builder._op[0] == "select":
                        return _Result([])  # the row was not visible yet
                    return result

                builder.execute = execute
                return builder

            store.table = _table
            try:
                return await credits.get_balance("elsewhere")
            finally:
                store.table = real_table

        seen = await _first_sight()

    ok &= check(
        "the existing balance is returned, not the opening grant",
        abs(seen.balance - (opening - 40)) < 1e-6,
        f"balance={seen.balance}, expected {opening - 40}",
    )
    ok &= check(
        "the spent column was not reset",
        abs(seen.spent - 40) < 1e-6,
        f"spent={seen.spent}",
    )
    ok &= check(
        "no grant was recorded for an account this process did not create",
        len(store.opening_rows()) == 0,
        f"{len(store.opening_rows())} grant rows",
    )
    ok &= check("and nothing was overwritten", store.overwrites() == 0)

    # -- 4. the ordinary paths still work -----------------------------------
    print("\n4. The ordinary paths are unchanged")
    store = _Store()
    with _Env(store):
        first = await credits.get_balance("solo")
        again = await credits.get_balance("solo")

    ok &= check(
        "a genuinely new account is granted its opening balance",
        first.balance == opening and first.granted == opening and first.spent == 0,
        f"{first}",
    )
    ok &= check("with one grant row", len(store.opening_rows()) == 1)
    ok &= check(
        "a second read returns the same account without recreating it",
        again.balance == opening and store.inserts("user_credits") == 1,
        f"{store.inserts('user_credits')} inserts",
    )

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
