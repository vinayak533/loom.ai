"""Project scan parsing and commit-message cleanup. No network, no sandbox.

    python scripts/test_analysis.py

The scan itself runs one shell script in an E2B sandbox, which is not something
an offline test can have. What it *can* have is the script's output, so these
assertions run `_parse` over realistic stdout and check the conclusions drawn
from it — which is where the bugs actually live. The shell half is exercised
live by `scripts/test_git_integration.py`.

Two things are worth pinning down:

  * a language's size is its **lines**, not its file count. A project with one
    4,000-line module and forty 20-line configs is a project in the first
    language, and a file-count histogram says the opposite;
  * excluded directories never reach the counter, so a vendored `node_modules`
    cannot swamp every number the panel shows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENCODE_API_KEY"):
    os.environ.setdefault(_k, "test-key-not-real")

from app import gitmsg  # noqa: E402
from app.analysis import EXCLUDE_DIRS, _parse, _script  # noqa: E402

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
# A realistic capture of what the script prints for a small full-stack repo.
# ---------------------------------------------------------------------------

SAMPLE = """@@FILES
package.json
tsconfig.json
requirements.txt
app/main.py
app/router.py
app/tests/test_router.py
web/src/index.tsx
web/src/App.tsx
web/src/App.css
README.md
Dockerfile
@@LINES
     420 app/main.py
     180 app/router.py
      95 app/tests/test_router.py
     240 web/src/index.tsx
     310 web/src/App.tsx
      60 web/src/App.css
      45 README.md
@@TODO
./app/router.py
./web/src/App.tsx
@@TODOCOUNT
7
@@TOP
app/
web/
package.json
requirements.txt
Dockerfile
@@END
"""

parsed = _parse(SAMPLE, "/home/user")

section("1. Basic measurement")
check("reports ok", parsed["ok"] is True)
check("counts files", parsed["file_count"] == 11, str(parsed["file_count"]))
check(
    "sums lines",
    parsed["total_lines"] == 420 + 180 + 95 + 240 + 310 + 60 + 45,
    str(parsed["total_lines"]),
)

section("2. Languages ranked by lines, not by file count")
langs = {lang["name"]: lang for lang in parsed["languages"]}
check("Python found", "Python" in langs)
check("TypeScript found", "TypeScript" in langs)
check(
    "Python lines correct",
    langs["Python"]["lines"] == 420 + 180 + 95,
    str(langs.get("Python", {}).get("lines")),
)
check(
    "TypeScript lines correct",
    langs["TypeScript"]["lines"] == 240 + 310,
    str(langs.get("TypeScript", {}).get("lines")),
)
check(
    "ranked by lines descending",
    [lang["name"] for lang in parsed["languages"]][:2] == ["Python", "TypeScript"],
    " > ".join(f"{lang['name']}:{lang['lines']}" for lang in parsed["languages"][:3]),
)
check(
    ".tsx and .ts collapse into one language",
    langs["TypeScript"]["files"] == 2,
    "index.tsx + App.tsx",
)

section("3. Manifests and ecosystems")
manifest_files = [m["file"] for m in parsed["manifests"]]
check("package.json found", "package.json" in manifest_files)
check("requirements.txt found", "requirements.txt" in manifest_files)
check("Dockerfile found", "Dockerfile" in manifest_files)
check(
    "ecosystems deduped and sorted",
    parsed["ecosystems"] == ["Docker", "Node", "Python"],
    str(parsed["ecosystems"]),
)

section("4. Tests, linting, TODOs")
check("test file detected", parsed["tests"]["files"] == 1, str(parsed["tests"]["files"]))
check(
    "tsconfig counts as lint/type config",
    "tsconfig.json" in parsed["lint_configs"],
    str(parsed["lint_configs"]),
)
check("TODO count read", parsed["todos"]["count"] == 7, str(parsed["todos"]["count"]))
check(
    "TODO paths normalised",
    parsed["todos"]["files"] == ["app/router.py", "web/src/App.tsx"],
    str(parsed["todos"]["files"]),
)
check("top level captured", "app/" in parsed["top_level"])


section("5. Empty and degenerate input")
empty = _parse("@@FILES\n@@LINES\n@@TODOCOUNT\n0\n@@TOP\n@@END\n", "/home/user")
check("empty tree is ok, not an error", empty["ok"] is True)
check("empty tree has no files", empty["file_count"] == 0)
check("empty tree has no languages", empty["languages"] == [])
check(
    "garbage output does not raise",
    _parse("total nonsense\nno markers here", "/home/user")["file_count"] == 0,
)
check(
    "a wc line with no count is skipped",
    _parse("@@LINES\n   not-a-number app/x.py\n@@END", "/home/user")["total_lines"] == 0,
)


section("6. The generated script")
script = _script("/home/user")
check("cd is quoted into the script", "cd /home/user" in script)
check(
    "every excluded directory is pruned",
    all(f"-name {d}" in script for d in EXCLUDE_DIRS),
    f"{len(EXCLUDE_DIRS)} directories",
)
check("node_modules is excluded", "-name node_modules" in script)
check(
    "the file list is capped",
    "head -20000" in script,
    "an unbounded find on a huge tree is a hung tool call",
)
check("every section marker is emitted", all(
    m in script for m in ("@@FILES", "@@LINES", "@@TODO", "@@TODOCOUNT", "@@TOP", "@@END")
))


section("7. Commit message cleanup")
check(
    "a fenced message is unwrapped",
    gitmsg._clean("```\nAdd rate limiting\n```") == "Add rate limiting",
)
check(
    "a language-tagged fence is unwrapped",
    gitmsg._clean("```text\nAdd rate limiting\n```") == "Add rate limiting",
)
check(
    "a Subject: label is stripped",
    gitmsg._clean("Subject: Add rate limiting") == "Add rate limiting",
)
check(
    "a 'Commit message:' label is stripped",
    gitmsg._clean("Commit message: Fix the leak") == "Fix the leak",
)
check(
    "a clean message is untouched",
    gitmsg._clean("Add rate limiting to the upload route")
    == "Add rate limiting to the upload route",
)
check(
    "a body survives unwrapping",
    gitmsg._clean("```\nAdd limits\n\nUploads were unbounded.\n```")
    == "Add limits\n\nUploads were unbounded.",
)
check("empty stays empty", gitmsg._clean("") == "")


section("8. LOOM.md reaches the prompt")

import asyncio  # noqa: E402

from app import analysis as _analysis  # noqa: E402
from app import preamble  # noqa: E402

_LOOM = {"text": ""}


async def _fake_read(session_id, repo=None):
    return _LOOM["text"]


_analysis.read_loom_file = _fake_read

check(
    "no session id means no read at all",
    asyncio.run(preamble.compose("BASE")) == "BASE",
)

_LOOM["text"] = ""
check(
    "an absent LOOM.md adds nothing",
    asyncio.run(preamble.compose("BASE", session_id="s1")) == "BASE",
)

_LOOM["text"] = "## What this is\nA FastAPI service."
composed = asyncio.run(preamble.compose("BASE", session_id="s1"))
check("the base prompt survives", "BASE" in composed)
check("LOOM.md content is injected", "A FastAPI service." in composed)
check(
    "it is labelled reference, not instruction",
    "reference material" in composed,
    "so a description of a Rails app is not read as an instruction to write Rails",
)
check(
    "it comes after the base prompt",
    composed.index("BASE") < composed.index("A FastAPI service."),
)


print()
if _failures:
    print(f"{RED}{_failures} check(s) failed.{RESET}")
    sys.exit(1)
print(f"{GREEN}All checks passed.{RESET}")
