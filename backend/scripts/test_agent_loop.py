"""End-to-end smoke test for the agent loop — no UI, no websocket.

    python -m scripts.test_agent_loop "write hello.py that prints hi, then run it"

Prints every event the websocket would have sent, so you can verify the loop
before touching the frontend. Requires OPENCODE_API_KEY and E2B_API_KEY.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()

from app.agent import runner  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.emitter import Emitter, registry  # noqa: E402
from app.tools.sandbox import sandbox_manager  # noqa: E402

DEFAULT_TASK = (
    "Create fizzbuzz.py in the working directory that prints FizzBuzz for 1-15, "
    "then run it and show me the output."
)

DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


async def drain(emitter: Emitter) -> None:
    """Render events as they arrive, the way the UI would."""
    while True:
        e = await emitter.queue.get()
        if e is None:
            return
        t = e["type"]
        if t == "agent_token":
            print(e["content"], end="", flush=True)
        elif t == "agent_thinking_delta":
            print(f"{DIM}{e['content']}{RESET}", end="", flush=True)
        elif t == "agent_thinking_start":
            print(f"\n{DIM}--- thinking ---{RESET}")
        elif t in {"agent_thinking_end", "agent_message_end"}:
            print()
        elif t == "tool_call_start":
            print(f"\n{CYAN}▶ {e['tool']}{RESET} {str(e['input'])[:160]}")
        elif t == "tool_output_chunk":
            print(f"{DIM}  │ {e['content'].rstrip()}{RESET}")
        elif t == "tool_call_result":
            mark = f"{GREEN}✓{RESET}" if e["success"] else f"{RED}✗{RESET}"
            print(f"{mark} {e['output'][:400]}")
        elif t == "file_changed":
            print(f"{YELLOW}~ {e['change']}: {e['path']}{RESET}")
        elif t == "usage":
            print(
                f"{DIM}  tokens in={e['input_tokens']} out={e['output_tokens']} "
                f"≈${e['cost_estimate']:.4f}{RESET}"
            )
        elif t == "max_iterations":
            print(f"\n{RED}{e['message']}{RESET}")
        elif t == "error":
            print(f"\n{RED}ERROR: {e['message']}{RESET}")
        elif t == "agent_done":
            print(f"\n{GREEN}done ({e['iterations']} iterations, {e['reason']}){RESET}")


async def main() -> int:
    task = " ".join(sys.argv[1:]) or DEFAULT_TASK
    settings = get_settings()

    missing = [
        name
        for name, present in (
            ("OPENCODE_API_KEY", settings.opencode_api_key),
            ("E2B_API_KEY", settings.e2b_api_key),
        )
        if not present
    ]
    if missing:
        print(f"{RED}Missing required keys: {', '.join(missing)}{RESET}")
        print("Copy .env.example to .env and fill them in.")
        return 1

    session_id = str(uuid.uuid4())
    print(f"{DIM}session {session_id} | model {settings.default_model_id}{RESET}")
    print(f"{CYAN}task:{RESET} {task}\n")

    await runner.startup()
    sandbox_manager.start_reaper()

    emitter = Emitter()
    registry.register(emitter)
    reader = asyncio.create_task(drain(emitter))

    try:
        final = await runner.run_turn(session_id, task, emitter)
        if final.get("stop_reason") != "max_iterations":
            from app import events as ev

            emitter.emit(
                ev.agent_done(
                    int(final.get("iterations") or 0),
                    final.get("stop_reason") or "end_turn",
                )
            )
        await asyncio.sleep(0.2)
    finally:
        emitter.close()
        await reader
        registry.unregister(emitter.id)
        await sandbox_manager.shutdown()
        await runner.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
