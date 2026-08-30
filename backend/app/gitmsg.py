"""Writing a commit message from a diff.

Its own module rather than a function in `tools/git.py`, which is deliberately
model-free: everything there runs a git command in the sandbox and parses the
output, and dropping an LLM call into it would make the one module that cannot
fail for provider reasons able to.

The output is a *suggestion*. It goes into the commit box and the user still
presses commit. Writing the message and committing it in one step removes the
only moment a person reads what is about to be recorded, and a wrong commit
message is permanent in a way a wrong chat reply is not.
"""

from __future__ import annotations

import logging

from app.credits import charge_llm
from app.llm_router import complete_with_fallback, estimate_cost, first_available

log = logging.getLogger(__name__)

#: Cheap-first, same convention as TITLE_MODELS / MEMORY_MODELS / ANALYSIS_MODELS.
COMMIT_MODELS = ("deepseek_v4_flash", "gpt-oss-120b", "nemotron-3", "llama-4-scout")

#: How much diff the model is shown. A large refactor's full diff is mostly
#: repetition, and the subject line is decided by the first few hunks in almost
#: every case — so this buys back the tokens rather than the accuracy.
MAX_DIFF_CHARS = 12_000

SYSTEM = """\
You write git commit messages from diffs.

Format:
- A subject line in the imperative mood, under 72 characters, no trailing full \
stop. "Add rate limiting to the upload route", not "Added" or "Adding".
- Then a blank line and a short body, but only when the change needs one. A \
body that restates the subject is worse than no body.
- Explain *why* where the diff shows it, not what changed line by line — the \
diff is already in the commit.

Never invent a reason the diff does not support. Never mention files by path \
in the subject. Output the message and nothing else: no fences, no preamble, \
no "here is a commit message".\
"""


async def suggest(
    session_id: str,
    diff: str,
    user_id: str | None = None,
) -> str:
    """A commit message for ``diff``, or "" when none could be produced.

    Returns "" rather than raising for a missing model, because the caller's
    fallback is simply that the user types the message themselves — which is
    what they would have done anyway.
    """
    diff = (diff or "").strip()
    if not diff:
        return ""

    model_id = first_available(*COMMIT_MODELS)
    if not model_id:
        return ""

    clipped = diff[:MAX_DIFF_CHARS]
    if len(diff) > MAX_DIFF_CHARS:
        clipped += "\n\n[diff truncated — describe what is shown above]"

    message, resolved = await complete_with_fallback(
        model_id,
        messages=[{"role": "user", "content": clipped}],
        tools=[],
        system=SYSTEM,
        max_tokens=300,
        section="code",
    )
    usage = getattr(message, "usage", {}) or {}
    await charge_llm(
        user_id,
        session_id=session_id,
        agent_id="gitmsg",
        model_id=resolved,
        cost_usd=estimate_cost(resolved, usage),
    )

    content = getattr(message, "content", None)
    if isinstance(content, str):
        text = content
    else:
        text = "\n".join(
            str(b.get("text") or "")
            for b in (content or [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return _clean(text)


def _clean(text: str) -> str:
    """Strip the wrappers models add despite being told not to."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # A leading "Subject:" or "Commit message:" label, which several models add
    # when asked for a message rather than a reply.
    for prefix in ("subject:", "commit message:", "message:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].lstrip()
            break
    return text.strip()
