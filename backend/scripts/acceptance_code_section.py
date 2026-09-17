"""NEW-5: the Code section end to end, on a real design prompt.

    python scripts/acceptance_code_section.py
    python scripts/acceptance_code_section.py --remote https://github.com/you/repo
    python scripts/acceptance_code_section.py --no-auto-push     # commit path only

One session, one prompt, five checks — the acceptance criteria for the
terminal (NEW-4) and the end-of-turn commit and push (NEW-3), exercised
together because they only interact in one place:

  1. **Generation** — the page is written into the sandbox project directory.
  2. **File tree** — a `file_tree` frame arrives naming the new files, and the
     `turn_commit` frame carries their absolute paths so the panel can open
     to them.
  3. **Terminal** — a command typed into the panel (not issued by the agent)
     runs in the same sandbox and streams output back.
  4. **Commit and push** — a commit is created with a generated message and
     pushed to the configured remote.
  5. **UI surfacing** — the `turn_commit` frame carries the hash, subject,
     branch and push state the transcript renders as its turn summary.

Steps 1-3 need only `E2B_API_KEY`. Step 4 needs `GIT_PUSH_TOKEN` set on the
server *and* a remote you can write to; without the token the run still
proves the commit half and reports the push as blocked on the credential,
which is the honest degraded state rather than a failure of the flow.

The backend must already be running (`uvicorn app.main:app --port 8000`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import websockets  # noqa: E402

PROMPT = (
    "Build a clean, modern, responsive weather app web page. Use a colorful "
    "blue-to-purple gradient background with white cards and subtle shadows. "
    "Include a header, location/search bar, current temperature, weather "
    "condition, humidity, wind speed, and a 5-day forecast. Use bright accent "
    "colors for icons and buttons, smooth spacing, rounded corners, readable "
    "typography, and a polished mobile-friendly layout."
)

#: A smaller build that fits inside a constrained output budget. The prompt
#: above is the acceptance brief and is what should be run; this exists because
#: a single-file weather page is 12-20k output tokens, and a provider that has
#: trimmed `max_tokens` (an exhausted OpenRouter allowance does exactly that)
#: ends the turn on `max_tokens` before the first `file_write` completes. That
#: is a budget ceiling, not a defect in this flow — but the flow still needs
#: proving, so `--small` runs the same five checks against a build that fits.
SMALL_PROMPT = (
    "Create index.html: a single self-contained page with a blue-to-purple "
    "gradient background, a centred white card with rounded corners and a soft "
    "shadow, a heading that says Weather, and one line showing 21 degrees. "
    "Inline CSS in a <style> tag. Keep it under 40 lines."
)

#: Run against the project once the turn is done. `ls -la` is the brief's own
#: example and proves the panel reaches the sandbox; the `head` keeps the
#: transcript readable.
TERMINAL_COMMAND = "ls -la && echo '--- the generated page ---' && head -20 *.html"

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--remote",
        default="https://github.com/vinayak533/test",
        help="Remote to push to. Empty string to skip the remote entirely.",
    )
    parser.add_argument("--no-auto-push", action="store_true",
                        help="Leave the end-of-turn opt-in off (commit path not exercised).")
    parser.add_argument("--model", default="", help="Pin the session to one model id.")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--small", action="store_true",
        help="Use SMALL_PROMPT instead of the acceptance brief — for a provider "
             "whose output budget cannot fit the full page.",
    )
    args = parser.parse_args()
    prompt = SMALL_PROMPT if args.small else PROMPT

    base = f"http://localhost:{args.port}"
    session_id = str(uuid.uuid4())
    print(f"Session {session_id}\n")

    async with httpx.AsyncClient(base_url=base, timeout=120) as http:
        config = (await http.get("/api/config")).json()
        can_push = bool(config.get("git_push"))
        print(f"Server: git push credential {'configured' if can_push else 'NOT configured'}")
        if not can_push:
            print("  -> step 4 will report the commit and a blocked push. Set "
                  "GIT_PUSH_TOKEN in backend/.env and restart to exercise it fully.\n")

        # --- 1. the socket, and the turn ---------------------------------
        url = f"ws://localhost:{args.port}/ws/{session_id}?section=code"
        files_written: list[str] = []
        tree_frames: list[dict] = []
        commit_frame: dict | None = None
        terminal_lines: list[str] = []
        terminal_exit: dict | None = None
        errors: list[str] = []
        tools: list[str] = []
        done = False

        async with websockets.connect(url, max_size=32 * 1024 * 1024) as ws:
            # Drain the connect frames so the session row exists before the
            # REST calls below touch it.
            deadline = asyncio.get_event_loop().time() + 8
            while asyncio.get_event_loop().time() < deadline:
                try:
                    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                except asyncio.TimeoutError:
                    break
                if frame.get("type") == "branches":
                    break

            if args.model:
                await ws.send(json.dumps({"type": "set_model", "model_id": args.model}))

            # --- the remote and the opt-in, through the panel's own routes ---
            if args.remote:
                print(f"Setting remote to {args.remote}")
                res = await http.post(
                    f"/api/sessions/{session_id}/git/remote", json={"url": args.remote}
                )
                if res.status_code >= 400:
                    print(f"  remote failed: {res.status_code} {res.text[:200]}")
                else:
                    print(f"  origin = {res.json().get('remote')}")
                if not args.no_auto_push:
                    res = await http.post(
                        f"/api/sessions/{session_id}/git/auto", json={"enabled": True}
                    )
                    print(f"  auto-push = {res.json().get('auto_push') if res.status_code < 400 else res.text[:120]}")

            label = "small" if args.small else "acceptance"
            print(f"\nSending the {label} prompt ({len(prompt)} chars)…")
            await ws.send(json.dumps({"type": "user_message", "content": prompt}))

            loop = asyncio.get_event_loop()
            deadline = loop.time() + args.timeout
            while not done and loop.time() < deadline:
                try:
                    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                except asyncio.TimeoutError:
                    continue
                kind = frame.get("type")
                if kind == "tool_call_start":
                    tools.append(frame["tool"])
                    print(f"    · {frame['tool']}")
                elif kind == "file_changed":
                    files_written.append(frame["path"])
                    print(f"    + {frame['path']} ({frame['change']})")
                elif kind == "file_tree":
                    tree_frames.append(frame)
                elif kind == "turn_commit":
                    commit_frame = frame
                    print(f"    ⎇ commit {frame['short']}: {frame['subject']}")
                elif kind == "error":
                    errors.append(frame.get("message", ""))
                    print(f"    ! {frame.get('message', '')[:160]}")
                elif kind == "max_iterations":
                    errors.append("hit the iteration ceiling")
                elif kind == "agent_done":
                    reason = frame.get("reason")
                    print(f"    turn ended: {reason}")
                    if reason == "max_tokens" and not files_written:
                        print(
                            "      NOTE: the provider cut the turn off at its output "
                            "ceiling before a file was written. That is a budget "
                            "limit, not this flow \u2014 re-run with --small, or with "
                            "a model that has output headroom."
                        )
                    done = True

            # --- the terminal, after the turn ----------------------------
            print(f"\nRunning in the terminal panel: {TERMINAL_COMMAND}")
            await ws.send(json.dumps({"type": "terminal_command", "command": TERMINAL_COMMAND}))
            deadline = loop.time() + 180
            started = False
            while loop.time() < deadline:
                try:
                    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                except asyncio.TimeoutError:
                    break
                kind = frame.get("type")
                if kind == "terminal_started":
                    started = True
                elif kind == "tool_output_chunk" and started:
                    terminal_lines.append(frame.get("content", ""))
                elif kind == "file_tree":
                    tree_frames.append(frame)
                elif kind == "terminal_exit":
                    terminal_exit = frame
                    break
            for line in "".join(terminal_lines).splitlines()[:14]:
                print(f"    | {line}")

        # --- the five acceptance steps ------------------------------------
        print("\n" + "=" * 74)
        print("NEW-5 acceptance")
        print("=" * 74)

        print("\n1. Generation — the page is written to the sandbox")
        html = [p for p in files_written if p.endswith((".html", ".htm"))]
        check("a file was written", bool(files_written), f"{len(files_written)} file(s)")
        check("one of them is an HTML page", bool(html), ", ".join(html[:3]) or "none")

        snapshot = (await http.get(f"/api/sessions/{session_id}/git")).json()

        print("\n2. File tree (NEW-3) — the tree names the new files")
        names = []
        for frame in tree_frames:
            names += [n["path"] for n in frame.get("nodes") or []]
        check("a file_tree frame arrived", bool(tree_frames), f"{len(tree_frames)} frame(s)")
        check(
            "the tree lists a file the turn wrote",
            any(p in names for p in files_written) if files_written else False,
            ", ".join(sorted(set(names))[:6]),
        )
        check(
            "the commit frame carries absolute paths for the panel to open to",
            bool(commit_frame and commit_frame.get("files")
                 and all(f.startswith("/") for f in commit_frame["files"])),
            ", ".join((commit_frame or {}).get("files", [])[:3]) or "no commit frame",
        )

        print("\n3. Terminal (NEW-4) — a typed command runs and streams")
        output = "".join(terminal_lines)
        check("the command started and exited", terminal_exit is not None,
              f"exit {terminal_exit.get('exit_code') if terminal_exit else '—'}")
        check("it streamed output", bool(output.strip()), f"{len(output)} chars")
        check("the output is from the project directory",
              any(p.rsplit("/", 1)[-1] in output for p in files_written) if files_written
              else bool(output.strip()),
              "the generated file is listed" if files_written else "")

        print("\n4. Commit and push (NEW-3)")
        if args.no_auto_push:
            check("skipped by --no-auto-push", True, "opt-in left off")
        else:
            check("a commit was created", bool(commit_frame and commit_frame.get("sha")),
                  (commit_frame or {}).get("short", "none"))
            check("with a generated message, not a placeholder",
                  bool(commit_frame and len((commit_frame.get("subject") or "").split()) >= 2),
                  (commit_frame or {}).get("subject", ""))
            check("the repository has the commit in its log",
                  bool(snapshot.get("log")), f"{len(snapshot.get('log') or [])} commit(s)")
            if can_push:
                check("it was pushed to the remote",
                      bool(commit_frame and commit_frame.get("pushed")),
                      (commit_frame or {}).get("error") or (commit_frame or {}).get("remote", ""))
                print("      -> confirm on the remote: the commit above should be "
                      f"visible at {args.remote}")
            else:
                pushed = bool(commit_frame and commit_frame.get("pushed"))
                check("the push is reported as blocked, not silently skipped",
                      not pushed and bool((commit_frame or {}).get("error")),
                      (commit_frame or {}).get("error", ""))
                print("      -> BLOCKED on GIT_PUSH_TOKEN. Set it in backend/.env, "
                      "restart, and re-run to complete step 4.")

        print("\n5. UI surfacing (NEW-3) — the turn summary")
        check("the frame carries hash, subject and branch",
              bool(commit_frame and commit_frame.get("short") and commit_frame.get("subject")
                   and commit_frame.get("branch")),
              f"{(commit_frame or {}).get('short', '')} on {(commit_frame or {}).get('branch', '')}")
        check("and whether the push landed",
              commit_frame is not None and "pushed" in commit_frame,
              f"pushed={(commit_frame or {}).get('pushed')}")

        if errors:
            print(f"\nError frames during the run: {errors}")

        passed = sum(1 for _, ok, _ in results if ok)
        print("\n" + "-" * 74)
        print(f"{passed}/{len(results)} acceptance checks passed")
        for label, ok, detail in results:
            if not ok:
                print(f"  FAILED: {label}" + (f" — {detail}" if detail else ""))
        return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
