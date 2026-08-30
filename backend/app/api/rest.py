"""REST surface: sessions, history replay, and uploads."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status

from app import events as ev
from app.agent import runner
from app.agent.llm import generate_project_meta
from app.api.auth import bearer_user
from app.api.ownership import (
    require_project,
    require_project_file,
    require_session,
    require_session_write,
)
from app.api.ratelimit import RateLimiter
from app.config import get_settings
from app.db import repository
from app.emitter import registry as emitter_registry
from app.files import MAX_UPLOAD_BYTES, PDF_TYPE, UploadRejected, save_upload
from app.llm_router import (
    AUTO_MODEL_ID,
    MODEL_REGISTRY,
    HINT_MODEL,
    auto_pool_available,
    auto_route,
    effective_default_model,
    available_models,
    display_name,
    section_default_model,
    section_default_models,
)
from app import memory
from app.learn import ingest
from app.sources import ingest_youtube, is_youtube_url
from app.tools import git, workspace
from app.tools.preview import preview_manager
from app.tools.sandbox import SandboxUnavailable, sandbox_manager

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_upload_limiter = RateLimiter(get_settings().rate_limit_uploads_per_minute)


@router.get("/config")
async def config():
    """Which integrations are wired up. Booleans only — never key values."""
    summary = get_settings().public_summary()
    summary["models"] = available_models()
    # `default_model_id` is what the client opens every new session on, so it
    # has to name a model the client can actually see in `models`. The raw
    # setting is only a preference: when its provider key is unset the model is
    # hidden from the list, and reporting it here left the selector showing an
    # id it could not resolve and a first turn that could not run. Report the
    # preference separately so the UI can still say what is configured.
    summary["configured_default_model_id"] = summary["default_model_id"]
    summary["default_model_id"] = effective_default_model()
    # Same treatment for the per-section map: `public_summary()` reports the
    # configured preferences, and the client needs the models it will actually
    # get. A section whose preferred model has no key resolves to one that has.
    summary["configured_default_model_ids"] = summary["default_model_ids"]
    summary["default_model_ids"] = section_default_models()
    # Same reasoning for the section table the Auto label reads from: it is
    # configuration and can name an unkeyed model, and `auto_route()` already
    # resolves past one at dispatch. Report what will actually run.
    summary["auto_routes"] = {
        section: auto_route(section) for section in summary["auto_routes"]
    }
    # And once more for the same reason. `public_summary()` derives this from
    # "is OPENCODE_API_KEY set", which is a proxy that can now be wrong: the
    # pool also breaks when one of its models is unavailable for some other
    # reason — an entry repointed at a refused vendor, say. Ask the router
    # whether it can actually task-route rather than inferring it from a key,
    # so the UI does not promise per-task routing that will not happen.
    summary["auto_task_routing"] = auto_pool_available()
    return summary


# --- sessions --------------------------------------------------------------


#: The two conversational surfaces that share this session table. Validated
#: rather than passed through, so a typo'd section cannot create a row that
#: neither history list will ever match.
_SECTIONS = ("chat", "code")


def _section(value: str | None) -> str:
    return value if value in _SECTIONS else "chat"


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    user_id: str | None = Depends(bearer_user),
    model_id: str | None = None,
    section: str | None = None,
):
    """Create a session, tagged with the surface that asked for it.

    `section` is what keeps Chat's history and Code's history apart — they are
    otherwise identical rows. Defaults to 'chat' when absent so an older client
    cannot create an untagged session.
    """
    resolved_section = _section(section)
    # The *section's* default, not the roster-wide one: a Code session opens on
    # the Code model and a Chat session on the Chat model. An explicit
    # `model_id` from the client still wins, which is what makes "new session
    # keeping my current pick" work.
    mid = model_id or section_default_model(resolved_section)
    return await repository.create_session(
        str(uuid.uuid4()), user_id, model_id=mid, section=resolved_section
    )


@router.get("/sessions")
async def list_sessions(
    user_id: str | None = Depends(bearer_user),
    archived: bool = False,
    section: str | None = None,
):
    """The session shelf, already ordered: pinned first, then by activity.

    `archived=true` returns the archive instead. The two lists are disjoint.
    `section` scopes to 'chat' or 'code'; a Chat session must never appear in
    Code's list, or the other way round.
    """
    return await repository.list_sessions(
        user_id, archived=archived, section=_section(section)
    )


@router.get("/sessions/search")
async def search_sessions(
    q: str = "",
    user_id: str | None = Depends(bearer_user),
    section: str | None = None,
    agent_id: str | None = None,
    limit: int = 30,
):
    """Find sessions by title or by something said in them.

    Two indexed `ilike` scans and a merge — no model is involved, and none
    should be. A search box that waits on a generative call is both slower and
    worse than one that does not, and this is a lookup, not a question.

    Declared *above* `/sessions/{session_id}` on purpose. FastAPI matches
    routes in declaration order, so the other way round "search" is swallowed
    as a session id and this endpoint is unreachable.

    An empty `q` returns an empty list rather than the whole shelf: the caller
    for that is `GET /sessions`, and quietly answering a different question is
    how a debounced input ends up fetching everything on every backspace.
    """
    return await repository.search_sessions(
        q,
        user_id=user_id,
        section=_section(section) if section else None,
        agent_id=agent_id,
        limit=max(1, min(limit, 50)),
    )


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    payload: dict,
    user_id: str | None = Depends(bearer_user),
):
    """Pin / archive / rename a session. Every field is independent and optional."""
    await require_session(user_id, session_id)
    payload = payload or {}
    pinned = payload.get("is_pinned")
    archived = payload.get("is_archived")
    title = payload.get("title")
    description = payload.get("description")

    if all(v is None for v in (pinned, archived, title, description)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Send `is_pinned`, `is_archived`, `title` and/or `description`.",
        )

    row = None
    if title is not None or description is not None:
        row = await repository.set_session_meta(
            session_id,
            title=None if title is None else str(title).strip(),
            description=None if description is None else str(description).strip(),
        )
    if pinned is not None or archived is not None:
        row = await repository.set_session_flags(
            session_id,
            is_pinned=None if pinned is None else bool(pinned),
            is_archived=None if archived is None else bool(archived),
        ) or row

    # Without Supabase there is no row to return, but the call is still a
    # success — the frontend keeps its optimistic state either way.
    return row or {
        k: v
        for k, v in {
            "id": session_id,
            "is_pinned": pinned,
            "is_archived": archived,
            "title": title,
            "description": description,
        }.items()
        if v is not None
    }


@router.post("/sessions/{session_id}/name")
async def name_session(session_id: str, user_id: str | None = Depends(bearer_user)):
    """Name and describe a session from what it actually built.

    Deliberately on demand rather than automatic. The session's `title` is
    already generated from its opening message, which is cheap and immediate;
    this reads the whole transcript, so it costs a real model call and is worth
    making the user's choice.
    """
    await require_session(user_id, session_id)
    state = await runner.get_state(session_id)
    transcript = _transcript_for_naming(state.get("messages") or [])
    if not transcript:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This session has nothing in it yet — send a message first.",
        )

    meta = await generate_project_meta(transcript)
    if not meta:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not generate a name right now. Try again, or type one in.",
        )

    row = await repository.set_session_meta(
        session_id, title=meta["title"], description=meta["description"]
    )
    # The generated pair always wins in the response. On a database that
    # predates the `description` column, `set_session_meta` falls back to
    # writing the title alone and returns a row with no description — the
    # caller asked for a description and computed one, so it is reported even
    # when it could not be stored.
    return {**(row or {"id": session_id}), **meta}


def _transcript_for_naming(messages: list[dict], limit: int = 14_000) -> str:
    """Flatten the checkpointed conversation into plain text for the namer.

    Tool *results* are dropped and tool *calls* are kept as one line each: the
    sequence of calls is the clearest signal of what was built ("wrote
    package.json, wrote App.tsx, started a dev server"), while their output is
    mostly stdout noise that would crowd out the prose.
    """
    lines: list[str] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, str):
            if role in {"user", "assistant"} and content.strip():
                lines.append(f"{role}: {content.strip()}")
            continue
        for block in content or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and block.get("text", "").strip():
                lines.append(f"{role}: {block['text'].strip()}")
            elif kind == "tool_use":
                args = block.get("input") or {}
                detail = args.get("path") or args.get("command") or args.get("query") or ""
                lines.append(f"[tool] {block.get('name')} {detail}".strip())

    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    # Keep both ends: the opening states the goal, the closing states the
    # result, and the middle is mostly iteration on files already named.
    head, tail = limit // 3, limit - limit // 3
    return f"{text[:head]}\n\n[...]\n\n{text[-tail:]}"


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, user_id: str | None = Depends(bearer_user)):
    await require_session(user_id, session_id)
    await sandbox_manager.destroy(session_id)
    await repository.delete_session(session_id)


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str, user_id: str | None = Depends(bearer_user)):
    """Full trace for a session.

    `checkpoint` is the LangGraph state (authoritative for resuming the agent);
    `log` is the flat Supabase row list including every tool call and result.

    `require_session_write`, not `require_session`, for the reason that helper
    exists: the browser mints a session id locally and the websocket handler is
    what inserts the row, so this endpoint is reachable — legitimately, on
    every fresh session — before there is a row to find. A hard 404 there was
    not protecting anything; it made the first load of every new session log a
    404 on the server and print a red console error in the browser, for a
    session that is simply empty. An empty trace is the honest answer. A row
    that *does* exist is still ownership-checked, which is the case that
    matters.
    """
    await require_session_write(user_id, session_id)
    state = await runner.get_state(session_id)
    return {
        "checkpoint": {
            "messages": state.get("messages") or [],
            "iterations": state.get("iterations") or 0,
            "usage": state.get("usage") or {},
        },
        "log": await repository.list_messages(session_id),
        "sandbox_id": sandbox_manager.sandbox_id_for(session_id),
    }


# --- workspace: inline editing, export, preview ----------------------------
# These act on the live sandbox rather than on the database. They are the
# user's own hands in the workspace, alongside the agent's.


@router.get("/sessions/{session_id}/file")
async def read_workspace_file(
    session_id: str,
    path: str,
    user_id: str | None = Depends(bearer_user),
):
    """Read one file for the inline editor.

    The diff panel already holds the content of files the *agent* touched; this
    is what makes every other file in the tree openable too.
    """
    await require_session_write(user_id, session_id)
    try:
        return await workspace.read_text(session_id, path)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.put("/sessions/{session_id}/file")
async def write_workspace_file(
    session_id: str,
    payload: dict,
    user_id: str | None = Depends(bearer_user),
):
    """Save an inline edit back to the sandbox.

    The response is shaped like a `file_changed` event so the client folds a
    manual save into the same state it keeps for the agent's edits. It is
    returned rather than broadcast: the browser that saved already has the
    content, and echoing it back over the socket would land in the editor the
    user is still typing in.
    """
    await require_session_write(user_id, session_id)
    payload = payload or {}
    path = payload.get("path")
    content = payload.get("content")
    if not path or content is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Send `path` and `content`."
        )
    try:
        return await workspace.write_text(session_id, str(path), str(content))
    except workspace.WorkspaceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/sessions/{session_id}/tree")
async def workspace_tree(session_id: str, user_id: str | None = Depends(bearer_user)):
    """Walk the sandbox now, rather than waiting for the agent's next turn.

    `file_tree` normally arrives as a side effect of the agent working. Opening
    a folder or renaming a file is the user working, and the sidebar has to
    catch up just the same.
    """
    await require_session_write(user_id, session_id)
    try:
        return await workspace.list_tree(session_id)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/sessions/{session_id}/files", status_code=status.HTTP_201_CREATED)
async def import_workspace_files(
    session_id: str,
    paths: str = Form(...),
    files: list[UploadFile] = File(...),
    dest: str = Form(""),
    user_id: str | None = Depends(bearer_user),
):
    """Import a batch of local files into the sandbox, keeping their structure.

    This is the "+ → Open folder" path. `paths` is a JSON array of relative
    paths parallel to `files`: a multipart part carries only a basename, and
    the whole point here is the folder structure the basename has dropped.

    A large folder arrives as several of these requests so the browser can show
    real progress, which is also why the rate limiter is not applied per batch —
    one folder would trip it. The size ceilings in `workspace` are the bound.
    """
    await require_session_write(user_id, session_id)
    import json

    try:
        relative = json.loads(paths)
        if not isinstance(relative, list):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "`paths` must be a JSON array."
        ) from exc

    if len(relative) != len(files):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "`paths` and `files` must have the same length.",
        )

    payload: list[tuple[str, bytes]] = []
    for rel, upload in zip(relative, files):
        payload.append((str(rel), await upload.read()))

    try:
        summary = await workspace.import_files(session_id, payload, dest=dest)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    await _announce_tree(session_id)
    return summary


@router.post("/sessions/{session_id}/fs")
async def workspace_fs(
    session_id: str,
    payload: dict,
    user_id: str | None = Depends(bearer_user),
):
    """Rename, delete, or create an entry in the sandbox tree.

    One route rather than three, because the client calls them from one context
    menu and they share every failure mode: a path outside the workspace, a
    sandbox that has been reaped, a name that already exists.
    """
    await require_session_write(user_id, session_id)
    payload = payload or {}
    op = str(payload.get("op") or "").lower()
    path = str(payload.get("path") or "")

    try:
        if op == "rename":
            result = await workspace.rename_entry(
                session_id, path, str(payload.get("name") or "")
            )
        elif op == "delete":
            result = await workspace.delete_entry(session_id, path)
        elif op in {"new_file", "new_dir"}:
            result = await workspace.create_entry(
                session_id, path, "dir" if op == "new_dir" else "file"
            )
        else:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "`op` must be one of: rename, delete, new_file, new_dir.",
            )
    except workspace.WorkspaceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    await _announce_tree(session_id)
    return {"op": op, **result}


async def _announce_tree(session_id: str) -> None:
    """Push a fresh `file_tree` to whichever socket this session is on.

    The caller already gets the tree back in its own response, so this is for
    the *other* surfaces — a second tab, and the diff panel's flash — and it is
    best effort: a stale sidebar is not worth failing a rename over.
    """
    emitter = emitter_registry.for_session(session_id)
    if emitter is None:
        return
    try:
        tree = await workspace.list_tree(session_id)
        emitter.emit(ev.file_tree(path=tree["path"], nodes=tree["nodes"]))
    except Exception:  # noqa: BLE001
        log.debug("Could not announce the tree for %s", session_id, exc_info=True)


@router.get("/sessions/{session_id}/export")
async def export_workspace(
    session_id: str,
    user_id: str | None = Depends(bearer_user),
):
    """Download the session's project as a zip.

    Streams the bytes straight through; the archive is built inside the sandbox
    and deleted there afterwards, so nothing is written to the backend's disk.
    """
    await require_session(user_id, session_id)
    try:
        data, summary = await workspace.export_zip(session_id)
    except workspace.WorkspaceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    row = await repository.get_session(session_id)
    filename = _export_filename(row.get("title") if row else None, session_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Read by the client to report "18 files · 240 KB" on the button.
            "X-Export-Files": str(summary.get("files", 0)),
            "X-Export-Bytes": str(summary.get("bytes", 0)),
            "Access-Control-Expose-Headers": (
                "Content-Disposition, X-Export-Files, X-Export-Bytes"
            ),
        },
    )


def _export_filename(title: str | None, session_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "").strip()).strip("-").lower()
    return f"{slug or 'project'}-{session_id[:8]}.zip"


@router.get("/sessions/{session_id}/preview")
async def preview_status(session_id: str, user_id: str | None = Depends(bearer_user)):
    """Whether a dev server is up for this session, and where.

    Lets a reconnecting browser restore a running preview: `preview_ready` is
    emitted once, at start, and a reload that happens afterwards would otherwise
    show an empty preview panel over a perfectly live server.
    """
    await require_session_write(user_id, session_id)
    return preview_manager.status(session_id)


@router.delete("/sessions/{session_id}/preview", status_code=status.HTTP_204_NO_CONTENT)
async def stop_preview(session_id: str, user_id: str | None = Depends(bearer_user)):
    """Stop the dev server from the UI rather than through the agent."""
    await require_session_write(user_id, session_id)
    await preview_manager.stop(session_id, reason="user")


# --- git -------------------------------------------------------------------
#
# The agent has a `git` tool, but the panel must work without spending a turn:
# a user who wants to see history, or to commit what they just edited by hand,
# should not have to ask the model to do it. Same operations, same module —
# these are just the door the UI comes in through.


def _broadcast_git(session_id: str, snapshot: dict) -> None:
    """Push repository state to whichever socket this session is on.

    Both mutating endpoints go through here. Without it the panel that made
    the change is the only thing that knows about it — a second tab on the
    same session, and the session's own agent trace, would carry on showing
    the previous history.
    """
    emitter = emitter_registry.for_session(session_id)
    if emitter is None or emitter.closed:
        return
    emitter.emit(
        ev.git_state(
            repo=snapshot["repo"],
            branch=snapshot["branch"],
            status=snapshot["status"],
            log=snapshot["log"],
            path=snapshot["path"],
        )
    )


@router.get("/sessions/{session_id}/git")
async def git_status(
    session_id: str,
    limit: int = 30,
    user_id: str | None = Depends(bearer_user),
):
    """Repository state for the Code panel: branch, working tree, history.

    Reports `repo: false` rather than 404 when no repository exists yet — "not
    initialised" is a state the panel renders (as an offer to start one), not
    an error. A session with no sandbox is the same answer: there is nothing
    to have a repository in yet, and waking a sandbox to say so would be worse.
    """
    await require_session_write(user_id, session_id)
    if sandbox_manager.sandbox_id_for(session_id) is None:
        return {"repo": False, "path": "", "branch": None, "status": [], "log": []}
    try:
        return await git.snapshot(session_id, limit=limit)
    except SandboxUnavailable:
        return {"repo": False, "path": "", "branch": None, "status": [], "log": []}


@router.post("/sessions/{session_id}/git/commit")
async def git_commit(
    session_id: str,
    payload: dict | None = None,
    user_id: str | None = Depends(bearer_user),
):
    """Commit the working tree from the UI. Initialises the repo if needed.

    Broadcasts `git_state` on the session's socket afterwards so an open Code
    panel updates itself, rather than relying on the caller to refetch.
    """
    await require_session_write(user_id, session_id)
    message = ((payload or {}).get("message") or "").strip()
    if not message:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A commit needs a message.")

    try:
        result = await git.commit(
            session_id, message=message, paths=(payload or {}).get("paths") or None
        )
    except git.GitError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SandboxUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    snapshot = await git.snapshot(session_id)
    _broadcast_git(session_id, snapshot)
    return {**result, "snapshot": snapshot}


@router.post("/sessions/{session_id}/git/init")
async def git_init(session_id: str, user_id: str | None = Depends(bearer_user)):
    """Start a repository for this session. Idempotent."""
    await require_session_write(user_id, session_id)
    try:
        info = await git.init(session_id)
    except (git.GitError, SandboxUnavailable) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    snapshot = await git.snapshot(session_id)
    # Init is a state change like a commit is, and was previously the one
    # mutating endpoint that stayed silent — so the panel that had just
    # created a repository carried on saying there wasn't one.
    _broadcast_git(session_id, snapshot)
    return {**info, "snapshot": snapshot}


# --- uploads ---------------------------------------------------------------


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    user_id: str | None = Depends(bearer_user),
):
    await require_session_write(user_id, session_id)
    allowed, retry_after = _upload_limiter.check(session_id)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many uploads. Try again in {retry_after}s.",
        )

    data = await file.read()
    try:
        return await save_upload(
            session_id,
            file.filename or "upload",
            file.content_type or "application/octet-stream",
            data,
        )
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc


# --- learning sources ------------------------------------------------------


@router.post("/sources/youtube", status_code=status.HTTP_201_CREATED)
async def add_youtube_source(
    payload: dict,
    user_id: str | None = Depends(bearer_user),
):
    """Resolve a YouTube URL to a transcript-backed source.

    Returns 201 even when the transcript is unavailable — the row carries an
    ``error`` string so the UI can show a degraded chip rather than losing the
    attachment entirely.
    """
    url = (payload or {}).get("url", "").strip()
    if not url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing `url`.")
    if not is_youtube_url(url):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a recognisable YouTube URL."
        )

    session_id = (payload or {}).get("session_id") or "anonymous"
    allowed, retry_after = _upload_limiter.check(session_id)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many source attachments. Try again in {retry_after}s.",
        )

    source = await ingest_youtube(url)
    return source.as_dict()


@router.get("/route")
async def route(section: str = "chat"):
    """How Auto mode will route.

    With OpenCode configured, Auto classifies each turn from live graph state,
    so there is no answer that can be computed up front — the response returns
    the hint table instead, and `section` only supplies the fallback. Without
    OpenCode, Auto degrades to the section table and `model_id` is exact.
    """
    task_routed = auto_pool_available()
    return {
        "section": section,
        "task_routed": task_routed,
        # The resolved model in fallback mode; Auto's starting point otherwise.
        "model_id": auto_route(section),
        "hints": {
            hint: {"model_id": mid, "name": display_name(mid)}
            for hint, mid in HINT_MODEL.items()
        }
        if task_routed
        else {},
    }


# --- response feedback -----------------------------------------------------


@router.get("/sessions/{session_id}/feedback")
async def get_feedback(
    session_id: str,
    user_id: str | None = Depends(bearer_user),
):
    """This person's thumbs on this session, so the controls come back set.

    Scoped to the caller, not the session. Two people looking at the same
    shared transcript are entitled to disagree about it, and a control that
    shows somebody else's verdict as your own is simply wrong.
    """
    await require_session(user_id, session_id)
    return await repository.list_feedback(session_id, user_id or "anonymous")


@router.post("/sessions/{session_id}/feedback")
async def set_feedback(
    session_id: str,
    payload: dict,
    user_id: str | None = Depends(bearer_user),
):
    """Record — or withdraw — a verdict on one assistant turn.

    `rating` is "up", "down", or null to clear. `turn_index` counts assistant
    turns from zero, which is the same coordinate scheme the branch switcher
    uses and for the same reason: a replayed transcript carries no per-message
    row ids, so position is the only thing both ends can agree on.
    """
    await require_session_write(user_id, session_id)
    payload = payload or {}
    rating = payload.get("rating")
    if rating not in (None, "up", "down"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "`rating` must be 'up', 'down' or null."
        )
    try:
        turn_index = int(payload.get("turn_index"))
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "`turn_index` must be an integer."
        ) from None

    ok = await repository.set_feedback(
        session_id,
        turn_index,
        rating,
        user_id=user_id or "anonymous",
        reason=(str(payload.get("reason") or "").strip() or None),
        model_id=payload.get("model_id"),
        section=payload.get("section"),
    )
    return {"ok": bool(ok), "turn_index": turn_index, "rating": rating}


# --- per-account preferences ------------------------------------------------


@router.get("/preferences")
async def get_preferences(user_id: str | None = Depends(bearer_user)):
    """Settings that follow the account rather than the browser.

    An anonymous caller has no account to hang them on, so it gets an empty
    object rather than a shared row — anonymous sessions are per-device by
    definition, and pooling their preferences would let one browser change
    another's.
    """
    if not user_id:
        return {
            "user_id": None,
            "default_model_id": None,
            "theme": "dark",
            "about_you": "",
            "response_style": "",
            "memory_enabled": False,
        }
    prefs = await repository.get_preferences(user_id)
    return {
        "user_id": user_id,
        "default_model_id": prefs.get("default_model_id"),
        "theme": prefs.get("theme") or "dark",
        # "" rather than null: these are textarea values, and the client would
        # otherwise have to coalesce every one of them before binding.
        "about_you": prefs.get("about_you") or "",
        "response_style": prefs.get("response_style") or "",
        "memory_enabled": bool(prefs.get("memory_enabled", True)),
    }


@router.put("/preferences")
async def put_preferences(
    payload: dict,
    user_id: str | None = Depends(bearer_user),
):
    """Save preferences for the signed-in account.

    `default_model_id` accepts a router model id, the literal 'auto', or ""
    to clear the preference. Clearing is distinct from choosing the current
    default: a user who has never expressed a preference should follow the
    build's default when it changes, and one who has chosen should not.
    """
    if not user_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Sign in to save preferences across devices.",
        )
    payload = payload or {}
    model_id = payload.get("default_model_id")
    if model_id is not None:
        model_id = str(model_id).strip()
        if model_id and model_id != AUTO_MODEL_ID and model_id not in MODEL_REGISTRY:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Unknown model `{model_id}`."
            )

    theme = payload.get("theme")
    if theme is not None:
        theme = str(theme).strip().lower()
        # This build ships one theme. Accepting arbitrary values would let the
        # column fill with names nothing renders, and answering "saved" to a
        # request that changes nothing visible is worse than declining it.
        if theme not in ("dark",):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This build is dark-only; `theme` must be 'dark'.",
            )

    # The two custom-instruction boxes. Capped at the same number
    # `memory.MAX_INSTRUCTION_CHARS` clips to when injecting, so the limit is
    # enforced where the user can see it fail rather than silently truncating
    # into a prompt later.
    for field in ("about_you", "response_style"):
        value = payload.get(field)
        if value is not None and len(str(value)) > memory.MAX_INSTRUCTION_CHARS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"`{field}` is at most {memory.MAX_INSTRUCTION_CHARS} characters.",
            )

    memory_enabled = payload.get("memory_enabled")
    if memory_enabled is not None:
        memory_enabled = bool(memory_enabled)

    saved = await repository.set_preferences(
        user_id,
        default_model_id=model_id,
        theme=theme,
        about_you=payload.get("about_you"),
        response_style=payload.get("response_style"),
        memory_enabled=memory_enabled,
    )
    return {
        "user_id": user_id,
        "default_model_id": saved.get("default_model_id"),
        "theme": saved.get("theme") or "dark",
        "about_you": saved.get("about_you") or "",
        "response_style": saved.get("response_style") or "",
        "memory_enabled": bool(saved.get("memory_enabled", True)),
    }


# --- projects --------------------------------------------------------------
#
# Every route keyed by a project id passes through `require_project` first.
# That is not belt-and-braces: this service holds the service-role key, so RLS
# does not apply to anything it does and these guards are the only thing
# standing between a guessed uuid and another account's project.


@router.get("/projects")
async def list_projects(
    user_id: str | None = Depends(bearer_user),
    archived: bool = False,
):
    """This account's projects, most recently touched first."""
    return await repository.list_projects(user_id, archived=archived)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: dict | None = None,
    user_id: str | None = Depends(bearer_user),
):
    """Create a project.

    A name is optional — a project created from the "new project" button has
    nothing in it yet and naming it before there is anything to name is busy
    work. It gets the column default and can be renamed later.
    """
    payload = payload or {}
    name = str(payload.get("name") or "").strip()
    if len(name) > 120:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "A project name is at most 120 characters."
        )
    row = await repository.create_project(
        user_id,
        name=name or "New project",
        description=payload.get("description"),
        instructions=payload.get("instructions"),
        color=str(payload.get("color") or "slate"),
        icon=payload.get("icon"),
    )
    if not row:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Projects need Supabase configured to persist.",
        )
    return row


@router.get("/projects/{project_id}")
async def get_project(project_id: str, user_id: str | None = Depends(bearer_user)):
    """One project, with its knowledge files and the sessions inside it."""
    project = await require_project(user_id, project_id)
    files = await repository.list_project_files(project_id)
    sessions = await repository.list_sessions(
        user_id, project_id=project_id, project_scoped=True, limit=200
    )
    return {**project, "files": files, "sessions": sessions}


@router.patch("/projects/{project_id}")
async def patch_project(
    project_id: str,
    payload: dict | None = None,
    user_id: str | None = Depends(bearer_user),
):
    """Rename, re-describe, re-instruct or archive a project."""
    await require_project(user_id, project_id)
    payload = payload or {}
    name = payload.get("name")
    if name is not None and not str(name).strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A project needs a name.")
    row = await repository.set_project(
        project_id,
        name=(str(name).strip() if name is not None else None),
        description=payload.get("description"),
        instructions=payload.get("instructions"),
        color=payload.get("color"),
        icon=payload.get("icon"),
        is_archived=payload.get("is_archived"),
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such project.")
    return row


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, user_id: str | None = Depends(bearer_user)):
    """Delete a project. The sessions inside it survive and become unfiled.

    That is the behaviour the FK enforces (`on delete set null`) and it is
    deliberate: deleting a folder is not a request to delete what was in it.
    """
    await require_project(user_id, project_id)
    await repository.delete_project(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/sessions/{session_id}/project")
async def set_session_project(
    session_id: str,
    payload: dict | None = None,
    user_id: str | None = Depends(bearer_user),
):
    """Move a session into a project, or out of one with a null `project_id`.

    Both the session and the destination project are checked, because this is
    the one route that can associate two resources — verifying only the session
    would let a caller file their own conversation into somebody else's project.
    """
    await require_session_write(user_id, session_id)
    project_id = (payload or {}).get("project_id")
    if project_id:
        await require_project(user_id, str(project_id))
    await repository.set_session_project(session_id, str(project_id) if project_id else None)
    return {"session_id": session_id, "project_id": project_id or None}


# --- project knowledge files -----------------------------------------------


@router.get("/projects/{project_id}/files")
async def list_project_files(
    project_id: str, user_id: str | None = Depends(bearer_user)
):
    """The project's knowledge files, without their text.

    Content is deliberately omitted: a listing renders names and sizes, and a
    project holding a few large documents would otherwise ship hundreds of
    kilobytes to draw a list.
    """
    await require_project(user_id, project_id)
    return await repository.list_project_files(project_id)


@router.post("/projects/{project_id}/files", status_code=status.HTTP_201_CREATED)
async def add_project_file(
    project_id: str,
    file: UploadFile = File(...),
    user_id: str | None = Depends(bearer_user),
):
    """Attach one PDF or text file as project knowledge.

    Storage and extraction both reuse what already exists rather than growing a
    second pipeline: `files.save_upload` puts the original in the same bucket
    the chat attachments use, and `learn.ingest` produces the text, which is
    the only thing a prompt can actually be given. A scanned PDF has no text
    layer and no OCR step exists here, so it is stored as a failed file with
    the reason attached rather than as an empty one the user has to diagnose.
    """
    allowed, retry = _upload_limiter.check(project_id)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many uploads. Try again in {retry}s.",
        )
    await require_project(user_id, project_id)

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "File is larger than the 20 MB limit.",
        )
    filename = file.filename or "upload"
    content_type = file.content_type or ""

    is_pdf = content_type == PDF_TYPE or filename.lower().endswith(".pdf")
    if is_pdf:
        stored = await save_upload(project_id, filename, PDF_TYPE, data)
        extracted = await asyncio.to_thread(ingest.from_pdf, data, filename)
        storage_path = stored.get("storage_path")
    else:
        # Anything else is treated as text. Decoded leniently on purpose: a
        # source file with one stray byte is still worth reading, and refusing
        # the whole upload over it would be a worse answer than dropping the
        # byte.
        try:
            raw = data.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "That file could not be read as text. Attach a PDF or a text file.",
            ) from exc
        extracted = ingest.from_text(raw, title=filename)
        storage_path = None

    row = await repository.add_project_file(
        project_id,
        name=filename,
        content=extracted.text if extracted.ok else "",
        storage_path=storage_path,
        mime=content_type or (PDF_TYPE if is_pdf else "text/plain"),
        bytes_=len(data),
        status="ready" if extracted.ok else "failed",
        error=None if extracted.ok else (extracted.error or "No text could be read."),
    )
    if not row:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Project knowledge needs Supabase configured to persist.",
        )
    row.pop("content", None)
    return row


@router.delete(
    "/projects/{project_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_project_file(
    project_id: str, file_id: str, user_id: str | None = Depends(bearer_user)
):
    await require_project_file(user_id, project_id, file_id)
    await repository.delete_project_file(file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- memory ----------------------------------------------------------------


@router.get("/memories")
async def list_memories(user_id: str | None = Depends(bearer_user)):
    """What this account is remembered to have said about itself.

    Anonymous callers get an empty list rather than a shared one: there is no
    account to remember against, and pooling memories under a sentinel would
    let one browser's stated preferences steer another's answers.
    """
    if not user_id:
        return {"enabled": False, "memories": []}
    prefs = await repository.get_preferences(user_id)
    return {
        "enabled": bool(prefs.get("memory_enabled", True)),
        "memories": await repository.list_memories(user_id),
    }


@router.post("/memories", status_code=status.HTTP_201_CREATED)
async def add_memory(
    payload: dict | None = None,
    user_id: str | None = Depends(bearer_user),
):
    """Add a memory by hand, from the Settings panel."""
    if not user_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Sign in to save what Loom remembers."
        )
    content = str((payload or {}).get("content") or "").strip()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A memory needs content.")
    if len(content) > 300:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A memory is at most 300 characters. Longer context belongs in a "
            "project's instructions, where it applies to the work it is about.",
        )
    row = await repository.add_memory(user_id, content)
    if not row:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Memory needs Supabase configured to persist.",
        )
    return row


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, user_id: str | None = Depends(bearer_user)):
    """Forget one thing.

    Scoped to the caller inside the query rather than by reading the row first
    and comparing: a memory has no route that exposes it to anyone else, so the
    scoped delete is both the check and the action.
    """
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to manage memory.")
    await repository.delete_memory(memory_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/memories", status_code=status.HTTP_204_NO_CONTENT)
async def clear_memories(user_id: str | None = Depends(bearer_user)):
    """Forget everything for this account."""
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to manage memory.")
    await repository.clear_memories(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
