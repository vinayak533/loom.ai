"""Owns the specialist graph, its checkpointer, and one-turn execution.

Mirrors :mod:`app.agent.runner` — same checkpointer selection, same
``thread_id == session_id`` keying — so a specialist conversation survives a
restart exactly the way a Code session does.

Why a second checkpointer
-------------------------
It is a second *saver instance*, not a second store: the SQLite file and the
Postgres URL are the same ones the Code section uses. LangGraph keys checkpoints
by thread id, and session ids are unique across the whole product, so the two
graphs coexist in one table without collision. Sharing the instance would have
been possible, but it would couple the two runners' startup order for no gain.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.agents.graph import build_specialist_graph, build_user_message
from app.agents.registry import get_agent
from app.config import get_settings
from app.emitter import Emitter
from app.files import load_content_blocks
from app.llm_router import AUTO_MODEL_ID

log = logging.getLogger(__name__)

_graph = None
_checkpointer_cm = None
_checkpointer = None


async def startup() -> None:
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
            log.info("Agents: using Postgres checkpointer")
        except ImportError:
            log.warning(
                "POSTGRES_CHECKPOINT_URL is set but langgraph-checkpoint-postgres "
                "is not installed. Agents fall back to SQLite."
            )
            _checkpointer = None

    if _checkpointer is None:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = settings.checkpoint_db_path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        _checkpointer_cm = AsyncSqliteSaver.from_conn_string(path)
        _checkpointer = await _checkpointer_cm.__aenter__()
        log.info("Agents: using SQLite checkpointer at %s", path)

    _graph = build_specialist_graph(_checkpointer)


async def shutdown() -> None:
    global _checkpointer_cm, _checkpointer, _graph
    if _checkpointer_cm is not None:
        try:
            await _checkpointer_cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            log.warning("Agents checkpointer teardown failed", exc_info=True)
    _checkpointer_cm = _checkpointer = _graph = None


def get_graph():
    if _graph is None:
        raise RuntimeError("Specialist graph is not initialised — call startup() first.")
    return _graph


def _config(session_id: str, emitter: Emitter) -> dict:
    settings = get_settings()
    return {
        "configurable": {"thread_id": session_id, "emitter_id": emitter.id},
        "recursion_limit": settings.max_agent_iterations * 3 + 20,
    }


async def get_state(session_id: str) -> dict[str, Any]:
    graph = get_graph()
    snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
    return dict(snapshot.values or {})


async def run_turn(
    session_id: str,
    agent_id: str,
    text: str,
    emitter: Emitter,
    file_ids: list[str] | None = None,
    model_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Append a user message and run this specialist until it stops."""
    graph = get_graph()
    get_agent(agent_id)  # raises UnknownAgent before anything is charged
    settings = get_settings()

    prior = await get_state(session_id)
    attachments = await load_content_blocks(session_id, file_ids or [])
    history = list(prior.get("messages") or [])
    history.append(build_user_message(text, attachments))

    selection = model_id or prior.get("model_id") or settings.default_model_id
    if selection == AUTO_MODEL_ID:
        routing_mode = "auto"
        effective = prior.get("model_id") or settings.default_model_id
        if effective == AUTO_MODEL_ID:
            effective = settings.default_model_id
    else:
        routing_mode = "manual"
        effective = selection

    payload = {
        "session_id": session_id,
        # Pinned from the session row rather than from prior state: the agent a
        # thread belongs to is decided when it is created and never changes.
        "agent_id": agent_id,
        "user_id": user_id or "",
        "model_id": effective,
        "routing_mode": routing_mode,
        "routing_hint": "" if routing_mode == "manual" else prior.get("routing_hint", ""),
        "messages": history,
        "pending": [],
        "tool_results": [],
        # The iteration cap is per task, so it resets on every user message.
        "iterations": 0,
        # Same scope for the tool-call budget: it bounds one request, so a
        # follow-up message is a fresh allowance rather than a dead session.
        "tool_calls_used": 0,
        "stop_reason": "",
        "usage": prior.get("usage") or {},
        # Per-turn, so the "spent this turn" readout means this turn.
        "credits_spent": 0.0,
    }

    return await graph.ainvoke(payload, config=_config(session_id, emitter))
