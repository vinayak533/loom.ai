"""REST surface for the Agentic Loop section.

Everything here is read-mostly: the catalogue the gallery renders, the credit
balance and ledger, the approval audit, and the tool audit. The conversation
itself runs entirely over `/ws/agent/{session_id}`.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents import approvals, registry, runner
from app.api.auth import bearer_user
from app.api.ownership import require_session
from app.config import get_settings
from app.credits import get_balance, grant, recent_ledger, store_status
from app.db import repository
from app.llm_router import effective_default_model

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents")


@router.get("")
async def list_agents():
    """The gallery. Includes each tool's live configured state."""
    return {
        "agents": registry.public_catalogue(),
        "order": list(registry.AGENT_ORDER),
    }


@router.get("/audit")
async def tool_audit():
    """Which tools are real integrations and which are reasoning patterns.

    Exists so that question is answerable against the running code rather than
    against a paragraph in a commit message. Generated from the registry, so it
    cannot go stale the way prose does.
    """
    return {"agents": registry.audit()}


@router.get("/{agent_id}")
async def agent_detail(agent_id: str):
    if not registry.is_agent(agent_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown agent `{agent_id}`.")
    agent = registry.get_agent(agent_id)
    catalogue = {a["id"]: a for a in registry.public_catalogue()}
    return {
        **catalogue[agent_id],
        # The assembled system prompt, so the persona is inspectable rather
        # than something the user has to infer from the agent's behaviour.
        "system_prompt": agent.system_prompt(),
    }


@router.post("/{agent_id}/sessions", status_code=status.HTTP_201_CREATED)
async def create_agent_session(
    agent_id: str,
    user_id: str | None = Depends(bearer_user),
    model_id: str | None = None,
):
    """Start a conversation with one specialist.

    The agent is pinned onto the session row here rather than passed per
    message: which specialist a transcript belongs to is decided once, and a
    thread whose persona could change mid-conversation would be incoherent.
    """
    if not registry.is_agent(agent_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown agent `{agent_id}`.")
    settings = get_settings()
    return await repository.create_session(
        str(uuid.uuid4()),
        user_id,
        model_id=model_id or effective_default_model(),
        agent_id=agent_id,
        title=f"New {registry.get_agent(agent_id).role.lower()} session",
    )


@router.get("/{agent_id}/sessions")
async def list_agent_sessions(
    agent_id: str,
    user_id: str | None = Depends(bearer_user),
    archived: bool = False,
):
    """This specialist's history, ordered by the same rule as every other list."""
    if not registry.is_agent(agent_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown agent `{agent_id}`.")
    return await repository.list_sessions(user_id, archived=archived, agent_id=agent_id)


# --- credits ----------------------------------------------------------------
# Mounted outside the /api/agents/{agent_id} space, because a balance belongs
# to a person rather than to one specialist.

credits_router = APIRouter(prefix="/api/credits")


@credits_router.get("")
async def credit_balance(user_id: str | None = Depends(bearer_user)):
    settings = get_settings()
    balance = await get_balance(user_id)
    return {
        **balance.as_dict(),
        "enabled": settings.credits_enabled,
        "credit_usd": settings.credit_usd,
        "minimum_to_start": settings.credit_minimum_to_start,
        # Whether this balance actually survives a restart. Surfaced rather
        # than hidden: an operator needs to know the meter has dropped to its
        # in-memory fallback, and the reason names the fix.
        "store": store_status(),
    }


@credits_router.get("/ledger")
async def credit_ledger(user_id: str | None = Depends(bearer_user), limit: int = 25):
    return {"entries": await recent_ledger(user_id, limit=max(1, min(limit, 100)))}


@credits_router.post("/topup")
async def credit_topup(
    payload: dict,
    user_id: str | None = Depends(bearer_user),
):
    """Add credits to the signed-in account.

    There is no billing integration behind this — it is the manual grant a
    developer or operator uses, and it is deliberately not reachable from the
    agent tools. Wiring it to a payment provider is the next step whenever this
    stops being a single-operator deployment.
    """
    amount = payload.get("amount")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "`amount` must be a number.")
    if amount <= 0 or amount > 1_000_000:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "`amount` must be between 0 and 1,000,000."
        )
    balance = await grant(user_id, amount, reason=str(payload.get("reason") or "Top-up"))
    return balance.as_dict()


# --- approvals --------------------------------------------------------------

approvals_router = APIRouter(prefix="/api/approvals")


@approvals_router.get("/{session_id}")
async def pending_approvals(
    session_id: str, user_id: str | None = Depends(bearer_user)
):
    """What this session is currently waiting on a human for.

    Only live, in-process requests appear here; a pending approval is
    meaningless once the run awaiting it is gone. The historical record lives
    in the `agent_approvals` table.
    """
    await require_session(user_id, session_id)
    return {"pending": approvals.pending_for_session(session_id)}


@approvals_router.post("/{approval_id}/resolve")
async def resolve_approval(
    approval_id: str,
    payload: dict,
    user_id: str | None = Depends(bearer_user),
):
    """Answer an approval over HTTP rather than the socket.

    The websocket path is the normal one; this exists so a decision is not lost
    if the socket has reconnected since the card was raised. It resolves the
    same in-process future.
    """
    # Keyed by approval rather than by session, so the owning session has to be
    # resolved before the decision can be attributed to anyone. A gate that any
    # passer-by can answer is not a gate.
    owning_session = approvals.session_for_approval(approval_id)
    if owning_session is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That approval is no longer waiting — it was already answered, or "
            "its run has ended.",
        )
    await require_session(user_id, owning_session)

    decision = str((payload or {}).get("decision") or "rejected")
    parameters = (payload or {}).get("parameters") or {}
    resolved = approvals.resolve(approval_id, decision, parameters)
    if not resolved:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That approval is no longer waiting — it was already answered, or "
            "its run has ended.",
        )
    return {"approval_id": approval_id, "decision": decision, "resolved": True}


# --- transcript -------------------------------------------------------------


@router.get("/sessions/{session_id}/messages")
async def agent_session_messages(
    session_id: str, user_id: str | None = Depends(bearer_user)
):
    """Full trace for one specialist session, for replay after a reload.

    Same shape as the Chat/Code endpoint so the client's history hydration is
    the same code — `checkpoint` is the authoritative graph state, `log` is the
    flat row list including every tool call and result.
    """
    await require_session(user_id, session_id)
    state = await runner.get_state(session_id)
    return {
        "checkpoint": {
            "messages": state.get("messages") or [],
            "iterations": state.get("iterations") or 0,
            "usage": state.get("usage") or {},
        },
        "log": await repository.list_messages(session_id),
        "agent_id": state.get("agent_id") or None,
        "sandbox_id": None,
    }
