"""Persistence for the Learn section.

Same contract as :mod:`app.db.repository` — every call is best-effort and the
supabase-py client is synchronous, so calls are pushed to a worker thread — with
one addition: Learn keeps working when Supabase is not configured.

That is not gold-plating. The rest of the app degrades gracefully without
Supabase (sessions live in the checkpointer, uploads in a bounded cache), and a
Learn section that showed an empty library until you signed up for a database
would be the only part of the product that simply does not run locally. The
in-memory store below is bounded and non-durable, exactly like `app.files`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.supabase_client import enabled, get_client

log = logging.getLogger(__name__)

TABLES = (
    "notebooks",
    "notebook_sources",
    "notebook_chunks",
    "notebook_notes",
    "notebook_lessons",
    "notebook_progress",
    # The course platform. Courses themselves are static content in
    # `app.learn.courses`; only what a person did with them is stored.
    "course_progress",
    "course_exam_attempts",
)

#: The no-Supabase store. Process-local and lost on restart, which is what the
#: `persisted` flag on `/api/learn/config` tells the UI to say.
_MEM: dict[str, list[dict]] = {t: [] for t in TABLES}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Exception:  # noqa: BLE001 - persistence is never fatal
        log.warning("Supabase call failed", exc_info=True)
        return None


# --- generic row access ----------------------------------------------------


async def insert(table: str, row: dict) -> dict:
    row = {"id": row.get("id") or new_id(), **row}
    if enabled():
        client = get_client()
        await _run(lambda: client.table(table).insert(row).execute())
    else:
        _MEM[table].append(row)
    return row


async def insert_many(table: str, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    rows = [{"id": r.get("id") or new_id(), **r} for r in rows]
    if enabled():
        client = get_client()
        # One request per batch, not per row: a 300-page PDF is ~300 chunks and
        # 300 round trips would make adding a source feel broken.
        for batch in _batched(rows, 100):
            await _run(lambda b=batch: client.table(table).insert(b).execute())
    else:
        _MEM[table].extend(rows)
    return rows


async def select(
    table: str,
    order: str | None = None,
    desc: bool = False,
    limit: int | None = None,
    columns: str = "*",
    **filters: Any,
) -> list[dict]:
    if enabled():
        client = get_client()

        def _query():
            q = client.table(table).select(columns)
            for key, value in filters.items():
                if value is not None:
                    q = q.eq(key, value)
            if order:
                q = q.order(order, desc=desc)
            if limit:
                q = q.limit(limit)
            return q.execute()

        res = await _run(_query)
        return getattr(res, "data", None) or []

    rows = [
        r
        for r in _MEM[table]
        if all(v is None or r.get(k) == v for k, v in filters.items())
    ]
    if order:
        rows = sorted(rows, key=lambda r: _sortable(r.get(order)), reverse=desc)
    rows = rows[:limit] if limit else rows
    # Projection matters in this mode too — `list_sources` excludes `content`
    # precisely so the workspace does not ship every document to the browser,
    # and an in-memory store that ignored the column list would quietly undo
    # that only when Supabase is off.
    if columns != "*":
        wanted = [c.strip() for c in columns.split(",")]
        rows = [{k: r.get(k) for k in wanted} for r in rows]
    return [dict(r) for r in rows]


async def find(table: str, row_id: str) -> dict | None:
    rows = await select(table, id=row_id, limit=1)
    return rows[0] if rows else None


async def update(table: str, row_id: str, patch: dict) -> dict | None:
    if enabled():
        client = get_client()
        res = await _run(
            lambda: client.table(table).update(patch).eq("id", row_id).execute()
        )
        data = getattr(res, "data", None) or []
        return data[0] if data else await find(table, row_id)

    for row in _MEM[table]:
        if row.get("id") == row_id:
            row.update(patch)
            return row
    return None


async def delete(table: str, **filters: Any) -> None:
    if enabled():
        client = get_client()

        def _query():
            q = client.table(table).delete()
            for key, value in filters.items():
                q = q.eq(key, value)
            return q.execute()

        await _run(_query)
        return

    _MEM[table] = [
        r for r in _MEM[table] if not all(r.get(k) == v for k, v in filters.items())
    ]


def _sortable(value: Any) -> Any:
    return "" if value is None else value


def _batched(rows: list[dict], size: int):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


# --- notebooks -------------------------------------------------------------


async def create_notebook(
    user_id: str | None, title: str, cover_image: str | None = None
) -> dict:
    return await insert(
        "notebooks",
        {
            "user_id": user_id,
            "title": title or "Untitled notebook",
            # A seed, not a URL — the cover is drawn from it in the browser.
            "cover_image": cover_image or new_id()[:8],
            "is_archived": False,
            "created_at": _now(),
            "updated_at": _now(),
        },
    )


async def list_notebooks(
    user_id: str | None = None, archived: bool = False
) -> list[dict]:
    rows = await select("notebooks", order="updated_at", desc=True, user_id=user_id)
    return [r for r in rows if bool(r.get("is_archived")) is archived]


async def touch_notebook(notebook_id: str) -> None:
    await update("notebooks", notebook_id, {"updated_at": _now()})


async def update_notebook(notebook_id: str, patch: dict) -> dict | None:
    return await update("notebooks", notebook_id, {**patch, "updated_at": _now()})


async def delete_notebook(notebook_id: str) -> None:
    """Delete a notebook and everything in it.

    Every child table declares `on delete cascade`, so on a schema built from
    `schema.sql` the last statement would be enough. The explicit deletes keep
    the in-memory store honest and stop a database whose keys were created
    without the cascade from orphaning a notebook's whole corpus.
    """
    for table in (
        "notebook_progress",
        "notebook_lessons",
        "notebook_notes",
        "notebook_chunks",
        "notebook_sources",
    ):
        await delete(table, notebook_id=notebook_id)
    await delete("notebooks", id=notebook_id)


# --- sources ---------------------------------------------------------------


async def add_source(notebook_id: str, **fields: Any) -> dict:
    row = await insert(
        "notebook_sources",
        {"notebook_id": notebook_id, "added_at": _now(), **fields},
    )
    await touch_notebook(notebook_id)
    return row


async def list_sources(notebook_id: str, with_content: bool = False) -> list[dict]:
    """Source rows for a notebook.

    `content` is excluded by default and it matters: a notebook with three PDFs
    holds a megabyte of extracted text, and the panel that lists them needs a
    title and a size, not the documents themselves.
    """
    columns = "*" if with_content else (
        "id,notebook_id,source_type,title,url,storage_path,char_count,status,error,added_at"
    )
    return await select(
        "notebook_sources",
        order="added_at",
        columns=columns,
        notebook_id=notebook_id,
    )


async def delete_source(notebook_id: str, source_id: str) -> None:
    await delete("notebook_chunks", source_id=source_id)
    await delete("notebook_sources", id=source_id)
    await touch_notebook(notebook_id)


# --- chunks ----------------------------------------------------------------


async def add_chunks(rows: list[dict]) -> list[dict]:
    return await insert_many("notebook_chunks", rows)


async def all_chunks(notebook_id: str) -> list[dict]:
    return await select("notebook_chunks", notebook_id=notebook_id)


async def match_chunks(
    notebook_id: str, query_embedding: list[float], limit: int
) -> list[dict] | None:
    """Nearest chunks via the pgvector RPC. None means "RPC unavailable"."""
    if not enabled():
        return None
    client = get_client()
    res = await _run(
        lambda: client.rpc(
            "match_notebook_chunks",
            {
                "p_notebook_id": notebook_id,
                "p_query_embedding": format_vector(query_embedding),
                "p_match_count": limit,
            },
        ).execute()
    )
    if res is None:
        return None
    return getattr(res, "data", None) or []


def format_vector(vector: list[float]) -> str:
    """pgvector's text input format.

    Sent as a string rather than a JSON array because that is the one
    representation PostgREST passes through to `vector`'s input function
    unchanged, whichever driver version is underneath.
    """
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


def parse_vector(value: Any) -> list[float]:
    """The inverse, for the fallback path — PostgREST returns vectors as text."""
    if isinstance(value, list):
        return [float(v) for v in value]
    if isinstance(value, str) and value.strip():
        try:
            return [float(p) for p in value.strip().strip("[]").split(",") if p]
        except ValueError:
            return []
    return []


# --- notes -----------------------------------------------------------------


async def add_note(notebook_id: str, content: str, source: str) -> dict:
    row = await insert(
        "notebook_notes",
        {
            "notebook_id": notebook_id,
            "content": content,
            "source": source,
            "created_at": _now(),
            "updated_at": _now(),
        },
    )
    await touch_notebook(notebook_id)
    return row


async def list_notes(notebook_id: str) -> list[dict]:
    return await select(
        "notebook_notes", order="updated_at", desc=True, notebook_id=notebook_id
    )


async def update_note(note_id: str, content: str) -> dict | None:
    return await update(
        "notebook_notes", note_id, {"content": content, "updated_at": _now()}
    )


async def delete_note(note_id: str) -> None:
    await delete("notebook_notes", id=note_id)


# --- lessons and progress --------------------------------------------------


async def replace_lessons(notebook_id: str, lessons: list[dict]) -> list[dict]:
    """Regenerating a course replaces it wholesale.

    Progress rows point at lesson ids, so they go with the old sections — a
    "60% complete" carried over onto a differently-shaped curriculum would be
    a number about nothing.
    """
    await delete("notebook_progress", notebook_id=notebook_id)
    await delete("notebook_lessons", notebook_id=notebook_id)
    rows = await insert_many(
        "notebook_lessons",
        [{"notebook_id": notebook_id, "created_at": _now(), **lesson} for lesson in lessons],
    )
    await touch_notebook(notebook_id)
    return rows


async def list_lessons(notebook_id: str) -> list[dict]:
    return await select("notebook_lessons", order="section_order", notebook_id=notebook_id)


async def list_progress(notebook_id: str, user_id: str | None) -> list[dict]:
    rows = await select("notebook_progress", notebook_id=notebook_id)
    return [r for r in rows if r.get("user_id") == user_id]


async def set_progress(
    notebook_id: str, user_id: str | None, section_id: str, completed: bool
) -> dict:
    existing = [
        r
        for r in await select("notebook_progress", notebook_id=notebook_id)
        if r.get("section_id") == section_id and r.get("user_id") == user_id
    ]
    patch = {"completed": completed, "completed_at": _now() if completed else None}
    if existing:
        return await update("notebook_progress", existing[0]["id"], patch) or {
            **existing[0],
            **patch,
        }
    return await insert(
        "notebook_progress",
        {
            "notebook_id": notebook_id,
            "user_id": user_id,
            "section_id": section_id,
            **patch,
        },
    )


# --- course platform -------------------------------------------------------
# Courses, chapters and exams are static content (`app.learn.courses`). What is
# stored here is only what a *person* did with them, which is why nothing below
# writes a title or a question — a course can be rewritten without a migration.
#
# `user_id` is filtered in Python rather than passed to `select`, because the
# generic filter treats `None` as "no filter" and an anonymous learner's rows
# are exactly the ones whose `user_id` is null.


def _mine(rows: list[dict], user_id: str | None) -> list[dict]:
    return [r for r in rows if r.get("user_id") == user_id]


async def list_course_progress(
    user_id: str | None, course_id: str | None = None
) -> list[dict]:
    rows = await select("course_progress", course_id=course_id)
    return _mine(rows, user_id)


async def _find_progress(
    user_id: str | None, course_id: str, chapter_id: str
) -> dict | None:
    rows = await list_course_progress(user_id, course_id)
    return next((r for r in rows if r.get("chapter_id") == chapter_id), None)


async def touch_chapter(user_id: str | None, course_id: str, chapter_id: str) -> dict:
    """Record that a chapter was opened.

    Opening is progress even when nothing is completed: it is what lets
    "Continue learning" reopen the chapter someone was actually reading rather
    than the first unfinished one in the list.
    """
    existing = await _find_progress(user_id, course_id, chapter_id)
    if existing:
        patch = {"last_accessed": _now()}
        return await update("course_progress", existing["id"], patch) or {
            **existing,
            **patch,
        }
    return await insert(
        "course_progress",
        {
            "user_id": user_id,
            "course_id": course_id,
            "chapter_id": chapter_id,
            "completed": False,
            "completed_at": None,
            "last_accessed": _now(),
        },
    )


async def set_chapter_progress(
    user_id: str | None, course_id: str, chapter_id: str, completed: bool
) -> dict:
    existing = await _find_progress(user_id, course_id, chapter_id)
    patch = {
        "completed": completed,
        "completed_at": _now() if completed else None,
        "last_accessed": _now(),
    }
    if existing:
        return await update("course_progress", existing["id"], patch) or {
            **existing,
            **patch,
        }
    return await insert(
        "course_progress",
        {
            "user_id": user_id,
            "course_id": course_id,
            "chapter_id": chapter_id,
            **patch,
        },
    )


async def add_exam_attempt(
    user_id: str | None,
    course_id: str,
    exam_id: str,
    *,
    score: int,
    correct_count: int,
    total_count: int,
    passed: bool,
    weak_topics: list[str],
    topic_scores: list[dict],
    answers: dict,
) -> dict:
    return await insert(
        "course_exam_attempts",
        {
            "user_id": user_id,
            "course_id": course_id,
            "exam_id": exam_id,
            "score": score,
            "correct_count": correct_count,
            "total_count": total_count,
            "passed": passed,
            "weak_topics": weak_topics,
            "topic_scores": topic_scores,
            "answers": answers,
            "created_at": _now(),
        },
    )


async def list_exam_attempts(
    user_id: str | None, course_id: str | None = None
) -> list[dict]:
    rows = await select(
        "course_exam_attempts", order="created_at", desc=True, course_id=course_id
    )
    return _mine(rows, user_id)
