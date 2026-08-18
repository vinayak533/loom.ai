"""E2B sandbox lifecycle, scoped to a session.

One sandbox per `session_id`. Sandboxes are created lazily on first tool use,
kept alive with a rolling 15-minute idle timeout (E2B kills them server-side
when the timeout lapses), and torn down by a background reaper as soon as we
notice they have gone idle locally.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from e2b import AsyncSandbox

from app.config import get_settings

log = logging.getLogger(__name__)

WORKDIR = "/home/user"


class SandboxUnavailable(RuntimeError):
    """Raised when we cannot give the agent a sandbox to work in."""


#: Fraction of the idle timeout after which the server-side deadline is pushed
#: forward again. Refreshing on *every* tool call cost an E2B round trip per
#: call for no benefit — the deadline is minutes away and only has to be
#: renewed comfortably before it lapses.
KEEPALIVE_FRACTION = 0.25

#: How often the reaper sweeps for idle sandboxes.
REAP_INTERVAL_SECONDS = 30


@dataclass
class SandboxEntry:
    sandbox: AsyncSandbox
    session_id: str
    last_used: float = field(default_factory=time.monotonic)
    #: When the server-side timeout was last pushed forward.
    last_keepalive: float = field(default_factory=time.monotonic)

    @property
    def sandbox_id(self) -> str:
        return self.sandbox.sandbox_id


class SandboxManager:
    """Process-local registry of live sandboxes keyed by session id."""

    def __init__(self) -> None:
        self._entries: dict[str, SandboxEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._reaper: asyncio.Task | None = None

    # --- lifecycle --------------------------------------------------------

    def start_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        for session_id in list(self._entries):
            try:
                await self.destroy(session_id)
            except Exception:  # noqa: BLE001
                # One failure must not strand the sessions after it in the list.
                log.exception("Failed to destroy sandbox for session %s", session_id)
        self._locks.clear()

    async def _reap_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(REAP_INTERVAL_SECONDS)
                try:
                    await self._reap_once()
                except Exception:  # noqa: BLE001
                    # One session's teardown must not end the sweep. This loop
                    # is the only thing that frees idle sandboxes, so letting an
                    # exception kill it leaks *every* sandbox from that moment
                    # on — a far worse failure than the one that caused it.
                    log.exception("Sandbox reaper pass failed; continuing")
        except asyncio.CancelledError:
            pass

    async def _reap_once(self) -> None:
        """One sweep: destroy idle sandboxes, then collect their locks.

        Split out from the loop so it can be driven directly by a test instead
        of waiting on wall-clock time.
        """
        idle = get_settings().sandbox_idle_timeout_seconds
        now = time.monotonic()
        stale = [
            sid for sid, e in list(self._entries.items()) if now - e.last_used > idle
        ]
        for sid in stale:
            log.info("Reaping idle sandbox for session %s", sid)
            try:
                await self.destroy(sid)
            except Exception:  # noqa: BLE001
                log.exception("Failed to reap sandbox for session %s", sid)

        # Locks are never dropped by `destroy` (see the note there). Collect
        # them here instead, and only when the session has no sandbox *and*
        # nothing holds the lock. A lock with waiters is by definition held, so
        # an unlocked one has nobody queued behind it, and `_lock()` -> `async
        # with` contains no await on the uncontended path — there is no window
        # in which a caller is about to acquire the object being removed.
        for sid, lock in list(self._locks.items()):
            if sid not in self._entries and not lock.locked():
                self._locks.pop(sid, None)

    # --- access -----------------------------------------------------------

    def _lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def get(self, session_id: str) -> AsyncSandbox:
        """Return this session's sandbox, creating it on first use."""
        settings = get_settings()
        if not settings.e2b_api_key:
            raise SandboxUnavailable(
                "E2B_API_KEY is not set — the agent has no sandbox to run in. "
                "Add it to backend/.env and restart the server."
            )

        idle = settings.sandbox_idle_timeout_seconds
        async with self._lock(session_id):
            entry = self._entries.get(session_id)
            if entry is not None:
                now = time.monotonic()
                entry.last_used = now
                # Roll the server-side timeout forward, but not on every single
                # tool call — that is a network round trip in front of every
                # read, write and shell command, renewing a deadline that is
                # still minutes out. Renew once per quarter-window instead.
                if now - entry.last_keepalive < idle * KEEPALIVE_FRACTION:
                    return entry.sandbox
                try:
                    await entry.sandbox.set_timeout(idle)
                except Exception:  # sandbox died server-side; rebuild below
                    log.warning("Sandbox %s unreachable, recreating", entry.sandbox_id)
                    self._entries.pop(session_id, None)
                    # The dev server died with it, and its forwarded URL now
                    # points at nothing. Drop the record so the next
                    # `start_dev_server` builds a fresh one rather than
                    # believing a preview is already up.
                    from app.tools.preview import preview_manager

                    await preview_manager.discard(session_id)
                else:
                    entry.last_keepalive = now
                    return entry.sandbox

            try:
                sandbox = await AsyncSandbox.create(
                    api_key=settings.e2b_api_key,
                    timeout=idle,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                raise SandboxUnavailable(f"Could not start E2B sandbox: {exc}") from exc

            log.info("Created sandbox %s for session %s", sandbox.sandbox_id, session_id)
            self._entries[session_id] = SandboxEntry(
                sandbox=sandbox, session_id=session_id
            )
            return sandbox

    def sandbox_id_for(self, session_id: str) -> str | None:
        entry = self._entries.get(session_id)
        return entry.sandbox_id if entry else None

    async def destroy(self, session_id: str) -> None:
        """Tear down a session's sandbox and its forwarded port.

        Holds the session's lock for the whole teardown. Without it, a destroy
        landing while `get()` was still inside `AsyncSandbox.create()` would
        return before that sandbox was ever registered — leaving a live sandbox
        with no reference anywhere to kill it, burning an E2B slot until its own
        server-side timeout lapsed. The reaper fires every 30s and a cold
        create takes seconds, so that window was open in ordinary use.

        Lock order is `preview -> sandbox`, never the reverse: `PreviewManager.
        start()` holds its own lock while awaiting `get()` here.
        `preview_manager.discard()` deliberately takes no lock, so calling it
        from inside this one cannot close a cycle.

        The lock object itself is *not* dropped here. Removing a lock that a
        concurrent `get()` is holding lets the next caller build a second lock
        and enter the critical section alongside it, which is the same orphan
        bug by another route. Dead locks are collected in `_reap_once()`.
        """
        # Imported lazily because `preview` imports this module.
        from app.tools.preview import preview_manager

        async with self._lock(session_id):
            await preview_manager.discard(session_id)

            entry = self._entries.pop(session_id, None)
            if entry is None:
                return
            try:
                await entry.sandbox.kill()
            except Exception:  # noqa: BLE001 - best effort teardown
                log.warning(
                    "Failed to kill sandbox %s", entry.sandbox_id, exc_info=True
                )


sandbox_manager = SandboxManager()
