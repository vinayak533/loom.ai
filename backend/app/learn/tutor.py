"""The two model calls the Learn section makes.

Both go through :func:`app.llm_router.complete_with_fallback` — the router is
untouched, this module only decides what to put in front of it, and gets
automatic model fallback on a provider-side failure for free. The rule both
prompts share: the notebook's sources are the only permitted evidence. A tutor that
quietly answers from general knowledge is worse than one that says the sources
do not cover it, because the user cannot tell the two apart.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.learn.retrieval import Passage, as_prompt
from app.credits import charge_llm
from app.llm_router import (
    ModelCallError,
    ModelUnavailableError,
    complete_with_fallback,
    estimate_cost,
    is_available,
)

log = logging.getLogger(__name__)

ANSWER_SYSTEM = """\
You are a study tutor working inside a single notebook. The <context> block \
holds passages retrieved from the user's own uploaded sources, and it is the \
only evidence you may use.

Rules:
- Answer from the passages. Do not add facts from your own knowledge, even if \
you are confident they are correct.
- Cite the passages you used inline as [1], [2] — the numbers are the passage \
ids in the context block.
- If the passages do not answer the question, say so plainly in one sentence \
and name what the notebook would need to cover it. Do not pad the answer out.
- Be direct and concrete. Prefer the source's own terminology over paraphrase.
- Markdown is fine; keep it light. No preamble like "Based on the sources".\
"""

OUTLINE_SYSTEM = """\
You design short, structured courses from a set of study sources.

You will be given passages from one notebook. Produce a sequential curriculum \
that teaches this material from the ground up: each section builds on the one \
before it, and every section is grounded in the passages rather than in what \
you happen to know about the subject.

Return ONLY a JSON object of this exact shape, with no prose around it:

{"sections": [
  {"title": "...",
   "summary": "one sentence on what this section covers",
   "content": "3-6 short paragraphs of markdown explaining this section",
   "quiz": [
     {"question": "...",
      "options": ["...", "...", "...", "..."],
      "answer": 0,
      "explanation": "why that option is right, from the sources"}
   ]}
]}

Constraints:
- 4 to 7 sections. Fewer if the sources are thin — do not invent breadth.
- `answer` is the 0-based index into `options`. Exactly 4 options.
- 2 or 3 quiz questions per section. A section whose material does not support \
a fair question may use an empty `quiz` array.
- Every explanation must be traceable to the passages.\
"""

#: Preference order for Learn's model when the caller does not name one.
#: Notebook answering is a reading-comprehension task over supplied text, so a
#: strong general model is worth more here than a fast one.
LEARN_MODELS = ("mimo_v2_5", "nemotron-3", "qwen3_7_plus", "llama-4-scout")


def default_model() -> str:
    for model_id in LEARN_MODELS:
        if is_available(model_id):
            return model_id
    return get_settings().default_model_id


def resolve_model(model_id: str | None) -> str:
    """Honour an explicit pick when it is usable, otherwise fall back.

    ``auto`` is the agent loop's sentinel and depends on live graph state that
    a notebook question does not have, so it resolves to the default here
    rather than being passed through to a router that cannot dispatch it.
    """
    if model_id and model_id != "auto" and is_available(model_id):
        return model_id
    return default_model()


@dataclass
class Answer:
    text: str
    model_id: str
    model_name: str
    citations: list[dict[str, Any]]
    usage: dict[str, int]


async def answer(
    question: str,
    passages: list[Passage],
    history: list[dict[str, str]] | None = None,
    model_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> Answer:
    """One grounded question, one answer, with its citations resolved.

    ``user_id`` is who the call is billed to. Learn was unmetered until this
    existed, so a notebook question cost the operator money and the user
    nothing, while the header showed them a balance.
    """
    resolved = resolve_model(model_id)

    messages: list[dict[str, Any]] = []
    # A few turns of history so follow-ups ("why?") work, but the retrieved
    # context is always rebuilt for the current question rather than carried
    # forward — otherwise the prompt grows without bound across a session.
    for turn in (history or [])[-6:]:
        content = (turn.get("content") or "").strip()
        if content:
            messages.append({"role": turn.get("role", "user"), "content": content})
    messages.append(
        {"role": "user", "content": f"{as_prompt(passages)}\n\nQuestion: {question}"}
    )

    # `resolved` is rebound to whatever answered: on a provider-side failure
    # the router retries another model, and both the credit debit and the
    # `model_id` returned to the client have to name that one, not the model
    # that errored before producing a single token.
    message, resolved = await complete_with_fallback(
        resolved,
        messages=messages,
        tools=[],
        system=ANSWER_SYSTEM,
        max_tokens=2000,
        section="learn",
    )

    usage = getattr(message, "usage", {}) or {}
    await charge_llm(
        user_id,
        session_id=session_id,
        agent_id="learn",
        model_id=resolved,
        cost_usd=estimate_cost(resolved, usage),
    )

    text = _text_of(message)
    return Answer(
        text=text,
        model_id=resolved,
        model_name=getattr(message, "model_name", resolved),
        citations=_citations(text, passages),
        usage=getattr(message, "usage", {}) or {},
    )


def _citations(text: str, passages: list[Passage]) -> list[dict[str, Any]]:
    """Resolve the [n] markers the model actually used back to their sources."""
    used = {int(n) for n in re.findall(r"\[(\d{1,2})\]", text)}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for index in sorted(used):
        if not 1 <= index <= len(passages):
            continue
        passage = passages[index - 1]
        key = (passage.source_id, passage.chunk_index)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "marker": index,
                "source_id": passage.source_id,
                "source_title": passage.source_title,
                "snippet": passage.content[:280].strip(),
                "similarity": round(passage.similarity, 4),
            }
        )
    return out


async def outline(passages: list[Passage], title: str, model_id: str | None = None) -> list[dict]:
    """Generate a course from a notebook's sources.

    Raises :class:`ValueError` if the model returns something that is not the
    requested JSON — the caller turns that into a 502 rather than storing a
    half-parsed curriculum.
    """
    resolved = resolve_model(model_id)
    message, resolved = await complete_with_fallback(
        resolved,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Notebook: {title}\n\n{as_prompt(passages)}\n\n"
                    "Design the course for this notebook."
                ),
            }
        ],
        tools=[],
        system=OUTLINE_SYSTEM,
        # No cap of our own. A course is the longest single generation in the
        # app — six sections of prose plus their quizzes — and on a thinking
        # model the reasoning is drawn from the same budget. Capping it below
        # the model's own limit truncates the JSON mid-array, which surfaces
        # as "the model did not return valid JSON" for a model that was in
        # fact answering correctly.
        max_tokens=None,
        section="learn_course",
    )

    text = _text_of(message)
    if getattr(message, "stop_reason", "") == "max_tokens" and not text.rstrip().endswith("}"):
        raise ValueError(
            "The course ran past the model's output limit. Try a smaller "
            "notebook, or a model with a larger output budget."
        )

    payload = _json_object(text)
    sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(sections, list) or not sections:
        raise ValueError("The model did not return any course sections.")

    lessons: list[dict] = []
    for order, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        lessons.append(
            {
                "section_title": str(section.get("title") or f"Section {order + 1}")[:200],
                "section_order": order,
                "summary": str(section.get("summary") or "")[:500],
                "content": str(section.get("content") or ""),
                "quiz_data": _clean_quiz(section.get("quiz")),
            }
        )
    if not lessons:
        raise ValueError("The model's course had no usable sections.")
    return lessons


def _clean_quiz(quiz: Any) -> list[dict]:
    """Keep only well-formed questions.

    A malformed question is dropped rather than repaired: a quiz whose answer
    index points at nothing marks a correct answer wrong, which is worse for a
    learner than a section with one question fewer.
    """
    if not isinstance(quiz, list):
        return []
    out: list[dict] = []
    for item in quiz:
        if not isinstance(item, dict):
            continue
        options = [str(o) for o in item.get("options") or [] if str(o).strip()]
        try:
            answer_index = int(item.get("answer"))
        except (TypeError, ValueError):
            continue
        if len(options) < 2 or not 0 <= answer_index < len(options):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        out.append(
            {
                "question": question,
                "options": options,
                "answer": answer_index,
                "explanation": str(item.get("explanation") or "").strip(),
            }
        )
    return out


def _text_of(message: Any) -> str:
    return "".join(
        block.get("text", "")
        for block in getattr(message, "content", []) or []
        if block.get("type") == "text"
    ).strip()


def _json_object(text: str) -> Any:
    """Parse the first JSON object in ``text``.

    Models fence JSON in ```json blocks or precede it with a sentence however
    firmly the prompt says not to, and re-running a 6k-token generation to
    punish that is not a good trade.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    salvaged = _salvage_sections(text)
    if salvaged:
        log.info("Recovered %d complete sections from a truncated response", len(salvaged))
        return {"sections": salvaged}

    raise ValueError("The model did not return valid JSON.")


def _salvage_sections(text: str) -> list[dict]:
    """Pull the complete section objects out of a truncated array.

    A course cut off mid-generation is still five usable sections and one
    unusable one. Throwing all of it away — after a minute of generation the
    user waited through — to punish the last object is the wrong trade, so the
    complete objects are recovered by scanning brace depth and the incomplete
    tail is dropped.
    """
    start = text.find('"sections"')
    if start == -1:
        return []
    start = text.find("[", start)
    if start == -1:
        return []

    sections: list[dict] = []
    depth = 0
    begin = -1
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                begin = i
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and begin != -1:
                try:
                    sections.append(json.loads(text[begin : i + 1]))
                except json.JSONDecodeError:
                    pass
                begin = -1
        elif char == "]" and depth == 0:
            break
    return [s for s in sections if isinstance(s, dict) and s.get("title")]


__all__ = [
    "Answer",
    "ModelCallError",
    "ModelUnavailableError",
    "answer",
    "default_model",
    "outline",
    "resolve_model",
]
