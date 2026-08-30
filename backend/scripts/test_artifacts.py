"""Artifact keys, kinds and versioning. No network, no keys, no spend.

    python scripts/test_artifacts.py

Runs against a stubbed repository, so what is being tested is this module's
behaviour rather than Supabase being reachable. The three things worth pinning:

  * a key is *coerced* where a model has plainly understood the request and
    mistyped the format, and *refused* where it has not — failing a call over
    "Pricing Page" teaches the model nothing and costs a turn;
  * every write is a new version, and a user's edit adds one rather than
    replacing what the model wrote;
  * an update keeps the title and kind it already had, so revising does not
    make the panel's heading flicker between synonyms.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENCODE_API_KEY"):
    os.environ.setdefault(_k, "test-key-not-real")

from app import artifacts  # noqa: E402
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


def run(coro):
    return asyncio.run(coro)


# --- a stub store ----------------------------------------------------------

_ROWS: list[dict] = []


async def _get_artifact(session_id, key):
    rows = [r for r in _ROWS if r["session_id"] == session_id and r["artifact_key"] == key]
    return max(rows, key=lambda r: r["version"]) if rows else None


async def _add_artifact(session_id, artifact_key, content, kind="markdown",
                        title="Untitled", language=None, created_by="agent",
                        user_id=None):
    latest = await _get_artifact(session_id, artifact_key)
    row = {
        "id": f"a{len(_ROWS)}",
        "session_id": session_id,
        "artifact_key": artifact_key,
        "version": int((latest or {}).get("version") or 0) + 1,
        "kind": kind,
        "title": title,
        "language": language,
        "content": content,
        "created_by": created_by,
        "user_id": user_id,
    }
    _ROWS.append(row)
    return dict(row)


repository.get_artifact = _get_artifact
repository.add_artifact = _add_artifact


# ---------------------------------------------------------------------------
section("1. Keys are coerced where the intent is clear")

check("a clean key passes through", artifacts.normalise_key("pricing-page") == "pricing-page")
check("spaces become hyphens", artifacts.normalise_key("Pricing Page") == "pricing-page")
check("case is lowered", artifacts.normalise_key("PricingPage") == "pricingpage")
check("underscores become hyphens", artifacts.normalise_key("pricing_page") == "pricing-page")
check("runs of hyphens collapse", artifacts.normalise_key("a---b") == "a-b")
check("leading/trailing hyphens are trimmed", artifacts.normalise_key("-a-") == "a")


def refuses(raw: str) -> bool:
    try:
        artifacts.normalise_key(raw)
    except artifacts.ArtifactError:
        return True
    return False


check("empty is refused", refuses(""))
check("punctuation-only is refused", refuses("!!!"))
check("a path is refused", refuses("../../etc/passwd"), "keys are handles, not paths")
check(
    "an over-long key is refused",
    refuses("x" * 100),
    "a key the model cannot retype exactly is not a handle",
)


section("2. Kinds tolerate the near-misses models produce")
check("a known kind passes", artifacts.normalise_kind("html", None) == "html")
check("md -> markdown", artifacts.normalise_kind("md", None) == "markdown")
check(
    "an unknown kind falls back to markdown, not to a kind nothing renders",
    artifacts.normalise_kind("diagram", None) == "markdown",
)
check("react -> code", artifacts.normalise_kind("react", None) == "code")
check(
    "a language with no kind means code",
    artifacts.normalise_kind(None, "python") == "code",
    "the single most common omission",
)
check("nothing at all defaults to markdown", artifacts.normalise_kind(None, None) == "markdown")


section("3. Every write is a new version")
_ROWS.clear()
v1 = run(artifacts.write("s1", "notes", "first", kind="markdown", title="Notes"))
check("first write is version 1", v1["version"] == 1)

v2 = run(artifacts.write("s1", "notes", "second"))
check("second write is version 2", v2["version"] == 2)
check("content is the new one", v2["content"] == "second")
check(
    "the first version still exists",
    any(r["version"] == 1 and r["content"] == "first" for r in _ROWS),
    "revising must never destroy what was there",
)

check(
    "an update keeps the existing title",
    v2["title"] == "Notes",
    "re-titling on every revision makes the panel heading flicker",
)
check("an update keeps the existing kind", v2["kind"] == "markdown")

v3 = run(artifacts.write("s1", "notes", "third", title="Release notes"))
check("an explicit new title is taken", v3["title"] == "Release notes")


section("4. A person's edit adds a version, attributed")
v4 = run(artifacts.write("s1", "notes", "mine", created_by="user"))
check("the user's edit is version 4", v4["version"] == 4)
check("it is attributed to the user", v4["created_by"] == "user")
check(
    "the model's version survives underneath",
    any(r["version"] == 3 and r["created_by"] == "agent" for r in _ROWS),
)
check(
    "the latest is what the model will read next turn",
    run(repository.get_artifact("s1", "notes"))["content"] == "mine",
)


section("5. Bounds and reporting")
too_big = "x" * (artifacts.MAX_CONTENT_CHARS + 1)
try:
    run(artifacts.write("s1", "huge", too_big))
    check("an oversized artifact is refused", False)
except artifacts.ArtifactError as exc:
    check("an oversized artifact is refused", True, str(exc)[:56] + "…")

_ROWS.clear()
created = run(artifacts.write("s1", "page", "line\nline\nline", kind="html", title="Page"))
summary = artifacts.summarise(created)
check("the summary names the key", "page" in summary)
check("the summary counts lines", "3 lines" in summary, summary)
check(
    "the summary does NOT echo the content",
    "line\nline" not in summary,
    "the model just wrote it; echoing doubles the cost for no information",
)
updated = run(artifacts.write("s1", "page", "one"))
check("an update says so", "version 2" in artifacts.summarise(updated))


print()
if _failures:
    print(f"{RED}{_failures} check(s) failed.{RESET}")
    sys.exit(1)
print(f"{GREEN}All checks passed.{RESET}")
