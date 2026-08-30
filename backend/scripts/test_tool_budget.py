"""The tool-call budget, proven offline.

    python scripts/test_tool_budget.py

No provider is called and no tool runs for real: the model and the tool
dispatcher are both replaced with stubs, so this asserts the *enforcement*
rather than the weather on any vendor's API. That is the point — the thing
under test is a cost control, and a test for a cost control should not cost
anything.

The stub model is deliberately the worst case: it asks for four `search_web`
calls every single turn and never stops on its own. Without the budget it runs
until `max_agent_iterations`, which is exactly the 23-call, ~714-credit run
that prompted this.

What is asserted
----------------
1. Exactly `max_tool_calls_per_turn` tool calls actually execute — no more,
   including the partial batch that straddles the ceiling.
2. Calls past the ceiling still come back as `tool_result` blocks (an
   unanswered `tool_use` makes the next request invalid) and are marked as
   refused rather than silently dropped.
3. The run does not simply stop: one final model call happens with no tools,
   and it is told to write up.
4. A `tool_budget_reached` frame reaches the client, so the UI can say why the
   answer is partial.
5. The turn ends with `stop_reason == "tool_budget"`.
6. An uncapped agent is untouched by any of it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import graph as agents_graph  # noqa: E402
from app.agents import registry  # noqa: E402
from app.agents.tools.base import ToolResult  # noqa: E402
from app.emitter import Emitter, registry as emitter_registry  # noqa: E402
from app.llm_router import NormalizedMessage, StreamEvent  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    return ok


class Recorder:
    """Stands in for the model and for every tool, counting what was asked."""

    def __init__(self, calls_per_turn: int = 4) -> None:
        self.calls_per_turn = calls_per_turn
        self.model_calls = 0
        self.tools_offered: list[int] = []
        self.systems: list[str] = []
        self.executed: list[str] = []
        self.final_text = ""

    # --- fake provider ---------------------------------------------------
    # Signature matches `llm_router.stream_with_fallback`, which is what the
    # graph calls now: same positional shape as `call_model` plus the
    # keyword-only `on_fallback` / `section`, both swallowed by **kw here.
    # Nothing in this test exercises fallback — that is
    # `scripts/test_model_fallback.py` — it only needs the boundary stubbed.
    def stream_with_fallback(self, model_id, messages, tools=None, system=None, **kw):
        self.model_calls += 1
        self.tools_offered.append(len(tools or []))
        self.systems.append(system or "")
        offered = bool(tools)

        async def _stream():
            if offered:
                # Always ask for more. A well-behaved model would stop; the
                # budget exists for the one that does not.
                content = [
                    {
                        "type": "tool_use",
                        "id": f"toolu_{self.model_calls}_{i}",
                        "name": "search_web",
                        "input": {"query": f"claim {self.model_calls}.{i}"},
                    }
                    for i in range(self.calls_per_turn)
                ]
                stop = "tool_use"
            else:
                # Tools were withheld: this is the wrap-up call.
                self.final_text = (
                    "I stopped early because I reached the tool-call limit for "
                    "this request. Here is what I established so far."
                )
                content = [{"type": "text", "text": self.final_text}]
                stop = "end_turn"
            yield StreamEvent(
                kind="done",
                message=NormalizedMessage(
                    content=content,
                    usage={"input_tokens": 1000, "output_tokens": 200},
                    stop_reason=stop,
                    model_name="stub",
                ),
            )

        return _stream()

    # --- fake tool dispatcher --------------------------------------------
    async def run_tool(self, name, ctx, args, allowed):
        self.executed.append(name)
        return ToolResult(output=f"stub result for {name}", meta={})


async def run_agent(agent_id: str, recorder: Recorder) -> tuple[dict, list[dict]]:
    """One turn through the real graph, with the two boundaries stubbed."""
    from langgraph.checkpoint.memory import MemorySaver

    emitter = Emitter()
    emitter_registry.register(emitter, f"budget-test-{agent_id}")

    original = (
        agents_graph.stream_with_fallback,
        agents_graph.run_agent_tool,
        agents_graph.charge_llm,
        agents_graph.charge_tool,
        agents_graph._emit_credits,
        agents_graph.repository.fire,
    )
    agents_graph.stream_with_fallback = recorder.stream_with_fallback
    agents_graph.run_agent_tool = recorder.run_tool
    # The meter is a separate concern with its own tests, and this must not
    # touch a real balance to prove a ceiling.
    agents_graph.charge_llm = lambda *a, **k: _zero()
    agents_graph.charge_tool = lambda *a, **k: _zero()
    agents_graph._emit_credits = lambda *a, **k: _none()
    agents_graph.repository.fire = lambda *a, **k: None

    try:
        compiled = agents_graph.build_specialist_graph(MemorySaver())
        final = await compiled.ainvoke(
            {
                "session_id": f"budget-test-{agent_id}",
                "agent_id": agent_id,
                "user_id": "",
                "model_id": "qwen3_7_plus",
                "routing_mode": "manual",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Fact-check this."}],
                    }
                ],
                "pending": [],
                "tool_results": [],
                "iterations": 0,
                "tool_calls_used": 0,
                "stop_reason": "",
                "usage": {},
                "credits_spent": 0.0,
            },
            config={
                "configurable": {
                    "thread_id": f"budget-test-{agent_id}",
                    "emitter_id": emitter.id,
                },
                "recursion_limit": 200,
            },
        )
    finally:
        (
            agents_graph.stream_with_fallback,
            agents_graph.run_agent_tool,
            agents_graph.charge_llm,
            agents_graph.charge_tool,
            agents_graph._emit_credits,
            agents_graph.repository.fire,
        ) = original
        emitter_registry.unregister(emitter.id)

    events: list[dict] = []
    while not emitter.queue.empty():
        item = emitter.queue.get_nowait()
        if item is not None:
            events.append(item)
    return dict(final), events


async def _zero() -> float:
    return 0.0


async def _none() -> None:
    return None


async def main() -> int:
    agent = registry.get_agent("research_fact_checker")
    budget = agent.max_tool_calls_per_turn
    print(f"\nAgent 7 declares a budget of {budget} tool calls per turn.\n")

    if not check("Agent 7 has a tool-call budget at all", budget > 0, f"{budget}"):
        return 1

    print("Running the worst-case model (4 calls per turn, never stops):")
    # 4 per turn against a budget of 10 lands mid-batch on the third turn,
    # which is the case that actually needs the tool node's half of the check.
    recorder = Recorder(calls_per_turn=4)
    final, events = await run_agent("research_fact_checker", recorder)

    ok = True
    ok &= check(
        "exactly the budgeted number of tool calls ran",
        len(recorder.executed) == budget,
        f"{len(recorder.executed)} executed, budget {budget}",
    )

    # `tool_results` is a buffer the agent node drains into the transcript, so
    # the finished state's copy is empty by design — count the blocks where
    # they actually ended up.
    results = [
        block
        for message in (final.get("messages") or [])
        for block in (message.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    requested = [
        block
        for message in (final.get("messages") or [])
        for block in (message.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    refused = [r for r in results if "budget" in str(r.get("content", "")).lower()]
    ok &= check(
        "the partial batch past the ceiling was refused, not dropped",
        len(refused) == len(requested) - budget,
        f"{len(refused)} refused, {len(requested)} requested, {budget} allowed",
    )
    ok &= check(
        "every requested call still has a tool_result",
        len(results) == len(requested),
        f"{len(results)} results for {len(requested)} tool_use blocks",
    )

    ok &= check(
        "a final model call ran with no tools offered",
        recorder.tools_offered and recorder.tools_offered[-1] == 0,
        f"schemas offered per call: {recorder.tools_offered}",
    )
    ok &= check(
        "that call was instructed to write up and admit it stopped early",
        "STOP SEARCHING" in recorder.systems[-1]
        and "tool-call limit" in recorder.systems[-1],
    )
    ok &= check(
        "the answer states it stopped early",
        "tool-call limit" in recorder.final_text,
    )

    frames = [e for e in events if e.get("type") == "tool_budget_reached"]
    ok &= check(
        "a tool_budget_reached frame reached the client",
        len(frames) == 1,
        frames[0]["message"][:80] + "…" if frames else "none emitted",
    )
    ok &= check(
        "the turn is recorded as budget-stopped",
        final.get("stop_reason") == "tool_budget",
        str(final.get("stop_reason")),
    )
    ok &= check(
        "the model was not left looping",
        recorder.model_calls <= 5,
        f"{recorder.model_calls} model calls",
    )

    print("\nControl: an uncapped agent is unaffected:")
    control = registry.get_agent("system_logic_router")
    ok &= check(
        "the router declares no budget",
        control.max_tool_calls_per_turn == 0,
    )
    recorder2 = Recorder(calls_per_turn=1)
    # One call, then the stub is asked again — it keeps going until the
    # iteration cap, which is the pre-existing behaviour and must stay.
    final2, events2 = await run_agent("system_logic_router", recorder2)
    ok &= check(
        "it ran past 10 tool calls, as before",
        len(recorder2.executed) > budget,
        f"{len(recorder2.executed)} calls, stopped on "
        f"{final2.get('stop_reason')}",
    )
    ok &= check(
        "and emitted no budget frame",
        not [e for e in events2 if e.get("type") == "tool_budget_reached"],
    )

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} — {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
