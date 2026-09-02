"""LLM helpers.

All model access is centralised in :mod:`app.llm_router`. This module keeps the
two convenience functions the rest of the codebase imports — ``estimate_cost``
(re-exported from the router) and ``generate_title`` (a best-effort session
naming call routed through the router) — so existing call sites do not change.
"""

from __future__ import annotations

import logging
import re

from app.agent.prompts import PROJECT_META_PROMPT, TITLE_PROMPT
from app.config import get_settings
from app.llm_router import (  # noqa: F401  (re-exported for existing imports)
    EMPTY_TURN_TEXT,
    ModelCallError,
    ModelUnavailableError,
    call_model,
    complete_with_fallback,
    estimate_cost,
    is_available,
)

log = logging.getLogger(__name__)


#: Preference order for the throwaway model that names a session. First one
#: whose key is configured wins; the session default is the last resort.
#:
#: Deliberately no OpenCode models here. They are reasoning models that spend
#: output budget thinking before they emit any text, so a 32-token call to one
#: comes back empty — see the note on ``OpenCodeAdapter``.
TITLE_MODELS = ("gpt-oss-120b", "llama-4-scout", "nemotron-3")


#: The shapes a session title must not have.
#:
#: All three are failures of the *same* kind: the naming call answered with
#: something that is not a name. Only the first was guarded originally, which
#: is why the shelf still filled with reasoning — a model that thinks out loud
#: about a 32-token prompt spends the whole budget narrating it, and the
#: narration was stored verbatim as the session's name.
#:
#: Prefix-anchored on purpose. A title is three to six words, so a phrase that
#: *opens* like an explanation is one; a title that merely contains the word
#: "user" is not, and matching anywhere would throw away good names.
#:
#: Shared with ``scripts.clean_session_debt``, which repairs the rows written
#: while this was loose. One definition, so the pass that cleans up and the
#: guard that stops the next one cannot drift apart.
TITLE_REJECTS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "empty-turn placeholder",
        re.compile(re.escape(EMPTY_TURN_TEXT[:45]), re.I),
    ),
    (
        "narrated prompt",
        re.compile(
            r"^\s*("
            r"the user (wants|is asking|asks|would like|said|has)"
            r"|we (need|have) to"
            r"|i (need|should|will|can|must) (to )?(write|produce|create|come up|make|give)"
            r"|okay[,.!]?\s+(so|the user|let)"
            r"|let'?s (think|write|see|start)"
            r"|(first|so)[,.]\s"
            # Only the preamble form. "Here is upstream data" is somebody's
            # actual opening message and makes a perfectly good title; "Here
            # is a title for the session" is the model clearing its throat.
            r"|here('?s| is) (a|the) (title|name|suggestion)"
            r"|(title|answer)\s*:"
            r"|sure[,.!]\s"
            r")",
            re.I,
        ),
    ),
    # A heading, a fence or a line break means a document was stored in a
    # column that holds a phrase.
    ("markdown document", re.compile(r"^\s*(#{1,6}\s|```)|\n")),
)

#: Above this, the answer is prose rather than a name. The prompt asks for
#: three to six words; sixteen is far enough past that to catch narration
#: which opens in a way the patterns above do not anticipate, without
#: second-guessing a model that simply wrote a long title.
TITLE_WORD_CAP = 16


def title_rejection(title: str | None) -> str | None:
    """Why this string is not a session title, or ``None`` if it is one."""
    if not title or not title.strip():
        return "empty"
    for reason, pattern in TITLE_REJECTS:
        if pattern.search(title):
            return reason
    if len(title.split()) > TITLE_WORD_CAP:
        return "prose, not a name"
    return None


def _title_model() -> str:
    for model_id in TITLE_MODELS:
        if is_available(model_id):
            return model_id
    return get_settings().default_model_id


async def generate_title(first_message: str) -> str | None:
    """Name a session using a cheap secondary model. Best-effort.

    Routed through the central router so model access stays in one place, and
    picked from :data:`TITLE_MODELS` so removing a model from the registry
    cannot leave this pointing at an id that no longer exists. A missing title
    is never worth failing a run over.
    """
    model_id = _title_model()

    # Fallback applies here too. A session whose title generation hits Groq's
    # per-minute cap is not worth leaving unnamed when another cheap model is
    # sitting right there — and this is the call most likely to hit that cap,
    # since it fires on the first message of every new session.
    try:
        message, _ = await complete_with_fallback(
            model_id,
            messages=[{"role": "user", "content": TITLE_PROMPT + first_message[:1000]}],
            tools=[],
            max_tokens=32,
            section="title",
        )
    except (ModelUnavailableError, ModelCallError):
        return None
    except Exception:  # noqa: BLE001
        log.warning("Title generation failed", exc_info=True)
        return None

    title = _text_of(message).strip().strip('"')

    # Two ways this call answers with something that is not a name, both from
    # the same cause. A turn that produced no text comes back as the router's
    # placeholder sentence rather than as an empty string — there has to be
    # *something* in an assistant message or the next request rejects the
    # history — and a reasoning model that does emit text spends a 32-token
    # budget narrating the prompt back ("The user wants a 3-6 word title…").
    # Both were being stored verbatim as the session's name. No name at all is
    # better: the session keeps its default and the next turn may try again.
    #
    # Guarded here rather than at the model choice because `TITLE_MODELS`
    # cannot promise it: `complete_with_fallback` may route past all three into
    # a reasoning model, which is exactly the case that produces both shapes.
    reason = title_rejection(title)
    if reason:
        log.info("Discarding generated title (%s): %r", reason, title[:80])
        return None
    return title[:80] or None


async def generate_project_meta(transcript: str) -> dict | None:
    """Name and describe a Code session from what it actually built.

    Distinct from :func:`generate_title`, which sees only the opening message.
    A project's identity is not knowable then — the first message is a request,
    and what gets built often drifts from it — so this reads the transcript and
    runs on demand rather than automatically.

    Best-effort in exactly the same way: a session that cannot be named keeps
    whatever name it has.
    """
    model_id = _title_model()

    try:
        message, _ = await complete_with_fallback(
            model_id,
            messages=[
                {"role": "user", "content": PROJECT_META_PROMPT + transcript[:14000]}
            ],
            tools=[],
            max_tokens=160,
            section="project_meta",
        )
    except (ModelUnavailableError, ModelCallError):
        return None
    except Exception:  # noqa: BLE001
        log.warning("Project meta generation failed", exc_info=True)
        return None

    return _parse_project_meta(_text_of(message))


def _text_of(message) -> str:
    return "".join(
        block.get("text", "")
        for block in message.content
        if block.get("type") == "text"
    )


def _parse_project_meta(text: str) -> dict | None:
    """Pull NAME/ABOUT out of the reply, tolerating a chatty model.

    Small models decorate: they add a preamble, bold the labels, or wrap the
    name in quotes. Prefixes are matched loosely and a reply with no labels at
    all falls back to its first two non-empty lines, because a usable name from
    a malformed answer beats discarding the call.
    """
    name = ""
    about = ""
    leftovers: list[str] = []

    for raw in text.splitlines():
        line = raw.strip().lstrip("*#-• ").strip()
        if not line:
            continue
        lowered = line.lower()
        if not name and lowered.startswith("name"):
            name = line.split(":", 1)[-1].strip()
        elif not about and lowered.startswith(("about", "description")):
            about = line.split(":", 1)[-1].strip()
        else:
            leftovers.append(line)

    if not name and leftovers:
        name = leftovers.pop(0)
    if not about and leftovers:
        about = leftovers[0]

    name = name.strip().strip('"').strip("*").strip()
    about = about.strip().strip('"').strip("*").strip()
    if not name:
        return None
    return {"title": name[:80], "description": about[:200]}