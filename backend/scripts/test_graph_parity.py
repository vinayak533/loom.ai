"""The same checks, run against both turn loops.

    .venv/Scripts/python scripts/test_graph_parity.py

Why this file exists
--------------------
There are two turn loops in this backend — `app/agent/graph.py` (Chat/Code)
and `app/agents/graph.py` (the ten specialists) — and they are two on purpose:
Code's tools contend over a shared filesystem and need per-family ordering, the
specialists' tools do not. But everything *around* that difference was the same
job written twice, and writing it twice is how the two drifted apart. Four
findings, all of them one loop having something the other did not:

  * the Chat/Code loop refused a tool call whose arguments the provider had cut
    off mid-write; the specialist loop dispatched it, every argument getter
    fell back to its default, and the tool answered confidently about nothing;
  * the Chat/Code loop estimated a stopped turn's input tokens from the prompt;
    the specialist loop billed a hardcoded zero, which on agents whose prompt
    is a persona plus ten tool schemas plus a whole document is most of the
    turn's real cost;
  * the Chat/Code loop closed an abandoned provider stream; the specialist loop
    had its own inline copy of the same code, one edit away from not;
  * the Chat/Code loop filed a `token_usage` row for a stopped turn; the
    specialist loop charged for the tokens and filed nothing, so the ledger and
    the usage table disagreed on every stop — in the direction that hides
    spending. Found by writing this suite, not by the audit that prompted it,
    which is the argument for the suite.

The shared parts live in `app/turnstop.py` and both graphs call them. This is
the thing that keeps them there: every check runs against *both* graphs, so a
fix applied to one and not the other fails here rather than shipping.

No provider is called, no sandbox is opened and no database is touched.
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

from app.agent import graph as chat_graph  # noqa: E402
from app.agents import graph as agents_graph  # noqa: E402
from app.llm_router import RAW_ARGUMENTS_KEY  # noqa: E402
from app.tools.impl import ToolResult  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"    [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def _drop(coro=None, *a, **kw):
    """Stand-in for `repository.fire`, which schedules a coroutine we have no
    database for. Closing it keeps the harness quiet: left un-awaited it warns,
    and a warning from the harness is indistinguishable from one from the code.
    """
    if hasattr(coro, "close"):
        coro.close()


# ---------------------------------------------------------------------------
# the two graphs, described in the terms this suite needs
# ---------------------------------------------------------------------------


class Dispatcher:
    """Stands in for every tool, recording what was actually asked to run."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def chat(self, name, session_id, args, call_id, emitter):
        self.executed.append((name, args))
        return ToolResult("done", True)

    async def agents(self, name, ctx, args, allowed):
        self.executed.append((name, args))
        return ToolResult("done", True)


async def _dispatch_chat(block: dict) -> tuple[dict, Dispatcher]:
    spy = Dispatcher()
    saved = chat_graph.run_tool, chat_graph.repository.fire
    chat_graph.run_tool, chat_graph.repository.fire = spy.chat, _drop
    try:
        result = await chat_graph._execute_call("sess-parity", block, None)
    finally:
        chat_graph.run_tool, chat_graph.repository.fire = saved
    return result, spy


async def _dispatch_agents(block: dict) -> tuple[dict, Dispatcher]:
    spy = Dispatcher()
    saved = agents_graph.run_agent_tool, agents_graph.repository.fire
    agents_graph.run_agent_tool, agents_graph.repository.fire = spy.agents, _drop
    state = {
        "session_id": "sess-parity",
        "agent_id": "document_summarizer",
        "user_id": None,
        "model_id": "",
    }
    try:
        result = await agents_graph._execute_call(state, block, None)
    finally:
        agents_graph.run_agent_tool, agents_graph.repository.fire = saved
    return result, spy


#: The tool each graph is asked to run, and in both cases the one whose
#: arguments carry a whole document — which is what makes it the call most
#: likely to run a turn into its output ceiling in the first place.
GRAPHS = [
    ("chat_graph", chat_graph, _dispatch_chat, "file_write"),
    ("agents_graph", agents_graph, _dispatch_agents, "chunk_document"),
]


# ---------------------------------------------------------------------------
# 1. truncated arguments
# ---------------------------------------------------------------------------

#: The real shape of the failure, shortened: an argument string that stops
#: partway through the document, with the JSON string still open.
TRUNCATED = (
    '{"path": "/home/user/index.html", "content": "<!DOCTYPE html>\n'
    '<html lang=\\"en\\">\n<head>\n  <title>Maple and Steam</title>'
)

#: Balanced braces, still unreadable. Needs the *other* message: telling a
#: model to write less cannot fix JSON that was simply wrong.
MALFORMED = '{"path": /home/user/a.html}'


async def truncated_arguments() -> bool:
    print("\n1. A call whose arguments were cut off is refused, not dispatched")
    ok = True
    for label, _mod, dispatch, tool in GRAPHS:
        print(f"  {label}")
        result, spy = await dispatch(
            {
                "type": "tool_use",
                "id": "toolu_cut",
                "name": tool,
                "input": {RAW_ARGUMENTS_KEY: TRUNCATED},
            }
        )
        body = result.get("content", "")
        ok &= check("no tool ran", spy.executed == [], f"executed: {spy.executed}")
        ok &= check("the result is an error", result.get("is_error") is True)
        ok &= check("it says the arguments were cut off", "cut off" in body)
        ok &= check(
            "it tells the model not to resend the same call",
            "Do not repeat this call unchanged" in body,
        )
        ok &= check("it names the tool that was refused", f"`{tool}`" in body)

        result2, spy2 = await dispatch(
            {
                "type": "tool_use",
                "id": "toolu_bad",
                "name": tool,
                "input": {RAW_ARGUMENTS_KEY: MALFORMED},
            }
        )
        body2 = result2.get("content", "")
        ok &= check("malformed JSON also runs nothing", spy2.executed == [])
        ok &= check("and is told its JSON was invalid", "not valid JSON" in body2)
        ok &= check("without being told to write less", "cut off" not in body2)

        good = {"document": "one two three", "chunk_size": 2}
        result3, spy3 = await dispatch(
            {"type": "tool_use", "id": "toolu_ok", "name": tool, "input": good}
        )
        ok &= check(
            "a well-formed call still runs",
            [n for n, _ in spy3.executed] == [tool],
        )
        ok &= check("with its arguments untouched", spy3.executed[0][1] == good)
        ok &= check("and is not an error", result3.get("is_error") is False)

    # The specialists' worst cases, specifically. These three take a whole
    # document, a whole file or a whole payload as one argument, which makes
    # them both the likeliest to be truncated and the worst to dispatch when
    # they are: every one of their getters defaults to empty, so a truncated
    # call used to come back as a confident answer about nothing — zero chunks,
    # zero findings, a passing lint.
    print("  agents_graph — the tools whose arguments are whole documents")
    for tool in ("chunk_document", "lint_code", "validate_json_schema"):
        result, spy = await _dispatch_agents(
            {
                "type": "tool_use",
                "id": f"toolu_{tool}",
                "name": tool,
                "input": {RAW_ARGUMENTS_KEY: TRUNCATED},
            }
        )
        body = result.get("content", "")
        ok &= check(
            f"`{tool}` is refused, not dispatched",
            spy.executed == [] and result.get("is_error") is True,
            f"executed: {spy.executed}",
        )
        ok &= check(
            f"`{tool}` gets the shared truncation message",
            "cut off" in body and "Do not repeat this call unchanged" in body,
            body[:80],
        )
        # The specific regression: the old path let the call through and the
        # tool answered about its own default, so the model was told its
        # *input* was empty rather than that its *output* had been cut off.
        ok &= check(
            f"`{tool}` is not told its argument was empty",
            "was empty" not in body,
            "the pre-fix failure mode was `Error: `text` was empty.`",
        )
    return ok


# ---------------------------------------------------------------------------
# 2 and 3. stop accounting and stream teardown
# ---------------------------------------------------------------------------

#: Long enough that an input estimate of zero is unmistakably wrong rather than
#: arguably a rounding difference. Roughly the size of a document an agent is
#: actually handed.
PROMPT_BODY = "The quick brown fox jumps over the lazy dog. " * 200

#: What the provider streamed before the user pressed Stop.
STREAMED = "Here is the beginning of an answer that the user cut short"


class _Event:
    def __init__(self, kind: str, content: str = "", message=None) -> None:
        self.kind, self.content, self.message = kind, content, message


class FakeStream:
    """A provider stream that yields a few deltas and records its own teardown.

    `aclose` is what the graphs are supposed to await when they abandon it.
    Left to the garbage collector instead, the provider keeps generating — and
    charging for — tokens nobody reads, so "was it closed" is the assertion.
    """

    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for piece in STREAMED.split(" "):
            yield _Event("text_delta", piece + " ")

    async def aclose(self) -> None:
        self.closed = True


async def _run_stopped_turn(mod, state: dict) -> tuple[dict, FakeStream, list]:
    """Drive one graph's `agent_node` through a mid-stream stop.

    Everything external is replaced: no provider, no ledger, no database. What
    is left running is the accounting itself, which is the thing under test.

    Returns the node's result, the stream it was handed, and the `record_usage`
    calls it made — the last because a turn that charges for tokens and then
    files no usage row leaves the ledger and the usage table disagreeing, and
    that is exactly what the agents' loop used to do on every stop.
    """
    stream = FakeStream()
    usage_rows: list[dict] = []

    async def _compose(prompt, **kw):
        return "system"

    async def _charge(*a, **kw):
        return 0.0

    saved = {
        "stream_with_fallback": mod.stream_with_fallback,
        "is_stopping": mod.is_stopping,
        "charge_llm": mod.charge_llm,
        "estimate_cost": mod.estimate_cost,
        "fire": mod.repository.fire,
        "compose": mod.preamble.compose,
    }
    mod.stream_with_fallback = lambda *a, **kw: stream

    # Both graphs ask twice, and the two answers have to differ. The *first*
    # ask is the pre-stream check, which returns early with no accounting at
    # all — a real turn only reaches it when the stop landed while the previous
    # iteration's tools were still running. Answering True there would end the
    # turn before the code under test ran. Every ask after it is a frame
    # boundary inside the stream loop, which is the mid-stream stop this suite
    # is about.
    asked = {"n": 0}

    def _is_stopping(_session_id: str) -> bool:
        asked["n"] += 1
        return asked["n"] > 1

    mod.is_stopping = _is_stopping
    mod.charge_llm = _charge
    mod.estimate_cost = lambda model, usage: 0.0
    mod.repository.fire = _drop
    mod.preamble.compose = _compose

    # `fire` closes the coroutine unawaited, so the call has to be recorded
    # where it is built rather than where it would have run.
    real_record = mod.repository.record_usage

    def _record(session_id, model, input_tokens, output_tokens, cost, **kw):
        usage_rows.append(
            {
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": cost,
                **kw,
            }
        )
        return real_record(session_id, model, input_tokens, output_tokens, cost, **kw)

    mod.repository.record_usage = _record
    try:
        result = await mod.agent_node(state, None)
    finally:
        mod.stream_with_fallback = saved["stream_with_fallback"]
        mod.is_stopping = saved["is_stopping"]
        mod.charge_llm = saved["charge_llm"]
        mod.estimate_cost = saved["estimate_cost"]
        mod.repository.fire = saved["fire"]
        mod.preamble.compose = saved["compose"]
        mod.repository.record_usage = real_record
    return result, stream, usage_rows


def _state_for(label: str) -> dict:
    """A turn with a real prompt behind it, shaped for one graph or the other.

    The two states differ only where the graphs genuinely differ — `section`
    for Chat/Code, `agent_id` for a specialist. Everything the accounting reads
    is identical, so the two input estimates are comparable.
    """
    base = {
        "session_id": "sess-parity-stop",
        "user_id": None,
        "project_id": None,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": PROMPT_BODY}]}
        ],
        "tool_results": [],
        "pending": [],
        "iterations": 0,
        "usage": {},
        "credits_spent": 0.0,
        "routing_mode": "manual",
        "model_id": "",
    }
    if label == "agents_graph":
        base["agent_id"] = "document_summarizer"
    else:
        base["section"] = "code"
    return base


async def stop_accounting_and_teardown() -> bool:
    print("\n2. A stopped turn bills for the prompt it actually sent")
    ok = True
    seen: dict[str, dict] = {}
    for label, mod, _dispatch, _tool in GRAPHS:
        print(f"  {label}")
        result, stream, usage_rows = await _run_stopped_turn(mod, _state_for(label))
        usage = result.get("usage") or {}
        seen[label] = {"usage": usage, "closed": stream.closed, "rows": usage_rows}

        ok &= check(
            "the turn ends as cancelled",
            result.get("stop_reason") == "cancelled",
            f"stop_reason={result.get('stop_reason')}",
        )
        ok &= check(
            "input tokens are counted, not zeroed",
            usage.get("input_tokens", 0) > 0,
            f"input_tokens={usage.get('input_tokens')}",
        )
        # The prompt is ~8800 characters at 4 chars/token, so a correct
        # estimate is in four figures. The bug this replaces produced 0.
        ok &= check(
            "and are the right order of magnitude for the prompt",
            usage.get("input_tokens", 0) > 1000,
            f"input_tokens={usage.get('input_tokens')} for a "
            f"{len(PROMPT_BODY)}-character prompt",
        )
        ok &= check(
            "output tokens count what was streamed before the stop",
            0 < usage.get("output_tokens", 0) < usage.get("input_tokens", 1),
            f"output_tokens={usage.get('output_tokens')}",
        )
        ok &= check("no tool call is left pending", result.get("pending") == [])
        # The turn was charged for these tokens, so the usage table has to know
        # about them too. Without this the ledger and the usage table disagree
        # on every stop, in the direction that hides real spending.
        ok &= check(
            "a token_usage row is filed for the stopped turn",
            len(usage_rows) == 1,
            f"{len(usage_rows)} rows recorded",
        )
        if usage_rows:
            ok &= check(
                "and it carries the same numbers the turn was billed for",
                usage_rows[0]["input_tokens"] == usage.get("input_tokens")
                and usage_rows[0]["output_tokens"] == usage.get("output_tokens"),
                f"row={usage_rows[0]['input_tokens']}/{usage_rows[0]['output_tokens']}, "
                f"usage={usage.get('input_tokens')}/{usage.get('output_tokens')}",
            )

    print("\n   both graphs, side by side")
    a = seen["chat_graph"]["usage"].get("input_tokens", 0)
    b = seen["agents_graph"]["usage"].get("input_tokens", 0)
    # Same prompt, same estimator: the two must agree closely. They are not
    # required to be identical — each graph composes its own system prompt —
    # but an order of magnitude apart means one is measuring something the
    # other is not.
    ok &= check(
        "the two graphs' input estimates agree to within 2x",
        a > 0 and b > 0 and 0.5 <= (a / b) <= 2.0,
        f"chat={a}, agents={b}",
    )

    print("\n3. Both graphs close the stream they abandon")
    for label in ("chat_graph", "agents_graph"):
        ok &= check(
            f"{label} awaited aclose() on the abandoned stream",
            seen[label]["closed"],
            "an unclosed stream keeps the provider generating billable tokens",
        )
    return ok


async def main() -> int:
    ok = True
    ok &= await truncated_arguments()
    ok &= await stop_accounting_and_teardown()

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
