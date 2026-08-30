"""Cooperative stop for a running turn.

Stopping a generation used to be `run_task.cancel()`, which is abrupt in
exactly the ways that matter:

  * the partial answer the model had already streamed was thrown away — it
    lived only in the local variables of the node that was cancelled, and
    LangGraph does not checkpoint a node that raised;
  * the tokens the provider had already produced were never billed, because
    the charge happens after the stream completes;
  * a stop delivered mid-tool-call left the `tool_use` block in the
    conversation with no matching `tool_result`, which the next request
    rejects outright.

So a stop is a *request* rather than an interrupt. The socket sets a flag; the
graph reads it at the points where stopping is safe — between stream events and
between tool calls — and then finishes the turn normally: partial text is
appended to the conversation, every outstanding tool call is closed with a
result, the checkpoint is written, and the usual `agent_done` is emitted with
``stop_reason == "cancelled"``.

The hard `task.cancel()` is still there as a backstop for a socket that goes
away entirely (see the `finally` in the websocket handlers), but it is no
longer how the Stop button works.

Flags are process-local and keyed by session id, which is the same scope the
emitter registry already uses. A flag is set by the socket reader and cleared
by whoever starts the next run, so a stop can never leak into a later turn.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: session_id -> stop requested. A set rather than a dict: the only state is
#: membership, and nothing needs to know *when* the stop was asked for.
_STOPPING: set[str] = set()


def request_stop(session_id: str) -> None:
    """Ask the run on ``session_id`` to stop at its next safe point."""
    _STOPPING.add(session_id)
    log.info("Stop requested for session %s", session_id)


def is_stopping(session_id: str) -> bool:
    return session_id in _STOPPING


def clear(session_id: str) -> None:
    """Forget any stop request. Called when a turn starts, and when it ends.

    Both, deliberately. Clearing on start is what stops a flag set after the
    previous turn already finished from killing the next one before it emits a
    token; clearing on end keeps the set from growing for the life of the
    process.
    """
    _STOPPING.discard(session_id)


def snapshot() -> set[str]:
    """Sessions currently asked to stop. For tests and diagnostics."""
    return set(_STOPPING)
