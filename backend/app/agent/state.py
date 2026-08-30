from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """Checkpointed graph state. Everything here must be JSON-serialisable —
    the checkpointer round-trips it through SQLite/Postgres."""

    session_id: str

    # Which surface this turn belongs to: "chat" or "code". Attribution only —
    # nothing routes on it. Defaults to "chat" on every read, so a checkpoint
    # written before this field existed resumes without a KeyError.
    section: str

    # Which project this session belongs to, or "" for none — which is the
    # normal case. Carried in state for the same reason `user_id` is: the graph
    # is resumed from a checkpoint on every turn, and the socket that knew the
    # session row is not in scope inside a node. Defaults to "" on every read,
    # so a checkpoint written before projects existed resumes without a
    # KeyError, exactly as `section` does above.
    project_id: str

    # Which model powers this session (see app.llm_router.MODEL_REGISTRY).
    # Carried in state so a mid-conversation switch survives checkpointing.
    # In auto mode this holds the model the *last* turn resolved to, so the
    # graph can tell a routing change from a steady state.
    model_id: str

    # "manual" (model_id is the user's fixed pick) or "auto" (the classifier
    # chooses per turn). Absent on checkpoints written before auto routing
    # existed, which is why every read defaults to "manual" — an existing
    # session keeps the model it was saved with.
    routing_mode: str

    # The hint the classifier returned for the most recent auto-routed turn.
    # Logged with token usage so the routing rules can be evaluated later.
    # Empty in manual mode.
    routing_hint: str

    # Full internal-format conversation. Assistant turns are stored verbatim
    # (including thinking blocks and their signatures) so they can be replayed.
    messages: list[dict[str, Any]]

    # tool_use blocks emitted by the model that have not been executed yet.
    pending: list[dict[str, Any]]

    # tool_result blocks accumulated for the *next* user turn. The format
    # requires all results from one assistant turn in a single user message,
    # which is why they buffer here instead of going straight into `messages`.
    tool_results: list[dict[str, Any]]

    iterations: int
    stop_reason: str
    usage: dict[str, Any]

    # Who is paying for this run. Carried in state because the graph is
    # resumed from a checkpoint on every turn and the socket that knew the
    # caller is not in scope inside a node.
    user_id: str

    # Credits debited so far *in this turn*. Reset by the socket before each
    # run, not accumulated across the session: the ceiling below is a per-turn
    # bound, and a long-lived session would otherwise trip it forever once its
    # lifetime spend crossed the line.
    credits_spent: float
