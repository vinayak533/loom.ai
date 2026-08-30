"""Artifacts: what the model writes beside the conversation.

A long document, a page, a component, a diagram. Things that are *worked on*
rather than said once — and the distinction is the whole reason this exists as
its own surface. A 300-line file pasted into a chat transcript is unreadable,
unscrollable, and gone the moment the next message arrives; the same file in a
panel beside the conversation can be read, edited, and revised across turns.

The Agents section has had this since it was built: a tool result can carry an
`artifact` payload, and `components/agents/ArtifactCard.tsx` renders it. What
was missing was any way for Chat and Code — the two surfaces where people
actually write documents — to produce one. This module is that.

Deliberate, not heuristic
------------------------
The model creates an artifact by *calling a tool*, not by writing a long enough
code fence. A heuristic ("any block over 40 lines becomes an artifact") is
wrong in both directions constantly: it promotes a long stack trace nobody
wants to edit, and it leaves a 12-line config the user has been iterating on
for five turns stuck in the transcript. Asking the model to decide costs a
sentence in a tool description and is right far more often, because the model
knows whether it just wrote a *thing* or an *explanation*.

Versions, not overwrites
------------------------
Every write is a new version. A person editing an artifact adds a version
attributed to them; nothing the model produced is destroyed by someone tidying
it up, and the model reads their edit on the next turn because it reads the
latest. See the note on `public.artifacts` in schema.sql.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.db import repository

log = logging.getLogger(__name__)

#: Kinds the UI knows how to render. A kind it does not recognise falls back to
#: plain text rather than failing, but the model is only told about these.
#: `mermaid` is deliberately absent. There is no mermaid renderer in this
#: frontend, so offering it would produce a "diagram" that displays as its own
#: source — a plausible-looking capability that does not work, which this
#: codebase already refuses elsewhere (see the Deploy button in ProjectPulse).
#: Add the renderer first, then add the kind.
KINDS = ("markdown", "code", "html", "svg")

#: Ceiling on one artifact's content. Generous — an artifact is meant to be
#: long — but not unbounded: the content round-trips through the websocket and
#: is listed with the session, and a model that has decided to emit a megabyte
#: has made a mistake this should contain rather than propagate.
MAX_CONTENT_CHARS = 100_000

#: A key is a handle the model reuses across turns, so it has to be something
#: it can remember and retype exactly. Lowercase, hyphenated, short.
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ArtifactError(ValueError):
    """A bad artifact request. Reported to the model, not raised at the user."""


def normalise_key(raw: str) -> str:
    """Coerce a model-supplied id into a usable key, or refuse it.

    Coerced rather than rejected on case and spacing, because a model that
    writes "Pricing Page" instead of "pricing-page" has understood the request
    and mistyped the format — failing that call teaches it nothing and costs a
    turn. Anything still unusable after coercion is a real error.
    """
    key = (raw or "").strip().lower().replace(" ", "-").replace("_", "-")
    key = re.sub(r"-+", "-", key).strip("-")
    if not _KEY_RE.match(key):
        raise ArtifactError(
            f"`{raw}` is not a usable artifact id. Use lowercase letters, "
            "numbers and hyphens, like `pricing-page`."
        )
    return key


def normalise_kind(raw: str | None, language: str | None) -> str:
    """Pick the kind, tolerating the near-misses models actually produce."""
    kind = (raw or "").strip().lower()
    if kind in KINDS:
        return kind
    # A language with no kind means code; this is the single most common
    # omission and guessing it is obviously right.
    if language:
        return "code"
    aliases = {
        "md": "markdown",
        "text": "markdown",
        "document": "markdown",
        "doc": "markdown",
        "react": "code",
        "component": "code",
        "webpage": "html",
        "page": "html",
    }
    return aliases.get(kind, "markdown")


async def write(
    session_id: str,
    key: str,
    content: str,
    *,
    kind: str | None = None,
    title: str | None = None,
    language: str | None = None,
    created_by: str = "agent",
    user_id: str | None = None,
) -> dict:
    """Create or revise an artifact. Returns the stored row.

    There is no separate create and update at this level: both are "write the
    next version of this key", and the version number says which it was. The
    two *tools* are separate because the model benefits from being told which
    it is doing, not because the storage does.
    """
    key = normalise_key(key)
    content = content or ""
    if len(content) > MAX_CONTENT_CHARS:
        raise ArtifactError(
            f"That artifact is {len(content):,} characters, over the "
            f"{MAX_CONTENT_CHARS:,} limit. Write it to a file in the sandbox "
            "instead."
        )

    previous = await repository.get_artifact(session_id, key)
    resolved_kind = normalise_kind(kind, language) if kind or not previous else (
        previous.get("kind") or "markdown"
    )
    row = await repository.add_artifact(
        session_id,
        artifact_key=key,
        content=content,
        kind=resolved_kind,
        # An update with no title keeps the one it had. Re-titling on every
        # revision would make the panel's heading flicker between synonyms.
        title=(title or (previous or {}).get("title") or "Untitled").strip()[:120],
        language=(language or (previous or {}).get("language") or None),
        created_by=created_by,
        user_id=user_id,
    )
    if not row:
        raise ArtifactError(
            "Artifacts need Supabase configured to persist. Nothing was saved."
        )
    return row


def summarise(row: dict[str, Any]) -> str:
    """The one-line description that goes back to the model as a tool result.

    Deliberately not the content. The model just wrote it and has it in
    context; echoing the whole document back doubles its cost for no
    information, which is the mistake that makes artifact tools expensive.
    """
    lines = (row.get("content") or "").count("\n") + 1
    version = row.get("version") or 1
    what = "Created" if version == 1 else f"Updated to version {version}:"
    return (
        f"{what} artifact `{row.get('artifact_key')}` "
        f"({row.get('kind')}, {lines} lines). It is open beside the "
        "conversation; the user can read and edit it there."
    )
