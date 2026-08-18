"""Persistence for sessions, messages, files and token usage.

The supabase-py client is synchronous, so every call is pushed to a worker
thread. Every method is best-effort: a logging failure must never take down a
live agent run.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db.supabase_client import enabled, get_client

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Exception:  # noqa: BLE001 - persistence is never fatal
        log.warning("Supabase call failed", exc_info=True)
        return None


# --- background writes -----------------------------------------------------

#: Strong references to in-flight background writes. Without this the event
#: loop only holds a weak reference and a task can be garbage-collected
#: mid-flight.
_PENDING: set[asyncio.Task] = set()


def fire(coro) -> None:
    """Run a write without blocking the caller.

    Logging is best-effort by design, so nothing in the agent loop should wait
    on a Supabase round trip to finish before taking its next step — a write
    awaited between a tool finishing and the next model call is pure added
    latency in the user's critical path.

    Ordering is still well defined for replay: every writer builds its row
    (including ``created_at``) before its first await, and tasks start in the
    order they were created, so ``order("created_at")`` reproduces call order
    even though the HTTP requests themselves finish out of order.
    """
    try:
        task = asyncio.ensure_future(coro)
    except RuntimeError:  # no running loop (sync context, or shutdown)
        coro.close()
        return
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def drain(timeout: float = 5.0) -> None:
    """Wait for outstanding background writes. Called on shutdown only."""
    if not _PENDING:
        return
    await asyncio.wait(set(_PENDING), timeout=timeout)


# --- sessions --------------------------------------------------------------


async def create_session(
    session_id: str | None = None,
    user_id: str | None = None,
    title: str = "New session",
    model_id: str | None = None,
    agent_id: str | None = None,
    section: str | None = None,
) -> dict:
    """Create a session row.

    ``agent_id`` names one of the Agentic Loop specialists and is null for
    every Chat and Code session — which is what the default listing filters on,
    so agent conversations cannot appear in the other sections' history.

    ``section`` is 'chat' or 'code' and is what keeps *those two* apart.
    ``agent_id`` cannot do it: both leave it null, so before this existed a
    session created in Chat was returned by Code's history query as well. It is
    written on creation because it is a fact about where the conversation began,
    and nothing later in the session's life can recover it.
    """
    session_id = session_id or str(uuid.uuid4())
    row = {
        "id": session_id,
        "user_id": user_id,
        "title": title,
        "model_id": model_id or get_settings().default_model_id,
        "status": "idle",
        "created_at": _now(),
        "updated_at": _now(),
    }
    if agent_id:
        row["agent_id"] = agent_id
    if section:
        row["section"] = section
    if enabled():
        client = get_client()
        res = await _run(lambda: client.table("sessions").upsert(row).execute())
        if res is None and (agent_id or section):
            # A database that has not run the migrations rejects the whole
            # insert. Retry without the new columns so the conversation is not
            # lost over one column — the socket resolves the agent from its
            # query parameter, and the listing degrades to showing more than it
            # should rather than nothing at all.
            log.warning(
                "sessions.agent_id/section rejected — run the `alter table` "
                "statements in schema.sql. Falling back to an insert without them."
            )
            await _run(
                lambda: client.table("sessions")
                .upsert(
                    {k: v for k, v in row.items() if k not in ("agent_id", "section")}
                )
                .execute()
            )
    # The shelf flags are column defaults rather than part of the insert, so a
    # database that has not run the `alter table` yet still accepts new
    # sessions — `messages` has a foreign key onto this row, so a failed insert
    # here loses the whole conversation, not just two booleans.
    return {
        **row,
        "agent_id": agent_id,
        "section": section,
        "is_pinned": False,
        "is_archived": False,
    }


async def get_session(session_id: str) -> dict | None:
    if not enabled():
        return None
    client = get_client()
    res = await _run(
        lambda: client.table("sessions").select("*").eq("id", session_id).single().execute()
    )
    return getattr(res, "data", None) if res else None


async def list_sessions(
    user_id: str | None = None,
    limit: int = 50,
    archived: bool = False,
    agent_id: str | None = None,
    section: str | None = None,
) -> list[dict]:
    """The session shelf.

    ``archived`` picks which shelf: the default list never contains archived
    sessions, and the archive view contains nothing else. Ordering is pinned
    first (most recently pinned at the top of that group), then everything else
    by most recent activity — the sort is applied here rather than in the
    client so the Chat and Code lists cannot drift apart.

    ``agent_id`` scopes the list to one Agentic Loop specialist. Passing None —
    which is what Chat and Code do — returns only sessions with no agent, so
    the ten specialists' conversations stay out of the other sections' history
    rather than being mixed into a list that has no way to label them.

    ``section`` scopes it further to 'chat' or 'code'. Both of those leave
    ``agent_id`` null, so without this a Chat session and a Code session are
    indistinguishable to the query and each section's history showed the
    other's. Passing None keeps the old behaviour and is only for callers that
    genuinely want every non-agent session (the audit script).

    Note on ``user_id``: passing None scopes the list to rows with a null
    ``user_id`` — the anonymous shelf — and NOT to every row in the table.
    This service holds the service-role key, so RLS does not apply to it and an
    unfiltered query here would hand one caller every other account's history.
    """
    if not enabled():
        return []
    client = get_client()

    def _query(filtered: bool, scoped: bool, sectioned: bool):
        q = client.table("sessions").select("*").order("updated_at", desc=True).limit(limit)
        # Always scope by owner. `is_("user_id", "null")` rather than "no
        # filter" is the whole point: anonymous callers get the anonymous
        # shelf, never everybody's.
        q = q.eq("user_id", user_id) if user_id else q.is_("user_id", "null")
        if filtered:
            q = q.eq("is_archived", archived)
        if scoped:
            q = q.eq("agent_id", agent_id) if agent_id else q.is_("agent_id", "null")
        if sectioned and section:
            # 'code' is strict. 'chat' also absorbs legacy rows whose section
            # was never recorded and could not be inferred by the backfill:
            # they have to remain reachable somewhere, and Chat is where a
            # session that never opened a sandbox came from in practice. They
            # stay null rather than being rewritten, so
            # `scripts/audit_session_sections.py` can still list them for
            # review — and either way they never reach Code's history.
            q = (
                q.or_("section.eq.chat,section.is.null")
                if section == "chat"
                else q.eq("section", section)
            )
        return q.execute()

    res = await _run(_query, True, True, True)
    sectioned_in_sql = res is not None
    if res is None:
        # One of the columns is missing on a database that has not run the
        # migrations. Rather than reporting an empty history, fall back through
        # the filters and reconcile in Python — an over-broad list is a much
        # better failure than a blank one.
        res = await _run(_query, True, True, False)
        if res is None:
            res = await _run(_query, True, False, False)
            scoped_in_sql = False
            if res is None:
                res = await _run(_query, False, False, False)
                rows = [r for r in (getattr(res, "data", None) or []) if not archived]
            else:
                rows = getattr(res, "data", None) or []
        else:
            scoped_in_sql = True
            rows = getattr(res, "data", None) or []
    else:
        scoped_in_sql = True
        rows = getattr(res, "data", None) or []

    if not scoped_in_sql:
        rows = [r for r in rows if (r.get("agent_id") or None) == agent_id]

    if section and not sectioned_in_sql:
        # Degraded path only. A legacy row whose section was never determined
        # is shown in Chat, which is where an un-sandboxed session came from in
        # practice — see the backfill in schema.sql.
        rows = [
            r
            for r in rows
            if (r.get("section") or ("chat" if not r.get("sandbox_id") else "code"))
            == section
        ]

    return _shelf_order(rows)


def _shelf_order(rows: list[dict]) -> list[dict]:
    """Pinned first, most recently pinned at the top; then the rest by activity."""
    pinned = [r for r in rows if r.get("is_pinned")]
    rest = [r for r in rows if not r.get("is_pinned")]
    pinned.sort(
        key=lambda r: r.get("pinned_at") or r.get("updated_at") or "", reverse=True
    )
    rest.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return pinned + rest


async def touch_session(
    session_id: str,
    *,
    status: str | None = None,
    title: str | None = None,
    sandbox_id: str | None = None,
    model_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    if not enabled():
        return
    client = get_client()
    patch: dict[str, Any] = {"updated_at": _now()}
    if status:
        patch["status"] = status
    if title:
        patch["title"] = title
    if sandbox_id:
        patch["sandbox_id"] = sandbox_id
    if model_id:
        patch["model_id"] = model_id
    if agent_id:
        patch["agent_id"] = agent_id
    res = await _run(
        lambda: client.table("sessions").update(patch).eq("id", session_id).execute()
    )
    if res is None and agent_id:
        # Same fallback as the insert: an un-migrated database must not lose a
        # status or title update over a column it does not have yet.
        await _run(
            lambda: client.table("sessions")
            .update({k: v for k, v in patch.items() if k != "agent_id"})
            .eq("id", session_id)
            .execute()
        )


async def set_session_meta(
    session_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> dict | None:
    """Rename / re-describe a session.

    Separate from :func:`touch_session` for the same reason
    :func:`set_session_flags` is: naming a project is not activity, and a rename
    should not jump the session to the top of the list. An empty string is a
    meaningful value here — it clears the field — so the checks are against
    ``None`` rather than falsiness.
    """
    if not enabled():
        return None
    client = get_client()
    patch: dict[str, Any] = {}
    if title is not None:
        patch["title"] = title[:120]
    if description is not None:
        patch["description"] = description[:400] or None
    if not patch:
        return await get_session(session_id)

    res = await _run(
        lambda: client.table("sessions").update(patch).eq("id", session_id).execute()
    )
    if res is None and "description" in patch:
        # A database that predates the description column rejects the whole
        # patch. Retry with just the title so a rename still lands rather than
        # failing wholesale on a field the user may not even have set.
        title_only = {k: v for k, v in patch.items() if k == "title"}
        if title_only:
            res = await _run(
                lambda: client.table("sessions")
                .update(title_only)
                .eq("id", session_id)
                .execute()
            )
    data = getattr(res, "data", None) or []
    return data[0] if data else None


async def set_session_flags(
    session_id: str,
    *,
    is_pinned: bool | None = None,
    is_archived: bool | None = None,
) -> dict | None:
    """Pin / unpin / archive / unarchive one session.

    Unlike :func:`touch_session` this deliberately does *not* bump
    ``updated_at``: pinning is not activity, and moving a session to the top of
    the list should not also reorder it inside the group it came from.
    """
    if not enabled():
        return None
    client = get_client()
    patch: dict[str, Any] = {}
    if is_pinned is not None:
        patch["is_pinned"] = is_pinned
        patch["pinned_at"] = _now() if is_pinned else None
    if is_archived is not None:
        patch["is_archived"] = is_archived
    if not patch:
        return await get_session(session_id)

    res = await _run(
        lambda: client.table("sessions").update(patch).eq("id", session_id).execute()
    )
    data = getattr(res, "data", None) or []
    return data[0] if data else None


async def delete_session(session_id: str) -> None:
    """Delete a session and everything hanging off it.

    `messages`, `files` and `token_usage` all declare `on delete cascade`, so
    the session delete alone is enough on a schema built from `schema.sql`.
    The explicit child deletes below run first anyway: they cost one round trip
    each, they are idempotent, and they mean a database whose foreign keys were
    created without the cascade does not quietly accumulate orphaned rows —
    which for `messages` means orphaned conversation content.
    """
    if not enabled():
        return
    client = get_client()
    for table in ("messages", "token_usage", "files"):
        await _run(
            lambda t=table: client.table(t).delete().eq("session_id", session_id).execute()
        )
    await _run(lambda: client.table("sessions").delete().eq("id", session_id).execute())


# --- messages --------------------------------------------------------------


async def add_message(
    session_id: str,
    role: str,
    content: Any,
    tool_calls: Any | None = None,
) -> None:
    """Persist one turn. Tool calls and tool results are stored too, not just
    the final answer, so the frontend can replay the full trace."""
    if not enabled():
        return
    client = get_client()
    row = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "role": role,
        "content": content if isinstance(content, str) else None,
        "tool_calls": tool_calls if tool_calls is not None else (
            None if isinstance(content, str) else content
        ),
        "created_at": _now(),
    }
    await _run(lambda: client.table("messages").insert(row).execute())


async def list_messages(session_id: str, limit: int = 500) -> list[dict]:
    if not enabled():
        return []
    client = get_client()

    def _query():
        return (
            client.table("messages")
            .select("*")
            .eq("session_id", session_id)
            .order("created_at")
            .limit(limit)
            .execute()
        )

    res = await _run(_query)
    return getattr(res, "data", None) or []


# --- files -----------------------------------------------------------------


async def add_file(
    session_id: str, filename: str, storage_path: str, file_type: str
) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "filename": filename,
        "storage_path": storage_path,
        "file_type": file_type,
        "created_at": _now(),
    }
    if enabled():
        client = get_client()
        await _run(lambda: client.table("files").insert(row).execute())
    return row


async def get_files(session_id: str, file_ids: list[str] | None = None) -> list[dict]:
    if not enabled():
        return []
    client = get_client()

    def _query():
        q = client.table("files").select("*").eq("session_id", session_id)
        if file_ids:
            q = q.in_("id", file_ids)
        return q.execute()

    res = await _run(_query)
    return getattr(res, "data", None) or []


async def download_file(storage_path: str, bucket: str = "uploads") -> bytes | None:
    if not enabled():
        return None
    client = get_client()
    return await _run(lambda: client.storage.from_(bucket).download(storage_path))


async def upload_file(
    storage_path: str, data: bytes, content_type: str, bucket: str = "uploads"
) -> bool:
    if not enabled():
        return False
    client = get_client()
    res = await _run(
        lambda: client.storage.from_(bucket).upload(
            storage_path, data, {"content-type": content_type, "upsert": "true"}
        )
    )
    return res is not None


# --- token usage -----------------------------------------------------------


async def record_usage(
    session_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_estimate: float,
    model_id: str | None = None,
    routing_mode: str | None = None,
    routing_hint: str | None = None,
) -> None:
    """Log one model call.

    ``routing_mode`` / ``routing_hint`` record *why* this model was used, not
    just which one: without them there is no way to tell later whether auto
    mode's classifier has been picking sensible models. ``routing_hint`` is
    null for manual selections.
    """
    if not enabled():
        return
    client = get_client()
    row = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "model": model,
        "model_id": model_id,
        "routing_mode": routing_mode or "manual",
        "routing_hint": routing_hint or None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_estimate": cost_estimate,
        "created_at": _now(),
    }
    await _run(lambda: client.table("token_usage").insert(row).execute())
