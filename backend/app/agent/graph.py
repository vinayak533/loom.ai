"""The agent loop, as a LangGraph StateGraph.

    START ──▶ agent ──(route)──┬──▶ bash_tool  ──┐
                              ├──▶ file_read  ──┤
                              ├──▶ file_write ──┼──(route)──▶ next tool | agent
                              ├──▶ file_edit  ──┤
                              └──▶ search_tool──┘
                              └──▶ END   (no tool_use, or iteration cap hit)

Two details worth knowing:

1. The model can request several tools in one turn. The node it routes to drains
   the entire `pending` list in one visit — concurrently when every call in the
   batch is read-only — and returns to `agent` with `pending` empty. The
   `agent` node flushes the buffered `tool_results` into a single user message
   on entry, which is what the API requires. (`route_after_tool` still exists
   and still loops, so a node that leaves work behind is handled correctly.)

2. The iteration cap is checked *before* the model call, so hitting it costs
   nothing and the run ends with `stop_reason == "max_iterations"`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app import events as ev
from app import preamble
from app.agent.prompts import SYSTEM_PROMPT
from app.cancel import is_stopping
from app.agent.state import AgentState
from app.agent.task_classifier import classify_task, explain
from app.config import get_settings
from app.db import repository
from app.emitter import Emitter, emitter_from_config
from app.llm_router import (
    HINT_REASON,
    FallbackNotice,
    ModelCallError,
    ModelUnavailableError,
    auto_pool_available,
    auto_route,
    effective_default_model,
    display_name,
    estimate_cost,
    is_available,
    model_for_hint,
    model_meta,
    stream_with_fallback,
)
from app.tools.impl import run_tool
from app.credits import charge_llm
from app.turnstop import (
    STOPPED_TOOL_TEXT,
    approx_tokens,
    partial_assistant_content,
    stopped_result_block,
)
from app.tools.schemas import LIST_FILES, READ_FILE, TOOL_NODE, TOOLS, WEB_SEARCH

log = logging.getLogger(__name__)

TOOL_NODES = ["bash_tool", "file_read", "file_write", "file_edit", "search_tool"]


# ---------------------------------------------------------------------------
# auto routing
# ---------------------------------------------------------------------------


def _resolve_auto(state: AgentState) -> tuple[str, str]:
    """Pick this turn's model in auto mode. Returns ``(model_id, hint)``.

    Classification is re-run on every agent iteration, not once per user
    message, so the route can follow the task as it develops — a thread that
    starts as a question and turns into a series of file edits moves onto the
    editing model at the point it actually starts editing.

    With no OpenCode key there is no task-routing pool, so this degrades to the
    older section-based route rather than failing. Startup already logged that
    loudly (see ``llm_router.validate_keys``).
    """
    if not auto_pool_available():
        return auto_route("chat"), ""

    hint = classify_task(dict(state))
    model_id = model_for_hint(hint)
    if log.isEnabledFor(logging.DEBUG):
        log.debug("Auto route -> %s %s", model_id, explain(dict(state)))
    return model_id, hint


def iterations_of(state: AgentState) -> int:
    """This turn's iteration count, without consuming another one.

    A stop is not an iteration: the node returns before the model is called,
    so incrementing here would make a stopped turn report one more step than
    it took.
    """
    return int(state.get("iterations") or 0)


# ---------------------------------------------------------------------------
# agent node
# ---------------------------------------------------------------------------


async def agent_node(state: AgentState, config: RunnableConfig) -> dict:
    settings = get_settings()
    emitter = emitter_from_config(config)
    session_id = state["session_id"]
    section = state.get("section") or "chat"

    # --- model selection -------------------------------------------------
    # In manual mode `model_id` is the user's pick. In auto mode it is
    # whatever the previous turn resolved to, and gets recomputed here.
    previous_model = state.get("model_id") or effective_default_model()
    routing_mode = state.get("routing_mode") or "manual"
    routing_hint = ""

    if routing_mode == "auto":
        model_id, routing_hint = _resolve_auto(state)
    else:
        model_id = previous_model

    try:
        meta = model_meta(model_id)
        if not is_available(model_id):
            raise ModelUnavailableError(model_id)
    except ModelUnavailableError:
        # Fall back to the default *for this turn only* so the run does not
        # crash; the user was warned when they selected it (or the default is
        # simply unavailable, in which case the error surfaces below).
        # The configured default can itself be unavailable (a
        # `DEFAULT_MODEL_ID` whose provider key is unset), so falling back to
        # it verbatim just re-raises the same failure a line later. Resolve to
        # a model that actually has a key instead.
        model_id = effective_default_model()
        meta = model_meta(model_id)

    # Tell the user when auto routing moved them, and only then — a steady
    # route must not narrate itself on every iteration.
    if emitter and routing_mode == "auto" and model_id != previous_model:
        emitter.emit(
            ev.model_changed(
                model_id=model_id,
                name=display_name(model_id),
                supports_tools=meta["supports_tools"],
                available=True,
                routing_mode="auto",
                routing_hint=routing_hint,
                reason=HINT_REASON.get(routing_hint, ""),
            )
        )

    messages = list(state.get("messages") or [])

    # Flush buffered tool results into the single user turn the format expects.
    buffered = state.get("tool_results") or []
    if buffered:
        messages.append({"role": "user", "content": buffered})

    # --- stop requested while the tools were running ----------------------
    # Checked *after* the flush above and *before* the model call, which is the
    # only ordering that leaves valid history behind. The flush is what pairs
    # every `tool_use` block the previous iteration emitted with its
    # `tool_result`; returning before it would strand them, and the next
    # request on this session would be rejected for exactly that. Returning
    # after it ends the turn on a complete assistant/tool exchange that the
    # model can be handed again verbatim.
    if is_stopping(session_id):
        # Close on an assistant turn rather than on the flushed tool results.
        # Two reasons, and the second is the one that bites: the transcript
        # would otherwise end with a row of tool cards and no word about why
        # nothing followed them, and the conversation would end on a `user`
        # entry — so the next turn would append a second consecutive user
        # message. Providers vary in how forgiving they are about that, and
        # depending on the forgiving ones is not a plan.
        messages.append(
            {"role": "assistant", "content": partial_assistant_content("")}
        )
        repository.fire(
            repository.add_message(
                session_id, "assistant", partial_assistant_content("")
            )
        )
        return {
            "messages": messages,
            "model_id": model_id,
            "routing_mode": routing_mode,
            "routing_hint": routing_hint,
            "tool_results": [],
            "pending": [],
            "iterations": iterations_of(state),
            "stop_reason": "cancelled",
            "usage": dict(state.get("usage") or {}),
            "credits_spent": float(state.get("credits_spent") or 0.0),
        }

    iterations = int(state.get("iterations") or 0) + 1
    if iterations > settings.max_agent_iterations:
        if emitter:
            emitter.emit(ev.max_iterations(settings.max_agent_iterations))
        return {
            "messages": messages,
            "model_id": model_id,
            "routing_mode": routing_mode,
            "routing_hint": routing_hint,
            "tool_results": [],
            "pending": [],
            "iterations": iterations,
            "stop_reason": "max_iterations",
        }

    # --- per-turn credit ceiling -----------------------------------------
    # Checked between iterations rather than only at the start, because the
    # cost of a turn is not knowable before it runs — which is exactly how a
    # balance of 19.64 ended one turn at -28.27. When the ceiling is crossed
    # the turn is not severed: tools are withdrawn and the model gets one last
    # call to write up what it already has, the same shape as Agent 7's
    # tool-call budget.
    spent_this_turn = float(state.get("credits_spent") or 0.0)
    ceiling = settings.credit_turn_ceiling
    over_ceiling = (
        settings.credits_enabled and ceiling > 0 and spent_this_turn >= ceiling
    )
    if over_ceiling:
        if emitter:
            emitter.emit(ev.credit_ceiling(spent_this_turn, ceiling))
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You have reached this turn's spending limit of "
                            f"{ceiling:.0f} credits. Stop calling tools now. "
                            "Write up what you have established so far, and say "
                            "plainly that you stopped early because the turn hit "
                            "its credit limit rather than because the work was "
                            "finished."
                        ),
                    }
                ],
            }
        )

    # --- automatic fallback ----------------------------------------------
    # If the provider fails for its own reasons — a 429, a 5xx, a dead
    # gateway — the router retries the same request on another model rather
    # than ending the turn. Which model ends up answering is recorded here and
    # read back as `answered_by` below, so cost and credits follow the model
    # that actually produced the response.
    #
    # This applies to a manual pick too. The user chose a model, but they
    # asked a question; a selection that cannot answer it right now is not
    # worth honouring to the point of failing. It is a rescue for this one
    # call, though — the session stays on their choice either way, and tries
    # it again next turn.
    fallback_state: dict[str, Any] = {"model_id": model_id, "notices": []}

    def _on_fallback(notice: FallbackNotice) -> None:
        fallback_state["model_id"] = notice.next_model_id
        fallback_state["notices"].append(notice)
        if emitter:
            nxt_meta = model_meta(notice.next_model_id)
            emitter.emit(
                ev.model_changed(
                    model_id=notice.next_model_id,
                    name=notice.next_name,
                    supports_tools=nxt_meta["supports_tools"],
                    available=True,
                    note=f"{notice.failed_name} {notice.reason}",
                    routing_mode=routing_mode,
                    routing_hint=routing_hint,
                    reason=notice.reason,
                    fallback_from=notice.failed_model_id,
                )
            )

    # The account's memory and the project's standing instructions, in front of
    # this surface's own prompt. Composed per turn rather than cached on the
    # state: a preference changed in Settings has to take effect on the next
    # message, not on the next session.
    system_prompt = await preamble.compose(
        SYSTEM_PROMPT,
        user_id=state.get("user_id") or None,
        project_id=state.get("project_id") or None,
    )

    try:
        stream = stream_with_fallback(
            model_id,
            messages=messages,
            tools=[] if over_ceiling else (TOOLS if meta["supports_tools"] else []),
            system=system_prompt,
            on_fallback=_on_fallback,
            section=section,
        )
        response = None
        # What the provider has actually produced so far. Kept outside the loop
        # because a stop can land at any point inside it, and this is the only
        # record of the answer that existed at that moment — the `done` frame
        # that carries the assembled message never arrives for a stopped
        # stream.
        partial_text = ""
        partial_thinking = ""
        stopped = False

        if emitter:
            async for se in stream:
                if se.kind == "thinking_start":
                    emitter.emit(ev.thinking_start())
                elif se.kind == "thinking_delta":
                    partial_thinking += se.content or ""
                    emitter.emit(ev.thinking_delta(se.content or ""))
                elif se.kind == "thinking_end":
                    emitter.emit(ev.thinking_end())
                elif se.kind == "text_start":
                    emitter.emit(ev.message_start())
                elif se.kind == "text_delta":
                    partial_text += se.content or ""
                    emitter.emit(ev.token(se.content or ""))
                elif se.kind == "text_end":
                    emitter.emit(ev.message_end())
                elif se.kind == "done":
                    response = se.message
                # Between events, never inside one. A stop is honoured at the
                # next frame boundary rather than by tearing the generator
                # down, so the HTTP response is closed by `aclose()` below in
                # the ordinary way and no half-decoded frame is left behind.
                if is_stopping(session_id):
                    stopped = True
                    break
            if stopped:
                # Close the provider stream explicitly. Letting it fall out of
                # scope leaves the connection to the garbage collector, which
                # on a stopped generation means the provider keeps producing
                # (and charging for) tokens nobody will read.
                await _close_stream(stream)
        else:
            async for se in stream:
                if se.kind == "text_delta":
                    partial_text += se.content or ""
                elif se.kind == "done":
                    response = se.message
                if is_stopping(session_id):
                    stopped = True
                    break
            if stopped:
                await _close_stream(stream)
    except ModelUnavailableError as exc:
        if emitter:
            emitter.emit(ev.error(f"Model unavailable: {exc}"))
        return {
            "messages": messages,
            "model_id": model_id,
            "routing_mode": routing_mode,
            "routing_hint": routing_hint,
            "tool_results": [],
            "pending": [],
            "iterations": iterations,
            "stop_reason": "error",
        }
    except ModelCallError as exc:
        log.exception(
            "Model call failed for %s (last attempted: %s)",
            model_id, fallback_state["model_id"],
        )
        if emitter:
            # `exc` is the provider's own error and stays in the log above. For
            # an OpenRouter 402 that is ~900 characters of nested JSON, account
            # id included, and it was being written verbatim into the
            # transcript. `user_message()` says what happened and what fixes it.
            emitter.emit(ev.error(exc.user_message()))
        return {
            "messages": messages,
            "model_id": model_id,
            "routing_mode": routing_mode,
            "routing_hint": routing_hint,
            "tool_results": [],
            "pending": [],
            "iterations": iterations,
            "stop_reason": "error",
        }
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, run ends
        log.exception(
            "Model call failed for %s (last attempted: %s)",
            model_id, fallback_state["model_id"],
        )
        if emitter:
            emitter.emit(
                ev.error(
                    "The model call failed unexpectedly. See the server log "
                    f"for details ({type(exc).__name__})."
                )
            )
        return {
            "messages": messages,
            "model_id": model_id,
            "routing_mode": routing_mode,
            "routing_hint": routing_hint,
            "tool_results": [],
            "pending": [],
            "iterations": iterations,
            "stop_reason": "error",
        }

    # Two different things, deliberately kept apart from here on:
    #
    #   `model_id`   the session's *selection*. A fallback must not rewrite it.
    #                The user picked this model; one rate limit is not a reason
    #                to move them off it permanently, and the next turn should
    #                try their choice again now the provider may have
    #                recovered.
    #   `answered_by` the model that actually produced this response. Cost,
    #                credits and the token_usage row follow this one, or the
    #                user is billed at the failed model's rate for an answer it
    #                never produced.
    answered_by = fallback_state["model_id"]

    # --- the user stopped mid-stream --------------------------------------
    # Everything below this block assumes a completed response. A stop has none
    # — no `done` frame, so no assembled message, no `stop_reason`, and above
    # all no `usage`. It is finished here instead, and finished *properly*:
    # the partial answer becomes a real assistant turn in the conversation, it
    # is written to the transcript, and the output the provider did generate is
    # billed. Falling through to the `response is None` branch below would end
    # the turn as an error and discard the text the user is looking at.
    if stopped:
        content = partial_assistant_content(partial_text, partial_thinking)
        messages.append({"role": "assistant", "content": content})

        # An estimate, and recorded as one. The provider reports usage only on
        # the frame that never arrived, so the alternative is billing nothing
        # for tokens that were produced — which would make Stop a way to use
        # the product for free.
        est = {
            "input_tokens": approx_tokens(_prompt_text(messages[:-1])),
            "output_tokens": approx_tokens(partial_text + partial_thinking),
        }
        cost = estimate_cost(answered_by, est)
        totals = dict(state.get("usage") or {})
        totals["input_tokens"] = totals.get("input_tokens", 0) + est["input_tokens"]
        totals["output_tokens"] = totals.get("output_tokens", 0) + est["output_tokens"]
        totals["cost_estimate"] = round(totals.get("cost_estimate", 0.0) + cost, 6)

        spent_this_turn += await charge_llm(
            state.get("user_id") or None,
            session_id=session_id,
            agent_id=section,
            model_id=answered_by,
            cost_usd=cost,
        )

        if emitter:
            emitter.emit(
                ev.usage(
                    totals["input_tokens"],
                    totals["output_tokens"],
                    totals["cost_estimate"],
                )
            )

        repository.fire(
            repository.record_usage(
                session_id,
                answered_by,
                est["input_tokens"],
                est["output_tokens"],
                cost,
                model_id=answered_by,
                routing_mode=routing_mode,
                routing_hint=routing_hint,
            )
        )
        repository.fire(repository.add_message(session_id, "assistant", content))

        return {
            "messages": messages,
            "model_id": model_id,
            "routing_mode": routing_mode,
            "routing_hint": routing_hint,
            # No `pending`: any tool call the model had begun to emit is
            # incomplete and must not be executed. Dropping it here is what
            # keeps a stop from leaving an orphaned `tool_use` in the history.
            "pending": [],
            "tool_results": [],
            "iterations": iterations,
            "stop_reason": "cancelled",
            "usage": totals,
            "credits_spent": spent_this_turn,
        }

    if response is None:  # pragma: no cover - defensive
        if emitter:
            emitter.emit(ev.error("Model returned an empty response."))
        return {
            "messages": messages,
            "model_id": model_id,
            "routing_mode": routing_mode,
            "routing_hint": routing_hint,
            "tool_results": [],
            "pending": [],
            "iterations": iterations,
            "stop_reason": "error",
        }

    content = response.content
    messages.append({"role": "assistant", "content": content})

    # --- usage accounting (per model) ------------------------------------
    cost = estimate_cost(answered_by, response.usage)
    totals = dict(state.get("usage") or {})
    totals["input_tokens"] = totals.get("input_tokens", 0) + (
        response.usage.get("input_tokens", 0)
    )
    totals["output_tokens"] = totals.get("output_tokens", 0) + (
        response.usage.get("output_tokens", 0)
    )
    totals["cost_estimate"] = round(totals.get("cost_estimate", 0.0) + cost, 6)

    # Debited per call rather than once at the end of the turn: a run that is
    # cancelled mid-loop still consumed what it already spent, and only billing
    # completed turns would make cancellation a way to use the product free.
    spent_this_turn += await charge_llm(
        state.get("user_id") or None,
        session_id=session_id,
        # The section, not the literal string "chat". Every Code turn was
        # being written into the credit ledger as Chat spend, which made the
        # per-surface breakdown wrong for the surface that costs the most.
        agent_id=section,
        model_id=answered_by,
        cost_usd=cost,
    )

    if emitter:
        emitter.emit(
            ev.usage(
                totals["input_tokens"], totals["output_tokens"], totals["cost_estimate"]
            )
        )

    # Persistence is deliberately not awaited: these are two Supabase round
    # trips sitting between the model's last token and the first tool call,
    # and nothing downstream reads what they write.
    repository.fire(
        repository.record_usage(
            session_id,
            response.model_name,
            response.usage.get("input_tokens", 0),
            response.usage.get("output_tokens", 0),
            cost,
            model_id=answered_by,
            routing_mode=routing_mode,
            routing_hint=routing_hint,
        )
    )
    repository.fire(repository.add_message(session_id, "assistant", content))

    pending = [] if over_ceiling else [
        b for b in content if b.get("type") == "tool_use"
    ]
    if over_ceiling:
        stop_reason = "credit_ceiling"
    else:
        stop_reason = "tool_use" if pending else (response.stop_reason or "end_turn")

    return {
        "messages": messages,
        "model_id": model_id,
        "routing_mode": routing_mode,
        "routing_hint": routing_hint,
        "pending": pending,
        "tool_results": [],
        "iterations": iterations,
        "stop_reason": stop_reason,
        "usage": totals,
        "credits_spent": spent_this_turn,
    }


def route_from_agent(state: AgentState) -> str:
    # "cancelled" belongs here with the rest: a stopped turn has already had
    # its partial answer written and its tool calls closed, so routing onward
    # would start work the user just asked to end.
    if state.get("stop_reason") in {
        "max_iterations",
        "error",
        "refusal",
        "cancelled",
    }:
        return END
    pending = state.get("pending") or []
    if not pending:
        return END
    return TOOL_NODE.get(pending[0]["name"], "bash_tool")


# ---------------------------------------------------------------------------
# tool nodes
# ---------------------------------------------------------------------------


#: Tools that only observe. A batch made up entirely of these has no ordering
#: constraint between its members, so they can run at the same time. Anything
#: that touches the filesystem stays strictly sequential — two writes to one
#: path, or a read racing the write that produced it, is a correctness bug
#: that no amount of saved latency pays for.
READ_ONLY_TOOLS = {READ_FILE, LIST_FILES, WEB_SEARCH}


async def _execute_call(
    session_id: str, block: dict, emitter: Emitter | None
) -> dict:
    """Run one `tool_use` block and return its `tool_result` block."""
    name = block["name"]
    args = block.get("input") or {}
    call_id = block["id"]

    if emitter:
        emitter.emit(ev.tool_call_start(name, args, call_id))
    repository.fire(
        repository.add_message(
            session_id,
            "tool_use",
            None,
            {"tool": name, "input": args, "call_id": call_id},
        )
    )

    result = await run_tool(name, session_id, args, call_id, emitter)

    if emitter:
        for side in result.events:
            emitter.emit(side)
        emitter.emit(
            ev.tool_call_result(call_id, result.output, result.success, result.meta)
        )
    repository.fire(
        repository.add_message(
            session_id,
            "tool_result",
            None,
            {
                "call_id": call_id,
                "tool": name,
                "success": result.success,
                "output": result.output[:20000],
                "meta": result.meta,
            },
        )
    )

    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": result.output or "(no output)",
        "is_error": not result.success,
    }


async def _execute_batch(
    session_id: str, pending: list[dict], emitter: Emitter | None
) -> list[dict]:
    """Execute a whole turn's tool calls, concurrently when that is safe.

    A stop request is honoured *between* calls, never inside one. A tool that
    has already started is allowed to finish: half of a `file_write` or a
    `bash_execute` is a worse outcome than one extra second of work, and the
    sandbox has no way to undo it. Calls that have not started are skipped and
    answered with :func:`stopped_result_block`, so every `tool_use` block still
    gets a `tool_result` and the conversation stays replayable.
    """
    if len(pending) == 1 or not all(
        b.get("name") in READ_ONLY_TOOLS for b in pending
    ):
        out: list[dict] = []
        for block in pending:
            if is_stopping(session_id):
                if emitter:
                    # The card was never opened for this call, so there is no
                    # `tool_call_start` to answer. Announcing it as a settled
                    # skip is what keeps the trace honest about which calls the
                    # stop actually pre-empted.
                    emitter.emit(
                        ev.tool_call_start(
                            block.get("name") or "tool",
                            block.get("input") or {},
                            block["id"],
                        )
                    )
                    emitter.emit(
                        ev.tool_call_result(
                            block["id"], STOPPED_TOOL_TEXT, True, {"stopped": True}
                        )
                    )
                out.append(stopped_result_block(block["id"]))
                continue
            out.append(await _execute_call(session_id, block, emitter))
        return out

    results = await asyncio.gather(
        *(_execute_call(session_id, b, emitter) for b in pending),
        # A single failure must not strand the siblings: every `tool_use` the
        # model emitted needs a matching `tool_result` or the next request is
        # rejected outright.
        return_exceptions=True,
    )
    out: list[dict] = []
    for block, result in zip(pending, results):
        if isinstance(result, asyncio.CancelledError):
            raise result  # the run was cancelled; let it unwind
        if isinstance(result, BaseException):
            log.exception(
                "Tool %s failed", block.get("name"), exc_info=result
            )
            out.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": (
                        f"Error: tool `{block.get('name')}` raised "
                        f"{type(result).__name__}: {result}"
                    ),
                    "is_error": True,
                }
            )
        else:
            out.append(result)
    return out


def make_tool_node(node_name: str):
    async def tool_node(state: AgentState, config: RunnableConfig) -> dict:
        emitter: Emitter | None = emitter_from_config(config)
        session_id = state["session_id"]
        pending = list(state.get("pending") or [])
        if not pending:
            return {}

        # The whole batch is drained in this one node visit. Executing a single
        # call per visit meant looping back through the router for each one,
        # and LangGraph checkpoints after every node — so a five-tool turn paid
        # five full-state writes (tens of KB each, a database round trip in
        # production) for no benefit, since `agent` cannot resume until the
        # last result is in anyway.
        tool_results = list(state.get("tool_results") or [])
        tool_results.extend(await _execute_batch(session_id, pending, emitter))
        return {"pending": [], "tool_results": tool_results}

    tool_node.__name__ = node_name
    return tool_node


def route_after_tool(state: AgentState) -> str:
    """Drain the rest of this turn's parallel tool calls, then loop to `agent`."""
    pending = state.get("pending") or []
    if pending:
        return TOOL_NODE.get(pending[0]["name"], "bash_tool")
    return "agent"


# ---------------------------------------------------------------------------
# graph construction
# ---------------------------------------------------------------------------


def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    for node in TOOL_NODES:
        builder.add_node(node, make_tool_node(node))

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        route_from_agent,
        {**{n: n for n in TOOL_NODES}, END: END},
    )
    for node in TOOL_NODES:
        builder.add_conditional_edges(
            node,
            route_after_tool,
            {**{n: n for n in TOOL_NODES}, "agent": "agent"},
        )

    return builder.compile(checkpointer=checkpointer)


def build_user_message(text: str, blocks: list[dict] | None = None) -> dict:
    """A user turn, with any uploaded PDFs/images placed before the text.

    A vision-capable model reads PDFs as `document` blocks and images as `image`
    blocks — there is no OCR step.
    """
    content: list[dict] = list(blocks or [])
    content.append({"type": "text", "text": text})
    return {"role": "user", "content": content}


def summarise_for_log(message: dict) -> str:
    return json.dumps(message)[:400]


async def _close_stream(stream) -> None:
    """Close a provider stream that is being abandoned mid-flight.

    Async generators expose `aclose()`; anything else is left alone rather than
    guessed at. Failures are swallowed on purpose — the turn is already ending
    and a teardown error is not worth surfacing over the answer the user is
    reading.
    """
    close = getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:  # noqa: BLE001
        log.debug("Stream close failed on stop", exc_info=True)


def _prompt_text(messages: list[dict]) -> str:
    """Every piece of text in the prompt, for estimating input tokens.

    Only used on the stopped path, where the provider never reported real
    usage. Image and document blocks are skipped: their token cost is not a
    function of any text they carry, and guessing at it would be worse than
    the small under-count of leaving them out.
    """
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("text", "reasoning", "thinking"):
                parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_result":
                inner = block.get("content")
                parts.append(inner if isinstance(inner, str) else json.dumps(inner))
            elif block.get("type") == "tool_use":
                parts.append(json.dumps(block.get("input") or {}))
    return "\n".join(parts)
