"""Tool implementations. Every one of these runs against the session sandbox.

Each returns a `ToolResult`; the caller turns that into an internal
`tool_result` block and forwards `result.events` to the browser.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import posixpath
from dataclasses import dataclass, field
from typing import Any

from app import events as ev
from app.config import get_settings
from app.emitter import Emitter
from app.tools import git
from app.tools.preview import PreviewError, preview_manager
from app.tools.sandbox import WORKDIR, SandboxUnavailable, sandbox_manager

log = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 30_000
MAX_TREE_ENTRIES = 400


@dataclass
class ToolResult:
    output: str
    success: bool = True
    events: list[dict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    dropped = len(text) - limit
    return f"{head}\n\n... [{dropped} characters truncated] ...\n\n{tail}"


def _abs(path: str) -> str:
    path = (path or "").strip() or WORKDIR
    if not path.startswith("/"):
        path = posixpath.join(WORKDIR, path)
    return posixpath.normpath(path)


def _unified_diff(old: str, new: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a{path}",
            tofile=f"b{path}",
            n=3,
        )
    )


async def _read_or_none(sandbox, path: str) -> str | None:
    try:
        return await sandbox.files.read(path)
    except Exception:  # noqa: BLE001 - "does not exist" is the common case
        return None


# ---------------------------------------------------------------------------
# bash
# ---------------------------------------------------------------------------


async def bash_execute(
    session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    command = (args.get("command") or "").strip()
    if not command:
        return ToolResult("Error: `command` was empty.", success=False)

    settings = get_settings()
    sandbox = await sandbox_manager.get(session_id)

    def on_stdout(chunk: str) -> None:
        if emitter:
            emitter.emit(ev.tool_output_chunk(call_id, "stdout", chunk))

    def on_stderr(chunk: str) -> None:
        if emitter:
            emitter.emit(ev.tool_output_chunk(call_id, "stderr", chunk))

    try:
        result = await sandbox.commands.run(
            command,
            cwd=WORKDIR,
            timeout=settings.bash_timeout_seconds,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )
        stdout, stderr, exit_code = result.stdout, result.stderr, result.exit_code
    except Exception as exc:  # noqa: BLE001 - non-zero exit raises in the SDK
        stdout = getattr(exc, "stdout", "") or ""
        stderr = getattr(exc, "stderr", "") or str(exc)
        exit_code = getattr(exc, "exit_code", 1) or 1

    body = ""
    if stdout:
        body += stdout
    if stderr:
        body += ("\n" if body else "") + f"[stderr]\n{stderr}"
    if not body:
        body = "(no output)"
    body = _truncate(body)
    output = f"exit_code: {exit_code}\n{body}"

    # The refreshed tree is for the sidebar only — the model never sees it —
    # so it must not sit between this command finishing and the next model
    # call. It walks the sandbox over the network, which took longer than most
    # commands it followed.
    _refresh_tree_later(sandbox, session_id, emitter)
    return ToolResult(
        output=output,
        success=exit_code == 0,
        meta={"exit_code": exit_code},
    )


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------


async def read_file(
    session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    path = _abs(args.get("path", ""))
    sandbox = await sandbox_manager.get(session_id)
    content = await _read_or_none(sandbox, path)
    if content is None:
        return ToolResult(f"Error: could not read `{path}`. It may not exist.", False)

    numbered = "\n".join(
        f"{i:>5}│{line}" for i, line in enumerate(content.splitlines(), start=1)
    )
    return ToolResult(_truncate(numbered) or "(empty file)")


async def write_file(
    session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    path = _abs(args.get("path", ""))
    content = args.get("content", "")
    sandbox = await sandbox_manager.get(session_id)

    previous = await _read_or_none(sandbox, path)
    try:
        await sandbox.files.write(path, content)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Error writing `{path}`: {exc}", False)

    diff = _unified_diff(previous or "", content, path)
    change = "modified" if previous is not None else "created"
    side = [ev.file_changed(path=path, diff=diff, content=content, change=change)]
    # Same as bash: the sidebar refresh is a background concern, and a new file
    # only ever adds a node the diff panel has already told the user about.
    _refresh_tree_later(sandbox, session_id, emitter)
    lines = len(content.splitlines())
    return ToolResult(f"Wrote {lines} lines to `{path}`.", events=side)


async def edit_file(
    session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    path = _abs(args.get("path", ""))
    old_str = args.get("old_str", "")
    new_str = args.get("new_str", "")

    if old_str == new_str:
        return ToolResult("Error: `old_str` and `new_str` are identical.", False)

    sandbox = await sandbox_manager.get(session_id)
    previous = await _read_or_none(sandbox, path)
    if previous is None:
        return ToolResult(f"Error: could not read `{path}`. It may not exist.", False)

    occurrences = previous.count(old_str)
    if occurrences == 0:
        return ToolResult(
            f"Error: `old_str` was not found in `{path}`. Read the file again and "
            "match the exact text, including indentation.",
            False,
        )
    if occurrences > 1:
        return ToolResult(
            f"Error: `old_str` appears {occurrences} times in `{path}`. Include "
            "more surrounding context so the match is unique.",
            False,
        )

    updated = previous.replace(old_str, new_str, 1)
    try:
        await sandbox.files.write(path, updated)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Error writing `{path}`: {exc}", False)

    diff = _unified_diff(previous, updated, path)
    side = [ev.file_changed(path=path, diff=diff, content=updated, change="modified")]
    return ToolResult(f"Edited `{path}`.\n\n{_truncate(diff, 4000)}", events=side)


async def list_files(
    session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    path = _abs(args.get("path") or WORKDIR)
    sandbox = await sandbox_manager.get(session_id)
    nodes = await _list_tree(sandbox, path)
    if nodes is None:
        return ToolResult(f"Error: could not list `{path}`.", False)

    rendered = _render_tree(nodes)
    return ToolResult(
        rendered or "(empty directory)", events=[_file_tree_event(nodes, path)]
    )


# ---------------------------------------------------------------------------
# dev server / live preview
# ---------------------------------------------------------------------------


async def start_dev_server(
    session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    """Bring up a dev server and forward its port to the browser.

    The `preview_ready` event is emitted by the manager rather than returned as
    a side event here: it fires only once the port genuinely answers, which is
    after this coroutine has already been waiting on it for however long the
    build took.
    """
    command = (args.get("command") or "").strip()
    raw_port = args.get("port")
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        return ToolResult(
            f"Error: `port` must be a number, got {raw_port!r}.", success=False
        )

    try:
        entry = await preview_manager.start(session_id, command, port, emitter)
    except PreviewError as exc:
        return ToolResult(f"Error: {exc}", success=False)

    return ToolResult(
        f"Dev server running: `{command}` on port {port}.\n"
        f"The user's Preview panel is now live at {entry.url}\n"
        "It stays running in the background — do not start it again, and do not "
        "run it through the shell. Further file edits are picked up by the "
        "server's own hot reload.",
        meta={"preview_url": entry.url, "port": port},
    )


async def stop_dev_server(
    session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    stopped = await preview_manager.stop(session_id, emitter, reason="agent")
    if not stopped:
        return ToolResult("No dev server was running for this session.")
    return ToolResult("Dev server stopped and the preview closed.")


# ---------------------------------------------------------------------------
# web search
# ---------------------------------------------------------------------------


async def web_search(
    session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    query = (args.get("query") or "").strip()
    if not query:
        return ToolResult("Error: `query` was empty.", False)
    num = max(1, min(int(args.get("num_results") or 5), 10))

    settings = get_settings()
    if not settings.exa_api_key:
        return ToolResult(
            "Error: web search is not configured (EXA_API_KEY is unset).", False
        )

    def _search() -> Any:
        from exa_py import Exa

        exa = Exa(api_key=settings.exa_api_key)
        return exa.search_and_contents(
            query, num_results=num, text={"max_characters": 1200}, type="auto"
        )

    try:
        response = await asyncio.to_thread(_search)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Error: Exa search failed: {exc}", False)

    results = getattr(response, "results", []) or []
    if not results:
        return ToolResult(f"No results for `{query}`.")

    chunks = []
    payload = []
    for i, r in enumerate(results, start=1):
        title = getattr(r, "title", None) or "(untitled)"
        url = getattr(r, "url", "") or ""
        text = (getattr(r, "text", None) or "").strip()
        chunks.append(f"{i}. {title}\n   {url}\n   {text[:800]}")
        payload.append({"title": title, "url": url, "snippet": text[:400]})

    return ToolResult("\n\n".join(chunks), meta={"results": payload})


# ---------------------------------------------------------------------------
# file tree helpers
# ---------------------------------------------------------------------------


async def _list_tree(sandbox, root: str, depth: int = 3) -> list[dict] | None:
    """Breadth-limited directory tree for the sidebar."""
    try:
        return await _walk(sandbox, root, depth, [0])
    except Exception:  # noqa: BLE001
        log.debug("tree walk failed for %s", root, exc_info=True)
        return None


async def _walk(sandbox, path: str, depth: int, budget: list[int]) -> list[dict]:
    if depth <= 0 or budget[0] >= MAX_TREE_ENTRIES:
        return []
    try:
        entries = await sandbox.files.list(path)
    except Exception:  # noqa: BLE001
        return []

    nodes: list[dict] = []
    subdirs: list[dict] = []
    for entry in entries:
        name = getattr(entry, "name", None) or str(entry)
        if name.startswith(".") and name not in {".env", ".gitignore"}:
            continue
        raw_type = getattr(entry, "type", None)
        type_name = getattr(raw_type, "value", raw_type)
        is_dir = str(type_name).lower().endswith("dir")
        full = getattr(entry, "path", None) or posixpath.join(path, name)
        budget[0] += 1
        node: dict[str, Any] = {
            "name": name,
            "path": full,
            "type": "dir" if is_dir else "file",
        }
        nodes.append(node)
        if is_dir:
            subdirs.append(node)
        if budget[0] >= MAX_TREE_ENTRIES:
            break

    # One level at a time, all siblings at once. Recursing serially made the
    # walk cost one sandbox round trip per directory in sequence; a shallow
    # project with a dozen folders spent most of the walk waiting.
    if subdirs:
        children = await asyncio.gather(
            *(_walk(sandbox, node["path"], depth - 1, budget) for node in subdirs)
        )
        for node, kids in zip(subdirs, children):
            node["children"] = kids

    nodes.sort(key=lambda n: (n["type"] != "dir", n["name"].lower()))
    return nodes


# --- background sidebar refresh --------------------------------------------

#: session_id -> the in-flight refresh task. At most one walk runs per session
#: at a time; a request that arrives while one is running sets `_TREE_AGAIN`
#: instead of starting a rival walk, so a burst of file writes collapses into
#: two walks rather than one per write — and the last one still reflects the
#: final state.
_TREE_TASKS: dict[str, asyncio.Task] = {}
_TREE_AGAIN: set[str] = set()


def _refresh_tree_later(sandbox, session_id: str, emitter: Emitter | None) -> None:
    """Walk the sandbox and emit a `file_tree` event, off the critical path."""
    if emitter is None:
        return
    existing = _TREE_TASKS.get(session_id)
    if existing is not None and not existing.done():
        _TREE_AGAIN.add(session_id)
        return

    async def _run() -> None:
        try:
            while True:
                _TREE_AGAIN.discard(session_id)
                nodes = await _list_tree(sandbox, WORKDIR)
                emitter.emit(_file_tree_event(nodes))
                if session_id not in _TREE_AGAIN:
                    return
        except Exception:  # noqa: BLE001 - a stale sidebar is not a failure
            log.debug("background tree refresh failed", exc_info=True)
        finally:
            _TREE_AGAIN.discard(session_id)
            _TREE_TASKS.pop(session_id, None)

    _TREE_TASKS[session_id] = asyncio.create_task(_run())


def _file_tree_event(nodes: list[dict] | None, path: str = WORKDIR) -> dict:
    return ev.file_tree(path=path, nodes=nodes or [])


def _render_tree(nodes: list[dict], prefix: str = "") -> str:
    lines: list[str] = []
    for i, node in enumerate(nodes):
        last = i == len(nodes) - 1
        lines.append(f"{prefix}{'└── ' if last else '├── '}{node['name']}")
        if node.get("children"):
            lines.append(
                _render_tree(node["children"], prefix + ("    " if last else "│   "))
            )
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------


async def git_tool(
    session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    """Dispatch one `git` operation and report git state to the UI.

    Every branch ends by emitting `git_state`, including the read-only ones:
    the panel is cheap to refresh and a status the user can see is worth more
    than the round trip it costs.
    """
    operation = (args.get("operation") or "").strip().lower()

    try:
        if operation == "init":
            info = await git.init(session_id)
            body = (
                f"Initialised an empty repository at {info['repo']} on branch "
                f"`{info['branch']}`, with a .gitignore for build output."
                if info["created"]
                else f"Already a git repository at {info['repo']} "
                f"(branch `{info['branch']}`)."
            )

        elif operation == "status":
            entries = await git.status(session_id)
            if not await git.is_repo(session_id):
                body = "Not a git repository yet. Run `git` with operation `init` first."
            elif not entries:
                body = "Working tree clean — nothing to commit."
            else:
                body = "\n".join(
                    f"  {(e['index'] or e['worktree'] or 'changed'):<10} {e['path']}"
                    for e in entries
                )
                body = f"{len(entries)} change(s):\n{body}"

        elif operation == "diff":
            text = await git.diff(
                session_id,
                path=args.get("path"),
                staged=bool(args.get("staged")),
            )
            body = _truncate(text) if text.strip() else "No differences."

        elif operation == "commit":
            result = await git.commit(
                session_id,
                message=args.get("message") or "",
                paths=args.get("paths") or None,
            )
            if not result["committed"]:
                body = result["reason"]
            else:
                c = result["commit"] or {}
                body = (
                    f"Committed {c.get('short', '')} on `{result['branch']}`: "
                    f"{c.get('subject', '')}"
                )

        elif operation == "log":
            commits = await git.log(session_id, limit=args.get("limit") or 30)
            if not commits:
                body = "No commits yet."
            else:
                body = "\n".join(
                    f"  {c['short']}  {c['date'][:10]}  {c['subject']}" for c in commits
                )
                body = f"{len(commits)} commit(s), newest first:\n{body}"

        else:
            return ToolResult(
                f"Error: unknown git operation `{operation}`. "
                "Use one of: init, status, diff, commit, log.",
                success=False,
            )
    except git.GitError as exc:
        return ToolResult(f"Error: {exc}", success=False)

    if emitter:
        try:
            snap = await git.snapshot(session_id)
            emitter.emit(
                ev.git_state(
                    repo=snap["repo"],
                    branch=snap["branch"],
                    status=snap["status"],
                    log=snap["log"],
                    path=snap["path"],
                )
            )
        except Exception:  # noqa: BLE001
            # The panel going stale is not worth failing the tool call over.
            log.debug("git_state emit failed", exc_info=True)

    return ToolResult(output=body, meta={"operation": operation})


HANDLERS = {
    "bash_execute": bash_execute,
    "git": git_tool,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_files": list_files,
    "web_search": web_search,
    "start_dev_server": start_dev_server,
    "stop_dev_server": stop_dev_server,
}


async def run_tool(
    name: str, session_id: str, args: dict, call_id: str, emitter: Emitter | None
) -> ToolResult:
    handler = HANDLERS.get(name)
    if handler is None:
        return ToolResult(f"Error: unknown tool `{name}`.", False)
    try:
        return await handler(session_id, args, call_id, emitter)
    except SandboxUnavailable as exc:
        return ToolResult(f"Error: {exc}", False)
    except Exception as exc:  # noqa: BLE001 - tool errors go back to the model
        log.exception("Tool %s failed", name)
        return ToolResult(f"Error: tool `{name}` raised {type(exc).__name__}: {exc}", False)
