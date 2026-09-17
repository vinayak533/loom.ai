"""Centralised LLM router.

All model access funnels through :func:`call_model`. Agent nodes never touch a
provider SDK directly — they hand the router a ``model_id`` (selected by the
user) and a conversation in the *normalised internal format* (block-structured
messages), and receive normalised stream events + a :class:`NormalizedMessage`
back. Provider adapters do the translation to/from each vendor's wire format.

Providers
---------
* **groq** — direct OpenAI-compatible API. One slot, ``gpt-oss-120b``.
* **openrouter** — an OpenAI-compatible gateway to many vendors. Two distinct
  model slugs are registered. Tool-calling uses the OpenAI function schema.
* **opencode** — OpenCode Go, an OpenAI-compatible gateway. Four of its models
  carry a ``routing_hint`` and are what Auto mode selects between per turn
  (see :mod:`app.agent.task_classifier`); the rest are manual-only.

One vendor is refused outright rather than merely absent: see
:data:`BANNED_MODEL_SUBSTRINGS`. Removing a registry entry is not sufficient on
its own, because every entry's wire id is settings-driven and one of the
upstream gateways still lists that vendor in its catalogue — so the denylist is
checked in :func:`is_available`, which every selection and dispatch path
already goes through.

Routing modes
-------------
* ``manual`` — the user picked one model id; every turn uses it.
* ``auto`` — the classifier reads the live graph state each turn, returns a
  hint, and :func:`model_for_hint` maps that hint onto an OpenCode model. The
  session's stored selection is the sentinel :data:`AUTO_MODEL_ID`.

Both modes are subject to automatic fallback: when a provider fails for a
reason that is *its* fault (429, 5xx, "model unavailable"), the router retries
the same request against the next model in the failing entry's
``fallback_chain`` rather than surfacing a raw error. A manual pick is not
exempt — completing the user's request beats honouring a selection that is
currently unusable, and the session stays on the user's choice afterwards
either way. See :func:`stream_with_fallback` and
:func:`complete_with_fallback`.

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

import asyncio
import json
import logging
import re
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
    """A provider call failed (rate limit, timeout, outage).

    Carries enough of the original failure to decide whether retrying the same
    request against a *different* model could plausibly help. That decision has
    to be made where the provider exception is still in hand — by the time this
    reaches the agent loop the status code is only a substring of a message —
    so the adapters classify at the raise site and record the verdict here.

    ``retryable`` is the whole point: see :func:`classify_failure`.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status_code: int | None = None,
        retryable: bool = False,
        kind: str = "error",
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable
        #: Short machine-readable label for the failure, used for the log line
        #: and to phrase the toast ("hit a rate limit" vs "is unavailable").
        self.kind = kind

    def user_message(self) -> str:
        """One sentence a person can act on, for the transcript.

        ``str(self)`` is the provider's own error and belongs in the log: for a
        402 that is 900 characters of nested JSON including a `user_id`, and it
        was being written straight into the chat. This says what happened and
        what fixes it, per failure kind, and never echoes the provider payload.
        """
        who = self.provider or "The model provider"
        if self.kind == "out_of_credit":
            return (
                f"{who} rejected the request because the account is out of "
                "credit, and no other configured model could take it either. "
                "Top up that provider's balance, or pick a model from a "
                "different provider in the model selector."
            )
        if self.kind == "rate_limit":
            return (
                f"{who} is rate limiting this account and the fallback models "
                "were unavailable too. Wait a moment and try again."
            )
        if self.kind == "unavailable":
            return (
                f"{who} does not currently serve the selected model. Pick "
                "another model, or check the model slugs in backend/.env."
            )
        if self.kind == "rejected":
            return (
                f"{who} refused the request. This is not a temporary outage — "
                "the request itself was rejected, so retrying the same thing "
                "will fail the same way. See the server log for the provider's "
                "own message."
            )
        return (
            f"The call to {who} failed. See the server log for the provider's "
            "own message."
        )


# --- retryability -----------------------------------------------------------
#
# Falling back is only worth doing when the failure belongs to the *provider*.
# A malformed request or a content-policy refusal will fail identically on the
# next model, so retrying there burns a second call, doubles the latency, and —
# worse — hides an application bug behind a model switch. The split below is
# therefore deliberately conservative: anything not recognised as a provider
# fault is treated as ours and surfaced.

#: Provider-side and worth another model. 413 is in here because Groq returns
#: it for "request too large for model ... on tokens per minute", which is a
#: rate limit wearing a payload-size hat — and a different provider with a
#: different cap genuinely does fix it. 404 covers "model unavailable", which
#: is what a retired-upstream slug looks like from here.
#:
#: 402 is here for the same class of reason, and leaving it out is what broke
#: whole turns. OpenRouter answers `402 "This request requires more credits, or
#: fewer max_tokens. You requested up to 8000 tokens, but can only afford
#: 5186"` once an account's balance drops below what the request's *ceiling*
#: would cost. Nothing about the request is malformed, and it is emphatically
#: not a property of the conversation: price per token differs per model, so a
#: cheaper model on the same key often goes through and a model on another
#: provider always does. Classified as a refusal it ended the turn with a raw
#: provider blob in the transcript; classified here the router just moves on.
RETRYABLE_STATUS: frozenset[int] = frozenset(
    {402, 408, 409, 413, 425, 429, 500, 502, 503, 504, 529}
)

#: Ours, or a refusal. Never retried.
#:
#: 401/403 are here on purpose even though another provider has a different
#: key: a bad or missing credential is a deployment fault that an operator has
#: to see, and quietly routing around it would leave a broken key in place
#: forever. `is_available()` already keeps unkeyed models out of dispatch, so
#: reaching a 401 means the key is present and wrong.
NON_RETRYABLE_STATUS: frozenset[int] = frozenset({400, 401, 403, 422, 451})

#: Substrings that mark a refusal rather than an outage, checked when the
#: provider gave no usable status code. Matched case-insensitively.
_REFUSAL_MARKERS = (
    "content_policy",
    "content policy",
    "content_filter",
    "safety",
    "moderation",
    "invalid_request_error",
    "invalid_api_key",
)

#: Substrings that mark a provider-side fault when no status code came through
#: — chiefly transport failures, which surface as connection/timeout errors.
_RETRYABLE_MARKERS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "quota",
    "requires more credits",
    "insufficient credit",
    "insufficient_quota",
    "too many requests",
    "overloaded",
    "capacity",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "timeout",
    "timed out",
    "connection error",
    "connection reset",
    "apiconnectionerror",
    "internal server error",
)


def classify_failure(exc: BaseException) -> tuple[bool, int | None, str]:
    """Decide whether `exc` justifies trying a different model.

    Returns ``(retryable, status_code, kind)``. ``kind`` is a short label used
    for logging and for the wording of the user-facing notice.

    The status code is authoritative when there is one. Only when there is not
    — a transport error never reaches HTTP — does this fall back to matching
    the message text, and even then the refusal markers are checked first so a
    message containing both cannot be mistaken for an outage.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        if status in NON_RETRYABLE_STATUS:
            return False, status, "rejected"
        if status == 429:
            return True, status, "rate_limit"
        if status == 413:
            return True, status, "rate_limit"
        if status == 402:
            # Its own kind rather than folded into `rate_limit`. The fix is
            # "top the account up", not "wait a minute", and the sentence the
            # user reads is written from this label.
            return True, status, "out_of_credit"
        if status == 404:
            return True, status, "unavailable"
        if status in RETRYABLE_STATUS:
            return True, status, "provider_error"
        if 500 <= status < 600:
            return True, status, "provider_error"
        return False, status, "rejected"

    text = f"{type(exc).__name__}: {exc}".lower()
    if any(m in text for m in _REFUSAL_MARKERS):
        return False, None, "rejected"
    if any(m in text for m in _RETRYABLE_MARKERS):
        kind = (
            "rate_limit"
            if any(m in text for m in ("rate limit", "rate_limit", "ratelimit", "quota", "too many requests"))
            else "provider_error"
        )
        return True, None, kind
    # Unrecognised. Treated as ours — see the note above this block.
    return False, None, "error"


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
    # --- Groq direct ------------------------------------------------------
    # The registry key was `llama-70b` for as long as this slot held a
    # llama-3.x model. It no longer does, and a key that names a model the
    # entry does not serve is a trap for the next person reading the code, so
    # the key is now the model. Sessions still stored against `llama-70b` are
    # handled by `resolve_stored_model()`, which is exactly the path built for
    # a retired id — they are reassigned to the default with a one-line notice
    # rather than dying at dispatch.
    #
    # Everything below is from Groq's own model page for openai/gpt-oss-120b
    # (console.groq.com/docs/model/openai/gpt-oss-120b), confirmed against the
    # live `GET /openai/v1/models` list, and tool calling re-verified with a
    # real call rather than assumed to carry over from the llama model:
    #
    #   wire id   openai/gpt-oss-120b     context   131,072
    #   tools     yes                     vision    no (text in, text out)
    #   pricing   $0.15 in / $0.60 out / $0.075 cached-in per 1M
    #
    # That output rate is the one thing here that is *not* what the previous
    # entry said: it carried $0.75/1M over from the llama model it replaced,
    # which over-charged every Groq turn by 25% on the output half.
    "gpt-oss-120b": {
        "provider": "groq",
        "group": "Groq",
        "model_name": lambda s: s.groq_model_a,
        "display_name": "GPT-OSS 120B",
        # No provider here. `detail` is the one-line "what is this model for"
        # under the name in the picker, and the gateway serving it is not part
        # of that answer — it is the only entry that still named one.
        "detail": "Fast inference",
        "supports_tools": True,
        "supports_vision": False,
        "max_tokens": lambda s: s.groq_max_tokens,
        "required_key": "groq_api_key",
        "fallback_chain": ["nemotron-3", "deepseek_v4_flash", "llama-4-scout"],
        "pricing": {
            "input": 0.15 / 1_000_000,
            "output": 0.60 / 1_000_000,
            "cache_read": 0.075 / 1_000_000,
        },
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
        "fallback_chain": ["deepseek_v4_flash", "gpt-oss-120b", "nemotron-3"],
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
        "fallback_chain": ["mimo_v2_5", "gpt-oss-120b", "llama-4-scout"],
        "pricing": {"input": 0.60 / 1_000_000, "output": 2.40 / 1_000_000},
    },
    # --- OpenCode Go: the Auto-mode pool, plus manual-only extras ---------
    # Every capability flag below was verified against the live API rather
    # than inferred from the model's description. What the probe found:
    #
    #   model              tools  images
    #   deepseek-v4-flash    ok   HTTP 400 "Model only supports text input"
    #   minimax-m2.7         ok   accepts the block, then answers that it
    #                             cannot see the image (silent blindness —
    #                             the reason its flag is False, not True).
    #                             SINCE RETIRED — see `minimax_m3` below;
    #                             `minimax-m3` inherits the same False flag,
    #                             not having been shown to do better.
    #   qwen3.7-plus         ok   ok (named the colour of a test image)
    #   mimo-v2.5            ok   ok (same test passed)
    #   mimo-v2.5-pro        ok   HTTP 400 wrapping upstream 404, "No
    #                             endpoints found that support image input"
    #
    # So `supports_vision` is True for exactly qwen3.7-plus and mimo-v2.5.
    # Auto still sends images to qwen per the routing priority; mimo's flag
    # matters when a long-horizon turn happens to carry an attachment.
    #
    # One trap worth recording, because it looks like a capability regression
    # and is not: qwen3.7-plus rejects an image smaller than 10x10 with
    # `InternalError.Algo.InvalidParameter ... must be larger than 10`. That is
    # a size floor, not a lack of vision. Probe with a real-sized image.
    #
    # Caveat on `pricing`: the Go plan bills a flat subscription, not per
    # token, so these are placeholders that keep the dashboard's relative
    # cost ordering sensible. They are NOT quoted rates — do not bill off
    # them. That applies to `mimo_v2_5_pro` below too: it is priced a notch
    # above the base model to reflect being the heavier of the pair, which is
    # a ranking, not a quote. (The Groq entry above is the opposite case —
    # those three numbers are Groq's published rates.) Every other capability
    # field here is test-verified.
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
        "fallback_chain": ["gpt-oss-120b", "llama-4-scout", "minimax_m3"],
        "pricing": {"input": 0.28 / 1_000_000, "output": 0.42 / 1_000_000},
    },
    # Keyed `minimax_m3`, not `minimax_m2_7`. The slug this entry serves moved
    # (see `opencode_model_edit` — 2.7 is dead upstream), and this file's own
    # rule is that a key naming a model the entry does not serve is a trap for
    # the next reader. Sessions stored against the old key are handled by
    # `resolve_stored_model` via `RETIRED_DISPLAY_NAMES` below.
    "minimax_m3": {
        "provider": "opencode",
        "group": "OpenCode",
        "model_name": lambda s: s.opencode_model_edit,
        "display_name": "MiniMax M3",
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
        # `mimo_v2_5` first, not `nemotron-3`. Both can take over a code
        # edit, but nemotron is the entry an OpenRouter balance runs out on
        # first (see FALLBACK_ORDER), so leading with it spent the turn's one
        # cheap retry on the least likely model to answer.
        "fallback_chain": ["mimo_v2_5", "deepseek_v4_flash", "gpt-oss-120b"],
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
        # mimo_v2_5 leads because it is the only other model in the roster
        # that can actually see an image, so a turn carrying an attachment
        # keeps its eyes. The two text-only entries behind it are the last
        # resort — images degrade to a text note there rather than failing.
        "fallback_chain": ["mimo_v2_5", "nemotron-3", "gpt-oss-120b"],
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
        # qwen3_7_plus first for the same reason: it sees.
        "fallback_chain": ["qwen3_7_plus", "nemotron-3", "gpt-oss-120b"],
        "pricing": {"input": 0.55 / 1_000_000, "output": 2.20 / 1_000_000},
    },
    # MiMo V2.5 Pro is a *separate model*, not a relabelling of the entry
    # above. OpenCode Go's live `GET /models` lists `mimo-v2.5` and
    # `mimo-v2.5-pro` as two distinct ids, and they do not behave alike: Pro
    # refuses image input outright where the base model reads images fine.
    # That difference is the whole reason this is a new entry rather than a
    # renamed one — merging them would have silently taken vision away from
    # every long-horizon turn that carries an attachment.
    #
    # No `routing_hint`: the four Auto slots are already filled, and
    # `complex_longhorizon` stays with the base model precisely because it can
    # see. Pro is manual-only — pick it for a hard text-only task.
    "mimo_v2_5_pro": {
        "provider": "opencode",
        "group": "OpenCode",
        "model_name": lambda s: s.opencode_model_complex_pro,
        "display_name": "MiMo V2.5 Pro",
        "detail": "Deep reasoning · text only",
        "supports_tools": True,
        # Probed, not assumed. See the table above.
        "supports_vision": False,
        "description": (
            "The heaviest reasoning model in the pool, for hard text-only "
            "work. Same family as MiMo V2.5 but does not accept images."
        ),
        "max_tokens": lambda s: s.opencode_max_tokens,
        "required_key": "opencode_api_key",
        "fallback_chain": ["mimo_v2_5", "nemotron-3", "gpt-oss-120b"],
        "pricing": {"input": 0.80 / 1_000_000, "output": 3.20 / 1_000_000},
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
    picked = routes.get(section, routes["chat"])
    # The section table is configuration and can name a model this deployment
    # has no key for (`AUTO_ROUTE_CHAT=qwen3_7_plus` with no
    # `OPENCODE_API_KEY`). Auto exists to always produce a runnable model, so
    # resolve to one here.
    return first_available(picked) or picked


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


def resolve_stored_model(
    model_id: str | None, default: str | None = None
) -> tuple[str, str | None]:
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

    ``default`` is what an absent or stale id resolves *to*. Callers that know
    which surface they are on pass that section's default — otherwise a Code
    session with no stored model would open on the Chat model, which is the
    one case where "the roster default" is demonstrably the wrong answer.
    Already resolved to an available model by
    :func:`section_default_model`; a caller that passes nothing gets the
    roster-wide default as before.
    """
    default = default or effective_default_model()
    if not model_id:
        return default, None
    if model_id == AUTO_MODEL_ID:
        return model_id, None
    # Registered *and* keyed is the bar. A model that is still in the registry
    # but whose provider key is unset fails at dispatch exactly like a retired
    # one, so it is reassigned on the same path rather than being allowed
    # through to die on the first turn.
    if model_id in MODEL_REGISTRY and is_available(model_id):
        return model_id, None
    if default == model_id or not is_available(default):
        # Nothing to switch to — say so instead of silently reassigning the
        # session to a model that is just as unusable.
        return model_id, None
    if model_id in MODEL_REGISTRY:
        log.info(
            "Session model `%s` has no API key configured; using `%s`.",
            model_id,
            default,
        )
        return default, (
            f"{display_name(model_id)} is not configured on this server. "
            f"This session is using {display_name(default)} instead."
        )
    log.info(
        "Session model `%s` is no longer registered; reassigning to `%s`.",
        model_id,
        default,
    )
    retired = RETIRED_DISPLAY_NAMES.get(model_id)
    if retired:
        return default, (
            f"{retired} is no longer available. This session has been switched "
            f"to {display_name(default)}."
        )
    # No name we are willing to print. Say what happened without echoing the
    # stored id back at the user — it is arbitrary text from a database row,
    # and for a deliberately removed vendor it would reintroduce the name the
    # removal existed to get rid of. The id is in the log line above for
    # anyone debugging.
    return default, (
        "The model this session was using is no longer available. It has been "
        f"switched to {display_name(default)}."
    )


#: Order in which a stand-in is chosen when the asked-for model has no key.
#:
#: Explicit rather than "first available in registry order", because that order
#: is a documentation choice and quietly decided which model every misconfigured
#: deployment ran on. What it is ranked by is headroom for a *full* agent turn —
#: system prompt plus tool schemas, not a bare chat completion:
#:
#: * `gpt-oss-120b` (Groq) is last despite being fast. Groq's free tier caps at
#:   8000 tokens/min, and one agent turn with the tool schemas attached
#:   measured 10079 — an immediate HTTP 413. It stays useful for the short
#:   calls it is actually chosen for (see `TITLE_MODELS`), and a paid Groq tier
#:   lifts the cap, so it is demoted rather than removed.
#: * The OpenCode pool leads. Those are reasoning models that spend output
#:   budget before emitting text, which is the cost of putting them first, and
#:   they are what every section now defaults to — so a stand-in from this pool
#:   is the smallest change from what the user was going to get anyway.
#: * `nemotron-3` was previously first and has been demoted. It is the most
#:   expensive entry per token, and OpenRouter prices a request by its
#:   `max_tokens` *ceiling* rather than by what it produces: at
#:   `OPENROUTER_MAX_TOKENS=8000` it is the first model in the roster an
#:   account can no longer afford, answering 402 while everything cheaper on
#:   the same key still works. Leading the stand-in list with the model most
#:   likely to be unaffordable is exactly backwards.
#:
#: Distinct from ``fallback_chain`` on each registry entry, which is a
#: *runtime* concern: this list answers "the configured model has no key, what
#: do we open on", the chains answer "the call just failed, what do we retry
#: against". They are allowed to disagree.
FALLBACK_ORDER: tuple[str, ...] = (
    "mimo_v2_5",
    "deepseek_v4_flash",
    "qwen3_7_plus",
    "llama-4-scout",
    "nemotron-3",
    "gpt-oss-120b",
)


#: Display names for ids that have been *removed* from the registry, so the
#: notice `resolve_stored_model()` shows a returning user names the model they
#: chose rather than printing a raw slug at them. Purely cosmetic: an id
#: missing from here still resolves, the notice just goes generic.
#:
#: Only names this project is willing to say out loud belong here. An id whose
#: vendor has been deliberately removed (see `BANNED_MODEL_SUBSTRINGS`) is
#: left out on purpose — naming it in a toast would put the vendor back in
#: front of the user, which is the thing the removal was for. Those fall to
#: the generic wording instead.
RETIRED_DISPLAY_NAMES: dict[str, str] = {
    "llama-70b": "Llama 3.3 70B",
    "claude-sonnet": "Claude Sonnet",
    # Not removed on policy or on price — the upstream model simply stopped
    # answering (HTTP 500 to every request). Sessions sitting on it are moved
    # to the default and told which model they used to be on.
    "minimax_m2_7": "Minimax M2.7",
}


#: Vendors this deployment refuses to route to, matched case-insensitively as
#: substrings of a model's *resolved wire id*.
#:
#: The registry alone cannot enforce this. Every entry's wire id comes from
#: settings — `OPENCODE_MODEL_FAST`, `GROQ_MODEL_A`, and so on — so an .env
#: line is enough to repoint an existing, innocuously-named entry at a banned
#: model, and OpenCode Go's catalogue in particular does serve one. That is the
#: hole this closes: `is_available()` consults it, so a banned slug is hidden
#: from the selector, refused at dispatch, and skipped as a fallback target,
#: whatever the entry is called.
#:
#: Deliberately a denylist of *substrings* rather than exact ids, because the
#: point is the vendor, not one version of one model.
BANNED_MODEL_SUBSTRINGS: tuple[str, ...] = ("grok",)


def is_banned(wire_id: str) -> bool:
    """True when `wire_id` names a vendor this deployment will not route to."""
    lowered = (wire_id or "").lower()
    return any(bad in lowered for bad in BANNED_MODEL_SUBSTRINGS)


def banned_entries() -> list[tuple[str, str]]:
    """``(model_id, wire_id)`` for every registry entry pointing at a banned
    model. Empty in a correctly configured deployment; non-empty means someone
    repointed a slug in .env. Reported loudly by :func:`validate_keys`.
    """
    out: list[tuple[str, str]] = []
    for mid, meta in MODEL_REGISTRY.items():
        try:
            wire = _resolve(meta, "model_name")
        except Exception:  # noqa: BLE001 - a broken entry is not this check's job
            continue
        if is_banned(wire):
            out.append((mid, wire))
    return out


def first_available(*preferred: str) -> str | None:
    """The first usable model id among `preferred`, else the best stand-in.

    "Usable" means registered *and* holding a configured key. Returns None only
    when no model in the registry has its key set, which is the genuinely
    unrunnable case the caller has to report rather than paper over.
    """
    for mid in preferred:
        if mid and mid != AUTO_MODEL_ID and is_available(mid):
            return mid
    for mid in FALLBACK_ORDER:
        if is_available(mid):
            return mid
    return next((mid for mid in MODEL_REGISTRY if is_available(mid)), None)


def effective_default_model() -> str:
    """`DEFAULT_MODEL_ID`, or a working stand-in when its key is unset.

    The configured default is a deployment's stated preference, not a promise
    that it is usable: `DEFAULT_MODEL_ID=qwen3_7_plus` with no
    `OPENCODE_API_KEY` opens every new session on a model that cannot answer.
    Falling back here keeps that a startup-log warning instead of a dead first
    message.
    """
    configured = get_settings().default_model_id
    return first_available(configured) or configured


def section_default_model(section: str | None) -> str:
    """The model a new session in `section` opens on, resolved to a live one.

    Two-step on purpose. `SECTION_DEFAULTS` states the deployment's preference
    for the surface; `first_available` turns that into a model this deployment
    can actually dispatch to, falling through the section's pick, then the
    roster-wide `DEFAULT_MODEL_ID`, then `FALLBACK_ORDER`. A section whose
    preferred model has no key therefore opens on something that answers,
    rather than on a session whose first message dies at dispatch — the same
    guarantee :func:`effective_default_model` already gave the global default,
    extended to the per-section ones.

    An unknown or absent `section` is not an error: it means "no surface said",
    which is precisely the roster default.
    """
    configured = get_settings().section_defaults.get(section or "", "")
    return first_available(configured, get_settings().default_model_id) or (
        configured or get_settings().default_model_id
    )


def section_default_models() -> dict[str, str]:
    """Every section's opening model, resolved. What `/api/config` reports."""
    return {
        section: section_default_model(section)
        for section in get_settings().section_defaults
    }


def is_available(model_id: str) -> bool:
    """Registered, keyed, and not pointing at a banned vendor.

    This is the single gate every other path already goes through — the model
    dropdown (`available_models`), dispatch (`_adapter_for`), stand-in
    selection (`first_available`) and fallback targets
    (`fallback_candidates`) all consult it. Putting the denylist here is what
    makes "cannot be reached under any provider" true rather than aspirational:
    there is no route to a model that fails this check.
    """
    try:
        meta = model_meta(model_id)
    except ModelUnavailableError:
        return False
    settings = get_settings()
    if not getattr(settings, meta["required_key"], ""):
        return False
    try:
        return not is_banned(_resolve(meta, "model_name"))
    except Exception:  # noqa: BLE001 - an unresolvable slug is not "available"
        return False


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

    We deliberately do *not* hard-crash. ``OPENCODE_API_KEY`` powers both the
    default model and the whole Auto pool, so it is the one that matters most
    in practice, but every provider here is individually optional — a missing
    key hides that provider's models from the dropdown instead of stopping the
    server.

    Auto mode gets its own check. Because nobody selects an OpenCode model by
    hand, a missing ``OPENCODE_API_KEY`` would otherwise only show up as a
    surprise mid-run when the classifier routed to one; this states it at
    startup instead, and Auto degrades to section routing rather than failing.
    """
    settings = get_settings()

    # A repointed slug is a configuration mistake, not a model outage, so it is
    # reported separately and before the per-model roll call — otherwise it
    # would read as "model unavailable" and get fixed by adding a key, which is
    # exactly the wrong response.
    for mid, wire in banned_entries():
        log.error(
            "LLM router: model `%s` is configured as `%s`, which this "
            "deployment refuses to route to (matched %s). The entry is being "
            "hidden from the model selector and cannot be dispatched to. Point "
            "it at a different model in backend/.env.",
            mid,
            wire,
            ", ".join(repr(b) for b in BANNED_MODEL_SUBSTRINGS),
        )

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
        # Say which cause applies rather than assuming the common one. A
        # hinted model can also be unavailable because its slug was repointed
        # at a refused vendor, and "add OPENCODE_API_KEY" is useless advice
        # when the key is already there.
        cause = (
            "`OPENCODE_API_KEY` is unset in backend/.env"
            if not settings.opencode_api_key
            else "check the errors above for why"
        )
        log.error(
            "LLM router: Auto mode CANNOT route by task — %d of %d hinted "
            "models are unavailable (%s); %s. Auto will fall back to the "
            "section table (%s).",
            len(missing),
            len(HINT_MODEL),
            ", ".join(missing),
            cause,
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


# --- OpenAI-compatible (OpenRouter / Groq / OpenCode) ----------------------


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


#: Some reasoning models do not use `reasoning_content` at all: they write
#: their thinking into `content`, wrapped in `<think>` ... `</think>`, and
#: leave the caller to separate the two. `minimax-m3` is the one in this
#: roster that does it (probed: `reasoning_content` is always empty and the
#: text begins with a literal `<think>` tag). Untreated, that XML is what
#: the user reads in the chat bubble, and it is also what gets stored as
#: the assistant turn and replayed into every later request.
#:
#: :class:`_ThinkTagSplitter` pulls it back apart, so an inline-think model
#: drives the same thinking UI as a `reasoning_content` one and its visible
#: answer is just the answer.
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


class _ThinkTagSplitter:
    """Split a *streamed* `<think>…</think>` prelude from the answer after it.

    Fed arbitrary chunks; yields ``(channel, text)`` pairs where channel is
    ``"thinking"`` or ``"text"``. Two properties make it safe on a live stream:

    * **Tags may be split across deltas.** `<thi` + `nk>` arrives as two
      chunks, and a naive `chunk.startswith("<think>")` misses it. Anything
      that could still become a tag is held back rather than emitted, and
      released as soon as it is decided.
    * **Only a *prelude* counts.** The opener is recognised only before any
      answer text has been emitted. A coding agent legitimately writes
      `<think>` inside a code block halfway through a reply, and rewriting
      that as thinking would silently eat part of the answer. Reasoning always
      comes first, so "at the very start" is both sufficient and safe.

    A model that never emits the tag pays one `startswith` per chunk and its
    output passes through byte for byte.
    """

    __slots__ = ("_state", "_buf", "_trim_next")

    def __init__(self) -> None:
        self._state = "start"  # start -> thinking -> text
        self._buf = ""
        #: The close tag can be the last thing in its chunk, with the blank
        #: line after it arriving in the next one — so the trim has to survive
        #: across feeds rather than being applied once at the transition.
        self._trim_next = False

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self._buf += chunk
        return self._drain(final=False)

    def flush(self) -> list[tuple[str, str]]:
        """Release whatever is still held back. Call once the stream ends."""
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        while self._buf:
            if self._state == "start":
                lead = self._buf.lstrip()
                if lead.startswith(_THINK_OPEN):
                    # Drop the leading whitespace with the tag: it is part of
                    # the prelude's formatting, not of the answer.
                    cut = self._buf.index(_THINK_OPEN) + len(_THINK_OPEN)
                    self._buf = self._buf[cut:]
                    self._state = "thinking"
                    continue
                if not final and (not lead or _THINK_OPEN.startswith(lead)):
                    # Still could become the opener once more arrives.
                    return out
                out.append(("text", self._emit_text()))
                self._state = "text"
                return out
            if self._state == "thinking":
                idx = self._buf.find(_THINK_CLOSE)
                if idx >= 0:
                    if idx:
                        out.append(("thinking", self._buf[:idx]))
                    self._buf = self._buf[idx + len(_THINK_CLOSE):]
                    self._state = "text"
                    # The newline the model puts after `</think>` is
                    # punctuation between two sections, not the answer's first
                    # character.
                    self._trim_next = True
                    continue
                if final:
                    # Unterminated. Everything so far was thinking; better
                    # that than printing a half-open tag at the user.
                    out.append(("thinking", self._buf))
                    self._buf = ""
                    return out
                # Hold back enough to recognise a close tag split across
                # chunks, release the rest.
                keep = len(_THINK_CLOSE) - 1
                if len(self._buf) > keep:
                    out.append(("thinking", self._buf[:-keep]))
                    self._buf = self._buf[-keep:]
                return out
            piece = self._emit_text()
            if piece:
                out.append(("text", piece))
            return out
        return out

    def _emit_text(self) -> str:
        """The buffer as answer text, dropping the blank line a close tag
        leaves behind. The flag is cleared only once something survives the
        trim, so a chunk that is *entirely* that blank line does not consume
        it."""
        piece, self._buf = self._buf, ""
        if self._trim_next:
            piece = piece.lstrip("\n")
            if piece:
                self._trim_next = False
        return piece


def split_think_tags(text: str) -> tuple[str, str]:
    """Non-streaming form. Returns ``(thinking, answer)``."""
    splitter = _ThinkTagSplitter()
    parts = splitter.feed(text) + splitter.flush()
    thinking = "".join(t for kind, t in parts if kind == "thinking")
    answer = "".join(t for kind, t in parts if kind == "text")
    return thinking, answer


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

#: Key holding a tool call's *unparsed* argument string when it was not valid
#: JSON. A model that runs out of output budget mid-argument stops in the
#: middle of the string, so this is overwhelmingly a truncation marker rather
#: than a malformed-model marker. It replaces the arguments entirely: a call
#: carrying it has no usable arguments at all and must not be dispatched — see
#: `agent.graph._broken_arguments`, which turns it into a `tool_result` error
#: the model can act on. Writing the fragment into the block rather than
#: discarding it is deliberate: the length is what tells the model it was cut
#: off rather than rejected.
RAW_ARGUMENTS_KEY = "_raw_arguments"


def _openai_blocks(
    content: str | None,
    tool_calls: list[dict],
    reasoning: str = "",
    inline_thinking: str = "",
) -> list[dict]:
    """Normalised blocks for one OpenAI-shaped reply.

    ``reasoning`` is the provider's `reasoning_content`: shown *and* stored,
    because the provider wants it handed back next turn. ``inline_thinking`` is
    a `<think>` prelude that was carved out of `content` instead — shown live,
    never stored, and used here only so that a turn which produced nothing but
    thinking shows the thinking rather than a placeholder.
    """
    blocks: list[dict] = []
    if content:
        blocks.append({"type": "text", "text": content})
    for tc in tool_calls or []:
        fn = tc.get("function", {})
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:  # noqa: BLE001
            args = {RAW_ARGUMENTS_KEY: raw_args}
        blocks.append(
            {
                "type": "tool_use",
                "id": tc.get("id"),
                "name": fn.get("name"),
                "input": args,
            }
        )
    if not blocks:
        # Reasoning models routinely finish a turn having emitted only their
        # thinking. That is still the model's output, so prefer it over a
        # placeholder — and never return an empty block list. Both kinds of
        # thinking count: a model that writes an inline `<think>` prelude and
        # then runs out of budget has produced exactly as much as one that
        # filled `reasoning_content` and stopped.
        blocks.append(
            {
                "type": "text",
                "text": (
                    reasoning.strip()
                    or inline_thinking.strip()
                    or EMPTY_TURN_TEXT
                ),
            }
        )
    # Kept alongside the visible blocks rather than instead of them: the
    # provider wants it back next turn (see REASONING_BLOCK), and the turn
    # cannot be reconstructed later from anything else.
    if reasoning.strip():
        blocks.insert(0, {"type": REASONING_BLOCK, "text": reasoning.strip()})
    return blocks


class _OpenAICompatAdapter(BaseAdapter):
    """Shared base for OpenAI-compatible providers (all of them, now)."""

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

    def _call_error(self, exc: BaseException) -> ModelCallError:
        """Wrap a provider exception, preserving why it failed.

        Classification happens here rather than at the agent loop because this
        is the last place the original exception — with its status code — is
        still intact. See :func:`classify_failure`.
        """
        retryable, status, kind = classify_failure(exc)
        return ModelCallError(
            f"{self.provider} call failed: {exc}",
            provider=self.provider,
            status_code=status,
            retryable=retryable,
            kind=kind,
        )

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
        # Inline `<think>` prelude, for providers whose models write their
        # reasoning into `content` instead of `reasoning_content`. Kept apart
        # from `reasoning_buf` on purpose: that buffer is round-tripped back
        # to the provider as `reasoning_content` next turn, which a model that
        # never sent the field would reject. Inline thinking is regenerated by
        # the model each turn, so it drives the UI and is not stored.
        splitter = _ThinkTagSplitter() if self.emit_reasoning else None
        inline_thinking = ""
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
                    for channel, piece in (
                        splitter.feed(dcontent)
                        if splitter is not None
                        else [("text", dcontent)]
                    ):
                        if not piece:
                            continue
                        if channel == "thinking":
                            if text_started:
                                # Cannot happen: the splitter only recognises
                                # a prelude. Guarded anyway so a future change
                                # cannot silently interleave the two.
                                continue
                            if not thinking_started:
                                thinking_started = True
                                yield StreamEvent("thinking_start")
                            inline_thinking += piece
                            yield StreamEvent("thinking_delta", piece)
                            continue
                        # Reasoning always precedes the answer; close it first
                        # so the two never interleave in the transcript.
                        if thinking_started:
                            thinking_started = False
                            yield StreamEvent("thinking_end")
                        if not text_started:
                            text_started = True
                            yield StreamEvent("text_start")
                        # Accumulated as well as streamed: the deltas drive the
                        # live UI, but this buffer is what ends up in `messages`
                        # and therefore in every later turn's context.
                        text_buf += piece
                        yield StreamEvent("text_delta", piece)
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
            raise self._call_error(exc) from exc

        # Anything the splitter was still holding back — an unterminated
        # `<think>`, or a few characters it could not yet classify — belongs to
        # the turn and must not be dropped.
        for channel, piece in (splitter.flush() if splitter is not None else []):
            if not piece:
                continue
            if channel == "thinking":
                inline_thinking += piece
                if not thinking_started:
                    thinking_started = True
                    yield StreamEvent("thinking_start")
                yield StreamEvent("thinking_delta", piece)
            else:
                if thinking_started:
                    thinking_started = False
                    yield StreamEvent("thinking_end")
                if not text_started:
                    text_started = True
                    yield StreamEvent("text_start")
                text_buf += piece
                yield StreamEvent("text_delta", piece)

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
                content=_openai_blocks(
                    content_text, tool_calls, reasoning_buf, inline_thinking
                ),
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
            raise self._call_error(exc) from exc
        msg = completion.choices[0].message
        # Same inline-`<think>` treatment as the streaming path. It matters
        # more here than it looks: this is the call behind session titles and
        # the Learn tutor, and an untreated prelude would put raw XML in a
        # session name or a grounded answer.
        content = msg.content
        inline_thinking = ""
        if self.emit_reasoning and content:
            inline_thinking, content = split_think_tags(content)
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
                content,
                tool_calls,
                getattr(msg, "reasoning_content", "") or "",
                inline_thinking,
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
    * **Vision is per-model, and one model lies about it.** The MiniMax entry
      accepts an `image_url` part and then answers that it cannot see any
      image; `deepseek-v4-flash` at least fails loudly with a 400. Both are
      registered `supports_vision: False` so their images become a text
      placeholder instead. Only `qwen3.7-plus` and `mimo-v2.5` really see.
    * **A live /models listing is not a live model.** `minimax-m2.7` and
      `mimo-v2-pro` are both on `GET /models` and both refuse every request
      (500 and 400 respectively), so the catalogue is a starting point for
      picking a slug and never the evidence that one works. Probe before
      putting an id in `.env`; `scripts/test_live_models.py` is the tool.
    """

    provider = "opencode"
    key_attr = "opencode_api_key"
    emit_reasoning = True
    # base_url set per-instance from settings


@lru_cache
def _adapters() -> dict[str, BaseAdapter]:
    settings = get_settings()
    groq = GroqAdapter()
    groq.base_url = settings.groq_base_url
    opencode = OpenCodeAdapter()
    opencode.base_url = settings.opencode_base_url
    return {
        "openrouter": OpenRouterAdapter(),
        "groq": groq,
        "opencode": opencode,
    }


def _adapter_for(model_id: str) -> tuple[BaseAdapter, dict[str, Any]]:
    meta = model_meta(model_id)
    if not is_available(model_id):
        # Two different causes, and telling them apart matters: "add your key"
        # is actively wrong advice for a slug that was refused on policy, and
        # would have someone checking a key that is already set.
        if is_banned(_resolve(meta, "model_name")):
            raise ModelUnavailableError(
                f"Model `{model_id}` is configured to point at a model this "
                "deployment does not route to. Change its slug in "
                "backend/.env and restart."
            )
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

# ---------------------------------------------------------------------------
# Automatic fallback
# ---------------------------------------------------------------------------
#
# `call_model` above is the raw dispatch and stays that way — one model, one
# call, errors surfaced. Everything below wraps it so that a *provider-side*
# failure moves the request to another model instead of ending the turn.
#
# Three properties this has to hold, in order of how badly getting them wrong
# would hurt:
#
# 1. **Never fall back mid-answer.** Once the model has emitted a token the
#    user is reading it. Restarting on another model would either duplicate the
#    text or silently truncate it. So the switch is only allowed *before* the
#    first content event; after that the error is surfaced exactly as before.
#    In practice this costs nothing, because the failures worth retrying — 429,
#    503, connection refused — all happen at request time, before a byte of
#    content exists.
# 2. **Never charge for a call that produced nothing.** Credits are debited by
#    the caller from `response.usage` after a *successful* turn, so an attempt
#    that raises is already free. Property 1 is what keeps it that way: because
#    a fallback can only happen pre-content, a failed attempt never has usage
#    to bill. There is no separate refund path and there should not be one.
# 3. **Say so.** A silent model switch is worse than the error it replaced —
#    the user gets an answer from a model they did not pick and never finds
#    out. The caller is handed a `FallbackNotice` and reuses the existing
#    `model_changed` toast.


@dataclass
class FallbackNotice:
    """One model handing off to another, described well enough to show a user."""

    failed_model_id: str
    next_model_id: str
    #: "rate_limit" | "provider_error" | "unavailable" — from `classify_failure`.
    kind: str
    status_code: int | None
    #: The provider's own message, for the log. Not shown to the user.
    detail: str
    #: 1 for the first fallback of this call, 2 for the next, and so on.
    attempt: int

    @property
    def failed_name(self) -> str:
        return display_name(self.failed_model_id)

    @property
    def next_name(self) -> str:
        return display_name(self.next_model_id)

    @property
    def reason(self) -> str:
        """Half a sentence, in the user's terms, for the toast."""
        return {
            "rate_limit": "hit a rate limit",
            "out_of_credit": "ran out of provider credit",
            "unavailable": "is unavailable",
            "provider_error": "had a provider error",
        }.get(self.kind, "failed")

    def message(self) -> str:
        """The whole sentence, e.g. "X hit a rate limit - switched to Y"."""
        return f"{self.failed_name} {self.reason} — switched to {self.next_name}."


#: Most alternate models one call may try after its first choice fails. Three
#: attempts total, so a systemic outage costs a bounded amount of latency
#: instead of walking the whole registry before admitting defeat.
MAX_FALLBACK_ATTEMPTS = 2


def fallback_candidates(model_id: str, limit: int = MAX_FALLBACK_ATTEMPTS) -> list[str]:
    """Models to try, in order, if `model_id` fails. Never includes itself.

    Filtered to what this deployment can actually run: an unkeyed model in a
    chain is skipped rather than counted against the attempt budget, so a
    deployment with only one provider configured does not "use up" its retries
    on models it was never going to reach.

    Falls back to :data:`FALLBACK_ORDER` when the entry declares no chain (or
    declares one that is entirely unavailable), so a new registry entry that
    forgets `fallback_chain` still gets covered rather than silently opting out.
    """
    try:
        chain = list(model_meta(model_id).get("fallback_chain") or [])
    except ModelUnavailableError:
        chain = []
    chain += [m for m in FALLBACK_ORDER if m not in chain]

    out: list[str] = []
    for mid in chain:
        if mid == model_id or mid in out:
            continue
        if mid not in MODEL_REGISTRY or not is_available(mid):
            continue
        out.append(mid)
        if len(out) >= limit:
            break
    return out


def _should_fall_back(exc: BaseException) -> tuple[bool, int | None, str]:
    """Retryability of an exception already wrapped by an adapter, or a raw one."""
    if isinstance(exc, ModelCallError):
        return exc.retryable, exc.status_code, exc.kind
    if isinstance(exc, ModelUnavailableError):
        # The key vanished or the id is unknown. Another model genuinely fixes
        # this, and it is the one case where the "error" is entirely ours to
        # route around rather than report.
        return True, None, "unavailable"
    return classify_failure(exc)


#: OpenRouter's 402 names the ceiling the account can currently pay for:
#: "You requested up to 8000 tokens, but can only afford 5870".
_AFFORDABLE = re.compile(r"can only afford\s+(\d+)", re.I)


def affordable_max_tokens(exc: BaseException) -> int | None:
    """The ceiling a 402 says this account can pay for, if it named one.

    OpenRouter prices a request by its `max_tokens` *ceiling* rather than by
    what it produces, and refuses outright once the balance cannot cover that
    ceiling. The refusal is not about the conversation and not about the
    model's own limits — it is arithmetic against a balance that moves — and
    it comes with the answer already worked out, so there is no need to guess.

    Worth using rather than falling straight to another model, because the
    ceiling is shared across a provider while affordability is per-token-price:
    `openrouter_max_tokens` at 8000 is comfortable for `llama-4-scout` and
    unaffordable for `nemotron-3-ultra-550b`, which is 550B parameters and
    priced to match. The result was a model listed as available that answered
    402 to *everything* — "reply with PONG" included — and was silently
    swapped out on every use. Retrying it at the ceiling it can afford is the
    difference between a model that works and a model that is only ever a
    toast about a different model.
    """
    match = _AFFORDABLE.search(str(exc))
    if not match:
        return None
    try:
        value = int(match.group(1))
    except ValueError:  # pragma: no cover - the group is \d+
        return None
    # A floor, because a ceiling of ~nothing is not worth an attempt: it would
    # buy a truncated answer and a second failure.
    return value if value >= 256 else None


def _log_fallback(notice: FallbackNotice, *, section: str = "") -> None:
    """One line per fallback, structured enough to aggregate later.

    Deliberately at WARNING: a fallback means a model this deployment offers is
    failing, which is worth noticing even though the user's request survived.
    Grepping `llm_fallback` gives the pattern — if one model dominates that
    list, it should probably leave the registry.
    """
    log.warning(
        "llm_fallback model=%s -> %s kind=%s status=%s attempt=%d section=%s detail=%s",
        notice.failed_model_id,
        notice.next_model_id,
        notice.kind,
        notice.status_code,
        notice.attempt,
        section or "-",
        notice.detail[:300],
    )


async def _announce(on_fallback: Any, notice: FallbackNotice) -> None:
    """Invoke the caller's fallback hook, sync or async."""
    if on_fallback is None:
        return
    result = on_fallback(notice)
    if hasattr(result, "__await__"):
        await result


def _next_candidate(current: str, tried: list[str]) -> str | None:
    for mid in fallback_candidates(current, limit=MAX_FALLBACK_ATTEMPTS + 1):
        if mid not in tried:
            return mid
    return None


async def stream_with_fallback(
    model_id: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    system: str | None = None,
    max_tokens: int | None = None,
    *,
    on_fallback: Any = None,
    section: str = "",
) -> AsyncIterator[StreamEvent]:
    """:func:`call_model` with ``stream=True``, plus automatic model fallback.

    Yields the same :class:`StreamEvent` sequence a direct call would. When an
    attempt fails for a provider-side reason *before emitting any content*, the
    next model in the chain is tried and ``on_fallback(notice)`` is invoked with
    a :class:`FallbackNotice` first — that is the caller's cue to emit the
    model-changed toast and to re-point its own cost accounting at the model
    that is actually going to answer.

    ``on_fallback`` may be sync or async. It is called before the retry, so a
    caller that raises from it stops the fallback, which is the correct
    behaviour for a cancelled run.

    Raises the last error when every candidate is exhausted, so the user sees a
    real failure rather than a generic one.
    """
    tried: list[str] = []
    current = model_id
    attempt = 0
    # Models already retried at the ceiling a 402 said they could afford, so
    # one refusal buys one reduced attempt and never a loop.
    trimmed: set[str] = set()
    ceiling = max_tokens

    while True:
        tried.append(current)
        produced_content = False
        try:
            async for se in call_model(
                current,
                messages=messages,
                tools=tools,
                system=system,
                stream=True,
                max_tokens=ceiling,
            ):
                # Anything the user can see, or the final message, commits us
                # to this model. `thinking_*` counts: it is rendered live.
                produced_content = True
                yield se
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised below when fatal
            retryable, status, kind = _should_fall_back(exc)
            if produced_content:
                # Mid-answer. Restarting would duplicate or truncate what the
                # user is already reading — see property 1 above.
                log.warning(
                    "llm_fallback_declined model=%s reason=mid_stream kind=%s: %s",
                    current,
                    kind,
                    exc,
                )
                raise
            if not retryable or attempt >= MAX_FALLBACK_ATTEMPTS:
                raise
            # A 402 that named an affordable ceiling is answerable by this
            # model, just not at this price. Retry it here rather than moving
            # on: switching models is the remedy for a model that cannot
            # answer, and this one can. Costs no fallback attempt, because it
            # is not one — the same model is being asked the same question.
            afford = affordable_max_tokens(exc)
            if status == 402 and afford and current not in trimmed:
                trimmed.add(current)
                log.warning(
                    "llm_budget_trim model=%s max_tokens=%s -> %s section=%s",
                    current,
                    ceiling,
                    afford,
                    section,
                )
                ceiling = afford
                tried.pop()
                continue
            nxt = _next_candidate(current, tried)
            if nxt is None:
                raise

            attempt += 1
            notice = FallbackNotice(
                failed_model_id=current,
                next_model_id=nxt,
                kind=kind,
                status_code=status,
                detail=str(exc),
                attempt=attempt,
            )
            _log_fallback(notice, section=section)
            await _announce(on_fallback, notice)
            current = nxt
            # Back to the configured ceiling. A trim is a fact about one
            # model's price against the balance, not about the request, and
            # carrying it onto a cheaper model would hand the model that
            # *can* afford the work a smaller budget than it was given —
            # which is how a `file_write` gets truncated mid-argument.
            ceiling = max_tokens


async def complete_with_fallback(
    model_id: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    system: str | None = None,
    max_tokens: int | None = None,
    *,
    on_fallback: Any = None,
    section: str = "",
) -> tuple[NormalizedMessage, str]:
    """:func:`call_model` with ``stream=False``, plus automatic model fallback.

    Returns ``(message, model_id_that_answered)``. The second element matters:
    the caller prices the call with it, and pricing a fallback answer against
    the model that failed would mis-bill the user in whichever direction the
    two rates happen to differ.

    Non-streaming has no mid-answer problem — nothing is shown until the whole
    message arrives — so every retryable failure here is eligible.
    """
    tried: list[str] = []
    current = model_id
    attempt = 0
    trimmed: set[str] = set()
    ceiling = max_tokens

    while True:
        tried.append(current)
        try:
            message = await call_model(
                current,
                messages=messages,
                tools=tools,
                system=system,
                stream=False,
                max_tokens=ceiling,
            )
            return message, current
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised below when fatal
            retryable, status, kind = _should_fall_back(exc)
            if not retryable or attempt >= MAX_FALLBACK_ATTEMPTS:
                raise
            # Same reduced-ceiling retry as the streaming path; see
            # `affordable_max_tokens`.
            afford = affordable_max_tokens(exc)
            if status == 402 and afford and current not in trimmed:
                trimmed.add(current)
                log.warning(
                    "llm_budget_trim model=%s max_tokens=%s -> %s section=%s",
                    current,
                    ceiling,
                    afford,
                    section,
                )
                ceiling = afford
                tried.pop()
                continue
            nxt = _next_candidate(current, tried)
            if nxt is None:
                raise
            attempt += 1
            notice = FallbackNotice(
                failed_model_id=current,
                next_model_id=nxt,
                kind=kind,
                status_code=status,
                detail=str(exc),
                attempt=attempt,
            )
            _log_fallback(notice, section=section)
            await _announce(on_fallback, notice)
            current = nxt
            # Back to the configured ceiling. A trim is a fact about one
            # model's price against the balance, not about the request, and
            # carrying it onto a cheaper model would hand the model that
            # *can* afford the work a smaller budget than it was given —
            # which is how a `file_write` gets truncated mid-argument.
            ceiling = max_tokens
