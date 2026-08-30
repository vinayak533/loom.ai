"""What gets prepended to a system prompt, and in what order.

Both graphs — the Chat/Code loop in `app/agent/graph.py` and the specialist
loop in `app/agents/graph.py` — need the same three things in front of their
own prompt, so they compose it through here rather than each growing a version
that drifts from the other.

Order is the design decision worth stating. It runs:

    <the surface's own system prompt>
    <per-account memory and custom instructions>
    <the project's standing instructions and knowledge>

which is least specific to most specific, so the narrower statement is the one
the model reads last. A project that says "answer in French" beats an account
preference for English, because the user set the project scope deliberately and
more recently. The base prompt goes first because it describes the machinery —
what tools exist, how the sandbox behaves — and no user preference should be
able to bury it.

Everything here is best-effort. A memory store that is briefly unreachable
costs the user their personalisation for that turn, not the turn itself, so a
failure anywhere below returns the base prompt unchanged.
"""

from __future__ import annotations

import asyncio
import logging

from app import memory, projects

log = logging.getLogger(__name__)

#: A separator the model will read as a section break in every prompt format
#: the registry's providers accept. Plain newlines are not enough: several
#: models treat a run of markdown headings as one document and blur the
#: boundary between "how you work" and "what this user prefers".
_RULE = "\n\n---\n\n"


async def compose(
    base: str,
    user_id: str | None = None,
    project_id: str | None = None,
) -> str:
    """The full system prompt for one turn.

    Returns ``base`` unchanged when there is nothing to add, which is the
    common case and must stay free — an anonymous user with no project should
    not pay two round trips to discover there is nothing to inject.
    """
    if not user_id and not project_id:
        return base

    # Independent reads: memory is keyed by user, the project block by project
    # id, and neither needs the other's answer. Sequentially they are two round
    # trips on the critical path before the first token of a turn.
    try:
        memory_block, project_block = await asyncio.gather(
            memory.context_block(user_id),
            projects.context_block(project_id),
            return_exceptions=False,
        )
    except Exception:  # noqa: BLE001 - see module docstring
        log.warning("Prompt preamble could not be composed", exc_info=True)
        return base

    parts = [base]
    if memory_block:
        parts.append(memory_block)
    if project_block:
        parts.append(project_block)
    return _RULE.join(parts)
