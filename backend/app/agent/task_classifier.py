"""What kind of turn is this?

Auto mode's entire decision lives here. :func:`classify_task` reads the live
LangGraph state and returns a ``routing_hint``; :func:`app.llm_router.model_for_hint`
turns that into a model. Nothing in this module knows which model wins a hint,
and nothing in the router knows how a hint is decided — so these rules and
thresholds can be retuned without touching dispatch code.

Priority order, highest first. The first rule that fires wins:

1. ``visual_structural``   — the turn carries an image or document.
2. ``code_editing``        — the agent is editing files, or the thread has
                             become an iterative code-modification loop.
3. ``complex_longhorizon`` — a large context, a long-running task, or the
                             agent's own plan says there are many steps left.
4. ``fast_simple``         — everything else. The cheap default.

Vision outranks editing because an unreadable attachment is unrecoverable: the
model cannot ask for the pixels back. Editing outranks complexity because a
model that holds a file's exact contents across a long thread beats a stronger
model that has to keep re-reading it.

Every threshold comes from ``app.config``; see the ``AUTO_*`` settings.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import get_settings
from app.tools.schemas import EDIT_FILE, WRITE_FILE

log = logging.getLogger(__name__)

#: Hints this module can return. Mirrors ``llm_router.RoutingHint``.
HINTS = ("fast_simple", "code_editing", "visual_structural", "complex_longhorizon")

#: Blocks that mean "there is something to look at in this turn".
VISUAL_BLOCKS = {"image", "document"}

#: Tool calls that are a file modification. Deliberately narrow: reading a file
#: or running a command is not editing, and treating it as such would pin most
#: sandbox work to the editing model.
EDIT_TOOLS = {WRITE_FILE, EDIT_FILE}

#: Rough bytes-per-token, used for the context-size estimate. Deliberately
#: crude — this decides a route, not a billing figure.
CHARS_PER_TOKEN = 4

#: A numbered or bulleted line in a plan: "1. ", "2) ", "- ", "* ".
_PLAN_STEP = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\S", re.MULTILINE)


# ---------------------------------------------------------------------------
# State readers
# ---------------------------------------------------------------------------


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _recent(state: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    messages = state.get("messages") or []
    return messages[-limit:] if limit > 0 else list(messages)


def has_visual_input(state: dict[str, Any], lookback: int) -> bool:
    """Is there an image or document in the recent conversation?

    Looks back over a window rather than only the newest message: a screenshot
    stays relevant while the agent works through the turns that follow it, and
    routing away from the only model that can see it mid-task would lose it.
    """
    for message in _recent(state, lookback):
        for block in _blocks(message):
            if block.get("type") in VISUAL_BLOCKS:
                return True
    return False


def is_file_editing(state: dict[str, Any], lookback: int) -> bool:
    """Is the agent modifying files right now?

    True when an edit is queued for this turn (``pending``), or when a recent
    assistant turn called an edit tool — the latter is what makes an iterative
    "read, patch, run tests, patch again" thread stay on one model.
    """
    for block in state.get("pending") or []:
        if block.get("type") == "tool_use" and block.get("name") in EDIT_TOOLS:
            return True

    for message in _recent(state, lookback):
        if message.get("role") != "assistant":
            continue
        for block in _blocks(message):
            if block.get("type") == "tool_use" and block.get("name") in EDIT_TOOLS:
                return True
    return False


def _block_chars(block: dict[str, Any]) -> int:
    """Character weight of one content block.

    The common heavy blocks are measured directly rather than serialised: a
    `tool_result` can hold 20 KB of command output, and this runs over the
    whole transcript on *every* agent iteration, so re-encoding it each time
    was pure overhead for a number that only has to be roughly right.
    """
    btype = block.get("type")
    if btype in VISUAL_BLOCKS:
        return 0  # skip the base64 blob
    if btype == "text":
        return len(block.get("text") or "")
    if btype == "thinking":
        return len(block.get("thinking") or "")
    if btype == "tool_result":
        content = block.get("content")
        if isinstance(content, str):
            return len(content)
    elif btype == "tool_use":
        payload = block.get("input")
        if isinstance(payload, dict):
            return sum(
                len(k) + (len(v) if isinstance(v, str) else 8)
                for k, v in payload.items()
            )
    try:
        return len(json.dumps(block))
    except (TypeError, ValueError):
        return len(str(block))


def estimate_context_tokens(state: dict[str, Any]) -> int:
    """Approximate size of the whole conversation, in tokens.

    Sums the message list's character weight and divides by
    :data:`CHARS_PER_TOKEN`. Base64 attachment payloads are excluded — they
    inflate the character count by orders of magnitude while saying nothing
    about how hard the task is.
    """
    total = 0
    for message in state.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
            continue
        total += sum(_block_chars(b) for b in _blocks(message))
    return total // CHARS_PER_TOKEN


def plan_step_count(state: dict[str, Any]) -> int:
    """How many steps the agent's own most recent plan lists.

    Reads the latest assistant prose and counts numbered or bulleted lines. A
    model that has just written out eight steps is telling us this is a
    long-horizon task before it has spent a single iteration on it.
    """
    for message in reversed(state.get("messages") or []):
        if message.get("role") != "assistant":
            continue
        text = "\n".join(
            b.get("text") or "" for b in _blocks(message) if b.get("type") == "text"
        )
        if text.strip():
            return len(_PLAN_STEP.findall(text))
    return 0


def is_long_horizon(state: dict[str, Any]) -> bool:
    """Does this turn look like a big, multi-step piece of work?

    Any one of three signals is enough: the context is already large, the task
    has burned through several iterations, or the agent's plan lists many steps.
    """
    settings = get_settings()
    if estimate_context_tokens(state) >= settings.auto_complex_context_tokens:
        return True
    if int(state.get("iterations") or 0) >= settings.auto_complex_iteration_count:
        return True
    if plan_step_count(state) >= settings.auto_complex_plan_steps:
        return True
    return False


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def classify_task(state: dict[str, Any]) -> str:
    """Return the ``routing_hint`` Auto mode should use for this turn.

    Always returns one of :data:`HINTS`; never raises. A classifier that threw
    would take down a turn that a plain default would have served fine.
    """
    try:
        lookback = get_settings().auto_lookback_messages

        if has_visual_input(state, lookback):
            return "visual_structural"
        if is_file_editing(state, lookback):
            return "code_editing"
        if is_long_horizon(state):
            return "complex_longhorizon"
        return "fast_simple"
    except Exception:  # noqa: BLE001 - routing must never break a run
        log.warning("Task classification failed; using the default hint", exc_info=True)
        return "fast_simple"


def explain(state: dict[str, Any]) -> dict[str, Any]:
    """The signals behind the current decision. For logging and tuning."""
    lookback = get_settings().auto_lookback_messages
    return {
        "hint": classify_task(state),
        "visual": has_visual_input(state, lookback),
        "editing": is_file_editing(state, lookback),
        "context_tokens": estimate_context_tokens(state),
        "iterations": int(state.get("iterations") or 0),
        "plan_steps": plan_step_count(state),
    }
