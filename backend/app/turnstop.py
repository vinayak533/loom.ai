"""The turn concerns both graphs share.

There are two turn loops in this backend — the Chat/Code loop in
`app/agent/graph.py` and the specialist loop in `app/agents/graph.py` — and
they are not one loop because their tool nodes genuinely differ: Code's tools
contend over a shared filesystem and need per-family ordering, the specialists'
tools do not. Everything *around* that difference is the same job done twice,
and doing it twice is how the two drifted: a truncated-argument guard and an
input-token estimate were fixed in the Chat/Code loop and silently absent from
the specialist one, so an agent turn dispatched calls the other graph refused
and billed stopped turns for zero input.

So the shared concerns live here and both graphs call the same functions:

  * **argument validation** — refuse a `tool_use` whose arguments did not
    survive the provider's output ceiling (:func:`broken_arguments`);
  * **prompt measurement** — put a number on a turn that stopped before the
    provider ever reported usage (:func:`prompt_text`, :func:`approx_tokens`);
  * **stream teardown** — close a provider stream being abandoned mid-flight
    (:func:`close_stream`);
  * **stop bookkeeping** — turn whatever was streamed before the stop into a
    valid assistant message (:func:`partial_assistant_content`), and close a
    tool call that will now never run so its `tool_use` block still has a
    matching `tool_result` (:func:`stopped_result_block`).

`scripts/test_graph_parity.py` runs both graphs through the first three, so a
fix landing in one of them and not the other fails a test rather than shipping.

The token count is an estimate and is labelled as one everywhere it is stored.
A stopped stream never reaches the provider's `usage` frame — that only arrives
with the final message — so the choice is between estimating and billing zero,
and billing zero for tokens that were generated (and paid for upstream) is the
worse of the two.
"""

from __future__ import annotations

import json
import logging

from app.llm_router import RAW_ARGUMENTS_KEY

log = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# argument validation
# ---------------------------------------------------------------------------


def broken_arguments(name: str, args: dict) -> str | None:
    """Why this call cannot be run, or ``None`` if its arguments are usable.

    A tool call whose arguments did not parse as JSON arrives carrying
    :data:`RAW_ARGUMENTS_KEY` and *nothing else* — none of the keys the tool
    reads. Running it anyway is worse than not running it: every argument
    getter falls back to its default, and the defaults are meaningful. A
    truncated `file_write` becomes `path=""`, which `_abs` resolves to the
    sandbox workdir, so the model gets back ``path is a directory:
    /home/user`` — an error about a path it never asked for, with no hint
    that its own output was cut off. It then retries the identical call, is
    cut off at the identical place, and the run burns its whole iteration
    budget without writing a file.

    The specialists fail the same way and worse. Their expensive tools take a
    whole document or file as one argument — `chunk_document`, `parse_source`,
    `analyse_code` — so they are the calls most likely to be cut off, and
    their getters default to empty rather than erroring: a truncated
    `chunk_document` returns a confident count of zero chunks and the agent
    reports it as a result.

    So the call is refused here and the *real* reason is handed back instead,
    phrased as an instruction the model can act on. Truncation is diagnosed
    from the fragment rather than from `stop_reason`: a batch can hold one
    truncated call beside several intact ones, and the turn's single stop
    reason cannot say which.
    """
    raw = args.get(RAW_ARGUMENTS_KEY)
    if raw is None:
        return None
    fragment = raw if isinstance(raw, str) else str(raw)
    try:
        json.loads(fragment)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
    else:  # pragma: no cover - only reached if the parse becomes non-repeatable
        detail = "arguments could not be read"

    # An argument list that opens but never closes was cut off mid-write; one
    # that is balanced but still unreadable is malformed. The two need
    # different advice, and telling a model to "write less" when its JSON was
    # simply wrong sends it down a road that cannot fix anything.
    if fragment.rstrip().startswith("{") and not fragment.rstrip().endswith("}"):
        return (
            f"Error: the arguments for `{name}` were cut off after "
            f"{len(fragment)} characters and the call was not run. The turn "
            "hit its output-token ceiling while still writing them, so "
            "nothing reached the sandbox.\n\n"
            "Do not repeat this call unchanged — it will be cut off in the "
            "same place. Send less in one call instead: write the file's "
            "first section now, then append each remaining section with a "
            "separate call, or split the work across several smaller files."
        )
    return (
        f"Error: the arguments for `{name}` were not valid JSON, so the call "
        f"was not run ({detail}). Send the call again with well-formed JSON "
        "arguments."
    )


# ---------------------------------------------------------------------------
# prompt measurement
# ---------------------------------------------------------------------------


def prompt_text(messages: list[dict]) -> str:
    """Every piece of text in the prompt, for estimating input tokens.

    Only used on the stopped path, where the provider never reported real
    usage. Image and document blocks are skipped: their token cost is not a
    function of any text they carry, and guessing at it would be worse than
    the small under-count of leaving them out.
    """
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("text", "reasoning", "thinking"):
                parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_result":
                inner = block.get("content")
                parts.append(inner if isinstance(inner, str) else json.dumps(inner))
            elif block.get("type") == "tool_use":
                parts.append(json.dumps(block.get("input") or {}))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# stream teardown
# ---------------------------------------------------------------------------


async def close_stream(stream) -> None:
    """Close a provider stream that is being abandoned mid-flight.

    Async generators expose `aclose()`; anything else is left alone rather than
    guessed at. Failures are swallowed on purpose — the turn is already ending
    and a teardown error is not worth surfacing over the answer the user is
    reading.

    Not optional politeness: letting the stream fall out of scope leaves the
    connection to the garbage collector, and on a stopped generation that means
    the provider keeps producing — and charging for — tokens nobody will read.
    """
    close = getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:  # noqa: BLE001
        log.debug("Stream close failed on stop", exc_info=True)
