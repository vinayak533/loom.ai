"""Centralised LLM router.

All model access funnels through :func:`call_model`. Agent nodes never touch a
provider SDK directly — they hand the router a ``model_id`` (selected by the
user) and a conversation in the *normalised internal format* (block-structured
messages), and receive normalised stream events + a :class:`NormalizedMessage`
back. Provider adapters do the translation to/from each vendor's wire format.

Providers
---------
* **xai** — Grok, via xAI's native OpenAI-compatible endpoint (NOT routed
  through OpenRouter). ``grok-4-5`` is the default model for every section.
* **openrouter** — an OpenAI-compatible gateway to many vendors. Two distinct
  model slugs are registered. Tool-calling uses the OpenAI function schema.
* **opencode** — OpenCode Go, an OpenAI-compatible gateway. Its four models
  are not meant to be picked by hand: each carries a ``routing_hint`` and Auto
  mode selects between them per turn (see :mod:`app.agent.task_classifier`).

Routing modes
-------------
* ``manual`` — the user picked one model id; every turn uses it.
* ``auto`` — the classifier reads the live graph state each turn, returns a
  hint, and :func:`model_for_hint` maps that hint onto an OpenCode model. The
  session's stored selection is the sentinel :data:`AUTO_MODEL_ID`.

The normalised internal conversation format is block-structured: messages are
``{"role": ..., "content": [blocks]}`` where blocks are ``text`` / ``thinking``
/ ``tool_use`` / ``tool_result``. Adapters translate at the boundary so the
LangGraph agent loop is unaware of which provider answered. (The shape is
inherited from the schema this project started on; it is now purely internal,
and every adapter translates out of it.)

Key management
--------------
Keys are read from ``app.config`` (and therefore ``backend/.env``) only. The
router never sends a key to the frontend. On startup :func:`validate_keys`
logs loudly for every model that is *not* wired up, and those models are hidden
from the registry's ``available`` list — selecting one is impossible, so the
failure mode is a clear startup error rather than a runtime crash.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, AsyncIterator, Literal

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalised types
# ---------------------------------------------------------------------------

StreamKind = Literal[
    "thinking_start",
    "thinking_delta",
    "thinking_end",
    "text_start",
    "text_delta",
    "text_end",
    "done",
]


@dataclass
class StreamEvent:
    kind: StreamKind
    content: str | None = None
    message: "NormalizedMessage | None" = None


@dataclass
class NormalizedMessage:
    """Provider-agnostic result of one model call, in internal block shape."""

    content: list[dict[str, Any]]
    usage: dict[str, int]
    stop_reason: str
    model_name: str


class ModelUnavailableError(RuntimeError):
    """The selected model's key is missing or the id is unknown."""


class ModelCallError(RuntimeError):
    """A provider call failed (rate limit, timeout, outage)."""


# ---------------------------------------------------------------------------
# Routing modes
# ---------------------------------------------------------------------------

RoutingMode = Literal["manual", "auto"]

#: Stored in ``sessions.model_id`` and carried in graph state when the user
#: picks "Auto". It is deliberately *not* a key in ``MODEL_REGISTRY`` — nothing
#: can dispatch to it, so a stale sentinel can never reach a provider.
AUTO_MODEL_ID = "auto"

RoutingHint = Literal[
    "fast_simple",
    "code_editing",
    "visual_structural",
    "complex_longhorizon",
]

#: Short phrase shown to the user when Auto switches models mid-conversation.
HINT_REASON: dict[str, str] = {
    "fast_simple": "quick answers",
    "code_editing": "code editing",
    "visual_structural": "visual input",
    "complex_longhorizon": "a complex multi-step task",
}


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

# USD per million tokens — rough, for the dashboard estimate. display_name is
# what the user sees in the dropdown; never the raw slug.
# ``group`` is the provider label the UI shows above each cluster in the model
# dropdown; ``detail`` is optional secondary text under the display name.
#
# ``supports_vision`` gates whether image/document blocks survive translation
# to the provider's wire format. It is stated explicitly on every entry: a
# model that silently ignores an attached screenshot is worse than one that
# says it cannot read it, so the flag is set from *tested* behaviour only.
#
# ``routing_hint`` marks a model as an Auto-mode target. Models without one are
# manual-selection only; models with one are still selectable by hand.
MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    # --- xAI direct: the default model -----------------------------------
    # Capabilities below are taken from xAI's published model page for
    # grok-4.5, not inferred from the older Grok entries this project once
    # carried: 500k context, function calling supported, input modalities
    # "text, image -> text", structured outputs supported.
    #
    # Pricing is xAI's *sub-200k* tier. xAI bills a request whose prompt
    # reaches 200k tokens at the higher tier ($4/$12) for every token in that
    # request, so a very long turn is under-estimated here by exactly 2x. That
    # is a dashboard estimate, not an invoice, and the alternative — pricing
    # every turn at the higher tier — would over-state the common case badly.
    "grok-4-5": {
        "provider": "xai",
        "group": "xAI",
        "model_name": lambda s: s.xai_model_grok_45,
        "display_name": "Grok 4.5",
        "detail": "Default · 500k context",
        "supports_tools": True,
        "supports_vision": True,
        "max_tokens": lambda s: s.xai_max_tokens,
        "required_key": "xai_api_key",
        "pricing": {
            "input": 2.00 / 1_000_000,
            "output": 6.00 / 1_000_000,
            "cache_read": 0.30 / 1_000_000,
        },
    },
    "llama-70b": {
        "provider": "groq",
        "group": "Groq",
        "model_name": lambda s: s.groq_model_a,
        "display_name": "Llama 70B",
        "supports_tools": True,
        "supports_vision": False,
        "max_tokens": lambda s: s.groq_max_tokens,
        "required_key": "groq_api_key",
        "pricing": {"input": 0.59 / 1_000_000, "output": 0.79 / 1_000_000},
    },
    # Spec placed this under Groq, but Groq has retired every llama-4 variant
    # (verified against its /models list). OpenRouter still serves it, so it is
    # grouped there — the label has to match where the call actually goes.
    "llama-4-scout": {
        "provider": "openrouter",
        "group": "OpenRouter",
        "model_name": lambda s: s.openrouter_model_c,
        "display_name": "Llama 4 Scout",
        "detail": "17B-16E-Instruct",
        "supports_tools": True,
        # Scout is multimodal upstream, but this project has never forwarded
        # images to it. Left False so its behaviour is unchanged; flip only
        # after testing it end to end.
        "supports_vision": False,
        "max_tokens": lambda s: s.openrouter_max_tokens,
        "required_key": "openrouter_api_key",
        "pricing": {"input": 0.11 / 1_000_000, "output": 0.34 / 1_000_000},
    },
    "nemotron-3": {
        "provider": "openrouter",
        "group": "OpenRouter",
        "model_name": lambda s: s.openrouter_model_a,
        "display_name": "Nemotron 3 Ultra",
        "supports_tools": True,
        "supports_vision": False,
        "max_tokens": lambda s: s.openrouter_max_tokens,
        "required_key": "openrouter_api_key",
        "pricing": {"input": 0.60 / 1_000_000, "output": 2.40 / 1_000_000},
    },
    # --- OpenCode Go: the Auto-mode pool ---------------------------------
    # Every capability flag below was verified against the live API rather
    # than inferred from the model's description. What the probe found:
    #
    #   model              tools  images
    #   deepseek-v4-flash    ok   HTTP 400 — rejects `image_url` outright
    #   minimax-m2.7         ok   accepts the block, then answers that it
    #                             cannot see the image (silent blindness —
    #                             the reason its flag is False, not True)
    #   qwen3.7-plus         ok   ok (named the colour of a test image)
    #   mimo-v2.5            ok   ok (same test passed)
    #
    # So `supports_vision` is True for exactly qwen3.7-plus and mimo-v2.5.
    # Auto still sends images to qwen per the routing priority; mimo's flag
    # matters when a long-horizon turn happens to carry an attachment.
    #
    # Caveat on `pricing`: the Go plan bills a flat subscription, not per
    # token, so these are placeholders that keep the dashboard's relative
    # cost ordering sensible. They are NOT quoted rates — do not bill off
    # them. Every other capability field here is test-verified.
    "deepseek_v4_flash": {
        "provider": "opencode",
        "group": "OpenCode",
        "model_name": lambda s: s.opencode_model_fast,
        "display_name": "DeepSeek V4 Flash",
        "detail": "Fast · lowest cost",
        "supports_tools": True,
        "supports_vision": False,
        "routing_hint": "fast_simple",
        "description": (
            "High-speed tasks, simple text chat, general Q&A, quick multi-step "
            "logic. Lowest token cost — the default for lightweight requests."
        ),
        "max_tokens": lambda s: s.opencode_max_tokens,
        "required_key": "opencode_api_key",
        "pricing": {"input": 0.28 / 1_000_000, "output": 0.42 / 1_000_000},
    },
    "minimax_m2_7": {
        "provider": "opencode",
        "group": "OpenCode",
        "model_name": lambda s: s.opencode_model_edit,
        "display_name": "Minimax M2.7",
        "detail": "Code editing",
        "supports_tools": True,
        "supports_vision": False,
        "routing_hint": "code_editing",
        "description": (
            "Editing existing code files, modifying projects, long "
            "conversational threads requiring tight context management "
            "without repetition."
        ),
        "max_tokens": lambda s: s.opencode_max_tokens,
        "required_key": "opencode_api_key",
        "pricing": {"input": 0.30 / 1_000_000, "output": 1.20 / 1_000_000},
    },
    "qwen3_7_plus": {
        "provider": "opencode",
        "group": "OpenCode",
        "model_name": lambda s: s.opencode_model_visual,
        "display_name": "Qwen 3.7 Plus",
        "detail": "Vision · layout",
        "supports_tools": True,
        "supports_vision": True,
        "routing_hint": "visual_structural",
        "description": (
            "Visual inputs (images/screenshots), front-end layout prototyping, "
            "complex tool-calling, structural layout tasks."
        ),
        "max_tokens": lambda s: s.opencode_max_tokens,
        "required_key": "opencode_api_key",
        "pricing": {"input": 0.80 / 1_000_000, "output": 2.40 / 1_000_000},
    },
    "mimo_v2_5": {
        "provider": "opencode",
        "group": "OpenCode",
        "model_name": lambda s: s.opencode_model_complex,
        "display_name": "MiMo V2.5",
        "detail": "Long-horizon reasoning",
        "supports_tools": True,
        "supports_vision": True,
        "routing_hint": "complex_longhorizon",
        "description": (
            "Highly complex, long-horizon coding tasks, deep cross-modal "
            "reasoning, processing large volumes of data at once."
        ),
        "max_tokens": lambda s: s.opencode_max_tokens,
        "required_key": "opencode_api_key",
        "pricing": {"input": 0.55 / 1_000_000, "output": 2.20 / 1_000_000},
    },
}

#: routing_hint -> model_id, derived from the registry so the two can never
#: drift apart. Adding a hinted model is enough to make it routable.
HINT_MODEL: dict[str, str] = {
    meta["routing_hint"]: mid
    for mid, meta in MODEL_REGISTRY.items()
    if meta.get("routing_hint")
}

#: Where Auto lands when the classifier has nothing to go on.
DEFAULT_HINT = "fast_simple"


def model_for_hint(hint: str) -> str:
    """Map a classifier hint onto a model id.

    Falls back to the default hint's model for an unknown hint, so a
    classifier change can never hand the dispatcher an unroutable id.
    """
    return HINT_MODEL.get(hint) or HINT_MODEL[DEFAULT_HINT]


def auto_pool_available() -> bool:
    """True when Auto can route by task, i.e. the OpenCode pool is usable."""
    return bool(HINT_MODEL) and all(
        is_available(mid) for mid in HINT_MODEL.values()
    )


def auto_route(section: str) -> str:
    """Model id Auto falls back to for a section.

    This is the pre-OpenCode behaviour, kept as the degraded path: with no
    ``OPENCODE_API_KEY`` there is no task-routing pool, so Auto resolves from
    the section table instead of failing.
    """
    routes = get_settings().auto_routes
    return routes.get(section, routes["chat"])


def _resolve(meta: dict[str, Any], key: str) -> Any:
    val = meta[key]
    return val(get_settings()) if callable(val) else val


def model_meta(model_id: str) -> dict[str, Any]:
    if model_id == AUTO_MODEL_ID:
        # The sentinel must be resolved to a concrete model by the caller
        # before dispatch. Reaching here means a routing-mode bug, not a
        # user error, so say so plainly.
        raise ModelUnavailableError(
            "`auto` is a routing mode, not a model — resolve it with "
            "model_for_hint() before calling the router."
        )
    meta = MODEL_REGISTRY.get(model_id)
    if meta is None:
        raise ModelUnavailableError(f"Unknown model id `{model_id}`.")
    return meta


def resolve_stored_model(model_id: str | None) -> tuple[str, str | None]:
    """Make a session's *stored* model id safe to run with.

    A session row outlives the registry. When a model is retired the id sitting
    in `sessions.model_id` still points at it, and every later turn on that
    session would raise `ModelUnavailableError` at dispatch — the session opens
    fine and then dies the moment the user sends anything.

    So a stale id is reassigned to the configured default here, at the point
    the session is loaded, and the caller persists the new value and shows the
    returned notice. Returns ``(model_id, notice)`` where ``notice`` is None
    when nothing was changed.

    The `auto` sentinel is deliberately passed through: it is a routing mode,
    resolved per turn, and is never stale.
    """
    default = get_settings().default_model_id
    if not model_id:
        return default, None
    if model_id == AUTO_MODEL_ID or model_id in MODEL_REGISTRY:
        return model_id, None
    log.info(
        "Session model `%s` is no longer registered; reassigning to `%s`.",
        model_id,
        default,
    )
    return default, (
        f"`{model_id}` is no longer available. This session has been switched "
        f"to {display_name(default)}."
    )


def is_available(model_id: str) -> bool:
    try:
        meta = model_meta(model_id)
    except ModelUnavailableError:
        return False
    settings = get_settings()
    return bool(getattr(settings, meta["required_key"], ""))


def available_models() -> list[dict[str, Any]]:
    """Models the UI may show in the dropdown (key present + id known)."""
    out: list[dict[str, Any]] = []
    for mid, meta in MODEL_REGISTRY.items():
        if not is_available(mid):
            continue
        out.append(
            {
                "id": mid,
                "name": meta["display_name"],
                "provider": meta["provider"],
                "group": meta.get("group", meta["provider"]),
                "detail": meta.get("detail"),
                "supports_tools": meta["supports_tools"],
                "supports_vision": meta.get("supports_vision", False),
                "routing_hint": meta.get("routing_hint"),
                "description": meta.get("description"),
            }
        )
    return out


def display_name(model_id: str) -> str:
    if model_id == AUTO_MODEL_ID:
        return "Auto"
    try:
        return model_meta(model_id)["display_name"]
    except ModelUnavailableError:
        return model_id


def supports_vision(model_id: str) -> bool:
    try:
        return bool(model_meta(model_id).get("supports_vision", False))
    except ModelUnavailableError:
        return False


# ---------------------------------------------------------------------------
# Cost estimation (per model, rough)
# ---------------------------------------------------------------------------


def estimate_cost(model_id: str, usage: dict[str, int] | Any) -> float:
    # Fall back to the configured default, then to any registry entry — an
    # unknown id must never raise here, it only skews a dashboard estimate.
    meta = (
        MODEL_REGISTRY.get(model_id)
        or MODEL_REGISTRY.get(get_settings().default_model_id)
        or next(iter(MODEL_REGISTRY.values()))
    )
    pricing = meta["pricing"]

    def _get(name: str) -> int:
        if isinstance(usage, dict):
            return int(usage.get(name) or 0)
        return int(getattr(usage, name, 0) or 0)

    return (
        _get("input_tokens") * pricing["input"]
        + _get("output_tokens") * pricing["output"]
        + _get("cache_creation_input_tokens") * pricing.get("cache_write", 0.0)
        + _get("cache_read_input_tokens") * pricing.get("cache_read", 0.0)
    )


# ---------------------------------------------------------------------------
# Serialisation helper (content blocks -> plain dicts)
# ---------------------------------------------------------------------------


def serialise_blocks(blocks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, dict):
            out.append(block)
        else:
            out.append(block.model_dump(exclude_none=True))
    return out


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------


def validate_keys() -> None:
    """Log loudly for every model whose key is missing. Called from the lifespan.

    We deliberately do *not* hard-crash: ``XAI_API_KEY`` powers the default
    model and is what the agent needs in practice, but OpenRouter / Groq /
    OpenCode are optional — a missing optional key hides those models from the
    dropdown instead of stopping the server.

    Auto mode gets its own check. Because nobody selects an OpenCode model by
    hand, a missing ``OPENCODE_API_KEY`` would otherwise only show up as a
    surprise mid-run when the classifier routed to one; this states it at
    startup instead, and Auto degrades to section routing rather than failing.
    """
    settings = get_settings()
    available = 0
    for mid, meta in MODEL_REGISTRY.items():
        key = meta["required_key"]
        present = bool(getattr(settings, key, ""))
        if present:
            available += 1
            log.info("LLM router: model `%s` (%s) available", mid, meta["display_name"])
        else:
            log.error(
                "LLM router: model `%s` (%s) is NOT available — env key `%s` is "
                "unset. It will be hidden from the model selector. Add the key "
                "to backend/.env and restart to enable it.",
                mid,
                meta["display_name"],
                key.upper(),
            )
    log.info("LLM router: %d/%d models available", available, len(MODEL_REGISTRY))

    missing = [mid for mid in HINT_MODEL.values() if not is_available(mid)]
    if not missing:
        log.info(
            "LLM router: Auto mode routes by task across %s",
            ", ".join(f"{h} -> {m}" for h, m in sorted(HINT_MODEL.items())),
        )
    else:
        log.error(
            "LLM router: Auto mode CANNOT route by task — %d of %d hinted "
            "models are unavailable (%s). `OPENCODE_API_KEY` is unset in "
            "backend/.env. Auto will fall back to the section table (%s). Add "
            "the key and restart to enable task-based routing.",
            len(missing),
            len(HINT_MODEL),
            ", ".join(missing),
            settings.auto_routes,
        )


# ---------------------------------------------------------------------------
# Adapter base + providers
# ---------------------------------------------------------------------------


class BaseAdapter:
    provider: str = ""

    # ``vision`` is a property of the *model*, not the provider (OpenCode
    # serves both sighted and blind models), so it travels as a call argument.

    async def stream(
        self,
        model_name: str,
        messages: list[dict],
        tools: list[dict],
        system: str | None,
        max_tokens: int,
        vision: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        raise NotImplementedError

    async def complete(
        self,
        model_name: str,
        messages: list[dict],
        tools: list[dict],
        system: str | None,
        max_tokens: int,
        vision: bool = False,
    ) -> NormalizedMessage:
        raise NotImplementedError


# --- OpenAI-compatible (xAI / OpenRouter / Groq / OpenCode) -----------------


def _image_part(block: dict) -> dict | None:
    """An internal ``image`` block as an OpenAI ``image_url`` part."""
    source = block.get("source") or {}
    if source.get("type") != "base64":
        # A URL-sourced image passes straight through.
        url = source.get("url")
        return {"type": "image_url", "image_url": {"url": url}} if url else None
    media = source.get("media_type") or "image/png"
    data = source.get("data")
    if not data:
        return None
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media};base64,{data}"},
    }


#: Carries a reasoning-mode model's `reasoning_content` through checkpointed
#: state so it can be handed back on the next call.
#:
#: It is an internal block type only — no provider defines it. Several
#: OpenCode models run in thinking
#: mode and reject a follow-up call whose assistant turn arrives without the
#: `reasoning_content` they produced ("The `reasoning_content` in the thinking
#: mode must be passed back to the API", HTTP 400) — which killed any
#: multi-turn conversation on `deepseek-v4-flash` the moment it reasoned once.
#: Storing it as a block keeps the whole round trip inside this module: the
#: OpenAI translation below turns it back into `reasoning_content`, and
#: providers that did not ask for it never see it (the translation only reads
#: `text` and `tool_use`).
REASONING_BLOCK = "reasoning"


def _reasoning_of(message: dict) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == REASONING_BLOCK
    ).strip()


def _internal_to_openai(
    messages: list[dict],
    system: str | None,
    tools: list[dict],
    vision: bool = False,
    include_reasoning: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Translate the internal block-format conversation to OpenAI chat format,
    and the internal tool schemas to OpenAI function schemas.

    Thinking blocks are dropped (OpenAI has no equivalent). Assistant turns that
    only contained thinking become an empty-content assistant message so the
    tool-call sequence stays intact.

    Attachments are handled by ``vision``:

    * ``vision=True`` — ``image`` blocks become ``image_url`` parts and the
      user turn is sent as a parts array.
    * ``vision=False`` — they become a short text placeholder. Dropping them
      outright (the behaviour before this function knew about images) left the
      model answering questions about a screenshot it was never told existed.

    ``document`` blocks (PDFs) are always placeholdered: the OpenAI chat schema
    has no portable document part, and this gateway rejects the ones that exist.
    """
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
            elif isinstance(content, list):
                tool_results = [
                    b for b in content if b.get("type") == "tool_result"
                ]
                for tr in tool_results:
                    tc = tr.get("content")
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr.get("tool_use_id"),
                            "content": (
                                tc if isinstance(tc, str) else json.dumps(tc)
                            ),
                        }
                    )

                parts: list[dict] = []
                texts: list[str] = []
                for b in content:
                    btype = b.get("type")
                    if btype == "text":
                        texts.append(b.get("text", ""))
                    elif btype == "image":
                        part = _image_part(b) if vision else None
                        if part:
                            parts.append(part)
                        else:
                            texts.append(
                                "[An image was attached to this message. It is "
                                "not readable by the current model — ask the "
                                "user to describe it, or say you cannot see it.]"
                            )
                    elif btype == "document":
                        title = b.get("title") or "document"
                        texts.append(
                            f"[A document ({title}) was attached to this "
                            "message. Its contents are not readable by the "
                            "current model.]"
                        )

                body = "\n".join(t for t in texts if t)
                if parts:
                    if body:
                        parts.insert(0, {"type": "text", "text": body})
                    out.append({"role": "user", "content": parts})
                elif body:
                    out.append({"role": "user", "content": body})
        elif role == "assistant":
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                tool_uses = [b for b in content if b.get("type") == "tool_use"]
                msg_out: dict[str, Any] = {"role": "assistant"}
                msg_out["content"] = "\n".join(texts) or None
                # Thinking-mode models require their own `reasoning_content`
                # back on the next call; everyone else must never see the field.
                if include_reasoning:
                    reasoning = _reasoning_of(msg)
                    if reasoning:
                        msg_out["reasoning_content"] = reasoning
                if tool_uses:
                    msg_out["tool_calls"] = [
                        {
                            "id": t.get("id"),
                            "type": "function",
                            "function": {
                                "name": t.get("name"),
                                "arguments": json.dumps(t.get("input") or {}),
                            },
                        }
                        for t in tool_uses
                    ]
                out.append(msg_out)

    oai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object"},
            },
        }
        for t in tools
    ]
    return out, oai_tools


_FINISH_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "max_tokens": "max_tokens",
}


#: Shown when a turn produced nothing usable. An assistant message with no
#: blocks at all renders as a blank bubble and is rejected as conversation
#: history on the next turn, so there is always *something* here.
EMPTY_TURN_TEXT = (
    "(The model ended its turn without producing an answer. This usually means "
    "it spent its output budget on reasoning — try asking again.)"
)


def _openai_blocks(
    content: str | None, tool_calls: list[dict], reasoning: str = ""
) -> list[dict]:
    blocks: list[dict] = []
    if content:
        blocks.append({"type": "text", "text": content})
    for tc in tool_calls or []:
        fn = tc.get("function", {})
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:  # noqa: BLE001
            args = {"_raw_arguments": raw_args}
        blocks.append(
            {
                "type": "tool_use",
                "id": tc.get("id"),
                "name": fn.get("name"),
                "input": args,
            }
        )
    if not blocks:
        # Reasoning models routinely finish a turn having emitted only
        # `reasoning_content`. That is still the model's output, so prefer it
        # over a placeholder — and never return an empty block list.
        blocks.append(
            {"type": "text", "text": reasoning.strip() or EMPTY_TURN_TEXT}
        )
    # Kept alongside the visible blocks rather than instead of them: the
    # provider wants it back next turn (see REASONING_BLOCK), and the turn
    # cannot be reconstructed later from anything else.
    if reasoning.strip():
        blocks.insert(0, {"type": REASONING_BLOCK, "text": reasoning.strip()})
    return blocks


class _OpenAICompatAdapter(BaseAdapter):
    """Shared base for OpenAI-compatible providers (OpenRouter, xAI)."""

    base_url: str = ""
    key_attr: str = ""
    _cached_client: Any = None

    #: Some providers stream a separate `reasoning_content` field alongside
    #: `content`. Off by default so existing providers keep their exact
    #: behaviour; OpenCode turns it on to drive the thinking UI.
    emit_reasoning: bool = False

    def _client(self):
        if self._cached_client is not None:
            return self._cached_client
        from openai import AsyncOpenAI

        settings = get_settings()
        key = getattr(settings, self.key_attr, "")
        if not key:
            raise ModelUnavailableError(
                f"{self.key_attr.upper()} is not set. Add it to backend/.env "
                "and restart."
            )
        # Keep connections warm so a turn does not start with a TLS
        # handshake, and cap the connect phase so a dead gateway fails fast
        # instead of hanging the run.
        timeout = httpx.Timeout(600.0, connect=5.0, read=600.0, write=30.0)
        self._cached_client = AsyncOpenAI(
            api_key=key,
            base_url=self.base_url,
            timeout=timeout,
            max_retries=2,
            http_client=httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=50,
                    max_keepalive_connections=20,
                    keepalive_expiry=300.0,
                ),
            ),
        )
        return self._cached_client

    async def stream(
        self, model_name, messages, tools, system, max_tokens, vision=False
    ):
        client = self._client()
        oai_messages, oai_tools = _internal_to_openai(
            messages,
            system,
            tools,
            vision=vision,
            # A provider that streams `reasoning_content` is a provider that
            # expects it back; one that does not would reject the field.
            include_reasoning=self.emit_reasoning,
        )
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if oai_tools:
            kwargs["tools"] = oai_tools

        text_started = False
        thinking_started = False
        text_buf = ""
        reasoning_buf = ""
        tool_acc: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        finish_reason: str | None = None

        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = {
                        "input_tokens": getattr(chunk.usage, "prompt_tokens", 0)
                        or 0,
                        "output_tokens": getattr(
                            chunk.usage, "completion_tokens", 0
                        )
                        or 0,
                    }
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                if finish_reason is None and getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                # reasoning (opt-in; not part of the OpenAI spec, but every
                # OpenCode reasoning model streams it in this field)
                if self.emit_reasoning:
                    reasoning = getattr(delta, "reasoning_content", None) or (
                        (getattr(delta, "model_extra", None) or {}).get(
                            "reasoning_content"
                        )
                    )
                    if reasoning:
                        reasoning_buf += reasoning
                        if not thinking_started:
                            thinking_started = True
                            yield StreamEvent("thinking_start")
                        yield StreamEvent("thinking_delta", reasoning)
                # text
                dcontent = getattr(delta, "content", None)
                if dcontent:
                    # Reasoning always precedes the answer; close it first so
                    # the two never interleave in the transcript.
                    if thinking_started:
                        thinking_started = False
                        yield StreamEvent("thinking_end")
                    if not text_started:
                        text_started = True
                        yield StreamEvent("text_start")
                    # Accumulated as well as streamed: the deltas drive the
                    # live UI, but this buffer is what ends up in `messages`
                    # and therefore in every later turn's context.
                    text_buf += dcontent
                    yield StreamEvent("text_delta", dcontent)
                # tool calls
                for tc in getattr(delta, "tool_calls", None) or []:
                    idx = getattr(tc, "index", 0)
                    slot = tool_acc.setdefault(
                        idx, {"id": None, "name": None, "arguments": ""}
                    )
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        if getattr(fn, "name", None):
                            slot["name"] = fn.name
                        if getattr(fn, "arguments", None):
                            slot["arguments"] += fn.arguments
        except ModelUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ModelCallError(f"{self.provider} call failed: {exc}") from exc

        # A turn that was pure reasoning (model went straight to a tool call)
        # still has to close its thinking block.
        if thinking_started:
            yield StreamEvent("thinking_end")
        if text_started:
            yield StreamEvent("text_end")

        content_text: str | None = text_buf or None
        tool_calls: list[dict] = []
        for slot in tool_acc.values():
            tool_calls.append(
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
            )

        yield StreamEvent(
            "done",
            message=NormalizedMessage(
                content=_openai_blocks(content_text, tool_calls, reasoning_buf),
                usage=usage,
                stop_reason=_FINISH_MAP.get(finish_reason or "", "end_turn"),
                model_name=model_name,
            ),
        )

    async def complete(
        self, model_name, messages, tools, system, max_tokens, vision=False
    ):
        client = self._client()
        oai_messages, oai_tools = _internal_to_openai(
            messages,
            system,
            tools,
            vision=vision,
            # A provider that streams `reasoning_content` is a provider that
            # expects it back; one that does not would reject the field.
            include_reasoning=self.emit_reasoning,
        )
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools
        try:
            completion = await client.chat.completions.create(**kwargs)
        except ModelUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ModelCallError(f"{self.provider} call failed: {exc}") from exc
        msg = completion.choices[0].message
        usage = {
            "input_tokens": getattr(completion.usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(completion.usage, "completion_tokens", 0) or 0,
        }
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in (getattr(msg, "tool_calls", None) or [])
        ]
        return NormalizedMessage(
            content=_openai_blocks(
                msg.content,
                tool_calls,
                getattr(msg, "reasoning_content", "") or "",
            ),
            usage=usage,
            stop_reason=_FINISH_MAP.get(
                getattr(completion.choices[0], "finish_reason", "") or "",
                "end_turn",
            ),
            model_name=model_name,
        )


class OpenRouterAdapter(_OpenAICompatAdapter):
    provider = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    key_attr = "openrouter_api_key"


class GroqAdapter(_OpenAICompatAdapter):
    provider = "groq"
    key_attr = "groq_api_key"
    # base_url set per-instance from settings


class XAIAdapter(_OpenAICompatAdapter):
    provider = "xai"
    key_attr = "xai_api_key"
    # base_url set per-instance from settings


class OpenCodeAdapter(_OpenAICompatAdapter):
    """OpenCode Go.

    **It IS OpenAI-compatible** — verified against the live API, not assumed.
    `POST {base}/chat/completions` with `Authorization: Bearer $OPENCODE_API_KEY`
    takes and returns the standard OpenAI chat schema, including `tools` /
    `tool_calls` and SSE streaming with `stream_options.include_usage`. That is
    why this subclasses the shared OpenAI-compatible adapter and adds nothing to
    the wire format. Things a future maintainer will want to know:

    * **Base URL is plan-specific.** Go is ``https://opencode.ai/zen/go/v1``;
      the Zen plan is ``https://opencode.ai/zen/v1`` with a different catalogue.
      A Go key does not authenticate against Zen. `GET {base}/models` lists what
      the key can actually reach and is the fastest way to check a plan.
    * **Cloudflare fronts the API.** A bare `urllib` request with its default
      user-agent gets `403 error code: 1010`. The `openai` SDK (httpx) is fine —
      do not swap it for a hand-rolled client without re-testing.
    * **Reasoning models.** Most of the catalogue streams `reasoning_content`
      alongside `content`, which the base adapter maps to thinking events.
      They also spend output budget on reasoning *before* emitting any text, so
      a small `max_tokens` can return an empty string with
      `finish_reason="length"`. Keep `max_tokens` generous, and do not route
      short utility calls (session titles) here.
    * **Vision is per-model, and one model lies about it.** `minimax-m2.7`
      accepts an `image_url` part and then answers that it cannot see any
      image; `deepseek-v4-flash` at least fails loudly with a 400. Both are
      registered `supports_vision: False` so their images become a text
      placeholder instead. Only `qwen3.7-plus` and `mimo-v2.5` really see.
    """

    provider = "opencode"
    key_attr = "opencode_api_key"
    emit_reasoning = True
    # base_url set per-instance from settings


@lru_cache
def _adapters() -> dict[str, BaseAdapter]:
    settings = get_settings()
    xai = XAIAdapter()
    xai.base_url = settings.xai_base_url
    groq = GroqAdapter()
    groq.base_url = settings.groq_base_url
    opencode = OpenCodeAdapter()
    opencode.base_url = settings.opencode_base_url
    return {
        "openrouter": OpenRouterAdapter(),
        "groq": groq,
        "xai": xai,
        "opencode": opencode,
    }


def _adapter_for(model_id: str) -> tuple[BaseAdapter, dict[str, Any]]:
    meta = model_meta(model_id)
    if not is_available(model_id):
        raise ModelUnavailableError(
            f"Model `{model_id}` is not available — its API key is not set. "
            "Add it to backend/.env and restart."
        )
    return _adapters()[meta["provider"]], meta


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def call_model(
    model_id: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    system: str | None = None,
    stream: bool = True,
    max_tokens: int | None = None,
) -> Any:
    """Dispatch a model call to the right provider adapter.

    Returns an async iterator of :class:`StreamEvent` (``stream=True``) or a
    coroutine yielding a :class:`NormalizedMessage` (``stream=False``).
    """
    adapter, meta = _adapter_for(model_id)
    model_name = _resolve(meta, "model_name")
    if max_tokens is None:
        max_tokens = _resolve(meta, "max_tokens")
    # If the model can't reliably use tools, degrade to text-only here — the
    # agent loop then ends the turn with plain prose instead of failing.
    effective_tools = list(tools or []) if meta["supports_tools"] else []
    # Same idea for attachments: a model that cannot see gets a text note in
    # place of the image rather than a dropped block or a provider 400.
    vision = bool(meta.get("supports_vision", False))
    if stream:
        return adapter.stream(
            model_name, messages, effective_tools, system, max_tokens, vision
        )
    return adapter.complete(
        model_name, messages, effective_tools, system, max_tokens, vision
    )