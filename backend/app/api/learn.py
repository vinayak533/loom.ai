"""REST surface for the Learn section: notebooks, sources, Q&A, notes, course.

Deliberately plain request/response. The agent's websocket contract
(`app/events.py`) describes a tool-running loop with a sandbox behind it;
answering a question from three PDFs has none of that shape, and widening the
event schema to carry it would couple two things that have no reason to move
together.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.auth import bearer_user
from app.api.ownership import (
    require_note,
    require_notebook,
    require_source,
)
from app.api.ratelimit import RateLimiter
from app.config import get_settings
from app.credits import InsufficientCredits, ensure_can_start
from app.db import learn_repository as repo
from app.files import MAX_UPLOAD_BYTES, PDF_TYPE, save_upload
from app.db.supabase_client import enabled as supabase_enabled
from app.learn import courses as catalog
from app.learn import embeddings, ingest, retrieval, tutor
from app.llm_router import ModelCallError, ModelUnavailableError, display_name

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/learn")

_limiter = RateLimiter(get_settings().rate_limit_uploads_per_minute)

#: Passages pulled for a course outline. More than a question gets, because the
#: outline has to cover the notebook rather than answer one thing about it.
OUTLINE_PASSAGES = 24


def _rate_limit(key: str) -> None:
    allowed, retry_after = _limiter.check(key)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many requests. Try again in {retry_after}s.",
        )


async def _require(user_id: str | None, notebook_id: str) -> dict:
    """Ownership-checked notebook lookup.

    This used to take only the id and assert the row existed, which is a 404 for
    a typo and no protection at all: this service holds the service-role key, so
    "the notebook is there" and "the notebook is yours" are entirely separate
    claims. `require_notebook` makes the second one.
    """
    return await require_notebook(user_id, notebook_id)


def _model_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ModelUnavailableError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


# --- config ----------------------------------------------------------------


@router.get("/config")
async def learn_config():
    """What the Learn section can do in this deployment.

    `persisted` is the one the UI acts on: without Supabase, notebooks live in
    the backend's memory and the library says so rather than letting someone
    build a corpus that a restart deletes.
    """
    return {
        "persisted": supabase_enabled(),
        "embedding_mode": "local",
        "embedding_dim": embeddings.EMBED_DIM,
        "model_id": tutor.default_model(),
        "model_name": display_name(tutor.default_model()),
    }


# --- notebooks -------------------------------------------------------------


@router.get("/notebooks")
async def list_notebooks(
    user_id: str | None = Depends(bearer_user), archived: bool = False
):
    """The library. Each card needs a source count, so it is counted here."""
    notebooks = await repo.list_notebooks(user_id, archived=archived)
    counts = await asyncio.gather(
        *(repo.list_sources(n["id"]) for n in notebooks)
    ) if notebooks else []
    return [
        {**notebook, "source_count": len(sources)}
        for notebook, sources in zip(notebooks, counts)
    ]


@router.post("/notebooks", status_code=status.HTTP_201_CREATED)
async def create_notebook(payload: dict | None = None, user_id: str | None = Depends(bearer_user)):
    title = ((payload or {}).get("title") or "Untitled notebook").strip()[:200]
    notebook = await repo.create_notebook(user_id, title)
    return {**notebook, "source_count": 0}


@router.get("/notebooks/{notebook_id}")
async def get_notebook(notebook_id: str, user_id: str | None = Depends(bearer_user)):
    """Everything one workspace needs, in a single round trip."""
    notebook = await _require(user_id, notebook_id)
    sources, notes, lessons, progress = await asyncio.gather(
        repo.list_sources(notebook_id),
        repo.list_notes(notebook_id),
        repo.list_lessons(notebook_id),
        repo.list_progress(notebook_id, user_id),
    )
    return {
        "notebook": {**notebook, "source_count": len(sources)},
        "sources": sources,
        "notes": notes,
        "lessons": lessons,
        "progress": progress,
    }


@router.patch("/notebooks/{notebook_id}")
async def update_notebook(notebook_id: str, payload: dict, user_id: str | None = Depends(bearer_user)):
    await _require(user_id, notebook_id)
    patch = {}
    if "title" in (payload or {}):
        patch["title"] = str(payload["title"]).strip()[:200] or "Untitled notebook"
    if "is_archived" in (payload or {}):
        patch["is_archived"] = bool(payload["is_archived"])
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send `title` or `is_archived`.")
    return await repo.update_notebook(notebook_id, patch)


@router.delete("/notebooks/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notebook(notebook_id: str, user_id: str | None = Depends(bearer_user)):
    await _require(user_id, notebook_id)
    await repo.delete_notebook(notebook_id)


# --- sources ---------------------------------------------------------------


async def _store_source(
    notebook_id: str,
    source_type: str,
    extracted: ingest.Extracted,
    *,
    url: str | None = None,
    storage_path: str | None = None,
) -> dict:
    """Persist an extracted source and index it.

    A failed extraction is still stored. The panel shows it with its error, so
    "I uploaded that and nothing happened" is never the experience — and the
    row can be deleted like any other.
    """
    row = await repo.add_source(
        notebook_id,
        source_type=source_type,
        title=(extracted.title or "Untitled source")[:200],
        url=url,
        storage_path=storage_path,
        content=extracted.text or None,
        char_count=len(extracted.text or ""),
        status="ready" if extracted.ok else "failed",
        error=extracted.error,
    )

    chunk_count = 0
    if extracted.ok:
        chunk_count = await retrieval.index_source(notebook_id, row["id"], extracted.text)

    return {**{k: v for k, v in row.items() if k != "content"}, "chunk_count": chunk_count}


@router.post("/notebooks/{notebook_id}/sources", status_code=status.HTTP_201_CREATED)
async def add_source(notebook_id: str, payload: dict, user_id: str | None = Depends(bearer_user)):
    """Add a URL or a block of pasted text. PDFs use the upload endpoint."""
    await _require(user_id, notebook_id)
    _rate_limit(notebook_id)

    payload = payload or {}
    source_type = (payload.get("source_type") or "").strip().lower()

    if source_type == "url":
        url = (payload.get("url") or "").strip()
        if not url:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing `url`.")
        extracted = await ingest.from_url(url)
        if payload.get("title"):
            extracted.title = str(payload["title"])[:200]
        return await _store_source(notebook_id, "url", extracted, url=url)

    if source_type == "text":
        raw = payload.get("text") or ""
        if not raw.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing `text`.")
        extracted = ingest.from_text(raw, payload.get("title"))
        return await _store_source(notebook_id, "text", extracted)

    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "`source_type` must be `url` or `text`. Upload PDFs to /sources/upload.",
    )


@router.post("/notebooks/{notebook_id}/sources/upload", status_code=status.HTTP_201_CREATED)
async def upload_source(
    notebook_id: str,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    user_id: str | None = Depends(bearer_user),
):
    """Add a PDF, reusing the app's existing upload pipeline for storage."""
    await _require(user_id, notebook_id)
    _rate_limit(notebook_id)

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File is larger than the 20 MB limit."
        )
    content_type = file.content_type or ""
    filename = file.filename or "upload.pdf"
    if content_type != PDF_TYPE and not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Notebook sources must be PDFs. Paste text or a link for anything else.",
        )

    # Storage is the same bucket and the same `files` row the chat uploads use;
    # the notebook id stands in for the session id, which is what keeps the
    # paths namespaced.
    stored = await save_upload(notebook_id, filename, PDF_TYPE, data)
    extracted = await asyncio.to_thread(ingest.from_pdf, data, filename)
    if title:
        extracted.title = title[:200]
    return await _store_source(
        notebook_id, "pdf", extracted, storage_path=stored.get("storage_path")
    )


@router.delete(
    "/notebooks/{notebook_id}/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_source(notebook_id: str, source_id: str, user_id: str | None = Depends(bearer_user)):
    await require_source(user_id, notebook_id, source_id)
    await repo.delete_source(notebook_id, source_id)


# --- grounded Q&A ----------------------------------------------------------


@router.post("/notebooks/{notebook_id}/ask")
async def ask(notebook_id: str, payload: dict, user_id: str | None = Depends(bearer_user)):
    """Answer a question from this notebook's sources and nothing else."""
    notebook = await _require(user_id, notebook_id)
    _rate_limit(notebook_id)

    question = ((payload or {}).get("question") or "").strip()
    if not question:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing `question`.")

    sources = await repo.list_sources(notebook_id)
    if not any(s.get("status") == "ready" for s in sources):
        return {
            "answer": (
                "This notebook has no readable sources yet. Add a PDF, a link, or "
                "some text on the left and I'll answer from it."
            ),
            "citations": [],
            "model_id": None,
            "model_name": None,
            "grounded": False,
        }

    passages = await retrieval.retrieve(notebook_id, question)
    history = [
        turn
        for turn in ((payload or {}).get("history") or [])
        if isinstance(turn, dict) and turn.get("role") in ("user", "assistant")
    ]

    # Same gate the Agents section uses, for the same reason: refuse a call the
    # balance cannot cover before contacting a provider, rather than after.
    try:
        await ensure_can_start(user_id, "learn")
    except InsufficientCredits as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc

    try:
        result = await tutor.answer(
            question,
            passages,
            history=history,
            model_id=(payload or {}).get("model_id"),
            user_id=user_id,
            session_id=notebook_id,
        )
    except (ModelUnavailableError, ModelCallError) as exc:
        raise _model_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        log.warning("Notebook answer failed for %s", notebook["id"], exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The model call failed.") from exc

    return {
        "answer": result.text,
        "citations": result.citations,
        "model_id": result.model_id,
        "model_name": result.model_name,
        "grounded": bool(passages),
        "usage": result.usage,
    }


# --- notes -----------------------------------------------------------------


@router.get("/notebooks/{notebook_id}/notes")
async def list_notes(notebook_id: str, user_id: str | None = Depends(bearer_user)):
    await _require(user_id, notebook_id)
    return await repo.list_notes(notebook_id)


@router.post("/notebooks/{notebook_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(notebook_id: str, payload: dict, user_id: str | None = Depends(bearer_user)):
    await _require(user_id, notebook_id)
    content = ((payload or {}).get("content") or "").strip()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A note needs content.")
    source = (payload or {}).get("source")
    source = source if source in ("ai_generated", "user_written") else "user_written"
    return await repo.add_note(notebook_id, content, source)


@router.patch("/notebooks/{notebook_id}/notes/{note_id}")
async def update_note(
    notebook_id: str, note_id: str, payload: dict, user_id: str | None = Depends(bearer_user)
):
    await require_note(user_id, notebook_id, note_id)
    content = ((payload or {}).get("content") or "").strip()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A note needs content.")
    row = await repo.update_note(note_id, content)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such note.")
    await repo.touch_notebook(notebook_id)
    return row


@router.delete(
    "/notebooks/{notebook_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_note(notebook_id: str, note_id: str, user_id: str | None = Depends(bearer_user)):
    await require_note(user_id, notebook_id, note_id)
    await repo.delete_note(note_id)


# --- course ----------------------------------------------------------------


@router.get("/notebooks/{notebook_id}/lessons")
async def list_lessons(notebook_id: str, user_id: str | None = Depends(bearer_user)):
    await _require(user_id, notebook_id)
    lessons, progress = await asyncio.gather(
        repo.list_lessons(notebook_id), repo.list_progress(notebook_id, user_id)
    )
    return {"lessons": lessons, "progress": progress}


@router.post("/notebooks/{notebook_id}/lessons/generate")
async def generate_lessons(
    notebook_id: str, payload: dict | None = None, user_id: str | None = Depends(bearer_user)
):
    """Build a course from the notebook's sources. Replaces any previous one."""
    notebook = await _require(user_id, notebook_id)
    _rate_limit(f"{notebook_id}:lessons")

    sources = await repo.list_sources(notebook_id)
    if not any(s.get("status") == "ready" for s in sources):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Add at least one readable source before generating a course.",
        )

    # Broad coverage rather than a targeted question: the outline should see
    # something from every corner of the notebook.
    passages = await retrieval.retrieve(
        notebook_id,
        f"{notebook.get('title') or ''} key concepts, definitions, methods and conclusions",
        limit=OUTLINE_PASSAGES,
    )

    try:
        lessons = await tutor.outline(
            passages,
            notebook.get("title") or "Untitled notebook",
            model_id=(payload or {}).get("model_id"),
        )
    except (ModelUnavailableError, ModelCallError) as exc:
        raise _model_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.warning("Course generation failed for %s", notebook_id, exc_info=True)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Course generation failed."
        ) from exc

    rows = await repo.replace_lessons(notebook_id, lessons)
    return {"lessons": rows, "progress": []}


@router.post("/notebooks/{notebook_id}/progress")
async def set_progress(
    notebook_id: str, payload: dict, user_id: str | None = Depends(bearer_user)
):
    await _require(user_id, notebook_id)
    section_id = ((payload or {}).get("section_id") or "").strip()
    if not section_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing `section_id`.")
    completed = bool((payload or {}).get("completed", True))
    return await repo.set_progress(notebook_id, user_id, section_id, completed)


# ============================================================================
#  The course platform
# ============================================================================
# A different shape from notebooks, deliberately. A notebook is a workspace the
# user fills; a course is authored content they move through. So courses are
# static (`app.learn.courses`), only progress is stored, and the endpoints are
# split by *how much* they return rather than by resource:
#
#   /courses                     grid metadata + progress   (small)
#   /courses/{id}                chapter titles + exams     (medium)
#   /courses/{id}/chapters/{id}  one chapter's body         (large, cacheable)
#
# That split is what keeps the catalogue from shipping 80 chapters of prose to
# render ten cards.


def _course_or_404(course_id: str) -> dict:
    course = catalog.get_course(course_id)
    if not course:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such course.")
    return course


def _progress_summary(course: dict, rows: list[dict]) -> dict:
    """Everything the UI says about where someone is in a course.

    Computed here rather than in the browser so the catalogue grid, the course
    page and the chapter footer cannot disagree about what "70%" means.
    """
    by_chapter = {r["chapter_id"]: r for r in rows}
    chapters = course["chapters"]
    done = [c for c in chapters if by_chapter.get(c["id"], {}).get("completed")]
    total = len(chapters)

    # Resume at the first unfinished chapter. When everything is done, reopen
    # whatever was read last — "continue" should never mean "start over".
    remaining = [c for c in chapters if not by_chapter.get(c["id"], {}).get("completed")]
    if remaining:
        next_chapter_id = remaining[0]["id"]
    else:
        seen = [r for r in rows if r.get("last_accessed")]
        seen.sort(key=lambda r: r.get("last_accessed") or "")
        next_chapter_id = seen[-1]["chapter_id"] if seen else chapters[0]["id"]

    accessed = [r.get("last_accessed") for r in rows if r.get("last_accessed")]

    return {
        "course_id": course["id"],
        "chapter_count": total,
        "completed_count": len(done),
        "percent": round(100 * len(done) / total) if total else 0,
        "next_chapter_id": next_chapter_id,
        "started": bool(rows),
        "completed": total > 0 and len(done) == total,
        "last_accessed": max(accessed) if accessed else None,
        "minutes_remaining": sum(
            c["minutes"] for c in chapters if not by_chapter.get(c["id"], {}).get("completed")
        ),
    }


def _exam_state(exam: dict, done_ids: set[str], attempts: list[dict]) -> dict:
    """An exam's summary plus this learner's history with it.

    Unlocking is by completion of the chapters it covers, not by a separate
    action: finishing chapter 4 is what makes the chapter 1–4 assessment appear.
    """
    mine = [a for a in attempts if a.get("exam_id") == exam["id"]]
    scores = [int(a.get("score") or 0) for a in mine]
    missing = [cid for cid in exam["chapter_ids"] if cid not in done_ids]
    return {
        **catalog.exam_summary(exam),
        "unlocked": not missing,
        "remaining_chapter_ids": missing,
        "attempt_count": len(mine),
        "best_score": max(scores) if scores else None,
        "last_score": scores[0] if scores else None,
        "passed": any(a.get("passed") for a in mine),
        "last_weak_topics": (mine[0].get("weak_topics") or []) if mine else [],
        "last_attempt_at": mine[0].get("created_at") if mine else None,
    }


@router.get("/courses")
async def list_courses(user_id: str | None = Depends(bearer_user)):
    """The catalogue grid: one card's worth of data per course, plus progress."""
    progress_rows, attempts = await asyncio.gather(
        repo.list_course_progress(user_id),
        repo.list_exam_attempts(user_id),
    )

    by_course: dict[str, list[dict]] = {}
    for row in progress_rows:
        by_course.setdefault(row["course_id"], []).append(row)

    out = []
    for course in catalog.all_courses():
        rows = by_course.get(course["id"], [])
        mine = [a for a in attempts if a.get("course_id") == course["id"]]
        scores = [int(a.get("score") or 0) for a in mine]
        out.append(
            {
                **catalog.course_summary(course),
                "progress": _progress_summary(course, rows),
                "exam_attempts": len(mine),
                "best_score": max(scores) if scores else None,
            }
        )

    started = [c for c in out if c["progress"]["started"]]
    all_scores = [int(a.get("score") or 0) for a in attempts]
    return {
        "courses": out,
        "stats": {
            "courses_started": len(started),
            "courses_completed": sum(1 for c in out if c["progress"]["completed"]),
            "chapters_completed": sum(c["progress"]["completed_count"] for c in out),
            "exams_taken": len(attempts),
            "average_score": round(sum(all_scores) / len(all_scores)) if all_scores else None,
        },
    }


@router.get("/courses/{course_id}")
async def get_course(course_id: str, user_id: str | None = Depends(bearer_user)):
    """The course page: chapter titles and exam state — no chapter bodies."""
    course = _course_or_404(course_id)
    progress_rows, attempts = await asyncio.gather(
        repo.list_course_progress(user_id, course_id),
        repo.list_exam_attempts(user_id, course_id),
    )
    by_chapter = {r["chapter_id"]: r for r in progress_rows}
    done_ids = {cid for cid, r in by_chapter.items() if r.get("completed")}

    return {
        "course": {
            **catalog.course_summary(course),
            "objectives": course["objectives"],
            "resources": course["resources"],
        },
        "chapters": [
            {
                **catalog.chapter_summary(chapter),
                "completed": chapter["id"] in done_ids,
                "completed_at": by_chapter.get(chapter["id"], {}).get("completed_at"),
                "last_accessed": by_chapter.get(chapter["id"], {}).get("last_accessed"),
            }
            for chapter in course["chapters"]
        ],
        "exams": [_exam_state(exam, done_ids, attempts) for exam in course["exams"]],
        "progress": _progress_summary(course, progress_rows),
    }


@router.get("/courses/{course_id}/chapters/{chapter_id}")
async def get_chapter(course_id: str, chapter_id: str):
    """One chapter's content.

    No auth dependency and no progress in the response, and both are on
    purpose: this is the only large payload in the section, it is identical for
    every learner, and keeping it free of per-user state is what lets the
    browser cache it for the whole session.
    """
    course = _course_or_404(course_id)
    chapter = catalog.get_chapter(course_id, chapter_id)
    if not chapter:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such chapter.")

    order = chapter["order"]
    siblings = course["chapters"]
    return {
        "course": {
            "id": course["id"],
            "title": course["title"],
            "short_title": course["short_title"],
        },
        "chapter": chapter,
        "chapter_count": len(siblings),
        "previous_id": siblings[order - 1]["id"] if order > 0 else None,
        "next_id": siblings[order + 1]["id"] if order + 1 < len(siblings) else None,
        # The exam this chapter belongs to, so the chapter footer can offer it
        # the moment the last chapter it covers is finished.
        "exam_id": next(
            (e["id"] for e in course["exams"] if chapter_id in e["chapter_ids"]), None
        ),
    }


@router.post("/courses/{course_id}/progress")
async def set_course_progress(
    course_id: str, payload: dict, user_id: str | None = Depends(bearer_user)
):
    """Mark a chapter complete, or just record that it was opened.

    One endpoint for both because they are the same row. Omitting `completed`
    means "I opened this" — which is what makes Continue resume where the
    learner actually was.
    """
    course = _course_or_404(course_id)
    chapter_id = ((payload or {}).get("chapter_id") or "").strip()
    if not catalog.get_chapter(course_id, chapter_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such chapter.")

    completed = (payload or {}).get("completed")
    if completed is None:
        await repo.touch_chapter(user_id, course_id, chapter_id)
    else:
        await repo.set_chapter_progress(user_id, course_id, chapter_id, bool(completed))

    rows, attempts = await asyncio.gather(
        repo.list_course_progress(user_id, course_id),
        repo.list_exam_attempts(user_id, course_id),
    )
    done_ids = {r["chapter_id"] for r in rows if r.get("completed")}
    return {
        "progress": _progress_summary(course, rows),
        # Returned with every completion so the UI can reveal a newly unlocked
        # assessment without a second round trip.
        "exams": [_exam_state(exam, done_ids, attempts) for exam in course["exams"]],
    }


@router.get("/courses/{course_id}/exams/{exam_id}")
async def get_exam(course_id: str, exam_id: str, user_id: str | None = Depends(bearer_user)):
    """The exam paper. Answers and explanations stay on the server."""
    course = _course_or_404(course_id)
    exam = catalog.get_exam(course_id, exam_id)
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such assessment.")

    rows, attempts = await asyncio.gather(
        repo.list_course_progress(user_id, course_id),
        repo.list_exam_attempts(user_id, course_id),
    )
    done_ids = {r["chapter_id"] for r in rows if r.get("completed")}
    state = _exam_state(exam, done_ids, attempts)

    return {
        "exam": {
            **state,
            "questions": [catalog.question_for_taking(q) for q in exam["questions"]],
        },
        "course": {"id": course["id"], "title": course["title"], "short_title": course["short_title"]},
    }


@router.post("/courses/{course_id}/exams/{exam_id}/submit")
async def submit_exam(
    course_id: str, exam_id: str, payload: dict, user_id: str | None = Depends(bearer_user)
):
    """Grade a submission, store the attempt, and return the analysis.

    The score is the least interesting part of the response. `topic_scores`,
    `weak_topics` and `recommendations` are what turn a result into the next
    thing to do — see `app.learn.courses.grade`.
    """
    course = _course_or_404(course_id)
    exam = catalog.get_exam(course_id, exam_id)
    if not exam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such assessment.")

    answers = (payload or {}).get("answers")
    if not isinstance(answers, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send `answers` as an object.")

    result = catalog.grade(course, exam, answers)

    attempt = await repo.add_exam_attempt(
        user_id,
        course_id,
        exam_id,
        score=result["score"],
        correct_count=result["correct_count"],
        total_count=result["total_count"],
        passed=result["passed"],
        weak_topics=result["weak_topics"],
        topic_scores=result["topic_scores"],
        answers={k: v for k, v in answers.items() if isinstance(k, str)},
    )

    rows, attempts = await asyncio.gather(
        repo.list_course_progress(user_id, course_id),
        repo.list_exam_attempts(user_id, course_id),
    )
    done_ids = {r["chapter_id"] for r in rows if r.get("completed")}

    return {
        "result": result,
        "attempt_id": attempt.get("id"),
        "attempt_number": len([a for a in attempts if a.get("exam_id") == exam_id]),
        "exams": [_exam_state(e, done_ids, attempts) for e in course["exams"]],
        "progress": _progress_summary(course, rows),
    }


@router.get("/courses/{course_id}/attempts")
async def list_attempts(course_id: str, user_id: str | None = Depends(bearer_user)):
    _course_or_404(course_id)
    return await repo.list_exam_attempts(user_id, course_id)
