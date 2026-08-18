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

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app import events as ev
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.task_classifier import classify_task, explain
from app.config import get_settings
from app.db import repository
from app.emitter import Emitter, emitter_from_config
from app.llm_router import (
    HINT_REASON,
    ModelCallError,
    ModelUnavailableError,
    auto_pool_available,
    auto_route,
    call_model,
    display_name,
    estimate_cost,
    is_available,
    model_for_hint,
    model_meta,
)
from app.tools.impl import run_tool
from app.credits import charge_llm
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


# ---------------------------------------------------------------------------
# agent node
# ---------------------------------------------------------------------------


async def agent_node(state: AgentState, config: RunnableConfig) -> dict:
    settings = get_settings()
    emitter = emitter_from_config(config)
    session_id = state["session_id"]

    # --- model selection -------------------------------------------------
    # In manual mode `model_id` is the user's pick. In auto mode it is
    # whatever the previous turn resolved to, and gets recomputed here.
    previous_model = state.get("model_id") or settings.default_model_id
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
        model_id = settings.default_model_id
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

    try:
        stream = call_model(
            model_id,
            messages=messages,
            tools=[] if over_ceiling else (TOOLS if meta["supports_tools"] else []),
            system=SYSTEM_PROMPT,
            stream=True,
        )
        response = None
        if emitter:
            async for se in stream:
                if se.kind == "thinking_start":
                    emitter.emit(ev.thinking_start())
                elif se.kind == "thinking_delta":
                    emitter.emit(ev.thinking_delta(se.content or ""))
                elif se.kind == "thinking_end":
                    emitter.emit(ev.thinking_end())
                elif se.kind == "text_start":
                    emitter.emit(ev.message_start())
                elif se.kind == "text_delta":
                    emitter.emit(ev.token(se.content or ""))
                elif se.kind == "text_end":
                    emitter.emit(ev.message_end())
                elif se.kind == "done":
                    response = se.message
        else:
            async for se in stream:
                if se.kind == "done":
                    response = se.message
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
        log.exception("Model call failed for %s", model_id)
        if emitter:
            emitter.emit(ev.error(f"Model call failed: {exc}"))
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
        log.exception("Model call failed for %s", model_id)
        if emitter:
            emitter.emit(ev.error(f"Model call failed: {exc}"))
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
    cost = estimate_cost(model_id, response.usage)
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
        agent_id="chat",
        model_id=model_id,
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
            model_id=model_id,
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
    if state.get("stop_reason") in {"max_iterations", "error", "refusal"}:
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
    """Execute a whole turn's tool calls, concurrently when that is safe."""
    if len(pending) == 1 or not all(
        b.get("name") in READ_ONLY_TOOLS for b in pending
    ):
        return [await _execute_call(session_id, b, emitter) for b in pending]

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
