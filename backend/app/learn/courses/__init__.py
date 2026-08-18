"""The course catalogue — a data-driven curriculum for the Learn section.

Every course is a plain dict in its own module under this package. Nothing here
knows what a course is *about*: the registry normalises whatever the modules
declare, derives the shapes the API returns (summaries, chapter metadata,
exams without answer keys), and grades a submission into weak topics and
recommendations.

That split is the whole point. Adding an eleventh course means writing one more
module and adding one line to `MODULES` — no endpoint, no component, and no
migration changes.

Nothing in this package touches the database. Progress lives in
`app.db.learn_repository`; the catalogue is static content and is therefore
built once at import and shared by every request.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Iterable

#: Course modules, in the order they appear in the catalogue.
MODULES = (
    "rag",
    "prompt_engineering",
    "python_fundamentals",
    "typescript",
    "react_nextjs",
    "fastapi_rest",
    "postgresql",
    "git",
    "system_design",
    "ml_basics",
)

#: Below this, a topic is reported as a weak area and earns a recommendation.
WEAK_THRESHOLD = 70

DIFFICULTIES = ("beginner", "intermediate", "advanced")


# --- normalisation ---------------------------------------------------------


def _resource(raw: dict) -> dict:
    return {
        "kind": raw.get("kind") or "doc",
        "title": raw.get("title") or raw.get("url") or "Resource",
        "url": raw.get("url") or "",
        "note": raw.get("note"),
    }


def _video(raw: dict | None) -> dict | None:
    """A video reference.

    Two shapes, because one of them cannot break. `id` embeds a specific
    YouTube video behind a click-to-load facade; `search` opens a YouTube
    search for a phrase, which stays correct even as channels re-upload and
    re-title. Content modules use `id` only where the video is a stable,
    canonical one.
    """
    if not raw:
        return None
    if raw.get("id"):
        return {
            "type": "id",
            "id": raw["id"],
            "title": raw.get("title") or "Watch on YouTube",
            "channel": raw.get("channel"),
        }
    query = raw.get("query") or raw.get("title") or ""
    return {
        "type": "search",
        "query": query,
        "title": raw.get("title") or query,
        "channel": raw.get("channel"),
    }


def _concept(raw: Any) -> dict:
    if isinstance(raw, (list, tuple)):
        term, definition = (list(raw) + ["", ""])[:2]
        return {"term": str(term), "definition": str(definition)}
    return {
        "term": str(raw.get("term", "")),
        "definition": str(raw.get("definition", "")),
    }


def _chapter(course_id: str, index: int, raw: dict) -> dict:
    return {
        "id": raw["id"],
        "course_id": course_id,
        "order": index,
        "title": raw["title"],
        "summary": raw.get("summary", ""),
        "topic": raw.get("topic") or raw["title"],
        "minutes": int(raw.get("minutes") or 15),
        "body": raw.get("body", "").strip(),
        "concepts": [_concept(c) for c in raw.get("concepts", [])],
        "takeaways": list(raw.get("takeaways", [])),
        "resources": [_resource(r) for r in raw.get("resources", [])],
        "video": _video(raw.get("video")),
        "notes": (raw.get("notes") or "").strip() or None,
    }


def _question(exam_id: str, index: int, raw: dict) -> dict:
    return {
        "id": raw.get("id") or f"{exam_id}-q{index + 1}",
        "type": raw.get("type") or "mcq",
        "topic": raw.get("topic") or "General",
        "chapter_id": raw.get("chapter_id"),
        "prompt": raw["prompt"],
        "code": raw.get("code"),
        "language": raw.get("language") or "python",
        "options": list(raw.get("options") or ["True", "False"]),
        "answer": int(raw.get("answer", 0)),
        "explanation": raw.get("explanation", ""),
    }


def _exam(course_id: str, index: int, raw: dict, chapter_ids: list[str]) -> dict:
    exam_id = raw.get("id") or f"{course_id}-exam-{index + 1}"
    covers = list(raw.get("chapter_ids") or chapter_ids)
    return {
        "id": exam_id,
        "course_id": course_id,
        "order": index,
        "title": raw.get("title") or f"Assessment {index + 1}",
        "description": raw.get("description", ""),
        "chapter_ids": covers,
        "pass_score": int(raw.get("pass_score") or 70),
        "questions": [
            _question(exam_id, i, q) for i, q in enumerate(raw.get("questions", []))
        ],
    }


def _build(raw: dict) -> dict:
    course_id = raw["id"]
    chapters = [_chapter(course_id, i, c) for i, c in enumerate(raw["chapters"])]
    chapter_ids = [c["id"] for c in chapters]
    exams = [_exam(course_id, i, e, chapter_ids) for i, e in enumerate(raw.get("exams", []))]
    difficulty = raw.get("difficulty", "beginner")
    return {
        "id": course_id,
        "title": raw["title"],
        "short_title": raw.get("short_title") or raw["title"],
        "subtitle": raw.get("subtitle", ""),
        "description": raw.get("description", "").strip(),
        "difficulty": difficulty if difficulty in DIFFICULTIES else "beginner",
        "tags": list(raw.get("tags", [])),
        "objectives": list(raw.get("objectives", [])),
        "resources": [_resource(r) for r in raw.get("resources", [])],
        "minutes": sum(c["minutes"] for c in chapters),
        "chapters": chapters,
        "exams": exams,
    }


def _load() -> dict[str, dict]:
    catalogue: dict[str, dict] = {}
    for name in MODULES:
        module = import_module(f"{__name__}.{name}")
        course = _build(module.COURSE)
        catalogue[course["id"]] = course
    return catalogue


#: Built once, at import. The catalogue is static content.
COURSES: dict[str, dict] = _load()


# --- lookups ---------------------------------------------------------------


def all_courses() -> list[dict]:
    return list(COURSES.values())


def get_course(course_id: str) -> dict | None:
    return COURSES.get(course_id)


def get_chapter(course_id: str, chapter_id: str) -> dict | None:
    course = COURSES.get(course_id)
    if not course:
        return None
    return next((c for c in course["chapters"] if c["id"] == chapter_id), None)


def get_exam(course_id: str, exam_id: str) -> dict | None:
    course = COURSES.get(course_id)
    if not course:
        return None
    return next((e for e in course["exams"] if e["id"] == exam_id), None)


# --- projections -----------------------------------------------------------
# The API never returns a whole course. The catalogue grid needs counts, the
# course page needs chapter titles, and only the chapter view needs a body —
# so each of those is a separate shape, and the answer key never leaves here.


def course_summary(course: dict) -> dict:
    return {
        "id": course["id"],
        "title": course["title"],
        "short_title": course["short_title"],
        "subtitle": course["subtitle"],
        "description": course["description"],
        "difficulty": course["difficulty"],
        "tags": course["tags"],
        "chapter_count": len(course["chapters"]),
        "exam_count": len(course["exams"]),
        "minutes": course["minutes"],
    }


def chapter_summary(chapter: dict) -> dict:
    return {
        "id": chapter["id"],
        "order": chapter["order"],
        "title": chapter["title"],
        "summary": chapter["summary"],
        "topic": chapter["topic"],
        "minutes": chapter["minutes"],
        "has_video": chapter["video"] is not None,
        "resource_count": len(chapter["resources"]),
    }


def exam_summary(exam: dict) -> dict:
    return {
        "id": exam["id"],
        "order": exam["order"],
        "title": exam["title"],
        "description": exam["description"],
        "chapter_ids": exam["chapter_ids"],
        "question_count": len(exam["questions"]),
        "pass_score": exam["pass_score"],
    }


def question_for_taking(question: dict) -> dict:
    """A question as the exam runner sees it — no `answer`, no `explanation`."""
    return {
        "id": question["id"],
        "type": question["type"],
        "topic": question["topic"],
        "prompt": question["prompt"],
        "code": question["code"],
        "language": question["language"],
        "options": question["options"],
    }


# --- grading ---------------------------------------------------------------


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def grade(
    course: dict, exam: dict, answers: dict[str, Any]
) -> dict:
    """Score a submission and turn its mistakes into something actionable.

    The score is the easy half. The half that matters is the second one: every
    question carries a `topic` and the chapter it came from, so a wrong answer
    points at a specific chapter and a specific set of resources rather than at
    a number. Topics under `WEAK_THRESHOLD` come back as weak areas, each with
    the chapter to revisit and the resources attached to it.
    """
    chapters = {c["id"]: c for c in course["chapters"]}
    per_question: list[dict] = []
    topics: dict[str, dict] = {}

    for question in exam["questions"]:
        raw = answers.get(question["id"])
        try:
            chosen = int(raw) if raw is not None and raw != "" else None
        except (TypeError, ValueError):
            chosen = None
        correct = chosen is not None and chosen == question["answer"]

        per_question.append(
            {
                "id": question["id"],
                "topic": question["topic"],
                "chapter_id": question["chapter_id"],
                "prompt": question["prompt"],
                "options": question["options"],
                "chosen": chosen,
                "answer": question["answer"],
                "correct": correct,
                "explanation": question["explanation"],
            }
        )

        bucket = topics.setdefault(
            question["topic"],
            {"topic": question["topic"], "correct": 0, "total": 0, "chapter_ids": []},
        )
        bucket["total"] += 1
        bucket["correct"] += 1 if correct else 0
        if question["chapter_id"] and question["chapter_id"] not in bucket["chapter_ids"]:
            bucket["chapter_ids"].append(question["chapter_id"])

    correct_count = sum(1 for q in per_question if q["correct"])
    total = len(per_question)
    score = _pct(correct_count, total)

    topic_scores = [
        {**bucket, "score": _pct(bucket["correct"], bucket["total"])}
        for bucket in topics.values()
    ]
    topic_scores.sort(key=lambda t: (t["score"], t["topic"]))

    strong = [t for t in topic_scores if t["score"] >= WEAK_THRESHOLD]
    weak = [t for t in topic_scores if t["score"] < WEAK_THRESHOLD]

    recommendations = [
        _recommendation(course, chapters, bucket, exam) for bucket in weak
    ]

    return {
        "exam_id": exam["id"],
        "course_id": course["id"],
        "score": score,
        "correct_count": correct_count,
        "total_count": total,
        "passed": score >= exam["pass_score"],
        "pass_score": exam["pass_score"],
        "questions": per_question,
        "topic_scores": topic_scores,
        "strong_topics": [t["topic"] for t in strong],
        "weak_topics": [t["topic"] for t in weak],
        "recommendations": recommendations,
    }


def _recommendation(
    course: dict, chapters: dict[str, dict], bucket: dict, exam: dict
) -> dict:
    """One weak topic, turned into "read this, then do this"."""
    targets = [chapters[cid] for cid in bucket["chapter_ids"] if cid in chapters]
    resources: list[dict] = []
    for chapter in targets:
        for resource in chapter["resources"]:
            if all(r["url"] != resource["url"] for r in resources):
                resources.append(resource)
    # Course-level resources are the backstop when a question was not tied to a
    # chapter — better a canonical doc than an empty panel.
    if not resources:
        resources = course["resources"][:2]

    if targets:
        names = ", ".join(f"Chapter {c['order'] + 1} — {c['title']}" for c in targets)
        advice = f"Revisit {names}, then retake the assessment."
    else:
        advice = f"Review the {bucket['topic']} material and retake the assessment."

    return {
        "topic": bucket["topic"],
        "score": _pct(bucket["correct"], bucket["total"]),
        "correct": bucket["correct"],
        "total": bucket["total"],
        "advice": advice,
        "chapters": [
            {"id": c["id"], "order": c["order"], "title": c["title"]} for c in targets
        ],
        "resources": resources[:3],
        "practice": _practice(targets, bucket["topic"]),
        "retake_exam_id": exam["id"],
    }


def _practice(chapters: Iterable[dict], topic: str) -> list[str]:
    """Additional practice, drawn from the chapters' own key takeaways.

    Deliberately generated from content the learner has already seen rather
    than invented: a practice prompt that points at something the course never
    taught is worse than none.
    """
    prompts: list[str] = []
    for chapter in chapters:
        for takeaway in chapter["takeaways"][:2]:
            prompts.append(f"Explain in your own words: {takeaway}")
        for concept in chapter["concepts"][:1]:
            # Plain text, not Markdown: these are rendered as prompts in a list,
            # not passed through the Markdown renderer.
            prompts.append(f"Define “{concept['term']}” without looking it up.")
    if not prompts:
        prompts.append(f"Write a short summary of {topic} from memory.")
    return prompts[:4]
