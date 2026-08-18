"""The shared specialist graph.

    START ──▶ agent ──(has tool calls?)──▶ tools ──▶ agent ──▶ … ──▶ END

One template, ten agents. Everything that differs between them — the system
prompt, the tool schemas, the dispatch allowlist — is read from
:mod:`app.agents.registry` using the ``agent_id`` carried in state, so adding
an eleventh specialist is a registry entry and a tool module, not a new graph.

How this differs from the Code section's graph
----------------------------------------------
The Code graph has one node per tool family, because its tools have real
ordering constraints against a shared filesystem — two writes to one path must
not race. These agents have no shared mutable substrate: a token count, a
readability score and a search are independent, so there is a single ``tools``
node and the batch runs concurrently unless it contains something that must
not.

What is deliberately the same
-----------------------------
The event stream. Every frame this emits — ``agent_token``,
``tool_call_start``, ``tool_call_result``, ``usage`` — is the same one the Code
section emits, produced by the same constructors in :mod:`app.events`. That is
what lets the Agents UI reuse the existing tool-call cards and streaming text
rather than reimplementing them.
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app import events as ev
from app.agents import registry
from app.agents.state import SpecialistState
from app.agents.tool_registry import run as run_agent_tool, schemas_for
from app.agents.tools.base import ToolContext
from app.config import get_settings
from app.credits import charge_llm, charge_tool
from app.db import repository
from app.emitter import Emitter, emitter_from_config
from app.llm_router import (
    HINT_REASON,
    AUTO_MODEL_ID,
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

log = logging.getLogger(__name__)

#: Tools that only read or compute. A batch made entirely of these has no
#: ordering constraint between its members, so they run at once. Anything that
#: spends money, sends something, or pauses for a human stays strictly
#: sequential — two approval cards racing each other would be unreadable, and
#: two image renders firing together is a double charge nobody asked for.
CONCURRENT_SAFE = {
    "parse_source", "chunk_document", "count_tokens",
    "list_email_templates", "score_subject_line", "check_spam_words",
    "lookup_tailwind", "lookup_lucide_icons",
    "normalize_aspect_ratio", "list_style_modifiers",
    "search_web", "read_url", "rank_domain_trust",
    "keyword_density", "readability_score",
    "validate_json_schema", "match_patterns", "evaluate_conditions",
    "parse_ast", "list_pending_approvals",
}

#: Appended to the system prompt for the one model call that follows an
#: exhausted tool budget. It is an instruction rather than a truncation: the
#: user paid for the calls already made, so the run's job now is to turn them
#: into an answer and to be honest that the answer is partial.
_WRAP_UP = """
## STOP SEARCHING — tool budget spent
You have used your entire tool-call allowance for this request ({used} of
{budget}), and your tools have been withdrawn for the rest of this turn. There
are no more calls available; asking for one is not possible.

Write up now, from what you already have:
- Answer with the evidence in this conversation. Do not describe a source you
  did not actually read.
- Anything you could not confirm is `UNVERIFIABLE` — say what was missing.
- Open your reply by stating plainly that you stopped early because you reached
  the {budget}-tool-call limit for one request, and name what you would have
  checked next. Do not present a truncated check as a complete one.
"""


def _budget(agent: registry.AgentDef) -> int:
    """This agent's per-turn tool-call ceiling. ``0`` means uncapped."""
    return max(0, int(agent.max_tool_calls_per_turn or 0))


def _resolve_auto(state: SpecialistState) -> tuple[str, str]:
    """Pick this turn's model in auto mode. Returns ``(model_id, hint)``.

    Reuses the Code section's classifier verbatim: it reads message shape and
    iteration count, both of which mean the same thing here. With no OpenCode
    key there is no pool to route across, so it degrades to the section table
    exactly as the other graph does.
    """
    if not auto_pool_available():
        return auto_route("chat"), ""
    from app.agent.task_classifier import classify_task

    hint = classify_task(dict(state))
    return model_for_hint(hint), hint


# ---------------------------------------------------------------------------
# agent node
# ---------------------------------------------------------------------------


def _stopped(state: SpecialistState, messages: list[dict], model_id: str,
             routing_mode: str, routing_hint: str, iterations: int,
             reason: str) -> dict:
    """The one shape every early return from `agent_node` takes."""
    return {
        "messages": messages,
        "model_id": model_id,
        "routing_mode": routing_mode,
        "routing_hint": routing_hint,
        "tool_results": [],
        "pending": [],
        "iterations": iterations,
        "stop_reason": reason,
    }


async def agent_node(state: SpecialistState, config: RunnableConfig) -> dict:
    settings = get_settings()
    emitter = emitter_from_config(config)
    session_id = state["session_id"]
    agent_id = state.get("agent_id") or ""

    try:
        agent = registry.get_agent(agent_id)
    except registry.UnknownAgent:
        if emitter:
            emitter.emit(ev.error(f"Unknown agent `{agent_id}`."))
        return _stopped(state, list(state.get("messages") or []), "", "manual", "", 0, "error")

    # --- model selection --------------------------------------------------
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
        model_id = settings.default_model_id
        meta = model_meta(model_id)

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
    buffered = state.get("tool_results") or []
    if buffered:
        messages.append({"role": "user", "content": buffered})

    iterations = int(state.get("iterations") or 0) + 1
    if iterations > settings.max_agent_iterations:
        if emitter:
            emitter.emit(ev.max_iterations(settings.max_agent_iterations))
        return _stopped(
            state, messages, model_id, routing_mode, routing_hint,
            iterations, "max_iterations",
        )

    # A specialist that cannot call tools is a specialist stripped of its
    # specialism, so this is worth saying out loud rather than silently
    # degrading to a chat model with an unusual system prompt.
    tools = schemas_for(agent.tools) if meta["supports_tools"] else []
    system = agent.system_prompt()

    # --- tool budget ------------------------------------------------------
    # Enforced by withholding the schemas rather than by cutting the run off.
    # The calls already made were paid for, so the cheapest useful thing left
    # is one more model call that turns them into an answer — and the prompt
    # below requires that answer to say it is partial. A silent truncation
    # would leave the user with a confident-looking half-check.
    budget = _budget(agent)
    used = int(state.get("tool_calls_used") or 0)
    exhausted = bool(budget) and used >= budget

    # --- per-turn credit ceiling -----------------------------------------
    # A tool-call budget bounds how many calls a turn makes, not what they
    # cost: this suite saw agent 7 finish inside its 10-call budget having
    # spent 314 credits, because three of those calls were expensive ones.
    # The ceiling is the second bound, enforced the same way as the budget -
    # schemas withheld, one wrap-up call, never a silent truncation.
    spent_so_far = float(state.get("credits_spent") or 0.0)
    ceiling = settings.credit_turn_ceiling
    over_ceiling = (
        settings.credits_enabled and ceiling > 0 and spent_so_far >= ceiling
    )

    if exhausted or over_ceiling:
        tools = []
        system = (
            f"{system}\n\n## STOP - this turn's credit limit is spent\n"
            f"You have spent {spent_so_far:.0f} of this turn's "
            f"{ceiling:.0f}-credit limit and your tools have been withdrawn "
            "for the rest of it. Write up what you already established, say "
            "plainly that you stopped because the turn reached its credit "
            "limit rather than because the work was done, and name what you "
            "would have checked next."
            if over_ceiling
            else f"{system}\n\n{_WRAP_UP.format(used=used, budget=budget).strip()}"
        )
        if over_ceiling:
            log.info(
                "Agent %s hit the per-turn credit ceiling (%.1f/%.0f) on "
                "session %s — writing up.",
                agent_id, spent_so_far, ceiling, session_id,
            )
        else:
            log.info(
                "Agent %s hit its tool budget (%d/%d) on session %s — writing up.",
                agent_id, used, budget, session_id,
            )
        if emitter:
            emitter.emit(
                ev.credit_ceiling(spent_so_far, ceiling)
                if over_ceiling
                else ev.tool_budget_reached(agent.name, budget, used)
            )

    if agent.tools and not meta["supports_tools"] and emitter and iterations == 1:
        emitter.emit(
            ev.error(
                f"{display_name(model_id)} cannot use tools, so {agent.name} is "
                "answering from its persona alone — its tools are unavailable "
                "while this model is selected."
            )
        )

    try:
        stream = call_model(
            model_id,
            messages=messages,
            tools=tools,
            system=system,
            stream=True,
        )
        response = None
        async for se in stream:
            if emitter:
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
            if se.kind == "done":
                response = se.message
    except (ModelUnavailableError, ModelCallError) as exc:
        log.warning("Specialist %s model call failed: %s", agent_id, exc)
        if emitter:
            emitter.emit(ev.error(str(exc)))
        return _stopped(state, messages, model_id, routing_mode, routing_hint, iterations, "error")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, run ends
        log.exception("Specialist %s model call failed", agent_id)
        if emitter:
            emitter.emit(ev.error(f"Model call failed: {exc}"))
        return _stopped(state, messages, model_id, routing_mode, routing_hint, iterations, "error")

    if response is None:  # pragma: no cover - defensive
        if emitter:
            emitter.emit(ev.error("The model returned an empty response."))
        return _stopped(state, messages, model_id, routing_mode, routing_hint, iterations, "error")

    content = response.content
    messages.append({"role": "assistant", "content": content})

    # --- accounting -------------------------------------------------------
    cost = estimate_cost(model_id, response.usage)
    totals = dict(state.get("usage") or {})
    totals["input_tokens"] = totals.get("input_tokens", 0) + response.usage.get("input_tokens", 0)
    totals["output_tokens"] = totals.get("output_tokens", 0) + response.usage.get("output_tokens", 0)
    totals["cost_estimate"] = round(totals.get("cost_estimate", 0.0) + cost, 6)

    # Charged here rather than at the end of the turn: a run that is cancelled
    # mid-loop still consumed the calls it already made, and not billing them
    # would make cancellation a way to use the product for free.
    spent = float(state.get("credits_spent") or 0.0)
    charged = await charge_llm(
        state.get("user_id") or None,
        session_id=session_id,
        agent_id=agent_id,
        model_id=model_id,
        cost_usd=cost,
    )
    spent += charged

    if emitter:
        emitter.emit(
            ev.usage(totals["input_tokens"], totals["output_tokens"], totals["cost_estimate"])
        )
        await _emit_credits(emitter, state.get("user_id") or None, spent)

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

    pending = [b for b in content if b.get("type") == "tool_use"]
    if pending:
        reason = "tool_use"
    elif exhausted or over_ceiling:
        # Distinct from `end_turn` so the run's own record says it was cut
        # short by the budget rather than finished by the model.
        reason = "credit_ceiling" if over_ceiling else "tool_budget"
    else:
        reason = response.stop_reason or "end_turn"
    return {
        "messages": messages,
        "model_id": model_id,
        "routing_mode": routing_mode,
        "routing_hint": routing_hint,
        "pending": pending,
        "tool_results": [],
        "iterations": iterations,
        "stop_reason": reason,
        "usage": totals,
        "credits_spent": round(spent, 4),
    }


async def _emit_credits(emitter: Emitter, user_id: str | None, spent: float) -> None:
    from app.credits import get_balance

    settings = get_settings()
    try:
        balance = await get_balance(user_id)
    except Exception:  # noqa: BLE001 - a readout must not fail a run
        return
    emitter.emit(
        ev.credits(balance.balance, spent, enabled=settings.credits_enabled)
    )


def route_from_agent(state: SpecialistState) -> str:
    if state.get("stop_reason") in {"max_iterations", "error", "refusal"}:
        return END
    return "tools" if state.get("pending") else END


# ---------------------------------------------------------------------------
# tool node
# ---------------------------------------------------------------------------


async def _execute_call(
    state: SpecialistState, block: dict, emitter: Emitter | None,
    charges: list[float] | None = None,
) -> dict:
    session_id = state["session_id"]
    agent_id = state.get("agent_id") or ""
    agent = registry.get_agent(agent_id)

    name = block.get("name") or ""
    args = block.get("input") or {}
    call_id = block.get("id") or ""

    if emitter:
        emitter.emit(ev.tool_call_start(name, args, call_id))
    repository.fire(
        repository.add_message(
            session_id, "tool_use", None,
            {"tool": name, "input": args, "call_id": call_id, "agent_id": agent_id},
        )
    )

    ctx = ToolContext(
        session_id=session_id,
        agent_id=agent_id,
        user_id=state.get("user_id") or None,
        call_id=call_id,
        emitter=emitter,
        model_id=state.get("model_id") or "",
    )
    result = await run_agent_tool(name, ctx, args, agent.tools)

    # Surcharge only on a tool that actually reached its paid third-party API.
    # A "not configured" result never called anyone, and charging for a
    # rejected approval would bill the user for saying no.
    #
    # `meta["billable"] = False` is the third case, and the one a tool has to
    # declare for itself: a tool with a free fallback path knows whether the
    # paid vendor was actually used, and nothing out here can tell. `read_url`
    # sets it when the built-in extractor served the page instead of Jina —
    # without it, every fallback read billed Jina's price for a call Jina never
    # received.
    billable = (
        result.success
        and result.meta.get("billable", True)
        and not result.meta.get("not_configured")
        and not (result.meta.get("approval") or {}).get("approved") is False
    )
    if billable:
        charged = await charge_tool(
            ctx.user_id, session_id=session_id, agent_id=agent_id, tool=name
        )
        # Collected so the turn's "spent" readout matches the ledger. Dropping
        # this return value made the number the user watches exclude the
        # surcharges — which are the only charges that are a per-call vendor
        # bill, so it understated precisely the spending worth watching.
        if charges is not None:
            charges.append(charged)

    if emitter:
        for side in result.events:
            emitter.emit(side)
        emitter.emit(ev.tool_call_result(call_id, result.output, result.success, result.meta))
    repository.fire(
        repository.add_message(
            session_id, "tool_result", None,
            {
                "call_id": call_id,
                "tool": name,
                "success": result.success,
                "output": result.output[:20000],
                # The artifact payload can carry a multi-megabyte data URI.
                # The transcript row is for replay, not for storing images.
                "meta": {k: v for k, v in result.meta.items() if k != "artifact"},
                "agent_id": agent_id,
            },
        )
    )

    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": result.output or "(no output)",
        "is_error": not result.success,
    }


def _refused_for_budget(block: dict, budget: int) -> dict:
    """The result a call gets when it falls past the ceiling.

    Every `tool_use` needs a matching `tool_result` or the next request is
    rejected outright, so a call that is not allowed to run still has to answer
    — and the answer has to tell the model *why*, or it will simply try again.
    """
    return {
        "type": "tool_result",
        "tool_use_id": block.get("id"),
        "content": (
            f"Not run: `{block.get('name')}` was refused because this request "
            f"has used its whole {budget}-tool-call budget. No further tool "
            "calls are available on this turn. Write up what you have and say "
            "that you stopped on the tool-call limit."
        ),
        "is_error": True,
    }


async def tool_node(state: SpecialistState, config: RunnableConfig) -> dict:
    emitter = emitter_from_config(config)
    pending = list(state.get("pending") or [])
    if not pending:
        return {}

    # --- tool budget ------------------------------------------------------
    # The ceiling is enforced here, at the point of spending, and not only by
    # withholding schemas in `agent_node`. A model that has already emitted a
    # batch of five calls with two left in budget must not get all five: this
    # is the half of the cap that actually holds the money.
    agent = registry.get_agent(state.get("agent_id") or "")
    budget = _budget(agent)
    used = int(state.get("tool_calls_used") or 0)

    refused: list[dict] = []
    if budget:
        allowance = max(0, budget - used)
        if len(pending) > allowance:
            refused = [_refused_for_budget(b, budget) for b in pending[allowance:]]
            log.info(
                "Agent %s tool budget: running %d of %d requested calls (%d/%d used).",
                agent.id, allowance, len(pending), used, budget,
            )
            pending = pending[:allowance]

    executed = len(pending)
    results: list[dict] = []
    #: Surcharges taken by this batch. Appended to from inside `_execute_call`
    #: because the concurrent path has no other way back — and a plain list is
    #: safe here: appends happen on one event loop, never from a thread.
    charges: list[float] = []

    safe = all(b.get("name") in CONCURRENT_SAFE for b in pending)
    if not pending:
        results = []
    elif len(pending) == 1 or not safe:
        results = [await _execute_call(state, b, emitter, charges) for b in pending]
    else:
        gathered = await asyncio.gather(
            *(_execute_call(state, b, emitter, charges) for b in pending),
            # Every `tool_use` needs a matching `tool_result` or the next
            # request is rejected outright, so one failure must not strand its
            # siblings.
            return_exceptions=True,
        )
        results = []
        for block, outcome in zip(pending, gathered):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, BaseException):
                log.exception("Agent tool %s failed", block.get("name"), exc_info=outcome)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.get("id"),
                        "content": (
                            f"Error: `{block.get('name')}` raised "
                            f"{type(outcome).__name__}: {outcome}"
                        ),
                        "is_error": True,
                    }
                )
            else:
                results.append(outcome)

    buffered = list(state.get("tool_results") or [])
    buffered.extend(results)
    buffered.extend(refused)
    spent = round(float(state.get("credits_spent") or 0.0) + sum(charges), 4)
    if emitter and charges:
        await _emit_credits(emitter, state.get("user_id") or None, spent)
    return {
        "pending": [],
        "tool_results": buffered,
        "credits_spent": spent,
        # Only the calls that actually ran count. A refusal cost nothing, so
        # charging it against the budget would shrink the allowance a retry
        # gets for free.
        "tool_calls_used": used + executed,
    }


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


def build_specialist_graph(checkpointer=None):
    builder = StateGraph(SpecialistState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_from_agent, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)


def build_user_message(text: str, blocks: list[dict] | None = None) -> dict:
    content: list[dict] = list(blocks or [])
    content.append({"type": "text", "text": text})
    return {"role": "user", "content": content}


__all__ = ["build_specialist_graph", "build_user_message", "AUTO_MODEL_ID"]
