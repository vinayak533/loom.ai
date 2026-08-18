"""Event plumbing between agent nodes and the websocket.

Nodes deep inside the LangGraph run need to push events to the browser, but
they must not hold a reference to the socket (and putting a callable into
`RunnableConfig["configurable"]` would end up in checkpoint metadata). So each
run registers an `Emitter` in a process-local registry under a plain string id,
and the id is what travels through the graph config.

`Emitter.emit` is deliberately synchronous and thread-safe: E2B's stdout/stderr
callbacks may fire from outside the event loop.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any


class Emitter:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        #: Set by the registry when the emitter is bound to a session.
        self.session_id: str | None = None
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def emit(self, ev: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            self._loop.call_soon_threadsafe(self.queue.put_nowait, ev)
        except RuntimeError:
            # Loop is gone (shutdown); dropping the event is the right call.
            pass

    def close(self) -> None:
        """Push the sentinel that stops the websocket writer task."""
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.call_soon_threadsafe(self.queue.put_nowait, None)
        except RuntimeError:
            pass


class _Registry:
    """Live emitters, addressable two ways.

    By id, for the agent run: the id travels through `RunnableConfig` and is
    stable for the length of one graph invocation.

    By session, for anything that outlives a single run — the live preview most
    of all. A dev server can die twenty minutes after the turn that started it,
    across any number of reconnects, and the notice has to reach whichever
    socket the session is on *now*. Holding the emitter that happened to be
    open at start time would send it to a closed queue.
    """

    def __init__(self) -> None:
        self._emitters: dict[str, Emitter] = {}
        self._by_session: dict[str, Emitter] = {}

    def register(self, emitter: Emitter, session_id: str | None = None) -> str:
        self._emitters[emitter.id] = emitter
        if session_id:
            emitter.session_id = session_id
            # Last connection wins. A reconnect replaces the old emitter, whose
            # socket is already gone.
            self._by_session[session_id] = emitter
        return emitter.id

    def get(self, emitter_id: str | None) -> Emitter | None:
        if not emitter_id:
            return None
        return self._emitters.get(emitter_id)

    def for_session(self, session_id: str | None) -> Emitter | None:
        if not session_id:
            return None
        emitter = self._by_session.get(session_id)
        return None if emitter is None or emitter.closed else emitter

    def unregister(self, emitter_id: str) -> None:
        emitter = self._emitters.pop(emitter_id, None)
        if emitter is None:
            return
        session_id = getattr(emitter, "session_id", None)
        # Only clear the session slot if this emitter still owns it — a slow
        # teardown must not unbind the reconnect that already replaced it.
        if session_id and self._by_session.get(session_id) is emitter:
            self._by_session.pop(session_id, None)


registry = _Registry()


def emitter_from_config(config: dict[str, Any] | None) -> Emitter | None:
    if not config:
        return None
    return registry.get((config.get("configurable") or {}).get("emitter_id"))


class NullEmitter(Emitter):
    """Used by the CLI test script, where there is no socket to write to."""

    def emit(self, ev: dict[str, Any]) -> None:  # pragma: no cover - debug aid
        pass
