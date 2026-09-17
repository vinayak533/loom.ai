"""Sessions get named, and are never named something that is not a name.

    python scripts/test_session_naming.py

Offline: no provider is called. What is asserted is the *policy* — which
answers are refused, and whether the call is given enough room to produce one
at all — because the failure this pins down was a policy failure, not a bad
model.

The bug this pins down
----------------------
Every session was called "New session". Naming ran, and its answer was thrown
away, on every first message.

`generate_title` budgeted 32 output tokens: enough for a name and nothing
else. Two of the three models on the roster reason before they answer, and the
first choice — `gpt-oss-120b`, the one picked wherever a Groq key is set —
spent the whole allowance thinking and ended its turn with no text. That
arrives as the router's empty-turn placeholder, `title_rejection` correctly
refused it, and the session kept its default name. Forever, because the guard
was doing its job on an answer that never had room to exist.

`generate_project_meta` had the same 32-token-class budget at 160 and a worse
ending: `_parse_project_meta` had no guard, so it stored the placeholder
sentence itself as the project's name, clipped to 80 characters.

Probed against the live roster: 128 tokens was 0/3 usable for both reasoning
models; 512 was 3/3 for all three; the longer project-meta prompt needed 1024
to reach 10/10. The roster now leads with the one model that does not reason,
which also keeps naming off Groq's 8000-token-per-minute budget.

What is asserted
----------------
1. Every shape a naming call answers with that is not a name is refused.
2. Real titles are not refused — including the ones the newest rule could
   plausibly have caught.
3. The project-meta parser refuses those same shapes instead of storing them.
4. A well-formed project meta still parses.
5. The roster leads with a model that answers without a preamble.
6. Both budgets clear the preamble that made the calls come back empty.
"""

from __future__ import annotations

import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.llm import (  # noqa: E402
    PROJECT_META_MAX_TOKENS,
    TITLE_MAX_TOKENS,
    TITLE_MODELS,
    _parse_project_meta,
    title_rejection,
)
from app.llm_router import EMPTY_TURN_TEXT  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


#: Answers that are not names, and the reason each is not one.
NOT_NAMES = [
    (EMPTY_TURN_TEXT, "the turn produced no text"),
    ("The user wants a 3-6 word title for this session.", "narrated the prompt"),
    ("Here is a title for the session: Auth Redirect Fix", "cleared its throat"),
    ("# Auth Redirect Fix", "answered with a document"),
    ("Auth Redirect Fix\nAnd some more prose about it", "answered with prose"),
    ("ing Graph State During Restart", "lost the front of its first token"),
    ("", "answered with nothing at all"),
]

#: Names that must survive. The last four are the ones the truncated-word rule
#: could plausibly have eaten.
NAMES = [
    "Checkpointing Graph State During Restart",
    "Maple & Steam Coffee Site",
    "Debug Flask 500 on Login",
    "iOS Build Fails on CI",
    "Ingesting CSV Files Into Postgres",
    "Integration Tests For The Router",
    "Mention Parsing In The Composer",
    "Alignment Of The Sidebar Icons",
]


def main() -> int:
    ok = True

    print("\n1. An answer that is not a name is refused")
    for text, why in NOT_NAMES:
        ok &= check(why, title_rejection(text) is not None, f"{text[:44]!r}")

    print("\n2. A real name is kept")
    for text in NAMES:
        ok &= check(f"{text!r}", title_rejection(text) is None)

    print("\n3. The project-meta parser refuses the same shapes")
    ok &= check(
        "the placeholder is not stored as a project name",
        _parse_project_meta(EMPTY_TURN_TEXT) is None,
        "it used to be, clipped to 80 characters",
    )
    ok &= check(
        "narration is not stored either",
        _parse_project_meta("The user wants a name for this project.") is None,
    )
    ok &= check("an empty answer yields nothing", _parse_project_meta("") is None)

    print("\n4. A well-formed project meta still parses")
    parsed = _parse_project_meta(
        "NAME: Maple & Steam\nABOUT: A one-page coffee shop site."
    )
    ok &= check("labelled form", parsed == {
        "title": "Maple & Steam",
        "description": "A one-page coffee shop site.",
    }, str(parsed))
    bare = _parse_project_meta("Maple & Steam\nA one-page coffee shop site.")
    ok &= check("unlabelled fallback still works", bare is not None, str(bare))

    print("\n5. The roster leads with a model that needs no preamble")
    ok &= check(
        "`llama-4-scout` is first",
        TITLE_MODELS[0] == "llama-4-scout",
        f"roster: {TITLE_MODELS}",
    )
    ok &= check(
        "the reasoning models are kept as fallbacks",
        "gpt-oss-120b" in TITLE_MODELS and "nemotron-3" in TITLE_MODELS,
    )

    print("\n6. Both budgets clear the preamble")
    ok &= check(
        "the title budget is past the 128 that measured 0/3",
        TITLE_MAX_TOKENS >= 512,
        f"{TITLE_MAX_TOKENS}",
    )
    ok &= check(
        "the project-meta budget is larger still",
        PROJECT_META_MAX_TOKENS >= 1024
        and PROJECT_META_MAX_TOKENS > TITLE_MAX_TOKENS,
        f"{PROJECT_META_MAX_TOKENS} vs {TITLE_MAX_TOKENS} — it reads a transcript",
    )

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
