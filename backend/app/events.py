"""THE WEBSOCKET EVENT CONTRACT.

This module is the single source of truth for everything that travels over the
`/ws/{session_id}` socket. The frontend's entire animation system is a pure
function of these event types, so treat this file as a public API:

  * Adding a field is safe.
  * Renaming or removing a `type` is a breaking change — bump the
    `PROTOCOL_VERSION` below and update `frontend/lib/events.ts` in the same
    commit. The two files are mirrors of each other.

--------------------------------------------------------------------------
SERVER -> CLIENT
--------------------------------------------------------------------------
{"type": "connected",             "session_id": "...", "protocol": 1}
{"type": "agent_thinking_start"}
{"type": "agent_thinking_delta",  "content": "..."}        # summarised reasoning
{"type": "agent_thinking_end"}
{"type": "agent_message_start"}
{"type": "agent_token",           "content": "..."}        # one text delta
{"type": "agent_message_end"}
{"type": "tool_call_start",       "tool": "bash_execute", "input": {...},
                                  "call_id": "toolu_..."}
{"type": "tool_output_chunk",     "call_id": "...", "stream": "stdout"|"stderr",
                                  "content": "..."}        # live bash lines
{"type": "tool_call_result",      "call_id": "...", "output": "...",
                                  "success": true, "meta": {...}}
{"type": "file_changed",          "path": "...", "diff": "...",
                                  "content": "...", "change": "created"|"modified"}
{"type": "file_tree",             "path": "/home/user", "nodes": [...]}
{"type": "artifact_created",      "key": "pricing-page", "version": 1,
                                  "kind": "html", "title": "...", "content": "..."}
{"type": "artifact_updated",      "key": "pricing-page", "version": 2, ...}
    # Created opens the panel; updated revises it in place. Stealing focus on
    # every revision is what makes a canvas unusable.
{"type": "preview_ready",         "url": "https://...", "port": 3000,
                                  "command": "npm run dev"}
{"type": "preview_error",         "message": "...", "fatal": true|false,
                                  "port": 3000|null}
    # `fatal` distinguishes "the dev server is gone" (the iframe is dead, show
    # the stopped state) from "it compiled with an error but is still serving"
    # (keep the iframe, raise a banner over it).
{"type": "preview_stopped",       "port": 3000|null, "reason": "..."}
{"type": "usage",                 "input_tokens": 0, "output_tokens": 0,
                                   "cost_estimate": 0.0}
{"type": "agent_done",            "iterations": 3, "reason": "end_turn"}
{"type": "max_iterations",        "iterations": 50, "message": "..."}
{"type": "tool_budget_reached",   "agent_name": "...", "budget": 10, "used": 10,
                                  "message": "..."}
    # A per-turn tool-call ceiling was hit (see `AgentDef.max_tool_calls_per_turn`).
    # The run does not end here: the agent loses its tools and is asked to write
    # up what it has, so this frame is the reason the answer that follows is
    # partial.
{"type": "model_changed",         "model_id": "...", "name": "...",
                                   "supports_tools": true,
                                   "available": true, "note": "...",
                                   "routing_mode": "manual"|"auto",
                                   "routing_hint": "code_editing"|...|"",
                                   "reason": "code editing",
                                   "fallback_from": ""|"<model that failed>"}
    # `fallback_from` is non-empty only when the switch was involuntary: the
    # selected model errored (429 / 5xx / unavailable) and the router retried
    # on this one. `reason` then reads "hit a rate limit" rather than naming a
    # routing hint.
{"type": "error",                 "message": "..."}
{"type": "branches",              "branches": [
                                    {"turn_index": 2, "active": 2,
                                     "versions": [{"version": 1, "label": "...",
                                                   "created_at": "..."}, ...]}]}
    # One entry per *branched* user turn — a turn that has been edited at least
    # once. Sent on connect, and again after any edit, regenerate or switch.
    # A session nobody has edited sends `{"branches": []}` and draws no
    # switchers. `turn_index` counts user turns, not entries in the message
    # array; see `repository.user_turn_positions` for why.
{"type": "history_replaced",      "reason": "edit"|"regenerate"|"branch",
                                  "turn_index": 2, "content": "..."}
    # The conversation on the server is no longer the one on screen. For an
    # edit or a regenerate the frame names the turn that was cut and the text
    # replacing it, so the client can truncate its own transcript at the same
    # place — refetching there races the re-run that has already started. For
    # a branch switch `turn_index` is -1 and the client refetches instead.

--------------------------------------------------------------------------
SERVER -> CLIENT — the Agentic Loop section only
--------------------------------------------------------------------------
These travel on `/ws/agent/{session_id}` and are never emitted on the Chat /
Code socket. They are additive: a client that does not know them can ignore
them, which is why the protocol version below is unchanged.

{"type": "agent_meta",     "agent_id": "email_copywriter", "name": "...",
                           "role": "...", "icon": "Mail", "accent": "#F0B54A",
                           "tools": [...], "credits": {...}}
    # Sent on connect. Tells the client which specialist this session belongs
    # to and which of its tools are configured, so the UI can show a
    # "not configured" state before a turn is spent discovering it.

{"type": "agent_paused",   "approval_id": "apr_...", "agent_id": "...",
                           "action": "send_email", "summary": "...",
                           "parameters": {...}, "risk": "high",
                           "editable": ["subject"], "options": [...],
                           "cost_note": "...", "timeout_seconds": 600}
    # The run is genuinely suspended awaiting a human. Nothing resumes it
    # except an `approval_resolve` frame or the stated timeout, which refuses.

{"type": "agent_resumed",  "approval_id": "apr_...", "agent_id": "...",
                           "decision": "approved|edited|rejected|expired|cancelled"}

{"type": "agent_handoff",  "from_agent": "system_logic_router",
                           "next_agent": "email_copywriter",
                           "next_agent_name": "...", "reason": "...",
                           "context": "..."}
    # A routing directive. The UI renders a button; a human clicks it. There is
    # deliberately no event that performs the handoff on its own.

{"type": "credits",        "balance": 942.5, "spent_this_turn": 3.2,
                           "enabled": true}

--------------------------------------------------------------------------
CLIENT -> SERVER
--------------------------------------------------------------------------
{"type": "user_message", "content": "...", "file_ids": ["..."]}
{"type": "set_model",   "model_id": "auto | qwen3_7_plus | mimo_v2_5 | ..."}
    # "auto" is a routing mode, not a model: the backend then classifies each
    # turn and picks for itself. Any other id pins the session to that model.
{"type": "cancel"}
    # A *request* to stop, not an interrupt. The run finishes the frame it is
    # on, keeps the partial answer, closes any tool call that had not started,
    # bills the tokens actually generated, and ends with
    # `agent_done.reason == "cancelled"`. See `app/cancel.py`.
{"type": "edit_message", "turn_index": 2, "content": "...", "file_ids": [...]}
    # Replace user turn `turn_index` and re-run the conversation from there.
    # The turns that followed are preserved as a branch, never deleted.
{"type": "regenerate"}
    # Re-run the most recent user turn with a fresh model call. Uses whichever
    # model is selected *now*, which is not necessarily the one that answered
    # the first time. The previous answer is kept as a branch.
{"type": "switch_branch", "turn_index": 2, "version": 1}
    # Make a stored branch the live conversation again. Affects what the model
    # sees on the next turn, not just what is drawn.
{"type": "ping"}

CLIENT -> SERVER — the Agentic Loop section only
{"type": "approval_resolve", "approval_id": "apr_...",
                             "decision": "approved|edited|rejected",
                             "parameters": {...}}
    # `parameters` is only read for "edited", and only the keys present are
    # applied — a card that exposes two of five fields cannot blank the rest.
"""

from __future__ import annotations

import time
from typing import Any, Literal

PROTOCOL_VERSION = 1

EventType = Literal[
    "connected",
    "agent_thinking_start",
    "agent_thinking_delta",
    "agent_thinking_end",
    "agent_message_start",
    "agent_token",
    "agent_message_end",
    "tool_call_start",
    "tool_output_chunk",
    "tool_call_result",
    "file_changed",
    "file_tree",
    "preview_ready",
    "preview_error",
    "preview_stopped",
    "usage",
    "agent_done",
    "max_iterations",
    "tool_budget_reached",
    "model_changed",
    "error",
    # --- Agentic Loop only ---
    "agent_meta",
    "agent_paused",
    "agent_resumed",
    "agent_handoff",
    "credits",
]


def event(type_: EventType, **payload: Any) -> dict[str, Any]:
    """Build a wire event. `ts` is milliseconds since epoch."""
    return {"type": type_, "ts": int(time.time() * 1000), **payload}


# --- Convenience constructors, so node code never hand-rolls a dict --------


def connected(session_id: str) -> dict:
    return event("connected", session_id=session_id, protocol=PROTOCOL_VERSION)


def thinking_start() -> dict:
    return event("agent_thinking_start")


def thinking_delta(content: str) -> dict:
    return event("agent_thinking_delta", content=content)


def thinking_end() -> dict:
    return event("agent_thinking_end")


def message_start() -> dict:
    return event("agent_message_start")


def token(content: str) -> dict:
    return event("agent_token", content=content)


def message_end() -> dict:
    return event("agent_message_end")


def tool_call_start(tool: str, input_: dict, call_id: str) -> dict:
    return event("tool_call_start", tool=tool, input=input_, call_id=call_id)


def tool_output_chunk(call_id: str, stream: str, content: str) -> dict:
    return event("tool_output_chunk", call_id=call_id, stream=stream, content=content)


def tool_call_result(
    call_id: str, output: str, success: bool, meta: dict | None = None
) -> dict:
    return event(
        "tool_call_result",
        call_id=call_id,
        output=output,
        success=success,
        meta=meta or {},
    )


def file_changed(path: str, diff: str, content: str, change: str) -> dict:
    return event("file_changed", path=path, diff=diff, content=content, change=change)


def git_state(
    repo: bool,
    branch: str | None,
    status: list,
    log: list,
    path: str = "",
) -> dict:
    """The session's git state, as the Code panel's history sidebar shows it.

    Pushed rather than polled: it is emitted after any operation that can move
    history (a `git` tool call, or the UI's own commit action), so the panel is
    correct without asking. `repo=False` means no repository exists yet, which
    the panel renders as an offer to start one rather than as an empty list.
    """
    return event(
        "git_state", repo=repo, branch=branch or "", status=status, log=log, path=path
    )


def artifact(row: dict, created: bool) -> dict:
    """A document the model wrote beside the conversation.

    Two types rather than one with a flag, because the client does genuinely
    different things: a *created* artifact opens its panel, an *updated* one
    must not — stealing focus mid-read every time the model revises something
    is the fastest way to make a canvas unusable.

    The full content travels on the event. The alternative is an id the client
    then fetches, which is a round trip to display something the server already
    had in hand, on the one path where the user is watching and waiting.
    """
    return event(
        "artifact_created" if created else "artifact_updated",
        artifact_id=row.get("id") or "",
        key=row.get("artifact_key") or "",
        version=int(row.get("version") or 1),
        kind=row.get("kind") or "markdown",
        title=row.get("title") or "Untitled",
        language=row.get("language") or "",
        content=row.get("content") or "",
        created_by=row.get("created_by") or "agent",
    )


def file_tree(path: str, nodes: list) -> dict:
    return event("file_tree", path=path, nodes=nodes)


def preview_ready(url: str, port: int, command: str = "") -> dict:
    """The session's dev server is serving on a public URL.

    Emitted once per successful ``start_dev_server``. The URL is E2B's
    forwarded host for ``port`` on this session's sandbox; it stops resolving
    the moment the sandbox is torn down, which is why the client also listens
    for :func:`preview_stopped`.
    """
    return event("preview_ready", url=url, port=port, command=command)


def preview_error(message: str, fatal: bool = True, port: int | None = None) -> dict:
    """Something went wrong with the preview.

    ``fatal=True`` means there is nothing left to show — the process exited or
    never came up, and the client should drop to its stopped state. ``False``
    means the server is still serving but reported a build/runtime error, so
    the iframe stays and the message is raised as a banner over it.
    """
    return event("preview_error", message=message, fatal=fatal, port=port)


def preview_stopped(port: int | None = None, reason: str = "") -> dict:
    """The preview was taken down deliberately (new server, or teardown)."""
    return event("preview_stopped", port=port, reason=reason)


def usage(input_tokens: int, output_tokens: int, cost_estimate: float) -> dict:
    return event(
        "usage",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=cost_estimate,
    )


def agent_done(iterations: int, reason: str = "end_turn") -> dict:
    return event("agent_done", iterations=iterations, reason=reason)


def tool_budget_reached(agent_name: str, budget: int, used: int) -> dict:
    """This agent has spent its per-turn tool-call allowance.

    Not an error and not the end of the run: the graph takes the tool schemas
    away and asks for a write-up, so the user still gets the partial result.
    The frame exists so the UI can say *why* the answer is partial — a run that
    quietly stops searching looks identical to one that decided it was done.
    """
    return event(
        "tool_budget_reached",
        agent_name=agent_name,
        budget=budget,
        used=used,
        message=(
            f"{agent_name} reached its {budget}-tool-call limit for this "
            "request (a cost control), so it is writing up what it found "
            "rather than continuing to search. Send another message to carry "
            "on from here."
        ),
    )


def credit_ceiling(spent: float, ceiling: float) -> dict:
    """This turn hit its spending limit and is wrapping up.

    Deliberately shaped like `tool_budget_reached` rather than like an error:
    the turn is not failing, it is being cut short with its findings intact,
    and the message has to say which of those two happened.
    """
    return event(
        "credit_ceiling",
        spent=round(spent, 2),
        ceiling=ceiling,
        message=(
            f"This turn reached its {ceiling:.0f}-credit limit (spent "
            f"{spent:.0f}). I stopped making tool calls and wrote up what I had. "
            "Send another message to carry on."
        ),
    )


def max_iterations(iterations: int) -> dict:
    return event(
        "max_iterations",
        iterations=iterations,
        message=(
            f"I hit the {iterations}-step limit for a single task, so I stopped "
            "to avoid running away. This task may be too complex for one pass — "
            "send another message if you want me to keep going."
        ),
    )


def model_changed(
    model_id: str,
    name: str,
    supports_tools: bool,
    available: bool,
    note: str | None = None,
    routing_mode: str = "manual",
    routing_hint: str | None = None,
    reason: str | None = None,
    fallback_from: str | None = None,
) -> dict:
    """Announce the model now in play.

    Sent on connect, on a manual switch, in auto mode whenever the router moves
    the session to a different model, and when a call *failed over* to another
    model. ``reason`` is the human-readable half of ``routing_hint`` ("code
    editing"), which is what the chat trace shows; the raw hint is there for
    the client to key off.

    ``fallback_from`` is set only on the last of those: it names the model
    whose call failed, and its presence is how the client tells an involuntary
    switch from a routing decision. Both land in the same toast — a user does
    not care about the distinction in the moment — but they are worded
    differently, and only a fallback is worth flagging as a problem.
    """
    return event(
        "model_changed",
        model_id=model_id,
        name=name,
        supports_tools=supports_tools,
        available=available,
        note=note or "",
        routing_mode=routing_mode,
        routing_hint=routing_hint or "",
        reason=reason or "",
        fallback_from=fallback_from or "",
    )


def error(message: str) -> dict:
    return event("error", message=message)


def branches(entries: list[dict]) -> dict:
    """Which turns have been edited, and what versions each one has.

    Sent on connect and after every operation that can change the set: an
    edit, a regenerate, a branch switch. Always the whole picture rather than a
    delta — there are at most a handful of entries even in a heavily edited
    session, and a delta protocol for something this small is a bug farm.
    """
    return event("branches", branches=entries)


def history_replaced(
    reason: str, turn_index: int = -1, content: str = ""
) -> dict:
    """The server's copy of the conversation has been rewritten.

    ``turn_index`` and ``content`` are what let the client redraw *without*
    refetching, and they exist because refetching here is a race the client
    loses. An edit truncates the checkpoint and immediately starts a new run,
    so a client that answers this frame with "fetch me the transcript" reads a
    checkpoint mid-rewrite: it comes back either empty or still holding the
    turns that were just cut, and the new reply then streams on top of
    whichever it got. The frame now says exactly what changed, so the client
    cuts its own transcript at the same place instead of guessing.

    For ``reason == "branch"`` both are omitted and the client refetches. There
    is no run to race there, and that path replaces an arbitrary suffix with a
    stored one — which is not expressible as a truncation.
    """
    return event(
        "history_replaced",
        reason=reason,
        turn_index=turn_index,
        content=content,
    )


# ---------------------------------------------------------------------------
# The Agentic Loop
# ---------------------------------------------------------------------------
# These only ever travel on `/ws/agent/{session_id}`. Adding them here rather
# than in a second contract file is deliberate: there is one event vocabulary
# for the whole product, and a second one would drift.


def agent_meta(
    agent_id: str,
    name: str,
    role: str,
    icon: str,
    accent: str,
    tools: list[dict],
    credits: dict | None = None,
) -> dict:
    """Announce which specialist this session belongs to. Sent on connect.

    ``tools`` carries each tool's configured state, so the client can show a
    "not configured" chip up front instead of the user discovering it by
    spending a turn on a tool that cannot run.
    """
    return event(
        "agent_meta",
        agent_id=agent_id,
        name=name,
        role=role,
        icon=icon,
        accent=accent,
        tools=tools,
        credits=credits or {},
    )


def agent_paused(
    approval_id: str,
    agent_id: str,
    action: str,
    summary: str,
    parameters: dict,
    risk: str = "medium",
    editable: list[str] | None = None,
    options: list[dict] | None = None,
    cost_note: str = "",
    timeout_seconds: int = 600,
) -> dict:
    """The run is suspended pending a human decision.

    This is not advisory. The graph is awaiting a future that only an
    ``approval_resolve`` frame (or the timeout, which refuses) can complete.
    """
    return event(
        "agent_paused",
        approval_id=approval_id,
        agent_id=agent_id,
        action=action,
        summary=summary,
        parameters=parameters,
        risk=risk,
        editable=editable or [],
        options=options or [],
        cost_note=cost_note,
        timeout_seconds=timeout_seconds,
    )


def agent_resumed(approval_id: str, agent_id: str, decision: str) -> dict:
    return event(
        "agent_resumed",
        approval_id=approval_id,
        agent_id=agent_id,
        decision=decision,
    )


def agent_handoff(
    from_agent: str,
    next_agent: str,
    next_agent_name: str,
    reason: str,
    context: str = "",
) -> dict:
    """A routing directive from Agent 8, offered to the user as a button.

    Emitting this does not perform the handoff — nothing on the server acts on
    it. The human-in-the-loop requirement lives in that gap, so keep it there.
    """
    return event(
        "agent_handoff",
        from_agent=from_agent,
        next_agent=next_agent,
        next_agent_name=next_agent_name,
        reason=reason,
        context=context,
    )


def credits(balance: float, spent_this_turn: float, enabled: bool = True) -> dict:
    return event(
        "credits",
        balance=round(balance, 3),
        spent_this_turn=round(spent_this_turn, 3),
        enabled=enabled,
    )
