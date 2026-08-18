"""Human-in-the-loop pause/resume.

This is Agent 9's mechanism and a *shared capability*: Agent 2's real email
send and Agent 5's billable image render both call :func:`request` before they
do anything irreversible, so the card the user sees is the same one wherever
the action came from.

How the pause works
-------------------
:func:`request` creates an :class:`asyncio.Future`, emits an ``agent_paused``
frame, and awaits it. The tool call — and therefore the whole graph run, since
the graph is awaiting the tool — is genuinely suspended: no polling, no busy
loop, no timer that quietly approves. The websocket reader resolves the future
when the user clicks, which is the only thing that can.

Why the registry is in-process
------------------------------
A pending approval is only meaningful while a socket is open to answer it. If
the backend restarts, the awaiting task is gone and nothing is left to resume,
so persisting the *pending* state would only produce approvals that can never
be honoured. The audit trail — what was asked, what was decided — is written to
Supabase, because that is the half worth keeping.

Failure is a rejection
----------------------
A timeout, a dropped socket, or a cancelled run all resolve to "not approved".
The default answer to "may I send this email?" is no.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app import events as ev
from app.agents.tools.base import ToolContext
from app.db.supabase_client import enabled as supabase_enabled, get_client

log = logging.getLogger(__name__)

#: How long a pause waits before giving up. Long enough that a user can go and
#: check something; short enough that an abandoned tab does not hold a graph
#: run open forever. Expiry resolves to *rejected*, never to approved.
DEFAULT_TIMEOUT_SECONDS = 600.0


@dataclass
class Decision:
    approval_id: str
    decision: str  # approved | rejected | edited | expired | disconnected
    parameters: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def approved(self) -> bool:
        # `edited` is an approval — of different parameters. Everything else,
        # including every failure mode, is not.
        return self.decision in {"approved", "edited"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "decision": self.decision,
            "approved": self.approved,
            "parameters": self.parameters,
            "note": self.note,
        }


@dataclass
class _Pending:
    approval_id: str
    session_id: str
    agent_id: str
    action: str
    parameters: dict[str, Any]
    future: asyncio.Future


#: approval_id -> pending request. Process-local by design; see the module
#: docstring.
_PENDING: dict[str, _Pending] = {}


def session_for_approval(approval_id: str) -> str | None:
    """Which session raised this approval, or None if it is no longer waiting.

    The resolve endpoint is keyed by approval id rather than by session, so this
    is what lets it check that the person answering owns the run that asked.
    """
    pending = _PENDING.get(approval_id)
    return pending.session_id if pending else None


def pending_for_session(session_id: str) -> list[dict[str, Any]]:
    return [
        {
            "approval_id": p.approval_id,
            "action": p.action,
            "agent_id": p.agent_id,
            "parameters": p.parameters,
        }
        for p in _PENDING.values()
        if p.session_id == session_id and not p.future.done()
    ]


async def request(
    ctx: ToolContext,
    *,
    action: str,
    summary: str,
    parameters: dict[str, Any],
    risk: str = "medium",
    editable: list[str] | None = None,
    options: list[dict[str, str]] | None = None,
    cost_note: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Decision:
    """Pause this run and wait for a person. Returns their decision.

    ``editable`` names the parameters the approval card lets the user rewrite
    before approving. That is the "action modification buffer" from the brief:
    the proposed parameters sit here in state, and what comes back — not what
    went out — is what the caller must act on.
    """
    approval_id = f"apr_{uuid.uuid4().hex[:12]}"

    if ctx.emitter is None or ctx.emitter.closed:
        # No socket, no human. Refusing is the only safe answer: the caller is
        # about to do something irreversible and nobody is there to say no.
        log.info("Approval %s auto-rejected — no live socket", approval_id)
        return Decision(
            approval_id,
            "disconnected",
            parameters,
            "No connected client to ask, so the action was refused.",
        )

    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    pending = _Pending(
        approval_id=approval_id,
        session_id=ctx.session_id,
        agent_id=ctx.agent_id,
        action=action,
        parameters=dict(parameters),
        future=future,
    )
    _PENDING[approval_id] = pending

    await _record_request(ctx, approval_id, action, parameters, risk)

    ctx.emitter.emit(
        ev.agent_paused(
            approval_id=approval_id,
            agent_id=ctx.agent_id,
            action=action,
            summary=summary,
            parameters=parameters,
            risk=risk,
            editable=editable or [],
            options=options or _default_options(editable),
            cost_note=cost_note,
            timeout_seconds=int(timeout),
        )
    )

    try:
        decision: Decision = await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        decision = Decision(
            approval_id,
            "expired",
            parameters,
            f"No answer within {int(timeout)}s, so the action was refused.",
        )
    except asyncio.CancelledError:
        # The run was cancelled out from under us. Clean up and let it unwind —
        # swallowing this would leave the graph running after a Stop.
        _PENDING.pop(approval_id, None)
        _emit_resume(ctx, approval_id, "cancelled")
        await _record_decision(approval_id, "cancelled", parameters)
        raise
    finally:
        _PENDING.pop(approval_id, None)

    _emit_resume(ctx, approval_id, decision.decision)
    await _record_decision(approval_id, decision.decision, decision.parameters)
    return decision


def resolve(
    approval_id: str,
    decision: str,
    parameters: dict[str, Any] | None = None,
    note: str = "",
) -> bool:
    """Answer a pending approval. Called from the websocket reader.

    Returns False when there is nothing waiting on that id — a double click, or
    a card left over from a run that has already ended. Not an error: the right
    response is to tell the client it is stale, not to fail the socket.
    """
    pending = _PENDING.get(approval_id)
    if pending is None or pending.future.done():
        return False

    verdict = decision if decision in {"approved", "rejected", "edited"} else "rejected"
    # An edit only replaces the keys the client actually sent, so a card that
    # exposes two of five parameters cannot silently blank the other three.
    final = dict(pending.parameters)
    if verdict == "edited" and parameters:
        final.update(parameters)

    pending.future.set_result(Decision(approval_id, verdict, final, note))
    return True


def _default_options(editable: list[str] | None) -> list[dict[str, str]]:
    options = [
        {"id": "approved", "label": "Approve", "detail": "Run it exactly as proposed."},
        {"id": "rejected", "label": "Reject", "detail": "Do not run it. Nothing is spent or sent."},
    ]
    if editable:
        options.insert(
            1,
            {
                "id": "edited",
                "label": "Edit and approve",
                "detail": f"Change {', '.join(editable)} first, then run it.",
            },
        )
    return options


def _emit_resume(ctx: ToolContext, approval_id: str, decision: str) -> None:
    if ctx.emitter and not ctx.emitter.closed:
        ctx.emitter.emit(
            ev.agent_resumed(
                approval_id=approval_id, agent_id=ctx.agent_id, decision=decision
            )
        )


async def _record_request(
    ctx: ToolContext,
    approval_id: str,
    action: str,
    parameters: dict[str, Any],
    risk: str,
) -> None:
    if not supabase_enabled():
        return
    client = get_client()
    row = {
        "id": approval_id_to_uuid(approval_id),
        "session_id": ctx.session_id,
        "agent_id": ctx.agent_id,
        "action": action,
        "parameters": _redact(parameters),
        "risk": risk,
        "decision": "pending",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    await _safe(lambda: client.table("agent_approvals").insert(row).execute())


async def _record_decision(
    approval_id: str, decision: str, parameters: dict[str, Any]
) -> None:
    if not supabase_enabled():
        return
    client = get_client()
    patch = {
        "decision": decision,
        "final_parameters": _redact(parameters),
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    await _safe(
        lambda: client.table("agent_approvals")
        .update(patch)
        .eq("id", approval_id_to_uuid(approval_id))
        .execute()
    )


def approval_id_to_uuid(approval_id: str) -> str:
    """A stable UUID for the `apr_…` id, since the column is a uuid.

    Derived rather than random so the insert and the later update agree on the
    key without having to carry a second id around.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"approval:{approval_id}"))


#: Parameter names whose values must never reach the audit table. The approval
#: card shows them to the person deciding — that is the point — but a durable
#: row is a different exposure, and an email body is the user's content rather
#: than ours to keep.
_REDACT_KEYS = {"body", "text", "html", "content", "password", "token", "api_key"}


def _redact(parameters: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (parameters or {}).items():
        if key.lower() in _REDACT_KEYS and isinstance(value, str):
            out[key] = f"[{len(value)} characters, not stored]"
        else:
            out[key] = value
    return out


async def _safe(fn):
    try:
        return await asyncio.to_thread(fn)
    except Exception:  # noqa: BLE001 - the audit write must never fail a run
        log.warning("Approval audit write failed", exc_info=True)
        return None
