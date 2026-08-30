"""Projects, memory and prompt composition. No network, no keys, no spend.

    python scripts/test_projects.py

Everything here runs against stubbed repository functions, so the assertions
are about *this* code's behaviour rather than about Supabase being reachable.
Three things are worth pinning down and are what this file exists for:

  * the injection budget is enforced, and truncation is announced rather than
    silent — a model that reads half a document and does not know it is worse
    than one with no document at all;
  * ordering in the composed prompt is base, then account, then project, so the
    narrower statement is read last and wins;
  * memory extraction parses what models actually return, including a JSON
    object wrapped in a code fence, and refuses what it should refuse.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENCODE_API_KEY"):
    os.environ.setdefault(_k, "test-key-not-real")

from app import memory, preamble, projects  # noqa: E402
from app.db import repository  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {label}" + (f" {DIM}({detail}){RESET}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{title}")


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_PROJECT: dict = {}
_FILES: list[dict] = []
_PREFS: dict = {}
_MEMORIES: list[dict] = []


async def _get_project(project_id):
    return dict(_PROJECT) if _PROJECT and _PROJECT.get("id") == project_id else None


async def _list_project_files(project_id, with_content=False):
    if with_content:
        return [dict(f) for f in _FILES]
    return [{k: v for k, v in f.items() if k != "content"} for f in _FILES]


async def _get_project_file(file_id):
    for f in _FILES:
        if f["id"] == file_id:
            return dict(f)
    return None


async def _get_project_files(file_ids):
    """The batched read `_files_block` uses: one query, keyed by id."""
    return {f["id"]: dict(f) for f in _FILES if f["id"] in set(file_ids)}


async def _get_preferences(user_id):
    return dict(_PREFS)


async def _list_memories(user_id, limit=200):
    return [dict(m) for m in _MEMORIES[:limit]]


repository.get_project = _get_project
repository.list_project_files = _list_project_files
repository.get_project_file = _get_project_file
repository.get_project_files = _get_project_files
repository.get_preferences = _get_preferences
repository.list_memories = _list_memories


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Project context
# ---------------------------------------------------------------------------

section("1. Project context block")

_PROJECT.clear()
check("no project id yields nothing", run(projects.context_block(None)) == "")
check("unknown project yields nothing", run(projects.context_block("nope")) == "")

_PROJECT.update(
    {
        "id": "p1",
        "name": "Ledger rewrite",
        "description": "Porting the billing service off Rails.",
        "instructions": "Always write Python 3.12. Never suggest Rails.",
    }
)
block = run(projects.context_block("p1"))
check("names the project", "Ledger rewrite" in block)
check("includes the description", "Porting the billing service" in block)
check("includes the instructions", "Never suggest Rails" in block)
check(
    "labels instructions as overriding",
    "override your general defaults" in block,
    "so the model knows which statement wins",
)

# --- budget ---------------------------------------------------------------
section("2. Knowledge file budget")

_FILES.clear()
_FILES.append(
    {
        "id": "f1",
        "name": "spec.md",
        "status": "ready",
        "char_count": 200,
        "content": "S" * 200,
    }
)
block = run(projects.context_block("p1"))
# Containment, not a character count: the block's own prose contains letters
# too ("Standing instructions" supplies an S), so counting would measure the
# heading as much as the file.
check("a small file is included whole", ("S" * 200) in block)
check("no truncation notice on a whole file", "truncated" not in block)

_FILES.clear()
huge = "H" * 50_000
_FILES.append(
    {
        "id": "f1",
        "name": "huge.md",
        "status": "ready",
        "char_count": len(huge),
        "content": huge,
    }
)
block = run(projects.context_block("p1"))
check(
    "one file cannot exceed the per-file cap",
    block.count("H") <= projects.MAX_ONE_FILE_CHARS,
    f"{block.count('H')} <= {projects.MAX_ONE_FILE_CHARS}",
)
check("truncation is announced", "truncated" in block)

_FILES.clear()
for i in range(10):
    body = "X" * 5_000
    _FILES.append(
        {
            "id": f"f{i}",
            "name": f"doc{i}.md",
            "status": "ready",
            "char_count": len(body),
            "content": body,
        }
    )
block = run(projects.context_block("p1"))
check(
    "total files stay within the combined budget",
    block.count("X") <= projects.MAX_FILES_CHARS,
    f"{block.count('X')} <= {projects.MAX_FILES_CHARS}",
)
check(
    "files left out are declared, not dropped silently",
    "further file(s) were not included" in block,
)

_FILES.clear()
_FILES.append(
    {
        "id": "f1",
        "name": "scan.pdf",
        "status": "failed",
        "char_count": 0,
        "content": "",
    }
)
block = run(projects.context_block("p1"))
check("a failed file is never injected", "scan.pdf" not in block)


# ---------------------------------------------------------------------------
# 3. Memory context
# ---------------------------------------------------------------------------

section("3. Memory context block")

_PREFS.clear()
_MEMORIES.clear()
check("anonymous gets nothing", run(memory.context_block(None)) == "")

_PREFS.update({"about_you": "Backend engineer, ten years in.", "memory_enabled": True})
block = run(memory.context_block("u1"))
check("about-you is injected", "ten years in" in block)

_PREFS["response_style"] = "Be terse. No preamble."
block = run(memory.context_block("u1"))
check("response style is injected", "No preamble" in block)
check(
    "the two are separate headings",
    "About the user" in block and "How the user wants you to respond" in block,
)

_MEMORIES.extend(
    [
        {"content": "Prefers TypeScript over JavaScript."},
        {"content": "Is building a logistics backend."},
    ]
)
block = run(memory.context_block("u1"))
check("stored facts are injected", "Prefers TypeScript" in block)

_PREFS["memory_enabled"] = False
block = run(memory.context_block("u1"))
check("the switch hides stored facts", "Prefers TypeScript" not in block)
check(
    "the switch does NOT hide typed instructions",
    "ten years in" in block and "No preamble" in block,
    "turning memory off means stop learning, not discard what I typed",
)

_PREFS["memory_enabled"] = True
_MEMORIES.clear()
for i in range(200):
    _MEMORIES.append({"content": f"Fact number {i} " + "y" * 60})
block = run(memory.context_block("u1"))
check(
    "the memory block is bounded",
    len(block) <= memory.MAX_MEMORY_CHARS + 500,
    f"{len(block)} chars",
)


# ---------------------------------------------------------------------------
# 4. Prompt composition order
# ---------------------------------------------------------------------------

section("4. Composition order")

_PREFS.clear()
_PREFS.update({"about_you": "ACCOUNT_MARKER", "memory_enabled": True})
_MEMORIES.clear()
_FILES.clear()
_PROJECT.update({"id": "p1", "name": "P", "instructions": "PROJECT_MARKER"})

composed = run(preamble.compose("BASE_MARKER", user_id="u1", project_id="p1"))
i_base = composed.index("BASE_MARKER")
i_account = composed.index("ACCOUNT_MARKER")
i_project = composed.index("PROJECT_MARKER")
check("base prompt comes first", i_base < i_account)
check(
    "project instructions come last",
    i_account < i_project,
    "least specific to most specific, so the narrower statement wins",
)

check(
    "nothing to add returns the base unchanged",
    run(preamble.compose("BASE", user_id=None, project_id=None)) == "BASE",
)


async def _boom(*a, **k):
    raise RuntimeError("store is down")


_saved = repository.get_preferences
repository.get_preferences = _boom
check(
    "an unreachable store costs personalisation, not the turn",
    run(preamble.compose("BASE", user_id="u1", project_id=None)) == "BASE",
)
repository.get_preferences = _saved


# ---------------------------------------------------------------------------
# 5. Extraction parsing
# ---------------------------------------------------------------------------

section("5. Memory extraction parsing")

check("plain JSON parses", memory._parse('{"memories": ["a fact here"]}') == ["a fact here"])
check(
    "a fenced block parses",
    memory._parse('```json\n{"memories": ["fenced fact"]}\n```') == ["fenced fact"],
    "models wrap JSON in a fence often enough to be worth recovering from",
)
check("empty list is respected", memory._parse('{"memories": []}') == [])
check("garbage yields nothing", memory._parse("I could not find any facts.") == [])
check("a non-object yields nothing", memory._parse('["a", "b"]') == [])
check("a missing key yields nothing", memory._parse('{"other": ["x"]}') == [])
check(
    "a paragraph-length fact is refused",
    memory._parse('{"memories": ["' + "z" * 400 + '"]}') == [],
    "that is a conversation summary, not a durable fact",
)
check(
    "at most five facts per exchange",
    len(memory._parse('{"memories": [' + ",".join(f'"fact {i} here"' for i in range(20)) + "]}")) == 5,
)
check(
    "non-string entries are dropped",
    memory._parse('{"memories": ["real fact", 42, null]}') == ["real fact"],
)


# ---------------------------------------------------------------------------

print()
if _failures:
    print(f"{RED}{_failures} check(s) failed.{RESET}")
    sys.exit(1)
print(f"{GREEN}All checks passed.{RESET}")
