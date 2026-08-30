"""Helpers for finishing a turn that the user stopped.

Both graphs — the Chat/Code loop in `app/agent/graph.py` and the specialist
loop in `app/agents/graph.py` — have to do the same three things when a stop
lands, so they do them through here rather than each growing its own version:

  * turn whatever was streamed before the stop into a valid assistant message
    (:func:`partial_assistant_content`);
  * close a tool call that will now never run, so the `tool_use` block it
    belongs to still has a matching `tool_result` (:func:`stopped_result_block`);
  * put a number on the output the provider actually produced, so the turn is
    billed for that and not for the answer it was going to write
    (:func:`approx_tokens`).

The token count is an estimate and is labelled as one everywhere it is stored.
A stopped stream never reaches the provider's `usage` frame — that only arrives
with the final message — so the choice is between estimating and billing zero,
and billing zero for tokens that were generated (and paid for upstream) is the
worse of the two.
"""

from __future__ import annotations

#: Characters per token. The usual rule of thumb for English prose across
#: BPE-family tokenizers; the models in this project's registry are all in that
#: family. Deliberately conservative rather than clever — a per-model tokenizer
#: would be exact, and would also mean shipping a tokenizer per provider to
#: refine a number that only ever decides a fraction of a credit.
CHARS_PER_TOKEN = 4


def approx_tokens(text: str) -> int:
    """Estimated token count for ``text``. Never negative, never fractional."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


#: Appended to the partial answer so the transcript says why it ends where it
#: does. Without it a stopped reply is indistinguishable from a model that
#: simply trailed off mid-sentence — including to the model itself on the next
#: turn, which will otherwise try to continue a thought the user cut short.
#
# Asterisks rather than underscores, deliberately. `components/Markdown.tsx`
# renders `*italic*` and not `_italic_`, and teaching it the second form would
# italicise every `snake_case` identifier in the model's own prose — a
# regression in the common case, to prettify a rare one.
STOPPED_SUFFIX = "\n\n*(Stopped by the user.)*"

#: The result handed to a tool call that the stop pre-empted. It is a normal,
#: non-error result: nothing failed, the call simply never happened, and
#: marking it `is_error` would have the model apologise for a fault of its own.
STOPPED_TOOL_TEXT = "The user stopped this turn before this tool call ran."


def partial_assistant_content(
    text: str, thinking: str = ""
) -> list[dict[str, str]]:
    """The content blocks for an assistant turn that was stopped mid-stream.

    Returns at least one text block. An assistant message with no blocks at all
    renders as an empty bubble and is rejected as history on the next request,
    so a stop that landed before the first token still produces a sentence
    saying exactly that.
    """
    blocks: list[dict[str, str]] = []
    if thinking.strip():
        # Kept as `reasoning` — the same block type the router emits for a
        # thinking model — so `itemsFromHistory` puts it under the same
        # collapsed Reasoning disclosure it uses for a completed turn.
        blocks.append({"type": "reasoning", "text": thinking})

    body = text.strip()
    if body:
        blocks.append({"type": "text", "text": body + STOPPED_SUFFIX})
    else:
        blocks.append(
            {
                "type": "text",
                "text": "*(Stopped by the user before the model replied.)*",
            }
        )
    return blocks


def stopped_result_block(call_id: str) -> dict[str, object]:
    """A `tool_result` for a `tool_use` that the stop pre-empted."""
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": STOPPED_TOOL_TEXT,
        "is_error": False,
    }
