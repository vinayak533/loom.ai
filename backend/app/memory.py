"""Per-account memory and custom instructions.

The unscoped half of "remember this": who the person is, how they want to be
answered, and facts worth carrying between conversations. Unlike a project
(:mod:`app.projects`) none of this is bounded to a container — it applies to
every section, every session, which is precisely why it gets its own on/off
switch and its own audit surface in Settings.

Three things live here.

**Custom instructions.** Two free-text columns on ``user_preferences``, asked
as two questions and injected under two headings, because "what should Loom
know about you" and "how should Loom respond" are answered differently and
mixing them produces a blob that does neither well.

**Stored facts.** Rows in ``user_memories``. Injected as a plain list.

**Extraction.** After a turn, a cheap model reads the exchange and proposes
facts worth keeping. Three rules make this safe enough to run automatically:

  * it is charged like any other model call — there is no free call anywhere
    in this codebase and this is not going to be the first;
  * it proposes nothing when memory is switched off, and never runs at all for
    an anonymous caller, who has no account to remember against;
  * it is told to extract *stable* facts and to refuse anything sensitive. The
    prompt below is the whole of that boundary, so it is written to be read.

Extraction failure is never surfaced. A turn that produced a good answer is
not a failed turn because the memory pass afterwards could not parse its own
JSON, so everything here logs and returns rather than raising.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.credits import charge_llm
from app.db import repository
from app.llm_router import (
    complete_with_fallback,
    estimate_cost,
    first_available,
)

log = logging.getLogger(__name__)

#: Ceilings on what gets injected. Memory is on every prompt of every turn, so
#: it is the one block that must stay small — a user with 200 remembered facts
#: should not pay for all of them on a one-line question.
MAX_INSTRUCTION_CHARS = 2_000
MAX_MEMORY_ITEMS = 40
MAX_MEMORY_CHARS = 3_000

#: Cheap models, in preference order. Same convention as `TITLE_MODELS` in
#: `agent/llm.py` and `LEARN_MODELS` in `learn/tutor.py`: a tuple resolved
#: through `first_available`, so retiring a model degrades to the next rather
#: than breaking the feature.
MEMORY_MODELS = ("deepseek_v4_flash", "gpt-oss-120b", "llama-4-scout")

EXTRACT_SYSTEM = """\
You extract durable facts about a user from one exchange, so an assistant can \
remember them in later conversations.

Extract only:
- stable preferences ("prefers TypeScript over JavaScript", "wants terse answers")
- durable context about their work ("is building a FastAPI backend for a \
logistics startup")
- explicit corrections of how they want to be addressed or answered

Never extract:
- anything about a one-off task ("wants fizzbuzz written") — that dies with the turn
- passwords, API keys, tokens, payment details, government identifiers, health \
information, or anything else sensitive, even when the user volunteers it
- guesses, inferences about mood, or anything the user did not actually state
- facts already obvious from the current message alone

Answer with JSON and nothing else:
{"memories": ["fact one", "fact two"]}

Most exchanges contain nothing worth keeping. Returning {"memories": []} is the \
correct answer far more often than not, and an empty list is much better than \
a weak fact.\
"""


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " [...]"


async def context_block(user_id: str | None) -> str:
    """This account's contribution to the system prompt, or "" when there is none.

    Anonymous callers get "": there is no account to hang memory on, and
    pooling it under a shared sentinel would let one browser's stated
    preferences steer another's answers.

    Never raises, for the same reason `projects.context_block` does not — a
    memory store that is briefly unreachable should cost the user their
    personalisation for that turn, not the turn.
    """
    if not user_id:
        return ""
    try:
        prefs = await repository.get_preferences(user_id)
    except Exception:  # noqa: BLE001
        log.warning("Preferences unreadable for %s", user_id, exc_info=True)
        return ""

    parts: list[str] = []

    about = _clip(prefs.get("about_you") or "", MAX_INSTRUCTION_CHARS)
    if about:
        parts.append("## About the user\n" + about)

    style = _clip(prefs.get("response_style") or "", MAX_INSTRUCTION_CHARS)
    if style:
        parts.append(
            "## How the user wants you to respond\n"
            "Follow this unless the current message asks for something else.\n\n"
            + style
        )

    # The switch gates the *stored facts*, not the custom instructions above.
    # Those were typed deliberately into a settings box; turning memory off
    # means "stop learning things about me", not "discard what I told you on
    # purpose".
    if prefs.get("memory_enabled", True):
        facts = await _facts_block(user_id)
        if facts:
            parts.append(facts)

    return "\n\n".join(parts)


async def _facts_block(user_id: str) -> str:
    try:
        rows = await repository.list_memories(user_id, limit=MAX_MEMORY_ITEMS)
    except Exception:  # noqa: BLE001
        log.warning("Memories unreadable for %s", user_id, exc_info=True)
        return ""
    if not rows:
        return ""

    lines: list[str] = []
    used = 0
    for row in rows:
        content = (row.get("content") or "").strip()
        if not content:
            continue
        if used + len(content) > MAX_MEMORY_CHARS:
            break
        lines.append(f"- {content}")
        used += len(content)

    if not lines:
        return ""
    return (
        "## What you remember about this user\n"
        "Learned across earlier conversations. Use it when relevant; do not "
        "recite it back or mention that you remembered it unless asked.\n\n"
        + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _text_of(message: Any) -> str:
    """Plain text of a normalised assistant message."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    parts = [
        str(block.get("text") or "")
        for block in (content or [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p).strip()


def _parse(text: str) -> list[str]:
    """Pull the memory list out of a model reply, tolerating a fenced block.

    A model asked for JSON will occasionally wrap it in ``` anyway. That is a
    formatting slip, not a refusal, so it is worth recovering from rather than
    discarding a good extraction over.
    """
    text = (text or "").strip()
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    items = payload.get("memories")
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        fact = item.strip()
        # A "fact" the length of a paragraph is a summary of the conversation,
        # not something worth remembering forever.
        if 3 < len(fact) <= 300:
            out.append(fact)
    return out[:5]


async def extract(
    user_id: str | None,
    session_id: str,
    user_text: str,
    assistant_text: str,
) -> list[dict]:
    """Propose and store memories from one exchange. Best-effort throughout.

    Returns the rows written, so a caller that wants to tell the UI can. The
    empty list is the overwhelmingly common answer and is not an error.
    """
    if not user_id or not (user_text or "").strip():
        return []

    try:
        prefs = await repository.get_preferences(user_id)
    except Exception:  # noqa: BLE001
        return []
    if not prefs.get("memory_enabled", True):
        return []

    model_id = first_available(*MEMORY_MODELS)
    if not model_id:
        log.debug("No model available for memory extraction; skipping.")
        return []

    # Bounded inputs: extraction reads an exchange, and a very long turn adds
    # cost without adding stable facts — those live in what the user said
    # about themselves, which is near the start of a message far more often
    # than buried in a 20 kB paste.
    exchange = (
        f"User said:\n{_clip(user_text, 4_000)}\n\n"
        f"Assistant replied:\n{_clip(assistant_text, 2_000)}"
    )

    try:
        message, resolved = await complete_with_fallback(
            model_id,
            messages=[{"role": "user", "content": exchange}],
            tools=[],
            system=EXTRACT_SYSTEM,
            max_tokens=400,
            section="memory",
        )
    except Exception:  # noqa: BLE001 - a failed extraction is not a failed turn
        log.debug("Memory extraction call failed", exc_info=True)
        return []

    usage = getattr(message, "usage", {}) or {}
    try:
        await charge_llm(
            user_id,
            session_id=session_id,
            agent_id="memory",
            model_id=resolved,
            cost_usd=estimate_cost(resolved, usage),
        )
    except Exception:  # noqa: BLE001
        log.warning("Memory extraction could not be charged", exc_info=True)

    facts = _parse(_text_of(message))
    if not facts:
        return []

    # Don't store what is already known. Exact-match only, deliberately: near
    # duplicate detection needs embeddings, and the failure mode of storing a
    # rephrasing is a slightly redundant list, while the failure mode of a
    # fuzzy match is silently dropping a real new fact.
    try:
        existing = {
            (r.get("content") or "").strip().lower()
            for r in await repository.list_memories(user_id, limit=MAX_MEMORY_ITEMS)
        }
    except Exception:  # noqa: BLE001
        existing = set()

    written: list[dict] = []
    for fact in facts:
        if fact.strip().lower() in existing:
            continue
        try:
            row = await repository.add_memory(user_id, fact, source_session_id=session_id)
        except Exception:  # noqa: BLE001
            log.warning("Memory could not be stored", exc_info=True)
            continue
        if row:
            written.append(row)
            existing.add(fact.strip().lower())

    if written:
        log.info("Memory: learned %d fact(s) for %s", len(written), user_id)
    return written
