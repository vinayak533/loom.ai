"""One place that knows every agent tool: its schema, its handler, its cost.

The graph never imports a tool module directly — it asks here for the schemas
an agent is allowed to see and for the handler behind a name the model just
called. That is what makes the toolset a real capability boundary: an agent
cannot invoke a tool that was not in the list it was given, because the schema
was never in its request and the dispatcher checks membership again on the way
back.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.agents.tools import code, documents, email, frontend, imagery, research, routing
from app.agents.tools.base import ToolContext, ToolResult
from app.config import get_settings
from app.credits import UnknownImagePricing, surcharge_for

log = logging.getLogger(__name__)

Handler = Callable[[ToolContext, dict], Awaitable[ToolResult]]

_MODULES = (documents, email, frontend, imagery, research, routing, code)

SCHEMAS: dict[str, dict] = {}
HANDLERS: dict[str, Handler] = {}

for _module in _MODULES:
    for _name, _schema in _module.SCHEMAS.items():
        if _name in SCHEMAS:  # pragma: no cover - a wiring mistake, not runtime
            raise RuntimeError(
                f"Duplicate agent tool `{_name}` in {_module.__name__}. Tool "
                "names are global across all ten agents; rename one."
            )
        SCHEMAS[_name] = _schema
    HANDLERS.update(_module.HANDLERS)

_missing = set(SCHEMAS) ^ set(HANDLERS)
if _missing:  # pragma: no cover - same
    raise RuntimeError(
        f"Agent tools with a schema but no handler (or vice versa): {sorted(_missing)}"
    )


# ---------------------------------------------------------------------------
# Configuration requirements
# ---------------------------------------------------------------------------

#: tool name -> (settings property that must be truthy, env var to name in the
#: message, what the user loses). Only tools that need an external key appear
#: here; everything else is always available because it runs on our own CPU.
#:
#: `search_web` is listed against Tavily but genuinely accepts either key —
#: `configured` below checks both, and the env var named in the UI is the one
#: the user is most likely to be missing.
REQUIREMENTS: dict[str, dict[str, str]] = {
    "generate_image": {
        "flag": "image_gen_enabled",
        "env": "STABILITY_API_KEY",
        "loses": "Agent 5 cannot render images. Everything else it does still works.",
    },
    "send_email": {
        "flag": "resend_enabled",
        "env": "RESEND_API_KEY",
        "loses": "Agent 2 can still write and check drafts; it just cannot send them.",
    },
    "search_web": {
        "flag": "_search_enabled",
        "env": "TEVILY_API_KEY (or EXA_API_KEY)",
        "loses": "Agents 6 and 7 cannot look anything up on the live web.",
    },
    "read_url": {
        # Never hard-unavailable: without Jina it falls back to the Learn
        # section's extractor, so this records the *degraded* state rather than
        # an unconfigured one. `configured` is therefore always True here.
        "flag": "_always",
        "env": "JINA_AI_READER_API_KEY",
        "loses": "Falls back to the built-in extractor, which handles fewer sites.",
    },
    "run_code": {
        "flag": "_sandbox_enabled",
        "env": "E2B_API_KEY",
        "loses": "Agent 10 can still audit code but cannot execute it to prove a fix.",
    },
    "lint_code": {
        "flag": "_always",
        "env": "E2B_API_KEY",
        "loses": "Without a sandbox this degrades to a syntax check only.",
    },
}


def _flag(name: str) -> bool:
    settings = get_settings()
    if name == "_always":
        return True
    if name == "_search_enabled":
        return settings.tavily_enabled or bool(settings.exa_api_key)
    if name == "_sandbox_enabled":
        return bool(settings.e2b_api_key)
    return bool(getattr(settings, name, False))


def is_configured(tool: str) -> bool:
    requirement = REQUIREMENTS.get(tool)
    return True if requirement is None else _flag(requirement["flag"])


def tool_public_meta(tool: str) -> dict[str, Any]:
    """What the UI shows about one tool. Never includes a key, only booleans."""
    schema = SCHEMAS.get(tool)
    requirement = REQUIREMENTS.get(tool)
    configured = is_configured(tool)
    # A tool whose price cannot be resolved (an image model with no published
    # rate) must not take the whole catalogue down with it. `None` here renders
    # as "price unknown"; the tool itself refuses to run, which is where that
    # misconfiguration belongs.
    try:
        surcharge: float | None = surcharge_for(tool)
    except UnknownImagePricing:
        surcharge = None
    return {
        "name": tool,
        # The first sentence of the schema description. The rest is written for
        # the model and reads oddly in a UI card.
        "summary": _first_sentence((schema or {}).get("description", "")),
        # Everything reachable through this registry is real code. The
        # prompt-engineered capabilities live in the registry's
        # `reasoning_tools` and never appear here.
        "kind": "real",
        "configured": configured,
        "requires_key": requirement["env"] if requirement else None,
        "degraded_without_key": bool(requirement) and configured and requirement["flag"] == "_always",
        "without_it": requirement["loses"] if requirement else None,
        "credit_surcharge": surcharge,
    }


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    for stop in (". ", "! ", "? "):
        if stop in text:
            return text.split(stop)[0] + stop.strip()
    return text[:180]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def schemas_for(tool_names: tuple[str, ...] | list[str]) -> list[dict]:
    """The tool schemas for one agent, in its declared order."""
    out: list[dict] = []
    for name in tool_names:
        schema = SCHEMAS.get(name)
        if schema is None:  # pragma: no cover - a registry typo
            log.error("Agent declares unknown tool `%s`; skipping it.", name)
            continue
        out.append(schema)
    return out


async def run(
    tool: str, ctx: ToolContext, args: dict, allowed: tuple[str, ...] | list[str]
) -> ToolResult:
    """Execute one tool call on behalf of an agent.

    ``allowed`` is checked even though the model was only ever handed those
    schemas: a model can hallucinate a tool name it saw in another context, and
    the answer to "may Agent 3 generate an image?" must be no in the dispatcher
    as well as in the prompt.
    """
    if tool not in allowed:
        return ToolResult(
            f"Error: `{tool}` is not one of your tools. You have: "
            f"{', '.join(allowed) or '(none)'}. If the task needs it, say which "
            "specialist agent has it.",
            success=False,
        )

    handler = HANDLERS.get(tool)
    if handler is None:
        return ToolResult(f"Error: unknown tool `{tool}`.", success=False)

    try:
        return await handler(ctx, args or {})
    except Exception as exc:  # noqa: BLE001 - a tool error goes back to the model
        log.exception("Agent tool %s failed", tool)
        return ToolResult(
            f"Error: `{tool}` raised {type(exc).__name__}: {exc}", success=False
        )
