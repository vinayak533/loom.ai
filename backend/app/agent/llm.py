"""LLM helpers.

All model access is centralised in :mod:`app.llm_router`. This module keeps the
two convenience functions the rest of the codebase imports — ``estimate_cost``
(re-exported from the router) and ``generate_title`` (a best-effort session
naming call routed through the router) — so existing call sites do not change.
"""

from __future__ import annotations

import logging

from app.agent.prompts import PROJECT_META_PROMPT, TITLE_PROMPT
from app.config import get_settings
from app.llm_router import (  # noqa: F401  (re-exported for existing imports)
    ModelCallError,
    ModelUnavailableError,
    call_model,
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
TITLE_MODELS = ("llama-70b", "llama-4-scout", "nemotron-3")


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

    try:
        message = await call_model(
            model_id,
            messages=[{"role": "user", "content": TITLE_PROMPT + first_message[:1000]}],
            tools=[],
            stream=False,
            max_tokens=32,
        )
    except (ModelUnavailableError, ModelCallError):
        return None
    except Exception:  # noqa: BLE001
        log.warning("Title generation failed", exc_info=True)
        return None

    title = _text_of(message).strip().strip('"')
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
        message = await call_model(
            model_id,
            messages=[
                {"role": "user", "content": PROJECT_META_PROMPT + transcript[:14000]}
            ],
            tools=[],
            stream=False,
            max_tokens=160,
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