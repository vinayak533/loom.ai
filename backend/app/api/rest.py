"""REST surface: sessions, history replay, and uploads."""

from __future__ import annotations

import logging
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status

from app import events as ev
from app.agent import runner
from app.agent.llm import generate_project_meta
from app.api.auth import bearer_user
from app.api.ownership import require_session, require_session_write
from app.api.ratelimit import RateLimiter
from app.config import get_settings
from app.db import repository
from app.emitter import registry as emitter_registry
from app.files import UploadRejected, save_upload
from app.llm_router import (
    HINT_MODEL,
    auto_pool_available,
    auto_route,
    available_models,
    display_name,
)
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
    settings = get_settings()
    mid = model_id or settings.default_model_id
    return await repository.create_session(
        str(uuid.uuid4()), user_id, model_id=mid, section=_section(section)
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
    """
    await require_session(user_id, session_id)
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
