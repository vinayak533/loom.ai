"""End-to-end check of the live-preview and workspace features.

Runs against a real E2B sandbox — it needs `E2B_API_KEY` in `backend/.env`.

    python scripts/test_preview_flow.py

Covers the paths that cannot be tested without a sandbox: port forwarding
actually resolving, the readiness probe rejecting a server that never binds,
the watcher noticing an exit, zip export, and inline-edit round trips.
"""

from __future__ import annotations

import asyncio
import os
import sys
import zipfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import httpx  # noqa: E402

from app.emitter import Emitter, registry  # noqa: E402
from app.tools import workspace  # noqa: E402
from app.tools.preview import PreviewError, preview_manager  # noqa: E402
from app.tools.sandbox import sandbox_manager  # noqa: E402

SESSION = "preview-selftest"
PORT = 3131

PASS, FAIL = "  PASS", "  FAIL"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{PASS if ok else FAIL}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


async def drain(emitter: Emitter) -> list[dict]:
    # `Emitter.emit` schedules onto the loop rather than putting directly (its
    # callers may be off-loop), so a drain has to let the loop run first.
    await asyncio.sleep(0.05)
    out = []
    while not emitter.queue.empty():
        ev = emitter.queue.get_nowait()
        if ev:
            out.append(ev)
    return out


async def main() -> int:
    emitter = Emitter()
    registry.register(emitter, SESSION)

    print("\n1. sandbox + project files")
    sandbox = await sandbox_manager.get(SESSION)
    print(f"     sandbox {sandbox.sandbox_id}")
    await sandbox.files.write(
        "/home/user/index.html",
        "<!doctype html><title>Atlas preview</title><h1>hello from the sandbox</h1>",
    )
    check("wrote a file into the workspace", True)

    print("\n2. start_dev_server")
    entry = await preview_manager.start(
        SESSION,
        f"python3 -m http.server {PORT} --bind 0.0.0.0",
        PORT,
        emitter,
    )
    check("preview started", bool(entry.url), entry.url)
    events = await drain(emitter)
    check(
        "emitted preview_ready",
        any(e["type"] == "preview_ready" for e in events),
        ", ".join(e["type"] for e in events),
    )

    print("\n3. the forwarded URL actually serves")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        res = await client.get(entry.url)
    check("HTTP 200 from the preview URL", res.status_code == 200, str(res.status_code))
    check("served our content", "hello from the sandbox" in res.text)

    print("\n4. status endpoint reports it")
    status = preview_manager.status(SESSION)
    check("status.running", status["running"] is True)
    check("status.port", status["port"] == PORT, str(status["port"]))

    print("\n5. a server that never binds fails loudly")
    try:
        await preview_manager.start(SESSION, "echo 'not a server'", 4999, emitter)
        check("rejects a non-server command", False, "no error raised")
    except PreviewError as exc:
        check("rejects a non-server command", True, str(exc)[:90])
    await drain(emitter)
    # Starting the bad one replaced the good one, so bring it back for the rest.
    entry = await preview_manager.start(
        SESSION, f"python3 -m http.server {PORT} --bind 0.0.0.0", PORT, emitter
    )
    await drain(emitter)

    print("\n6. the watcher notices the process dying")
    await sandbox.commands.run(f"pkill -f 'http.server {PORT}'", timeout=15)
    for _ in range(30):
        await asyncio.sleep(0.5)
        events = await drain(emitter)
        if any(e["type"] == "preview_error" and e["fatal"] for e in events):
            check("emitted a fatal preview_error on exit", True)
            break
    else:
        check("emitted a fatal preview_error on exit", False, "timed out after 15s")
    check("registry cleared", preview_manager.status(SESSION)["running"] is False)

    print("\n7. reserved / invalid ports")
    for bad in (49982, 70000):
        try:
            await preview_manager.start(SESSION, "true", bad, emitter)
            check(f"rejects port {bad}", False)
        except PreviewError:
            check(f"rejects port {bad}", True)

    print("\n8. inline editing round trip")
    read = await workspace.read_text(SESSION, "index.html")
    check("read a file", "hello from the sandbox" in read["content"])
    saved = await workspace.write_text(
        SESSION, "index.html", "<!doctype html><h1>edited by hand</h1>"
    )
    check("wrote a file", saved["change"] == "modified", saved["change"])
    check("produced a diff", "edited by hand" in saved["diff"])
    back = await workspace.read_text(SESSION, "index.html")
    check("edit landed in the sandbox", "edited by hand" in back["content"])

    print("\n9. path confinement")
    for escape in ("../../etc/passwd", "/etc/passwd"):
        try:
            await workspace.read_text(SESSION, escape)
            check(f"refuses `{escape}`", False, "read succeeded")
        except workspace.WorkspaceError:
            check(f"refuses `{escape}`", True)

    print("\n10. zip export")
    await sandbox.files.write("/home/user/src/app.js", "console.log('hi')\n")
    await sandbox.commands.run(
        "mkdir -p /home/user/node_modules && "
        "echo junk > /home/user/node_modules/should-not-ship.js",
        timeout=20,
    )
    data, summary = await workspace.export_zip(SESSION)
    archive = zipfile.ZipFile(BytesIO(data))
    names = archive.namelist()
    check("zip is valid", archive.testzip() is None)
    check("contains project files", "index.html" in names and "src/app.js" in names, str(names))
    check(
        "excludes node_modules",
        not any(n.startswith("node_modules") for n in names),
    )
    check("summary counts files", summary.get("files", 0) >= 2, str(summary))

    print("\n11. teardown tears down forwarding")
    await preview_manager.start(
        SESSION, f"python3 -m http.server {PORT} --bind 0.0.0.0", PORT, emitter
    )
    check("running again", preview_manager.status(SESSION)["running"] is True)
    await sandbox_manager.destroy(SESSION)
    check(
        "sandbox teardown cleared the preview",
        preview_manager.status(SESSION)["running"] is False,
    )

    registry.unregister(emitter.id)
    print(f"\n{'-' * 60}")
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
