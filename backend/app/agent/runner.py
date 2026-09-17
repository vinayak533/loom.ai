"""Owns the compiled graph, the checkpointer, and one-turn execution.

The checkpointer is what makes a session survive a backend restart or a Render
cold start: LangGraph writes the full `AgentState` after every node, keyed by
`thread_id == session_id`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.agent.graph import build_graph, build_user_message
from app.config import get_settings
from app.emitter import Emitter
from app.files import load_content_blocks
from app.llm_router import AUTO_MODEL_ID, effective_default_model

log = logging.getLogger(__name__)

_graph = None
_checkpointer_cm = None
_checkpointer = None


async def startup() -> None:
    """Open the checkpointer and compile the graph. Called from the lifespan."""
    global _graph, _checkpointer, _checkpointer_cm
    settings = get_settings()

    if settings.postgres_checkpoint_url:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            _checkpointer_cm = AsyncPostgresSaver.from_conn_string(
                settings.postgres_checkpoint_url
            )
            _checkpointer = await _checkpointer_cm.__aenter__()
            await _checkpointer.setup()
            log.info("Using Postgres checkpointer")
        except ImportError:
            log.warning(
                "POSTGRES_CHECKPOINT_URL is set but langgraph-checkpoint-postgres "
                "is not installed. Falling back to SQLite."
            )
            _checkpointer = None

    if _checkpointer is None:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = settings.checkpoint_db_path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        _checkpointer_cm = AsyncSqliteSaver.from_conn_string(path)
        _checkpointer = await _checkpointer_cm.__aenter__()
        log.info("Using SQLite checkpointer at %s", path)

    _graph = build_graph(_checkpointer)


async def shutdown() -> None:
    global _checkpointer_cm, _checkpointer, _graph
    if _checkpointer_cm is not None:
        try:
            await _checkpointer_cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            log.warning("Checkpointer teardown failed", exc_info=True)
    _checkpointer_cm = _checkpointer = _graph = None


def get_graph():
    if _graph is None:
        raise RuntimeError("Agent graph is not initialised — call startup() first.")
    return _graph


def _config(session_id: str, emitter: Emitter) -> dict:
    settings = get_settings()
    return {
        "configurable": {"thread_id": session_id, "emitter_id": emitter.id},
        # Each agent turn is 1 node + up to N tool nodes; give the graph room
        # for the full iteration budget before LangGraph's own guard fires.
        "recursion_limit": settings.max_agent_iterations * 4 + 20,
    }


async def get_state(session_id: str) -> dict[str, Any]:
    """Read the checkpointed state for a session (used to resume history)."""
    graph = get_graph()
    snapshot = await graph.aget_state(
        {"configurable": {"thread_id": session_id}}
    )
    return dict(snapshot.values or {})


async def set_messages(session_id: str, messages: list[dict[str, Any]]) -> None:
    """Replace the checkpointed conversation for a session.

    This is what makes an edit or a branch switch real rather than cosmetic.
    `AgentState["messages"]` is a plain list with no reducer on it, so
    `aupdate_state` replaces it outright — which is exactly the semantics
    needed here, and would not be if the field ever grew an `add_messages`
    annotation. If it does, this has to become a rewrite of the channel rather
    than an update, or every branch switch will append instead of replacing.

    `pending` and `tool_results` are cleared alongside it. They describe work
    belonging to the turn that is being cut away; leaving them would have the
    next turn flush results for tool calls that are no longer in the
    conversation, which the provider rejects.
    """
    graph = get_graph()
    await graph.aupdate_state(
        {"configurable": {"thread_id": session_id}},
        {
            "messages": list(messages),
            "pending": [],
            "tool_results": [],
            "stop_reason": "",
        },
    )


async def run_turn(
    session_id: str,
    text: str,
    emitter: Emitter,
    file_ids: list[str] | None = None,
    model_id: str | None = None,
    user_id: str | None = None,
    section: str = "chat",
    project_id: str | None = None,
) -> dict[str, Any]:
    """Append a user message and run the loop until the agent stops.

    ``project_id`` is read from the session row by the caller, not from prior
    state: a session moved into a project between turns has to pick the
    project's instructions up on its very next message, and prior state would
    still be holding the value from before the move.

    ``section`` is which surface this turn belongs to — 'chat' or 'code'. It
    is carried into graph state purely for attribution: the credit ledger, the
    `token_usage` row and the fallback log all record it, and every one of
    them said "chat" for Code turns before it was threaded through.
    """
    graph = get_graph()
    config = _config(session_id, emitter)

    prior = await get_state(session_id)
    attachments = await load_content_blocks(session_id, file_ids or [])
    history = list(prior.get("messages") or [])
    history.append(build_user_message(text, attachments))

    # Selection precedence: explicit arg > prior checkpointed state > default.
    # `AUTO_MODEL_ID` is a mode rather than a model, so it is unpacked here
    # into `routing_mode` and never travels on into dispatch.
    selection = model_id or prior.get("model_id") or effective_default_model()
    if selection == AUTO_MODEL_ID:
        routing_mode = "auto"
        # Seed with the model the last auto turn resolved to, so the graph can
        # tell "unchanged" from "just switched" and stays quiet when it should.
        effective_model = prior.get("model_id") or effective_default_model()
        if effective_model == AUTO_MODEL_ID:
            effective_model = effective_default_model()
    else:
        # An existing session that never opted into auto keeps its saved
        # model: absent state means manual, it is never inferred.
        routing_mode = "manual"
        effective_model = selection

    payload = {
        "session_id": session_id,
        "section": section,
        "project_id": project_id or "",
        "model_id": effective_model,
        "routing_mode": routing_mode,
        "routing_hint": "" if routing_mode == "manual" else prior.get("routing_hint", ""),
        "messages": history,
        "pending": [],
        "tool_results": [],
        # The 50-step cap is per task, so it resets on every user message.
        "iterations": 0,
        "stop_reason": "",
        "usage": prior.get("usage") or {},
        "user_id": user_id,
        # Per *turn*, like the iteration cap directly above — not cumulative
        # over the session, or a long conversation would sit permanently over
        # the ceiling once its lifetime spend crossed it.
        "credits_spent": 0.0,
    }

    return await graph.ainvoke(payload, config=config)
