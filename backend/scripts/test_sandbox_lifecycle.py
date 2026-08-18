"""Sandbox lifecycle under concurrency: no orphans, no leaked port forwards.

Every E2B call is faked, so this runs offline and deterministically. What it
actually exercises is `SandboxManager`'s bookkeeping, which is where a leak
comes from: a sandbox that gets created but never lands in `_entries` has no
reference left to kill it, and it sits burning an E2B slot until the
server-side timeout lapses.

The interleavings below are the ones normal use produces — the 30s reaper
firing while a cold `AsyncSandbox.create()` is still in flight, and a session
being deleted from the REST route at the same moment.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("E2B_API_KEY", "test-key")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

import app.tools.sandbox as sb  # noqa: E402

results: list[bool] = []


def check(label: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))


# --- fakes ------------------------------------------------------------------

created: list["FakeSandbox"] = []


class FakeSandbox:
    """Records whether it was ever killed. That is the whole point."""

    _next_id = 0

    def __init__(self) -> None:
        FakeSandbox._next_id += 1
        self.sandbox_id = f"sbx-{FakeSandbox._next_id}"
        self.killed = False
        self.timeouts: list[int] = []

    async def set_timeout(self, seconds: int) -> None:
        self.timeouts.append(seconds)

    async def kill(self) -> None:
        self.killed = True


class SlowCreate:
    """Stands in for AsyncSandbox.create, with a controllable delay.

    The delay is the whole mechanism: a cold sandbox takes seconds to come up,
    which is a wide open window for the reaper to run in.
    """

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay

    async def __call__(self, **kw):
        await asyncio.sleep(self.delay)
        s = FakeSandbox()
        created.append(s)
        return s


class FakePreviewManager:
    def __init__(self) -> None:
        self.discarded: list[str] = []

    async def discard(self, session_id: str) -> None:
        await asyncio.sleep(0)  # a real discard awaits; keep the yield point
        self.discarded.append(session_id)


def fresh_manager(delay: float = 0.05) -> tuple[sb.SandboxManager, FakePreviewManager]:
    created.clear()
    sb.AsyncSandbox = type("AsyncSandbox", (), {"create": staticmethod(SlowCreate(delay))})
    preview = FakePreviewManager()
    import app.tools.preview as pv

    pv.preview_manager = preview
    return sb.SandboxManager(), preview


def orphans() -> list[FakeSandbox]:
    return [s for s in created if not s.killed]


# --- 1. the race ------------------------------------------------------------


async def test_destroy_during_create() -> None:
    print("1. A destroy landing while a sandbox is still being created")
    mgr, _ = fresh_manager(delay=0.05)
    sid = "sess-race"

    async def opener():
        return await mgr.get(sid)

    a = asyncio.create_task(opener())
    await asyncio.sleep(0.01)          # A is inside create(), holding the lock
    await mgr.destroy(sid)             # the reaper / a session delete
    c = asyncio.create_task(opener())  # a second tool call arrives
    await asyncio.gather(a, c)
    await asyncio.sleep(0.02)

    tracked = mgr.sandbox_id_for(sid)
    live = [s for s in created if not s.killed]
    check("every sandbox created is either tracked or killed",
          all(s.sandbox_id == tracked for s in live),
          f"created={[s.sandbox_id for s in created]} tracked={tracked} "
          f"orphaned={[s.sandbox_id for s in live if s.sandbox_id != tracked]}")

    await mgr.destroy(sid)
    check("and teardown then kills the last one", not orphans(),
          f"still alive: {[s.sandbox_id for s in orphans()]}")


# --- 2. concurrent first use ------------------------------------------------


async def test_concurrent_first_use() -> None:
    print("\n2. Several tool calls racing to open the same session's sandbox")
    mgr, _ = fresh_manager(delay=0.03)
    sid = "sess-burst"

    got = await asyncio.gather(*(mgr.get(sid) for _ in range(6)))
    check("exactly one sandbox is created for six callers", len(created) == 1,
          f"{len(created)} created")
    check("and all six got the same one", len({s.sandbox_id for s in got}) == 1)

    await mgr.destroy(sid)
    check("teardown leaves nothing alive", not orphans())


# --- 3. reaper resilience ---------------------------------------------------


async def test_reaper_survives_failure() -> None:
    print("\n3. The reaper keeps running after a teardown raises")
    mgr, preview = fresh_manager(delay=0.0)

    boom = {"n": 0}

    async def exploding_discard(session_id: str) -> None:
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("preview teardown blew up")
        preview.discarded.append(session_id)

    preview.discard = exploding_discard

    await mgr.get("sess-a")
    await mgr.get("sess-b")
    for entry in mgr._entries.values():
        entry.last_used -= 10_000  # both are now well past the idle window

    mgr.start_reaper()
    await asyncio.sleep(0.05)
    # Drive the loop rather than waiting 30s of real time.
    for _ in range(3):
        await mgr._reap_once()

    check("the failing session did not stop the sweep", boom["n"] >= 2,
          f"discard called {boom['n']}x")
    check("the healthy session was still reaped", "sess-b" not in mgr._entries,
          str(list(mgr._entries)))
    check("the reaper task is still alive",
          mgr._reaper is not None and not mgr._reaper.done())
    await mgr.shutdown()


# --- 4. shutdown ------------------------------------------------------------


async def test_shutdown_kills_everything() -> None:
    print("\n4. Shutdown tears down every session")
    mgr, preview = fresh_manager(delay=0.0)
    for i in range(4):
        await mgr.get(f"sess-{i}")
    check("four sandboxes are live", len(created) == 4)

    await mgr.shutdown()
    check("all four were killed", not orphans(),
          f"survivors: {[s.sandbox_id for s in orphans()]}")
    check("each one's port forward was discarded too",
          sorted(preview.discarded) == [f"sess-{i}" for i in range(4)],
          str(sorted(preview.discarded)))
    check("the registry is empty", not mgr._entries, str(list(mgr._entries)))


# --- 5. lock bookkeeping ----------------------------------------------------


async def test_lock_table_does_not_grow_forever() -> None:
    print("\n5. The per-session lock table is bounded")
    mgr, _ = fresh_manager(delay=0.0)
    for i in range(50):
        await mgr.get(f"sess-{i}")
        await mgr.destroy(f"sess-{i}")
    await mgr._reap_once()
    check("dead sessions' locks are collected", len(mgr._locks) == 0,
          f"{len(mgr._locks)} locks retained")

    # ...but never one that is in use.
    sid = "sess-held"
    mgr2, _ = fresh_manager(delay=0.05)
    task = asyncio.create_task(mgr2.get(sid))
    await asyncio.sleep(0.01)
    held = mgr2._locks.get(sid)
    await mgr2._reap_once()
    check("a lock currently held is never collected", mgr2._locks.get(sid) is held)
    await task
    await mgr2.destroy(sid)


async def main() -> int:
    await test_destroy_during_create()
    await test_concurrent_first_use()
    await test_reaper_survives_failure()
    await test_shutdown_kills_everything()
    await test_lock_table_does_not_grow_forever()
    print(f"\n{sum(results)}/{len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
