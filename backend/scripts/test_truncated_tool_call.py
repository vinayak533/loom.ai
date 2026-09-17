"""A tool call cut off mid-argument, proven offline.

    python scripts/test_truncated_tool_call.py

No provider is called and no sandbox is opened. Both halves of the path are
stubbed, because the thing under test is a *refusal*: the assertion worth
making is that nothing ran, and that is only visible with a real dispatcher
replaced by a counter.

The bug this pins down
----------------------
Asked for a whole one-page site, a model emits one `file_write` whose
`content` is the entire document. If the turn's output ceiling lands inside
that argument, the provider still reports the call — with a JSON fragment
that stops mid-string. `_openai_blocks` cannot parse it, so it hands back
`{RAW_ARGUMENTS_KEY: "<fragment>"}`: a dict with none of the keys the tool
reads.

Dispatched, that call was not inert. `write_file` read `args["path"]` as
`""`, `_abs` turned `""` into the sandbox workdir, and the model got back
`path is a directory: /home/user` — an error about a path it never wrote,
saying nothing about truncation. It retried the identical call, was cut off
identically, and the run spent its iteration budget without writing a file.

What is asserted
----------------
1. The contract holds end to end: a truncated argument string really does
   arrive from `llm_router` carrying `RAW_ARGUMENTS_KEY`.
2. Such a call never reaches the tool dispatcher.
3. Its `tool_result` is an error, and the error says the arguments were cut
   off and tells the model not to resend them unchanged.
4. Balanced-but-invalid JSON gets the *other* message — "write less" is
   useless advice for a model whose JSON was simply wrong.
5. A well-formed call is untouched and still runs.
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

from app.agent import graph  # noqa: E402
from app.llm_router import RAW_ARGUMENTS_KEY, _openai_blocks  # noqa: E402
from app.tools.impl import ToolResult  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


# The real shape of the failure, shortened: a `file_write` whose `content`
# stops partway through the document, with the string still open.
TRUNCATED = (
    '{"path": "/home/user/index.html", "content": "<!DOCTYPE html>\n'
    '<html lang=\\"en\\">\n<head>\n  <title>Maple & Steam</title>'
)


class Dispatcher:
    """Stands in for every tool, recording what was actually asked to run."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def run_tool(self, name, session_id, args, call_id, emitter):
        self.executed.append((name, args))
        return ToolResult("wrote 420 lines", True)


async def dispatch(block: dict) -> tuple[dict, Dispatcher]:
    """Run one `tool_use` block through `_execute_call` with tools stubbed."""
    spy = Dispatcher()
    real_run_tool, real_fire = graph.run_tool, graph.repository.fire
    graph.run_tool = spy.run_tool
    # `add_message` is a coroutine and `fire` normally schedules it. Closing
    # it here keeps the test quiet: left un-awaited it warns, and a warning
    # from the harness would be indistinguishable from one from the code.
    def _drop(coro=None, *a, **kw):
        if hasattr(coro, "close"):
            coro.close()

    graph.repository.fire = _drop  # no database in this test
    try:
        result = await graph._execute_call("sess-truncated", block, None)
    finally:
        graph.run_tool, graph.repository.fire = real_run_tool, real_fire
    return result, spy


async def main() -> int:
    ok = True

    print("\n1. The fragment survives the router as a marked block")
    blocks = _openai_blocks(
        None,
        [{"id": "toolu_1", "function": {"name": "file_write", "arguments": TRUNCATED}}],
    )
    tool_use = [b for b in blocks if b.get("type") == "tool_use"]
    ok &= check("the call is still a tool_use block", len(tool_use) == 1)
    args = tool_use[0]["input"] if tool_use else {}
    ok &= check(
        "its arguments carry the marker and nothing else",
        list(args) == [RAW_ARGUMENTS_KEY],
        f"keys: {list(args)}",
    )
    ok &= check(
        "and `path` is absent, not empty",
        "path" not in args,
    )

    print("\n2. A truncated call is refused rather than run")
    result, spy = await dispatch(
        {"type": "tool_use", "id": "toolu_1", "name": "file_write", "input": args}
    )
    ok &= check("no tool ran", spy.executed == [], f"executed: {spy.executed}")
    ok &= check("the result is an error", result.get("is_error") is True)
    body = result.get("content", "")
    ok &= check("it says the arguments were cut off", "cut off" in body)
    ok &= check("it says nothing was written", "nothing reached the sandbox" in body)
    ok &= check(
        "it tells the model not to resend the same call",
        "Do not repeat this call unchanged" in body,
    )
    ok &= check(
        "it says what to do instead",
        "separate call" in body,
    )
    ok &= check(
        "it never mentions the workdir it used to fail on",
        "/home/user" not in body,
        "the old error blamed a path the model never sent",
    )

    print("\n3. Malformed-but-complete JSON gets different advice")
    result3, spy3 = await dispatch(
        {
            "type": "tool_use",
            "id": "toolu_3",
            "name": "file_write",
            "input": {RAW_ARGUMENTS_KEY: '{"path": /home/user/a.html}'},
        }
    )
    ok &= check("no tool ran", spy3.executed == [])
    body3 = result3.get("content", "")
    ok &= check("it says the JSON was invalid", "not valid JSON" in body3)
    ok &= check(
        "and does not tell the model to write less",
        "cut off" not in body3,
        "truncation advice cannot fix malformed JSON",
    )

    print("\n4. A well-formed call is unaffected")
    good = {"path": "/home/user/index.html", "content": "<!DOCTYPE html>"}
    result4, spy4 = await dispatch(
        {"type": "tool_use", "id": "toolu_4", "name": "file_write", "input": good}
    )
    ok &= check("the tool ran", [n for n, _ in spy4.executed] == ["file_write"])
    ok &= check("with the arguments untouched", spy4.executed[0][1] == good)
    ok &= check("and the result is not an error", result4.get("is_error") is False)

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
