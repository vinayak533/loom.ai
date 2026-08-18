"""Live preview: a dev server running in the sandbox, exposed to the browser.

One preview per `session_id`, mirroring `SandboxManager`'s shape exactly — the
two registries are keyed the same way and torn down together, so a session can
never end up with a forwarded port pointing at a sandbox that is already gone.

The flow is deliberately explicit rather than inferred from arbitrary bash
output: the agent calls `start_dev_server`, we background the command, wait for
the port to actually accept connections, ask E2B for the forwarded host, and
only then announce `preview_ready`. A server that never binds fails loudly here
instead of producing an iframe pointed at nothing.

While the server runs we keep reading its output for two reasons:

  * a framework that compiles with an error keeps serving, so that has to reach
    the UI as a *banner* (`fatal=False`) rather than as a dead preview;
  * the process exiting is the one thing the browser cannot detect on its own —
    the iframe just goes blank — so the watcher turns it into `preview_error`.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from dataclasses import dataclass, field

from app import events as ev
from app.emitter import Emitter, registry
from app.tools.sandbox import WORKDIR, sandbox_manager

log = logging.getLogger(__name__)

#: How long we wait for the dev server to bind its port before giving up. Cold
#: `next dev` on a fresh sandbox is the slow case this is sized for.
READY_TIMEOUT_SECONDS = 90.0

#: How often we poll the port while waiting.
READY_POLL_SECONDS = 0.75

#: Ports we refuse to forward. 49982 is E2B's own envd port — forwarding it
#: would expose the sandbox control plane, and binding it breaks the sandbox.
RESERVED_PORTS = {22, 49982, 49983}

#: How much of the dev server's output we keep for the error banner.
MAX_LOG_CHARS = 8000

#: Lines that mean "the build broke" without meaning "the process died".
#: Matched against a *window* of recent output, so a stack trace under a
#: "Failed to compile" header is reported with its first useful line.
_ERROR_PATTERNS = (
    re.compile(r"^\s*Failed to compile", re.I | re.M),
    re.compile(r"^\s*(?:Syntax|Type|Reference|Module build|Module not found)Error", re.I | re.M),
    re.compile(r"^\s*Error:\s", re.M),
    re.compile(r"^\s*error\s+TS\d+:", re.I | re.M),
    re.compile(r"Cannot find module", re.I),
    re.compile(r"\[vite\].*error", re.I),
    re.compile(r"ELIFECYCLE|EADDRINUSE", re.I),
)

#: Once an error banner has been raised, a line matching one of these clears it
#: — the developer fixed the file and the server recompiled.
_RECOVERY_PATTERNS = (
    re.compile(r"compiled successfully|compiled .*in \d|ready in \d|\bReady\b", re.I),
    re.compile(r"hmr update|page reload", re.I),
)


class PreviewError(RuntimeError):
    """Raised when a dev server could not be brought up."""


@dataclass
class PreviewEntry:
    session_id: str
    command: str
    port: int
    url: str = ""
    handle: object | None = None
    watcher: asyncio.Task | None = None
    started_at: float = field(default_factory=time.monotonic)
    #: Trailing output, kept so a late-arriving error can quote the real line.
    log: str = ""
    #: Whether a non-fatal error banner is currently raised.
    erroring: bool = False


class PreviewManager:
    """Process-local registry of running dev servers, keyed by session id."""

    def __init__(self) -> None:
        self._entries: dict[str, PreviewEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # --- access -----------------------------------------------------------

    def _lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    @staticmethod
    def _emit(session_id: str, event: dict, fallback: Emitter | None = None) -> None:
        """Send an event to whichever socket the session is on right now.

        A preview outlives the turn that started it and every reconnect after
        that, so the emitter is looked up at emit time rather than captured.
        `fallback` covers the one caller with no bound socket — the CLI test
        harness — and is otherwise the same object the lookup returns.
        """
        emitter = registry.for_session(session_id) or fallback
        if emitter is not None and not emitter.closed:
            emitter.emit(event)

    def get(self, session_id: str) -> PreviewEntry | None:
        return self._entries.get(session_id)

    def status(self, session_id: str) -> dict:
        """What the REST layer reports for a session. Never raises."""
        entry = self._entries.get(session_id)
        if entry is None or not entry.url:
            return {"running": False, "url": None, "port": None, "command": None}
        return {
            "running": True,
            "url": entry.url,
            "port": entry.port,
            "command": entry.command,
            "erroring": entry.erroring,
            "uptime_seconds": round(time.monotonic() - entry.started_at, 1),
        }

    # --- lifecycle --------------------------------------------------------

    async def start(
        self,
        session_id: str,
        command: str,
        port: int,
        emitter: Emitter | None,
    ) -> PreviewEntry:
        """Start a dev server and forward its port. Raises `PreviewError`.

        Starting a second server replaces the first: two previews for one
        session would leave the UI with no honest answer to "which one am I
        looking at", and the old process would keep holding its port.
        """
        command = (command or "").strip()
        if not command:
            raise PreviewError("`command` was empty.")
        if port in RESERVED_PORTS:
            raise PreviewError(
                f"Port {port} is reserved by the sandbox runtime. Pick another one."
            )
        if not 1 <= port <= 65535:
            raise PreviewError(f"Port {port} is out of range.")

        async with self._lock(session_id):
            await self._stop_locked(session_id, emitter, reason="replaced")

            sandbox = await sandbox_manager.get(session_id)
            entry = PreviewEntry(session_id=session_id, command=command, port=port)

            def on_output(chunk: str) -> None:
                self._ingest(entry, chunk)

            try:
                handle = await sandbox.commands.run(
                    command,
                    cwd=WORKDIR,
                    background=True,
                    on_stdout=on_output,
                    on_stderr=on_output,
                    # A dev server is meant to outlive the call that started it;
                    # `timeout=0` disables the per-command deadline that every
                    # other tool relies on.
                    timeout=0,
                )
            except Exception as exc:  # noqa: BLE001 - reported to the agent
                raise PreviewError(f"Could not launch `{command}`: {exc}") from exc

            entry.handle = handle
            self._entries[session_id] = entry

            try:
                await self._await_port(sandbox, entry)
            except PreviewError:
                # Nothing bound the port. Kill the process so a retry is not
                # racing a half-started server, and surface whatever it printed
                # — that output *is* the diagnosis nine times out of ten.
                await self._kill(entry)
                self._entries.pop(session_id, None)
                raise

            try:
                host = sandbox.get_host(port)
                # `get_host` is synchronous in the SDK we build against, but it
                # is cheap insurance to accept a coroutine too — this is the one
                # call in the flow whose signature we do not control.
                if inspect.isawaitable(host):
                    host = await host
            except Exception as exc:  # noqa: BLE001
                await self._kill(entry)
                self._entries.pop(session_id, None)
                raise PreviewError(f"Could not forward port {port}: {exc}") from exc

            entry.url = host if str(host).startswith("http") else f"https://{host}"
            entry.watcher = asyncio.create_task(self._watch(entry))

            log.info(
                "Preview up for session %s on port %s -> %s", session_id, port, entry.url
            )
            self._emit(
                session_id, ev.preview_ready(entry.url, port, command), emitter
            )
            return entry

    async def stop(
        self, session_id: str, emitter: Emitter | None = None, reason: str = "stopped"
    ) -> bool:
        """Tear the preview down. Returns whether there was one to stop."""
        async with self._lock(session_id):
            return await self._stop_locked(session_id, emitter, reason)

    async def _stop_locked(
        self, session_id: str, emitter: Emitter | None, reason: str
    ) -> bool:
        entry = self._entries.pop(session_id, None)
        if entry is None:
            return False
        if entry.watcher and not entry.watcher.done():
            entry.watcher.cancel()
        await self._kill(entry)
        self._emit(session_id, ev.preview_stopped(entry.port, reason), emitter)
        log.info("Preview for session %s taken down (%s)", session_id, reason)
        return True

    async def discard(self, session_id: str) -> None:
        """Forget a session's preview without emitting anything.

        Called from sandbox teardown: the sandbox is already going away, so the
        process dies with it and there is no socket left to tell.

        Deliberately takes no lock. Sandbox teardown runs while
        `SandboxManager`'s own per-session lock is held, and `start()` holds
        *this* manager's lock while it waits on that one — so acquiring here
        would close the cycle. The session's lock object is also left in place
        rather than dropped, so a `start()` already waiting on it stays
        serialised against the next one.
        """
        entry = self._entries.pop(session_id, None)
        if entry is None:
            return
        if entry.watcher and not entry.watcher.done():
            entry.watcher.cancel()
        await self._kill(entry)

    async def shutdown(self) -> None:
        for session_id in list(self._entries):
            await self.discard(session_id)

    # --- internals --------------------------------------------------------

    @staticmethod
    async def _kill(entry: PreviewEntry) -> None:
        handle = entry.handle
        if handle is None:
            return
        try:
            await handle.kill()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - the sandbox may already be gone
            log.debug("Preview process kill failed", exc_info=True)

    async def _await_port(self, sandbox, entry: PreviewEntry) -> None:
        """Block until something is listening on the port, or give up.

        Polling from *inside* the sandbox rather than fetching the forwarded URL
        from here: a proxy that answers 502 while the server boots is
        indistinguishable from one that answers 502 because nothing will ever
        listen, and the in-sandbox check has neither ambiguity nor the round
        trip.
        """
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        probe = (
            f"python3 -c \"import socket,sys; s=socket.socket(); "
            f"s.settimeout(1); sys.exit(s.connect_ex(('127.0.0.1',{entry.port})))\""
        )

        while time.monotonic() < deadline:
            handle = entry.handle
            exit_code = getattr(handle, "exit_code", None)
            if exit_code is not None:
                raise PreviewError(
                    f"`{entry.command}` exited with code {exit_code} before it "
                    f"listened on port {entry.port}.{self._tail(entry)}"
                )
            try:
                result = await sandbox.commands.run(probe, timeout=10)
                if result.exit_code == 0:
                    return
            except Exception:  # noqa: BLE001 - a failed probe is just "not yet"
                pass
            await asyncio.sleep(READY_POLL_SECONDS)

        raise PreviewError(
            f"`{entry.command}` did not listen on port {entry.port} within "
            f"{int(READY_TIMEOUT_SECONDS)}s. Check the port is the one the dev "
            f"server actually binds, and that it listens on 0.0.0.0."
            f"{self._tail(entry)}"
        )

    @staticmethod
    def _tail(entry: PreviewEntry, limit: int = 1200) -> str:
        text = entry.log.strip()
        if not text:
            return ""
        return "\n\nOutput:\n" + text[-limit:]

    def _ingest(self, entry: PreviewEntry, chunk: str) -> None:
        """Fold one chunk of dev-server output into the entry's state.

        Runs on E2B's callback, which may not be on the event loop — so it does
        nothing but string work and `Emitter.emit`, which is threadsafe by
        design.
        """
        entry.log = (entry.log + chunk)[-MAX_LOG_CHARS:]

        if entry.erroring:
            if any(p.search(chunk) for p in _RECOVERY_PATTERNS):
                entry.erroring = False
            return

        for pattern in _ERROR_PATTERNS:
            if pattern.search(chunk):
                entry.erroring = True
                self._emit(
                    entry.session_id,
                    ev.preview_error(
                        _first_useful_line(chunk), fatal=False, port=entry.port
                    ),
                )
                return

    async def _watch(self, entry: PreviewEntry) -> None:
        """Await the dev server's exit and report it as a fatal preview error."""
        handle = entry.handle
        if handle is None:
            return
        try:
            await handle.wait()  # type: ignore[union-attr]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - includes non-zero exit
            code = getattr(exc, "exit_code", None)
            self._report_exit(entry, code)
            return
        else:
            self._report_exit(entry, getattr(handle, "exit_code", None))

    def _report_exit(self, entry: PreviewEntry, code: int | None) -> None:
        # A preview that was replaced or torn down has already been removed from
        # the registry and announced; its watcher firing afterwards must not
        # contradict that.
        if self._entries.get(entry.session_id) is not entry:
            return
        self._entries.pop(entry.session_id, None)
        log.info("Preview for session %s exited (code %s)", entry.session_id, code)
        self._emit(
            entry.session_id,
            ev.preview_error(
                "The dev server stopped"
                + (f" (exit code {code})" if code is not None else "")
                + f".{self._tail(entry, 600)}",
                fatal=True,
                port=entry.port,
            ),
        )


def _first_useful_line(chunk: str) -> str:
    """The most informative line in a chunk of compiler output.

    Build tools lead with a banner ("Failed to compile") and follow with the
    line that actually names the problem. The banner alone tells the user
    nothing they cannot see from the broken page, so prefer the line after it
    when there is one.
    """
    lines = [line.strip() for line in chunk.splitlines() if line.strip()]
    if not lines:
        return "The dev server reported an error."
    for i, line in enumerate(lines):
        if re.match(r"^(failed to compile|error|✘|×|✗)\b", line, re.I) and i + 1 < len(lines):
            follow = lines[i + 1]
            return f"{line} — {follow}"[:400]
    return lines[0][:400]


preview_manager = PreviewManager()
