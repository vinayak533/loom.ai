"""What is actually in this sandbox, and what it adds up to.

Two halves, deliberately separable.

:func:`scan` is **pure measurement**: languages, line counts, entry points,
dependencies, a TODO census, whether tests and linting exist. No model is
called, nothing is charged, and the same tree always produces the same numbers.
That matters because it is what the health panel polls — a readout that costs a
model call every time a file changes is a readout nobody can afford to leave
open.

:func:`summarise` is **the narrative**: one model call that turns those numbers
plus a directory listing into prose about what the project is and how it is put
together. It is charged like every other model call and is asked for
explicitly, never on a timer.

Why one shell command rather than a tree walk
---------------------------------------------
The obvious implementation walks the tree through `sandbox.files.list`, which
is what the sidebar does. For a real repository that is hundreds of round trips
over the E2B transport, and the sidebar's walker is depth-limited to 3 and
budget-capped precisely because of it — it is built to render a sidebar, not to
count a codebase. This sends one script instead and parses its output. One
round trip, no depth limit, and `find`/`wc` do the counting where the files
actually are.
"""

from __future__ import annotations

import logging
import posixpath
import shlex
from typing import Any

from app.credits import charge_llm
from app.llm_router import complete_with_fallback, estimate_cost, first_available
from app.tools.sandbox import WORKDIR, sandbox_manager

log = logging.getLogger(__name__)

#: Never counted, never walked into. Mirrors `IMPORT_EXCLUDE_DIRS` in
#: `tools/workspace.py` — a directory not worth uploading is not worth
#: measuring either, and a `node_modules` counted as source would swamp every
#: number this produces.
EXCLUDE_DIRS = (
    "node_modules",
    ".git",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".cache",
    "dist",
    "build",
    "out",
    "target",
    "vendor",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "coverage",
)

#: Extension -> language. Only what the counter reports on; anything else falls
#: into "other" rather than growing this table with every file type in
#: existence.
LANGUAGES = {
    "py": "Python",
    "ts": "TypeScript",
    "tsx": "TypeScript",
    "js": "JavaScript",
    "jsx": "JavaScript",
    "mjs": "JavaScript",
    "cjs": "JavaScript",
    "go": "Go",
    "rs": "Rust",
    "java": "Java",
    "kt": "Kotlin",
    "rb": "Ruby",
    "php": "PHP",
    "cs": "C#",
    "c": "C",
    "h": "C",
    "cpp": "C++",
    "hpp": "C++",
    "swift": "Swift",
    "sh": "Shell",
    "sql": "SQL",
    "css": "CSS",
    "scss": "CSS",
    "html": "HTML",
    "md": "Markdown",
    "json": "JSON",
    "yml": "YAML",
    "yaml": "YAML",
    "toml": "TOML",
}

#: Manifest -> ecosystem. Presence of one is the strongest single signal about
#: what a project *is*, which is why they are looked for by name rather than
#: inferred from the language histogram.
MANIFESTS = {
    "package.json": "Node",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "Pipfile": "Python",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker",
}

#: Config files that mean "this project lints" / "this project tests".
LINT_FILES = (
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.json",
    "eslint.config.js",
    "eslint.config.mjs",
    ".ruff.toml",
    "ruff.toml",
    ".flake8",
    "setup.cfg",
    ".prettierrc",
    "tsconfig.json",
    ".golangci.yml",
)

def _find_prune() -> str:
    """The `-prune` clause excluding every directory in EXCLUDE_DIRS."""
    names = " -o ".join(f"-name {d}" for d in EXCLUDE_DIRS)
    return f"\\( {names} \\) -prune -o"


def _script(root: str) -> str:
    """One shell script that measures everything :func:`scan` reports.

    Written as a single pipeline per section rather than a loop per file: the
    cost here is the round trip, not the CPU, and `find | xargs wc -l` counts a
    whole repository in the time one `sandbox.files.list` call takes.
    """
    prune = _find_prune()
    # Quoted: `root` is a parameter, and a path with a space in it would
    # otherwise turn one `cd` into two arguments and silently measure the
    # wrong directory.
    return f"""
cd {shlex.quote(root)} 2>/dev/null || exit 0

echo "@@FILES"
find . {prune} -type f -print 2>/dev/null | sed 's|^\\./||' | head -20000

echo "@@LINES"
find . {prune} -type f \\( -name '*.py' -o -name '*.ts' -o -name '*.tsx' \\
  -o -name '*.js' -o -name '*.jsx' -o -name '*.go' -o -name '*.rs' \\
  -o -name '*.java' -o -name '*.rb' -o -name '*.php' -o -name '*.cs' \\
  -o -name '*.c' -o -name '*.h' -o -name '*.cpp' -o -name '*.swift' \\
  -o -name '*.kt' -o -name '*.sh' -o -name '*.sql' -o -name '*.css' \\
  -o -name '*.scss' -o -name '*.html' -o -name '*.md' \\) -print0 2>/dev/null \\
  | xargs -0 wc -l 2>/dev/null | grep -v ' total$' | sed 's|\\./||'

echo "@@TODO"
grep -rIl --exclude-dir={{{','.join(EXCLUDE_DIRS)}}} -E 'TODO|FIXME|HACK|XXX' . 2>/dev/null | head -200

echo "@@TODOCOUNT"
grep -rIo --exclude-dir={{{','.join(EXCLUDE_DIRS)}}} -E 'TODO|FIXME|HACK|XXX' . 2>/dev/null | wc -l

echo "@@TOP"
ls -1p . 2>/dev/null | head -60

echo "@@END"
""".strip()


async def scan(session_id: str, repo: str | None = None) -> dict[str, Any]:
    """Measure the sandbox. No model call, no charge, deterministic.

    Returns ``{"ok": False, "reason": ...}`` rather than raising when there is
    no sandbox — "nothing has run in this session yet" is a state the panel
    renders, not an error it should have to catch.
    """
    root = posixpath.normpath(repo or WORKDIR)

    # Never create one. `sandbox_manager.get` would, and the health panel calls
    # this on mount — so merely opening the Code tab would cold-start an E2B
    # sandbox, burn a slot and start the 15-minute idle clock for a session
    # where nothing has been asked for yet. The same guard `read_loom_file`
    # uses, for the same reason.
    if sandbox_manager.sandbox_id_for(session_id) is None:
        return {
            "ok": False,
            "reason": "Nothing has run in this session yet, so there is nothing to measure.",
        }

    try:
        sandbox = await sandbox_manager.get(session_id)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        return {"ok": False, "reason": str(exc) or "No sandbox for this session yet."}

    try:
        result = await sandbox.commands.run(_script(root), timeout=90)
        raw = result.stdout or ""
    except Exception:  # noqa: BLE001
        log.warning("Project scan failed for %s", session_id, exc_info=True)
        return {"ok": False, "reason": "The scan could not run in this sandbox."}

    return _parse(raw, root)


def _sections(raw: str) -> dict[str, list[str]]:
    """Split the script's output on its @@ markers."""
    out: dict[str, list[str]] = {}
    current = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            current = line[2:].strip()
            out.setdefault(current, [])
            continue
        if current is not None and line.strip():
            out[current].append(line.rstrip())
    return out


def _parse(raw: str, root: str) -> dict[str, Any]:
    parts = _sections(raw)
    files = parts.get("FILES", [])

    # --- languages ---------------------------------------------------------
    # Counted from the `wc -l` section, so a language's size is its lines and
    # not its file count. A project with one 4,000-line module and forty
    # 20-line configs is a project in that first language, and a file-count
    # histogram says the opposite.
    languages: dict[str, dict[str, int]] = {}
    total_lines = 0
    for line in parts.get("LINES", []):
        stripped = line.strip()
        count, _, path = stripped.partition(" ")
        path = path.strip()
        if not path or not count.isdigit():
            continue
        lines = int(count)
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        name = LANGUAGES.get(ext)
        if not name:
            continue
        bucket = languages.setdefault(name, {"files": 0, "lines": 0})
        bucket["files"] += 1
        bucket["lines"] += lines
        total_lines += lines

    ranked = sorted(
        ({"name": k, **v} for k, v in languages.items()),
        key=lambda d: d["lines"],
        reverse=True,
    )

    # --- manifests and entry points ---------------------------------------
    basenames = {posixpath.basename(f): f for f in files}
    manifests = [
        {"file": path, "ecosystem": MANIFESTS[name]}
        for name, path in basenames.items()
        if name in MANIFESTS
    ]
    # Root manifests first: a `package.json` at the top of the tree describes
    # the project, one three directories down describes a package inside it.
    manifests.sort(key=lambda m: (m["file"].count("/"), m["file"]))

    # --- tests and linting -------------------------------------------------
    test_files = [
        f
        for f in files
        if "/test" in f.lower()
        or f.lower().startswith("test")
        or posixpath.basename(f).lower().startswith("test_")
        or ".test." in f.lower()
        or ".spec." in f.lower()
    ]
    lints = sorted({name for name in basenames if name in LINT_FILES})

    todo_count = 0
    for line in parts.get("TODOCOUNT", []):
        if line.strip().isdigit():
            todo_count = int(line.strip())
            break

    return {
        "ok": True,
        "root": root,
        "file_count": len(files),
        "total_lines": total_lines,
        "languages": ranked,
        "manifests": manifests,
        "ecosystems": sorted({m["ecosystem"] for m in manifests}),
        "tests": {"files": len(test_files), "sample": test_files[:10]},
        "lint_configs": lints,
        "todos": {
            "count": todo_count,
            "files": [f.lstrip("./") for f in parts.get("TODO", [])][:50],
        },
        "top_level": [e for e in parts.get("TOP", []) if e not in (".", "..")],
    }


# ---------------------------------------------------------------------------
# The narrative
# ---------------------------------------------------------------------------

#: Cheap-first, same convention as `TITLE_MODELS` and `MEMORY_MODELS`. The
#: long-horizon models are a poor trade here: the input is a page of numbers
#: and a directory listing, and what is wanted back is four paragraphs.
ANALYSIS_MODELS = ("mimo_v2_5", "nemotron-3", "gpt-oss-120b", "deepseek_v4_flash")

ANALYSIS_SYSTEM = """\
You are describing a codebase to a developer who has just opened it and has \
not read any of it yet.

You are given measurements and a directory listing — not the source. Write \
about what they actually show, and say so plainly when they do not settle \
something. Never invent a framework, a database or an architecture that is not \
evidenced by a manifest, a directory name or a file count.

Cover, in this order, with a short heading each:

1. **What this is** — one paragraph. The kind of project, its stack, roughly \
how big it is.
2. **How it is laid out** — the top-level directories and what each one \
appears to hold.
3. **How to run it** — inferred from the manifests present. If nothing \
indicates it, say that.
4. **What stands out** — anything the numbers make notable: no tests, a large \
TODO count, one directory holding most of the code, a language mix that \
suggests two things living in one repository.

Be concrete and brief. No preamble, no summary of what you are about to say, \
and no praise. If the project is nearly empty, say that in two sentences and \
stop — there is nothing to pad out.\
"""


def _brief(scanned: dict[str, Any]) -> str:
    """The measurements, as the compact text the model is asked to read."""
    lines: list[str] = [
        f"Files: {scanned['file_count']}",
        f"Lines of code: {scanned['total_lines']}",
    ]
    if scanned["languages"]:
        lines.append("Languages by lines:")
        for lang in scanned["languages"][:12]:
            lines.append(f"  {lang['name']}: {lang['lines']} lines in {lang['files']} files")
    if scanned["manifests"]:
        lines.append("Manifests:")
        for m in scanned["manifests"][:15]:
            lines.append(f"  {m['file']} ({m['ecosystem']})")
    lines.append(f"Test files: {scanned['tests']['files']}")
    if scanned["tests"]["sample"]:
        lines.append("  e.g. " + ", ".join(scanned["tests"]["sample"][:5]))
    lines.append(
        "Lint/type config: "
        + (", ".join(scanned["lint_configs"]) if scanned["lint_configs"] else "none found")
    )
    lines.append(f"TODO/FIXME markers: {scanned['todos']['count']}")
    if scanned["top_level"]:
        lines.append("Top level: " + ", ".join(scanned["top_level"][:40]))
    return "\n".join(lines)


async def summarise(
    session_id: str,
    scanned: dict[str, Any],
    user_id: str | None = None,
) -> dict[str, Any]:
    """One model call turning a scan into prose. Charged like any other."""
    if not scanned.get("ok"):
        return {"ok": False, "reason": scanned.get("reason") or "Nothing to analyse."}
    if not scanned.get("file_count"):
        return {
            "ok": True,
            "text": "This sandbox is empty — there are no files to analyse yet.",
            "model_id": None,
        }

    model_id = first_available(*ANALYSIS_MODELS)
    if not model_id:
        return {"ok": False, "reason": "No model is available to write the summary."}

    message, resolved = await complete_with_fallback(
        model_id,
        messages=[{"role": "user", "content": _brief(scanned)}],
        tools=[],
        system=ANALYSIS_SYSTEM,
        max_tokens=1200,
        section="code",
    )
    usage = getattr(message, "usage", {}) or {}
    await charge_llm(
        user_id,
        session_id=session_id,
        agent_id="analysis",
        model_id=resolved,
        cost_usd=estimate_cost(resolved, usage),
    )

    content = getattr(message, "content", None)
    if isinstance(content, str):
        text = content.strip()
    else:
        text = "\n".join(
            str(b.get("text") or "")
            for b in (content or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()

    return {"ok": True, "text": text, "model_id": resolved, "usage": usage}


# ---------------------------------------------------------------------------
# LOOM.md
# ---------------------------------------------------------------------------

LOOM_FILE = "LOOM.md"

_HEADER = """\
# LOOM.md

Written by Loom from a scan of this project. It is read back into the agent's
system prompt on every turn in this session, so understanding of the codebase
survives past the conversation that produced it.

Regenerate it after a change big enough to make it wrong. Edit it by hand
freely — nothing here is overwritten except by an explicit regenerate.
"""


async def write_loom_file(
    session_id: str, text: str, repo: str | None = None
) -> dict[str, Any]:
    """Write the analysis to LOOM.md at the sandbox root."""
    root = posixpath.normpath(repo or WORKDIR)
    path = posixpath.join(root, LOOM_FILE)
    try:
        sandbox = await sandbox_manager.get(session_id)
        await sandbox.files.write(path, f"{_HEADER}\n---\n\n{text.strip()}\n")
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not write %s", path, exc_info=True)
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "path": path}


async def read_loom_file(session_id: str, repo: str | None = None) -> str:
    """LOOM.md's contents, or "" when there is none.

    Two properties this must have, because it runs on the prompt path of every
    turn:

    **It never creates a sandbox.** `sandbox_manager.get` would happily spin
    one up, and a Chat session — which has no sandbox and never wants one —
    would then pay a cold E2B create before its first token, on every turn, to
    read a file that cannot exist. The id probe is a dict lookup and is the
    whole guard.

    **It never raises.** A sandbox that has been reaped between turns is the
    normal end of a session's life, not an error the conversation should stop
    for.
    """
    if sandbox_manager.sandbox_id_for(session_id) is None:
        return ""
    root = posixpath.normpath(repo or WORKDIR)
    path = posixpath.join(root, LOOM_FILE)
    try:
        sandbox = await sandbox_manager.get(session_id)
        content = await sandbox.files.read(path)
    except Exception:  # noqa: BLE001 - absent is the common case, not an error
        return ""
    return (content or "").strip() if isinstance(content, str) else ""
