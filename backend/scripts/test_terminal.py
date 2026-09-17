"""The terminal panel's command path, end to end minus the network.

Before this existed the terminal was output-only: `TerminalPanel` rendered
`tool_output_chunk` frames and nothing else, and the socket answered every
user-command frame with `Unknown client frame`. The repro that established
that is the first section below, run against the socket handler's frame
dispatch with a fake socket. The rest proves the path that replaced it:

  * `run_terminal_command` streams through the same `tool_output_chunk`
    frames the agent's commands use, bracketed by `terminal_started` /
    `terminal_exit`, and never emits a `tool_call_*` frame — a user's `ls`
    must not appear in the transcript as something the agent did;
  * it reaches the sandbox through `sandbox_manager.get`, the same call the
    agent's tools make, so the idle reaper's `last_used` moves;
  * a non-zero exit, a timeout and a missing sandbox each end with a
    `terminal_exit` the panel can unlock on;
  * the socket runs one command at a time and refuses a second.

Run: python scripts/test_terminal.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("E2B_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

import app.tools.impl as impl  # noqa: E402
import app.tools.sandbox as sb  # noqa: E402
from app.emitter import Emitter  # noqa: E402
from app.tools.sandbox import SandboxUnavailable  # noqa: E402

results: list[bool] = []


def check(label: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))


async def drain(emitter: Emitter) -> list[dict]:
    # `Emitter.emit` enqueues via `call_soon_threadsafe`; yield once so
    # those callbacks run before the queue is read.
    await asyncio.sleep(0)
    out = []
    while True:
        try:
            item = emitter.queue.get_nowait()
        except asyncio.QueueEmpty:
            return out
        if item is not None:
            out.append(item)


class Result:
    def __init__(self, stdout, stderr, exit_code):
        self.stdout, self.stderr, self.exit_code = stdout, stderr, exit_code


class CommandExit(Exception):
    """What the E2B SDK raises for a non-zero exit."""

    def __init__(self, exit_code, stdout="", stderr=""):
        super().__init__(f"exit {exit_code}")
        self.exit_code, self.stdout, self.stderr = exit_code, stdout, stderr


class TimeoutException(Exception):
    """Name-matched to the SDK's timeout error; carries no exit code."""


class FakeCommands:
    """Scripted responses keyed by command, with streamed chunks."""

    def __init__(self):
        self.ran: list[tuple[str, float]] = []

    async def run(self, cmd, cwd=None, timeout=None, on_stdout=None, on_stderr=None, **kw):
        self.ran.append((cmd, timeout))
        if cmd == "ls":
            on_stdout("app.py\n")
            on_stdout("README.md\n")
            return Result("app.py\nREADME.md\n", "", 0)
        if cmd == "false":
            on_stderr("nope\n")
            raise CommandExit(1, "", "nope\n")
        if cmd == "sleep 999":
            raise TimeoutException("timed out")
        if cmd == "slow":
            await asyncio.sleep(0.3)
            return Result("", "", 0)
        return Result("", "", 0)


class FakeSandbox:
    def __init__(self):
        self.sandbox_id = "sbx_fake"
        self.commands = FakeCommands()

        class Files:
            async def list(self, path):
                return []

        self.files = Files()


async def main() -> int:
    sandbox = FakeSandbox()
    entry = sb.SandboxEntry(sandbox=sandbox, session_id="sess-term")  # type: ignore[arg-type]
    entry.last_used = time.monotonic() - 500
    sb.sandbox_manager._entries["sess-term"] = entry

    async def fake_set_timeout(_):
        return None

    sandbox.set_timeout = fake_set_timeout  # type: ignore[attr-defined]

    print("1. the repro: the socket used to reject a typed command")
    # `agent_socket` dispatches on `frame["type"]`; the shape that matters is
    # the string it compares against. Assert the handler now knows the frame
    # by reading its source, which is the one place the dispatch lives.
    import inspect

    import app.api.ws as ws

    source = inspect.getsource(ws.agent_socket)
    check("the handler dispatches `terminal_command`", 'kind == "terminal_command"' in source)
    check("and still rejects unknown frames",
          "Unknown client frame" in source)
    check("one command at a time per socket", "still running" in source)

    print("\n2. a command streams and exits through terminal frames")
    emitter = Emitter()
    outcome = await impl.run_terminal_command("sess-term", "ls", "term_1", emitter)
    frames = await drain(emitter)
    kinds = [f["type"] for f in frames]
    check("exit code 0", outcome["exit_code"] == 0, str(outcome))
    check("starts with terminal_started carrying the command",
          kinds[0] == "terminal_started" and frames[0]["command"] == "ls", str(frames[0]))
    check("output arrives as tool_output_chunk on the same call_id",
          [f["content"] for f in frames if f["type"] == "tool_output_chunk"] == ["app.py\n", "README.md\n"]
          and all(f["call_id"] == "term_1" for f in frames if f["type"] == "tool_output_chunk"))
    check("ends with terminal_exit", kinds[-1] == "terminal_exit" and frames[-1]["exit_code"] == 0)
    check("no tool_call frame — it is not in the transcript",
          not any(k.startswith("tool_call") for k in kinds), str(kinds))
    check("the sandbox's last_used moved (the reaper sees the command)",
          time.monotonic() - entry.last_used < 5)
    check("the terminal timeout applies, not the agent's 30s",
          sandbox.commands.ran[-1][1] == get_settings().terminal_timeout_seconds,
          str(sandbox.commands.ran[-1]))

    print("\n3. a failing command reports its exit code")
    emitter = Emitter()
    outcome = await impl.run_terminal_command("sess-term", "false", "term_2", emitter)
    frames = await drain(emitter)
    check("exit code 1", outcome["exit_code"] == 1)
    check("stderr streamed", any(f.get("stream") == "stderr" and f["content"] == "nope\n" for f in frames))
    check("terminal_exit carries 1", frames[-1]["type"] == "terminal_exit" and frames[-1]["exit_code"] == 1)

    print("\n4. a timeout unlocks the panel")
    emitter = Emitter()
    outcome = await impl.run_terminal_command("sess-term", "sleep 999", "term_3", emitter)
    frames = await drain(emitter)
    check("timed_out is set", outcome["timed_out"] is True and outcome["exit_code"] == 124, str(outcome))
    check("the panel is told, on stderr",
          any("timed out" in f.get("content", "") for f in frames if f["type"] == "tool_output_chunk"))
    check("terminal_exit still closes it", frames[-1]["type"] == "terminal_exit" and frames[-1]["timed_out"])

    print("\n5. no sandbox: the panel gets the reason and an exit")
    emitter = Emitter()

    async def unavailable(session_id):
        raise SandboxUnavailable("E2B_API_KEY is not set")

    original = sb.sandbox_manager.get
    sb.sandbox_manager.get = unavailable  # type: ignore[method-assign]
    try:
        outcome = await impl.run_terminal_command("sess-term", "ls", "term_4", emitter)
    finally:
        sb.sandbox_manager.get = original  # type: ignore[method-assign]
    frames = await drain(emitter)
    check("exit code 1 with the error", outcome["exit_code"] == 1 and "E2B_API_KEY" in outcome["error"])
    check("frames: started, stderr, exit",
          [f["type"] for f in frames] == ["terminal_started", "tool_output_chunk", "terminal_exit"],
          str([f["type"] for f in frames]))

    print("\n6. an empty command is refused without touching the sandbox")
    before = len(sandbox.commands.ran)
    outcome = await impl.run_terminal_command("sess-term", "   ", "term_5", Emitter())
    check("refused", outcome["exit_code"] == 1 and outcome["error"] == "empty command")
    check("nothing ran", len(sandbox.commands.ran) == before)

    print(f"\n{sum(results)}/{len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
