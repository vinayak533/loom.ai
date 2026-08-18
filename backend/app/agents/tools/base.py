"""Shared plumbing for agent tools.

Deliberately thin. The interesting part of each tool is in its own module; this
only supplies the call context, the "not configured" answer that every
key-dependent tool must give, and the artifact helper the UI renders from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.emitter import Emitter
from app.tools.impl import ToolResult

__all__ = ["ToolContext", "ToolResult", "not_configured", "artifact", "as_json"]


@dataclass
class ToolContext:
    """Everything a tool is allowed to know about the run it is part of.

    Passed by value rather than reached for out of a global, so a tool is
    testable on its own and so nothing in here can be mutated into the graph's
    checkpointed state by accident.
    """

    session_id: str
    agent_id: str
    user_id: str | None
    call_id: str
    emitter: Emitter | None = None
    #: The model powering this turn. `count_tokens` needs it to pick a
    #: tokenizer; nothing else should care.
    model_id: str = ""


def not_configured(tool: str, env_key: str, consequence: str) -> ToolResult:
    """The single, honest answer for a tool whose API key is missing.

    One function so the wording cannot drift between tools, and so the failure
    is always a *result* the model can read and report — never an exception
    that ends the turn, and never a fabricated success.

    ``meta.not_configured`` is what the frontend keys off to render the tool
    card's distinct "not configured" state rather than a generic error.
    """
    return ToolResult(
        output=(
            f"`{tool}` is not configured: the `{env_key}` environment variable "
            f"is unset in backend/.env. {consequence} Tell the user this tool "
            "is unavailable and carry on without it — do not invent a result."
        ),
        success=False,
        meta={"not_configured": True, "tool": tool, "env_key": env_key},
    )


#: Conventions the graph reads out of `ToolResult.meta`:
#:
#:   not_configured  the tool's key is missing; it called nobody. Set by
#:                   :func:`not_configured`.
#:   billable        defaults to True. Set it False when a tool that *usually*
#:                   costs money took a free path this time — a fallback
#:                   extractor, a cache hit — so the surcharge is not charged
#:                   for a vendor call that never happened. Only the tool knows.
#:   artifact        structured payload for the UI; see :func:`artifact`.


def artifact(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Structured output for the UI to render as something other than text.

    Tools whose result is a picture, a citation list or an approval card put
    the machine-readable version here. The model still gets the prose in
    ``ToolResult.output``; this is the parallel channel the frontend draws
    from, so neither side has to parse the other's format.
    """
    return {"artifact": {"kind": kind, **payload}}


def as_json(value: Any, limit: int = 12_000) -> str:
    """Compact JSON for a tool's text output, truncated rather than unbounded.

    A tool that returns a whole document would otherwise push the rest of the
    conversation out of the context window on its own.
    """
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text) - limit} more characters]"
