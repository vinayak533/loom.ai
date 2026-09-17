"""Persistence for sessions, messages, files and token usage.

The supabase-py client is synchronous, so every call is pushed to a worker
thread. Every method is best-effort: a logging failure must never take down a
live agent run.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
from datetime import datetime, timezone
from typing import Any

from app.llm_router import effective_default_model
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


async def _read(fn, *args, **kwargs):
    """As :func:`_run`, but retries a transport failure once.

    Reads only, and the distinction is not stylistic. `_run` answers every
    failure with ``None``, and for a read that is the same value as "there is
    no such row" — a collision `get_session` already documents, because a
    caller that cannot tell them apart turns an unreachable database into a
    404 for a session that exists. A dropped connection should not be able to
    say that.

    Observed rather than theorised: a burst of six concurrent reads failed
    together with `httpx.ReadError: [WinError 10035]`, mid-run, during an
    ordinary page reload. It did not reproduce under a 12-way cold burst or
    after 75 and 150 seconds of idling, which is the signature of a transient
    socket error rather than a broken pool — precisely the kind a second
    attempt clears.

    Only :class:`httpx.TransportError` is retried: it means the response never
    arrived, so nothing is known to have happened. An API error is a real
    answer from the server and gets no second attempt. Writes call `_run` and
    keep their single attempt, because a retried insert whose first try landed
    would duplicate the row — the response is what went missing, not the
    effect.
    """
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except httpx.TransportError:
        log.warning("Supabase read failed at the transport; retrying once")
    except Exception:  # noqa: BLE001 - persistence is never fatal
        log.warning("Supabase call failed", exc_info=True)
        return None
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Exception:  # noqa: BLE001
        log.warning("Supabase read failed again", exc_info=True)
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
        "model_id": model_id or effective_default_model(),
        "status": "idle",
        "created_at": _now(),
        "updated_at": _now(),
    }
    if agent_id:
        row["agent_id"] = agent_id
    if section:
        row["section"] = section
    created = True
    if enabled():
        client = get_client()
        # `_read`, not `_run`, although this is a write. The retry rule `_read`
        # documents is "only retry when nothing is known to have happened", and
        # its usual counter-example — a retried insert whose first attempt
        # landed duplicates the row — does not apply here: this is an `upsert`
        # on a `session_id` the caller already chose, so a second attempt on
        # the same id is the same row, not another one.
        #
        # It matters because everything else about a conversation hangs off
        # this row. `messages` and `token_usage` carry a foreign key onto it,
        # and both are written through `fire`, which logs a failure at warning
        # and moves on. So a single transport blip here used to end with a run
        # that streamed perfectly, answered the user, and saved not one word of
        # itself — silently, because every FK violation that followed was
        # swallowed one by one.
        res = await _read(lambda: client.table("sessions").upsert(row).execute())
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
            res = await _read(
                lambda: client.table("sessions")
                .upsert(
                    {k: v for k, v in row.items() if k not in ("agent_id", "section")}
                )
                .execute()
            )
        created = res is not None
        if not created:
            # Said once, loudly, and reported to the caller. Everything after
            # this point writes against a row that is not there, and the
            # failures are individually swallowed — so this line is the only
            # place the problem is visible before someone notices a whole
            # conversation missing from their history.
            log.error(
                "Could not create session row `%s`; nothing from this "
                "conversation will be persisted (its messages and usage rows "
                "have a foreign key onto it).",
                session_id,
            )
    # The shelf flags are column defaults rather than part of the insert, so a
    # database that has not run the `alter table` yet still accepts new
    # sessions — `messages` has a foreign key onto this row, so a failed insert
    # here loses the whole conversation, not just two booleans.
    #
    # `persisted` is the one field here that is not a column. It is the honest
    # answer to "did this land", so a caller who cares — the websocket handler
    # does, because the user is about to type into a conversation that will not
    # be saved — can tell the user rather than finding out from an empty
    # history later.
    return {
        **row,
        "agent_id": agent_id,
        "section": section,
        "is_pinned": False,
        "is_archived": False,
        "persisted": created and enabled(),
    }


async def get_session(session_id: str) -> dict | None:
    """The session row, or None when there isn't one yet.

    Deliberately *not* PostgREST's `.single()`. A session id reaches this
    function before its row exists on every single connect — the browser mints
    the id locally and the websocket handler is what inserts the row — and
    `.single()` answers "no rows" by raising `PGRST116`. That turned the most
    ordinary state in the system into an exception, which `_run` then logged as
    a `WARNING` with a full traceback: three of them per new session, in a log
    an operator is meant to be able to scan for real failures.

    Worse than the noise, it erased a distinction that matters. `_run` returns
    None for *any* exception, so "this session does not exist" and "Supabase is
    unreachable" arrived here identically — and the caller treats None as "no
    such session", so an outage read as a missing row. `.limit(1)` makes the
    empty result an empty list, which is data rather than an error, and leaves
    None meaning only what it should: the call itself failed.
    """
    if not enabled():
        return None
    client = get_client()
    res = await _read(
        lambda: client.table("sessions").select("*").eq("id", session_id).limit(1).execute()
    )
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else None


async def list_sessions(
    user_id: str | None = None,
    limit: int = 50,
    archived: bool = False,
    agent_id: str | None = None,
    section: str | None = None,
    project_id: str | None = None,
    project_scoped: bool = False,
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

    ``project_id`` narrows to one project, but only when ``project_scoped`` is
    True. The two arguments exist separately because None is a real value here
    and means something specific — "sessions in no project at all", which is
    what the default shelf shows — so it cannot double as "do not filter".

    Note on ``user_id``: passing None scopes the list to rows with a null
    ``user_id`` — the anonymous shelf — and NOT to every row in the table.
    This service holds the service-role key, so RLS does not apply to it and an
    unfiltered query here would hand one caller every other account's history.
    """
    if not enabled():
        return []
    client = get_client()

    def _query(filtered: bool, scoped: bool, sectioned: bool, projected: bool = False):
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
        if projected:
            q = (
                q.eq("project_id", project_id)
                if project_id
                else q.is_("project_id", "null")
            )
        return q.execute()

    res = await _read(_query, True, True, True, project_scoped)
    sectioned_in_sql = res is not None
    # The project column is only ever filtered on the first attempt; every
    # fallback below drops it, so a database missing the column still returns a
    # usable list and the narrowing is redone in Python at the end.
    projected_in_sql = res is not None
    if res is None:
        # One of the columns is missing on a database that has not run the
        # migrations. Rather than reporting an empty history, fall back through
        # the filters and reconcile in Python — an over-broad list is a much
        # better failure than a blank one.
        res = await _read(_query, True, True, False)
        if res is None:
            res = await _read(_query, True, False, False)
            scoped_in_sql = False
            if res is None:
                res = await _read(_query, False, False, False)
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

    if project_scoped and not projected_in_sql:
        rows = [r for r in rows if (r.get("project_id") or None) == (project_id or None)]

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

    res = await _read(_query)
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

    res = await _read(_query)
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


# --- conversation branches -------------------------------------------------
#
# See the long note on `public.message_branches` in schema.sql for why a branch
# is a stored *suffix* rather than a node in a tree. The short version: the
# conversation the agent reads is a flat list inside a LangGraph checkpoint,
# nothing in it has a per-message identity, and a suffix snapshot needs neither.


def user_turn_positions(messages: list[dict]) -> list[int]:
    """Indices in ``messages`` of the turns a person actually typed.

    Not every ``role == "user"`` entry is one. The block format requires a
    turn's ``tool_result`` blocks to travel in a user message, so a
    tool-using conversation is full of user entries nobody wrote. Counting
    those would move every branch pointer the moment a turn called a tool.

    A real turn is a user message carrying text, an image or a document. That
    is the same test `itemsFromHistory` applies in the browser when it decides
    whether to draw a bubble, which is what lets the two sides agree on "the
    third message I sent" without exchanging ids.
    """
    positions: list[int] = []
    for index, message in enumerate(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if content.strip():
                positions.append(index)
            continue
        for block in content or []:
            if isinstance(block, dict) and block.get("type") in (
                "text",
                "image",
                "document",
            ):
                positions.append(index)
                break
    return positions


async def list_branches(session_id: str) -> list[dict]:
    """Every stored branch for a session, oldest turn and version first."""
    if not enabled():
        return []
    client = get_client()

    def _query():
        return (
            client.table("message_branches")
            .select("*")
            .eq("session_id", session_id)
            .order("turn_index")
            .order("version")
            .execute()
        )

    res = await _read(_query)
    return getattr(res, "data", None) or []


async def branch_versions(session_id: str, turn_index: int) -> list[dict]:
    """The versions recorded at one turn, without their message payloads.

    The switcher needs a count and a label per version; the snapshots are tens
    of kilobytes each and are only read when someone actually switches.
    """
    if not enabled():
        return []
    client = get_client()

    def _query():
        return (
            client.table("message_branches")
            .select("id,version,label,created_at")
            .eq("session_id", session_id)
            .eq("turn_index", turn_index)
            .order("version")
            .execute()
        )

    res = await _read(_query)
    return getattr(res, "data", None) or []


async def save_branch(
    session_id: str,
    turn_index: int,
    version: int,
    messages: list[dict],
    label: str = "",
    user_id: str | None = None,
) -> bool:
    """Record one version of the conversation from ``turn_index`` onwards.

    Upserted on the unique (session, turn, version) index so re-recording a
    version — which happens every time a branch is re-run — replaces it rather
    than accumulating duplicates that the switcher would then count.
    """
    if not enabled():
        return False
    client = get_client()
    row = {
        "session_id": session_id,
        "user_id": user_id,
        "turn_index": turn_index,
        "version": version,
        "label": (label or "")[:120] or None,
        "messages": messages,
        "created_at": _now(),
    }
    res = await _run(
        lambda: client.table("message_branches")
        .upsert(row, on_conflict="session_id,turn_index,version")
        .execute()
    )
    return res is not None


async def get_branch(
    session_id: str, turn_index: int, version: int
) -> dict | None:
    """One branch snapshot, payload included."""
    if not enabled():
        return None
    client = get_client()

    def _query():
        return (
            client.table("message_branches")
            .select("*")
            .eq("session_id", session_id)
            .eq("turn_index", turn_index)
            .eq("version", version)
            .limit(1)
            .execute()
        )

    res = await _read(_query)
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else None


async def set_active_branch(
    session_id: str, turn_index: int, version: int
) -> None:
    """Mark one version as the branch currently spliced into the checkpoint.

    Two writes rather than one, and in this order: clear the turn, then set the
    winner. A single upsert cannot express "exactly one of these" and the brief
    window where none is active is harmless — every reader falls back to the
    highest version when nothing is marked.
    """
    if not enabled():
        return
    client = get_client()
    await _run(
        lambda: client.table("message_branches")
        .update({"is_active": False})
        .eq("session_id", session_id)
        .eq("turn_index", turn_index)
        .execute()
    )
    await _run(
        lambda: client.table("message_branches")
        .update({"is_active": True})
        .eq("session_id", session_id)
        .eq("turn_index", turn_index)
        .eq("version", version)
        .execute()
    )


async def drop_branches_from(session_id: str, turn_index: int) -> None:
    """Forget every branch at or after ``turn_index``.

    Called when a *new* message is sent normally at the end of a thread that
    had been branched earlier. Those snapshots describe a conversation that no
    longer exists downstream of this point, and keeping them would give the
    switcher versions that cannot be restored without contradicting the turns
    that came after.
    """
    if not enabled():
        return
    client = get_client()
    await _run(
        lambda: client.table("message_branches")
        .delete()
        .eq("session_id", session_id)
        .gte("turn_index", turn_index)
        .execute()
    )


# --- response feedback -----------------------------------------------------


async def set_feedback(
    session_id: str,
    turn_index: int,
    rating: str | None,
    user_id: str = "anonymous",
    reason: str | None = None,
    model_id: str | None = None,
    section: str | None = None,
) -> bool:
    """Record — or, with ``rating=None``, withdraw — a verdict on one reply.

    Upserted per (session, user, turn): clicking thumbs-down after thumbs-up
    is a change of mind, not a second vote. Clicking the same thumb again
    clears it, which is what ``rating=None`` is for.
    """
    if not enabled():
        return False
    client = get_client()

    if rating is None:
        res = await _run(
            lambda: client.table("message_feedback")
            .delete()
            .eq("session_id", session_id)
            .eq("user_id", user_id)
            .eq("turn_index", turn_index)
            .execute()
        )
        return res is not None

    row = {
        "session_id": session_id,
        "user_id": user_id,
        "turn_index": turn_index,
        "rating": rating,
        "reason": (reason or None),
        "model_id": model_id,
        "section": section,
        "created_at": _now(),
        "updated_at": _now(),
    }
    res = await _run(
        lambda: client.table("message_feedback")
        .upsert(row, on_conflict="session_id,user_id,turn_index")
        .execute()
    )
    return res is not None


async def list_feedback(session_id: str, user_id: str = "anonymous") -> list[dict]:
    """This person's verdicts on this session, for rehydrating the controls."""
    if not enabled():
        return []
    client = get_client()

    def _query():
        return (
            client.table("message_feedback")
            .select("turn_index,rating,reason")
            .eq("session_id", session_id)
            .eq("user_id", user_id)
            .execute()
        )

    res = await _read(_query)
    return getattr(res, "data", None) or []


# --- per-account preferences -----------------------------------------------


async def get_preferences(user_id: str) -> dict:
    """This account's saved preferences, or an empty dict when it has none.

    Empty is meaningful and is not the same as "the defaults": a user who has
    never expressed a preference should keep following the build's default
    model when that default changes, and one who has chosen should not.
    """
    if not enabled() or not user_id:
        return {}
    client = get_client()

    def _query():
        return (
            client.table("user_preferences")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

    res = await _read(_query)
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else {}


async def set_preferences(
    user_id: str,
    default_model_id: str | None = None,
    theme: str | None = None,
    about_you: str | None = None,
    response_style: str | None = None,
    memory_enabled: bool | None = None,
) -> dict:
    """Write the preferences this call names, leaving the rest alone.

    ``default_model_id=""`` is how a preference is *cleared* — distinct from
    None, which means "this call is not about the default model".
    """
    if not enabled() or not user_id:
        return {}
    client = get_client()
    row: dict[str, Any] = {"user_id": user_id, "updated_at": _now()}
    if default_model_id is not None:
        row["default_model_id"] = default_model_id or None
    if theme is not None:
        row["theme"] = theme
    # The two custom-instruction columns follow the same convention as the
    # model preference above: "" clears, None means this call is not about
    # them. A user who blanks the box is saying something different from one
    # who never opened it.
    if about_you is not None:
        row["about_you"] = about_you.strip() or None
    if response_style is not None:
        row["response_style"] = response_style.strip() or None
    if memory_enabled is not None:
        row["memory_enabled"] = bool(memory_enabled)
    res = await _run(
        lambda: client.table("user_preferences")
        .upsert(row, on_conflict="user_id")
        .execute()
    )
    if res is None:
        return {}
    return await get_preferences(user_id)


# --- history search --------------------------------------------------------


async def search_sessions(
    query: str,
    user_id: str | None = None,
    section: str | None = None,
    agent_id: str | None = None,
    limit: int = 30,
    across: bool = False,
) -> list[dict]:
    """Sessions whose title *or transcript* matches ``query``.

    Two queries rather than one, because they answer different questions and
    PostgREST cannot join them in a single request: `sessions.title` is an
    `ilike`, and message bodies live in another table entirely. Titles are
    listed first because a title match is a stronger signal than one hit
    somewhere in a long transcript, and each row carries the snippet that
    matched so the result explains itself.

    No model is involved. This is two indexed `ilike` scans and a merge — a
    search box that waited on an LLM would be both slower and worse.

    ``across`` searches every surface at once — Chat, Code and all ten
    specialists — and is the one mode that cannot be expressed by leaving the
    other two arguments unset. Absent ``agent_id`` does not mean "any agent";
    it means ``agent_id is null``, which is precisely how a Chat/Code search
    keeps the specialists out of its results. So "everywhere" needs saying
    rather than defaulting, and it drops both filters instead of setting them.
    """
    term = (query or "").strip()
    if not enabled() or not term:
        return []
    # Escape *then* re-check for emptiness. A query of nothing but wildcards
    # ("%", "_") passes the check above, strips to "" here, and would become
    # the pattern "%%" — which matches every session in the account. Searching
    # for a wildcard should find the sessions that literally contain one, and
    # since the escape drops them, the honest answer is nothing.
    needle = _escape_like(term)
    if not needle:
        return []
    client = get_client()
    pattern = f"%{needle}%"

    def _scope(q):
        """The surface filter, applied identically to both scoping queries."""
        if across:
            return q
        q = q.eq("agent_id", agent_id) if agent_id else q.is_("agent_id", "null")
        if section == "chat":
            # Pre-migration rows carry no section and are shown in Chat; see
            # scripts/audit_session_sections.py.
            q = q.or_("section.eq.chat,section.is.null")
        elif section:
            q = q.eq("section", section)
        return q

    def _sessions():
        q = client.table("sessions").select("*").ilike("title", pattern)
        q = q.eq("user_id", user_id) if user_id else q.is_("user_id", "null")
        q = q.eq("is_archived", False)
        q = _scope(q)
        return q.order("updated_at", desc=True).limit(limit).execute()

    def _messages():
        return (
            client.table("messages")
            .select("session_id,content,created_at")
            .eq("role", "user")
            .ilike("content", pattern)
            .order("created_at", desc=True)
            .limit(limit * 6)
            .execute()
        )

    title_res, body_res = await asyncio.gather(
        _run(_sessions), _run(_messages)
    )

    out: list[dict] = []
    seen: set[str] = set()
    for row in getattr(title_res, "data", None) or []:
        seen.add(row["id"])
        out.append({**row, "match": "title", "snippet": None})

    body_rows = getattr(body_res, "data", None) or []
    # Which sessions the matching messages belong to, minus the ones already
    # listed by title, and only the ones this caller is allowed to see. The
    # ownership filter is a second query rather than trust in the first: the
    # message search cannot filter by user, because `messages` has no
    # `user_id`, so the ids it returns have to be checked against `sessions`.
    candidates = [r["session_id"] for r in body_rows if r["session_id"] not in seen]
    if candidates:
        # De-duplicated, order preserved: the first (most recent) hit per
        # session is the snippet worth showing.
        ordered: list[str] = []
        snippets: dict[str, str] = {}
        for row in body_rows:
            sid = row["session_id"]
            if sid in seen or sid in snippets:
                continue
            ordered.append(sid)
            snippets[sid] = _snippet(row.get("content") or "", needle)

        def _owned():
            q = client.table("sessions").select("*").in_("id", ordered[: limit * 3])
            q = q.eq("user_id", user_id) if user_id else q.is_("user_id", "null")
            q = q.eq("is_archived", False)
            q = _scope(q)
            return q.execute()

        owned = await _read(_owned)
        by_id = {r["id"]: r for r in (getattr(owned, "data", None) or [])}
        for sid in ordered:
            row = by_id.get(sid)
            if row is None:
                continue
            out.append({**row, "match": "message", "snippet": snippets.get(sid)})

    return out[:limit]


def _escape_like(term: str) -> str:
    """Neutralise the wildcards a user can type into an `ilike` pattern.

    Without this, searching for `100%` matches every session in the account and
    `_` matches any character — surprising, and on a large history slow.
    PostgREST also treats `,` and `.` as structure inside some filter strings,
    so both are dropped from the pattern rather than escaped.
    """
    out = term.replace("%", "").replace("_", "").replace(",", " ")
    return out.strip()


def _snippet(content: str, term: str, width: int = 90) -> str:
    """The matched phrase with a little context either side, on one line."""
    flat = " ".join(content.split())
    at = flat.lower().find(term.lower())
    if at < 0:
        return flat[:width]
    start = max(0, at - width // 3)
    end = min(len(flat), at + len(term) + width // 2)
    return ("… " if start else "") + flat[start:end] + (" …" if end < len(flat) else "")


# --- projects --------------------------------------------------------------
#
# A project is a container for sessions plus the standing instructions and
# knowledge files that apply to them. Ownership follows `sessions`: uuid
# user_id, and None means the anonymous shelf rather than "every row" — the
# same rule `list_sessions` documents at length, and for the same reason.


async def create_project(
    user_id: str | None = None,
    name: str = "New project",
    description: str | None = None,
    instructions: str | None = None,
    color: str = "slate",
    icon: str | None = None,
) -> dict:
    """Create a project. Returns the row, or {} when persistence is off."""
    if not enabled():
        return {}
    client = get_client()
    row: dict[str, Any] = {
        "user_id": user_id,
        "name": name.strip() or "New project",
        "description": (description or "").strip() or None,
        "instructions": (instructions or "").strip() or None,
        "color": color or "slate",
        "icon": icon,
        "created_at": _now(),
        "updated_at": _now(),
    }
    res = await _run(lambda: client.table("projects").insert(row).execute())
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else {}


async def list_projects(
    user_id: str | None = None,
    archived: bool = False,
    limit: int = 100,
) -> list[dict]:
    """This account's projects, most recently touched first.

    Passing ``user_id=None`` scopes to rows whose ``user_id`` is null — the
    anonymous shelf — and NOT to every project in the table. This service holds
    the service-role key, so an unfiltered query here would hand one caller
    every other account's projects.
    """
    if not enabled():
        return []
    client = get_client()

    def _query():
        q = client.table("projects").select("*").eq("is_archived", archived)
        q = q.is_("user_id", "null") if user_id is None else q.eq("user_id", user_id)
        return q.order("updated_at", desc=True).limit(limit).execute()

    res = await _read(_query)
    return (getattr(res, "data", None) if res else None) or []


async def get_project(project_id: str) -> dict | None:
    """One project by id, or None.

    Entitlement is the caller's job — see `api/ownership.require_project`.
    """
    if not enabled():
        return None
    client = get_client()

    def _query():
        return (
            client.table("projects")
            .select("*")
            .eq("id", project_id)
            .limit(1)
            .execute()
        )

    res = await _read(_query)
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else None


async def set_project(project_id: str, **fields: Any) -> dict | None:
    """Update the columns this call names, leaving the rest alone.

    Only the columns listed below can be written, so a stray key in a request
    body cannot reach the table.
    """
    if not enabled():
        return None
    allowed = {
        "name",
        "description",
        "instructions",
        "color",
        "icon",
        "is_archived",
    }
    row: dict[str, Any] = {
        k: v for k, v in fields.items() if k in allowed and v is not None
    }
    if not row:
        return await get_project(project_id)
    row["updated_at"] = _now()
    client = get_client()
    await _run(
        lambda: client.table("projects").update(row).eq("id", project_id).execute()
    )
    return await get_project(project_id)


async def delete_project(project_id: str) -> None:
    """Delete a project. Sessions inside it survive — the FK is `set null`.

    That is deliberate and is explained on the column in schema.sql: removing
    the container is not a statement about the conversations in it.
    """
    if not enabled():
        return
    client = get_client()
    await _run(lambda: client.table("projects").delete().eq("id", project_id).execute())


async def set_session_project(session_id: str, project_id: str | None) -> None:
    """Move a session into a project, or out of one with ``None``."""
    if not enabled():
        return
    client = get_client()
    await _run(
        lambda: client.table("sessions")
        .update({"project_id": project_id, "updated_at": _now()})
        .eq("id", session_id)
        .execute()
    )


# --- project knowledge files -----------------------------------------------


async def add_project_file(
    project_id: str,
    name: str,
    content: str | None = None,
    storage_path: str | None = None,
    mime: str | None = None,
    bytes_: int = 0,
    status: str = "ready",
    error: str | None = None,
) -> dict:
    """Attach one knowledge file to a project.

    ``char_count`` is stored rather than derived: `projects.context_block`
    decides what fits in the injection budget before it reads any content, and
    would otherwise have to fetch every file to find out how big it is.
    """
    if not enabled():
        return {}
    client = get_client()
    row: dict[str, Any] = {
        "project_id": project_id,
        "name": name,
        "content": content,
        "char_count": len(content or ""),
        "storage_path": storage_path,
        "mime": mime,
        "bytes": bytes_,
        "status": status,
        "error": error,
        "added_at": _now(),
    }
    res = await _run(lambda: client.table("project_files").insert(row).execute())
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else {}


async def list_project_files(project_id: str, with_content: bool = False) -> list[dict]:
    """Files attached to a project.

    ``with_content=False`` omits the text column, which is what every listing
    wants — a project with a few large files would otherwise send hundreds of
    kilobytes to render a list of names.
    """
    if not enabled():
        return []
    client = get_client()
    columns = (
        "*"
        if with_content
        else "id,project_id,name,storage_path,char_count,mime,bytes,status,error,added_at"
    )

    def _query():
        return (
            client.table("project_files")
            .select(columns)
            .eq("project_id", project_id)
            .order("added_at")
            .execute()
        )

    res = await _read(_query)
    return (getattr(res, "data", None) if res else None) or []


async def get_project_files(file_ids: list[str]) -> dict[str, dict]:
    """Content for several knowledge files, keyed by id, in ONE query.

    `projects.context_block` runs on the prompt path of every turn, and doing
    this per file meant a Supabase round trip each — six small files was six
    sequential round trips before the model saw a single token. The budget is
    still decided from the listing's `char_count` first; this only fetches the
    text for the files that survived it.
    """
    if not enabled() or not file_ids:
        return {}
    client = get_client()

    def _query():
        return (
            client.table("project_files")
            .select("*")
            .in_("id", file_ids)
            .execute()
        )

    res = await _read(_query)
    rows = (getattr(res, "data", None) if res else None) or []
    return {r["id"]: r for r in rows if r.get("id")}


async def get_project_file(file_id: str) -> dict | None:
    """One knowledge file by id, content included."""
    if not enabled():
        return None
    client = get_client()

    def _query():
        return (
            client.table("project_files")
            .select("*")
            .eq("id", file_id)
            .limit(1)
            .execute()
        )

    res = await _read(_query)
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else None


async def delete_project_file(file_id: str) -> None:
    if not enabled():
        return
    client = get_client()
    await _run(
        lambda: client.table("project_files").delete().eq("id", file_id).execute()
    )


# --- memory ----------------------------------------------------------------
#
# Per-account facts, applying to every section. `text` user_id to match
# `user_preferences`, which holds the on/off switch and the two custom
# instruction columns and is read on the same path.


async def list_memories(user_id: str, limit: int = 200) -> list[dict]:
    if not enabled() or not user_id:
        return []
    client = get_client()

    def _query():
        return (
            client.table("user_memories")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

    res = await _read(_query)
    return (getattr(res, "data", None) if res else None) or []


async def add_memory(
    user_id: str, content: str, source_session_id: str | None = None
) -> dict:
    """Store one fact. Returns the row so the UI can show it immediately."""
    if not enabled() or not user_id:
        return {}
    content = (content or "").strip()
    if not content:
        return {}
    client = get_client()
    row: dict[str, Any] = {
        "user_id": user_id,
        "content": content,
        "source_session_id": source_session_id,
        "created_at": _now(),
        "updated_at": _now(),
    }
    res = await _run(lambda: client.table("user_memories").insert(row).execute())
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else {}


async def delete_memory(memory_id: str, user_id: str) -> None:
    """Delete one memory.

    Scoped by ``user_id`` as well as id: this is the one place a memory is
    removed, and the service-role key would otherwise happily delete somebody
    else's row given a guessed uuid.
    """
    if not enabled() or not user_id:
        return
    client = get_client()
    await _run(
        lambda: client.table("user_memories")
        .delete()
        .eq("id", memory_id)
        .eq("user_id", user_id)
        .execute()
    )


async def clear_memories(user_id: str) -> None:
    """Forget everything for this account. Used by the Settings panel."""
    if not enabled() or not user_id:
        return
    client = get_client()
    await _run(
        lambda: client.table("user_memories").delete().eq("user_id", user_id).execute()
    )


# --- artifacts -------------------------------------------------------------
#
# Every save is a new row: `artifact_key` is the stable identity and `version`
# counts up within it, so a user's edit adds to the history rather than
# destroying what the model wrote. The current artifact is the highest version
# for its key.


async def add_artifact(
    session_id: str,
    artifact_key: str,
    content: str,
    kind: str = "markdown",
    title: str = "Untitled",
    language: str | None = None,
    created_by: str = "agent",
    user_id: str | None = None,
) -> dict:
    """Write the next version of an artifact. Returns the row.

    The version number is read and then written, which is a race if two writers
    touch one key at once. The unique index on (session_id, artifact_key,
    version) is what makes that race fail loudly instead of quietly producing
    two version 3s that later reads would order arbitrarily — and the only two
    writers are one agent turn and one person, who are not editing the same
    artifact in the same millisecond.
    """
    if not enabled():
        return {}
    client = get_client()
    latest = await get_artifact(session_id, artifact_key)
    row: dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "artifact_key": artifact_key,
        "version": int((latest or {}).get("version") or 0) + 1,
        "kind": kind,
        "title": title,
        "language": language,
        "content": content,
        "created_by": created_by,
        "created_at": _now(),
    }
    res = await _run(lambda: client.table("artifacts").insert(row).execute())
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else {}


async def get_artifact(session_id: str, artifact_key: str) -> dict | None:
    """The current version of one artifact, or None."""
    if not enabled():
        return None
    client = get_client()

    def _query():
        return (
            client.table("artifacts")
            .select("*")
            .eq("session_id", session_id)
            .eq("artifact_key", artifact_key)
            .order("version", desc=True)
            .limit(1)
            .execute()
        )

    res = await _read(_query)
    rows = getattr(res, "data", None) if res else None
    return rows[0] if rows else None


async def list_artifacts(session_id: str) -> list[dict]:
    """The current version of every artifact in a session, newest first.

    Content is included: an artifact is the thing being looked at, and a
    listing that omitted it would be followed immediately by a read of each
    one. They are bounded by `artifacts.MAX_CONTENT_CHARS` on the way in.
    """
    if not enabled():
        return []
    client = get_client()

    def _query():
        return (
            client.table("artifacts")
            .select("*")
            .eq("session_id", session_id)
            .order("version", desc=True)
            .execute()
        )

    res = await _read(_query)
    rows = (getattr(res, "data", None) if res else None) or []
    # Highest version wins per key. Ordered by version descending above, so the
    # first row seen for a key is its current version.
    seen: dict[str, dict] = {}
    for row in rows:
        key = row.get("artifact_key")
        if key and key not in seen:
            seen[key] = row
    return sorted(
        seen.values(), key=lambda r: r.get("created_at") or "", reverse=True
    )


async def artifact_versions(session_id: str, artifact_key: str) -> list[dict]:
    """Every version of one artifact, oldest first, for the version switcher."""
    if not enabled():
        return []
    client = get_client()

    def _query():
        return (
            client.table("artifacts")
            .select("*")
            .eq("session_id", session_id)
            .eq("artifact_key", artifact_key)
            .order("version")
            .execute()
        )

    res = await _read(_query)
    return (getattr(res, "data", None) if res else None) or []
