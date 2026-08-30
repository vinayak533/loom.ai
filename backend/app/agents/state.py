from __future__ import annotations

from typing import Any, TypedDict


class SpecialistState(TypedDict, total=False):
    """Checkpointed state for one specialist agent's conversation.

    Deliberately close to the Code section's :class:`app.agent.state.AgentState`
    — same message format, same pending/tool_results split, same usage totals —
    so anything that reads a checkpoint understands both. It is a separate type
    rather than a reused one because the two graphs have genuinely different
    fields: this one carries the agent identity and the credit accounting, and
    has no concept of a sandbox preview.

    Everything here must be JSON-serialisable; the checkpointer round-trips it.
    """

    session_id: str

    #: Which of the ten specialists this thread belongs to. Written once at
    #: session creation and never changed — switching an existing conversation
    #: to a different agent would leave a transcript written under one persona
    #: being continued under another.
    agent_id: str

    # Which project this session belongs to, or "" for none. Same contract as
    # the Chat/Code state: absent on checkpoints written before projects
    # existed, so every read defaults to "".
    project_id: str

    #: The user this turn is charged to. Carried in state so a resumed run
    #: still bills the right account after a reconnect.
    user_id: str

    #: Router model id, and how it was chosen. Same semantics as the Code
    #: section's, so the model selector and the Auto router behave identically
    #: here — see app.llm_router.
    model_id: str
    routing_mode: str
    routing_hint: str

    #: Internal block-format conversation, stored verbatim.
    messages: list[dict[str, Any]]

    #: `tool_use` blocks the model emitted that have not been run yet.
    pending: list[dict[str, Any]]

    #: `tool_result` blocks buffered for the next user turn. The format requires
    #: every result from one assistant turn in a single user message.
    tool_results: list[dict[str, Any]]

    iterations: int
    stop_reason: str
    usage: dict[str, Any]

    #: Tool calls executed on this turn, counted against the agent's
    #: `max_tool_calls_per_turn`. Reset per user message, like `iterations` —
    #: the budget is "per fact-check", not "per conversation".
    tool_calls_used: int

    #: Credits spent on this turn, accumulated across model calls and tool
    #: surcharges. Reset per turn; the running balance lives in the database.
    credits_spent: float
