"""Agent 10 — Code Refactoring Assistant.

    parse_ast  REAL. Python's own `ast` module, in process: structure, real
               cyclomatic complexity, nesting depth, argument counts, and the
               specific inefficiency patterns worth flagging (string
               concatenation in a loop, a linear scan over a list where a set
               would do, a bare `except`). Python only, and it says so for
               anything else rather than pretending.
    lint_code  REAL. Runs an actual linter — `ruff` or `pyflakes` for Python,
               `node --check` for JavaScript — inside the session's E2B sandbox,
               the same one the Code section uses. Falls back to Python's own
               `compile()` for a real syntax check when no sandbox is available,
               so the tool still reports something true rather than nothing.
    run_code   REAL. Executes in that same E2B sandbox and returns real stdout,
               stderr and exit code. No simulation; if the sandbox is
               unavailable it says the refactor is unverified.
"""

from __future__ import annotations

import ast
import logging
import shlex
from typing import Any

from app.agents.tools.base import ToolContext, ToolResult, as_json
from app.config import get_settings
from app.tools.sandbox import SandboxUnavailable, sandbox_manager

log = logging.getLogger(__name__)

MAX_CODE_CHARS = 60_000
MAX_OUTPUT_CHARS = 12_000

#: Interpreter and lint command per language, inside the sandbox. Extensions
#: matter: `node --check` keys off the file suffix, and so does ruff.
LANGUAGES: dict[str, dict[str, Any]] = {
    "python": {
        "extension": "py",
        "run": "python3 {file}",
        # Ordered by preference; the first one present in the sandbox wins.
        "lint": [
            ("ruff", "ruff check --no-cache --output-format concise {file}"),
            ("pyflakes", "python3 -m pyflakes {file}"),
            ("compileall", "python3 -m py_compile {file}"),
        ],
    },
    "javascript": {
        "extension": "js",
        "run": "node {file}",
        "lint": [("node", "node --check {file}")],
    },
    "typescript": {
        "extension": "ts",
        # `node --experimental-strip-types` runs TS directly on modern Node;
        # if it is not available the run reports that rather than silently
        # executing something else.
        "run": "node --experimental-strip-types {file}",
        "lint": [("tsc", "npx --yes tsc --noEmit --skipLibCheck {file}")],
    },
    "bash": {"extension": "sh", "run": "bash {file}", "lint": [("bash", "bash -n {file}")]},
}

_ALIASES = {
    "py": "python", "python3": "python",
    "js": "javascript", "node": "javascript", "mjs": "javascript",
    "ts": "typescript", "tsx": "typescript",
    "sh": "bash", "shell": "bash", "zsh": "bash",
}


def _language(raw: str) -> str:
    key = (raw or "python").strip().lower()
    return _ALIASES.get(key, key)


# ---------------------------------------------------------------------------
# parse_ast
# ---------------------------------------------------------------------------

#: Nodes that add a branch. This is the standard cyclomatic-complexity set:
#: one for the function itself, plus one per decision point. `BoolOp` counts
#: n-1 because `a and b and c` is two extra paths, not one.
_BRANCHING = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
    ast.With, ast.AsyncWith, ast.Assert, ast.IfExp,
    ast.comprehension,
)


class _FunctionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[dict[str, Any]] = []
        self.classes: list[dict[str, Any]] = []
        self.imports: list[str] = []
        self.findings: list[dict[str, Any]] = []
        #: Names seen assigned a string anywhere in the module. Flat rather
        #: than scoped: this drives a *suggestion*, and a false positive
        #: ("consider joining a list") costs the reader a sentence, while the
        #: false negative it prevents is a real O(n²) loop going unreported.
        self._string_names: set[str] = set()

    def prime_string_names(self, tree: ast.AST) -> None:
        """Pre-pass: every name the module ever assigns a string to."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _looks_like_string(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._string_names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                annotated_str = (
                    isinstance(node.annotation, ast.Name) and node.annotation.id == "str"
                )
                if annotated_str or (node.value is not None and _looks_like_string(node.value)):
                    self._string_names.add(node.target.id)
            elif isinstance(node, ast.arg) and isinstance(node.annotation, ast.Name):
                if node.annotation.id == "str":
                    self._string_names.add(node.arg)

    # --- structure --------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.extend(alias.name for alias in node.names)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self.imports.extend(f"{module}.{a.name}" if module else a.name for a in node.names)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(
            {
                "name": node.name,
                "line": node.lineno,
                "methods": [
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ],
                "has_docstring": ast.get_docstring(node) is not None,
            }
        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node, is_async=True)

    def _function(self, node, is_async: bool) -> None:
        complexity = 1 + sum(
            (len(n.values) - 1) if isinstance(n, ast.BoolOp) else 1
            for n in ast.walk(node)
            if isinstance(n, _BRANCHING) or isinstance(n, ast.BoolOp)
        )
        args = node.args
        arg_count = (
            len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
            + (1 if args.vararg else 0) + (1 if args.kwarg else 0)
        )
        depth = _max_depth(node)
        entry = {
            "name": node.name,
            "line": node.lineno,
            "async": is_async,
            "arguments": arg_count,
            "cyclomatic_complexity": complexity,
            "max_nesting_depth": depth,
            "lines": (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1,
            "has_docstring": ast.get_docstring(node) is not None,
            "returns": sum(1 for n in ast.walk(node) if isinstance(n, ast.Return)),
        }
        self.functions.append(entry)

        # Thresholds are the conventional ones and are stated in the output, so
        # the model reports "above the threshold" rather than "too complex".
        if complexity > 10:
            self.findings.append(
                {
                    "severity": "high" if complexity > 20 else "medium",
                    "line": node.lineno,
                    "kind": "complexity",
                    "message": (
                        f"`{node.name}` has cyclomatic complexity {complexity} "
                        "(conventional threshold: 10). Each branch is another "
                        "path a test has to cover."
                    ),
                }
            )
        if depth > 4:
            self.findings.append(
                {
                    "severity": "medium",
                    "line": node.lineno,
                    "kind": "nesting",
                    "message": (
                        f"`{node.name}` nests {depth} levels deep. Early "
                        "returns or a guard clause usually flatten this."
                    ),
                }
            )
        if arg_count > 6:
            self.findings.append(
                {
                    "severity": "low",
                    "line": node.lineno,
                    "kind": "signature",
                    "message": f"`{node.name}` takes {arg_count} parameters.",
                }
            )
        self.generic_visit(node)

    # --- inefficiency and correctness patterns ----------------------------

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.findings.append(
                {
                    "severity": "high",
                    "line": node.lineno,
                    "kind": "bare_except",
                    "message": (
                        "Bare `except:` also catches KeyboardInterrupt and "
                        "SystemExit, so Ctrl-C stops working. Catch `Exception` "
                        "at minimum, and name the real exception where you can."
                    ),
                }
            )
        elif (
            isinstance(node.type, ast.Name)
            and node.type.id == "Exception"
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        ):
            self.findings.append(
                {
                    "severity": "medium",
                    "line": node.lineno,
                    "kind": "swallowed_exception",
                    "message": "`except Exception: pass` hides the failure entirely. Log it at least.",
                }
            )
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        # String building by `+=` inside a loop is O(n²) in CPython for large
        # n, because every concatenation copies the accumulated string.
        #
        # Two signals, either of which is enough. The value being string-shaped
        # catches `out += f"{x}"`; the *accumulator* being a known string
        # catches the far more common `out = ""` … `out += str(x)`, where the
        # right-hand side is an arbitrary call and says nothing on its own.
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.AugAssign)
                and isinstance(inner.op, ast.Add)
                and isinstance(inner.target, ast.Name)
                and (
                    _looks_like_string(inner.value)
                    or inner.target.id in self._string_names
                )
            ):
                self.findings.append(
                    {
                        "severity": "medium",
                        "line": inner.lineno,
                        "kind": "quadratic_string_build",
                        "message": (
                            f"`{inner.target.id} += …` inside a loop copies the "
                            "whole accumulated string each pass — O(n²). Append "
                            "to a list and `''.join(...)` once at the end."
                        ),
                    }
                )
            # `x in some_list` inside a loop is O(n) per test, so the loop is
            # O(n·m). A set makes the membership test O(1).
            if isinstance(inner, ast.Compare) and any(
                isinstance(op, ast.In) for op in inner.ops
            ):
                for comparator in inner.comparators:
                    if isinstance(comparator, (ast.List, ast.Tuple)) and len(
                        getattr(comparator, "elts", [])
                    ) > 5:
                        self.findings.append(
                            {
                                "severity": "low",
                                "line": inner.lineno,
                                "kind": "linear_membership",
                                "message": (
                                    "Membership test against a list/tuple literal "
                                    "inside a loop is O(n) each time. Make it a "
                                    "set literal for O(1)."
                                ),
                            }
                        )
        # `for i in range(len(xs))` when the body only uses `xs[i]`.
        if (
            isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Name)
            and node.iter.func.id == "range"
            and len(node.iter.args) == 1
            and isinstance(node.iter.args[0], ast.Call)
            and isinstance(node.iter.args[0].func, ast.Name)
            and node.iter.args[0].func.id == "len"
        ):
            self.findings.append(
                {
                    "severity": "low",
                    "line": node.lineno,
                    "kind": "range_len",
                    "message": (
                        "`for i in range(len(xs))` — iterate the sequence "
                        "directly, or use `enumerate(xs)` if you need the index."
                    ),
                }
            )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(
                comparator, ast.Constant
            ) and comparator.value is None:
                self.findings.append(
                    {
                        "severity": "low",
                        "line": node.lineno,
                        "kind": "none_comparison",
                        "message": "Compare to None with `is` / `is not`, not `==`.",
                    }
                )
        self.generic_visit(node)

    def visit_arguments(self, node: ast.arguments) -> None:
        for default in list(node.defaults) + [d for d in node.kw_defaults if d]:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.findings.append(
                    {
                        "severity": "high",
                        "line": default.lineno,
                        "kind": "mutable_default",
                        "message": (
                            "Mutable default argument. It is created once at "
                            "definition and shared by every call — use `None` "
                            "and build it inside the function."
                        ),
                    }
                )
        self.generic_visit(node)


#: Calls that always return a string. Enough to recognise the right-hand side
#: of a concatenation without doing type inference.
_STRING_CALLS = {"str", "repr", "format", "chr", "hex", "oct"}
_STRING_METHODS = {
    "join", "format", "strip", "lstrip", "rstrip", "lower", "upper", "title",
    "replace", "capitalize", "casefold", "zfill", "ljust", "rjust", "removeprefix",
    "removesuffix",
}


def _looks_like_string(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.JoinedStr):  # f-string
        return True
    if isinstance(node, ast.BinOp):
        return _looks_like_string(node.left) or _looks_like_string(node.right)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _STRING_CALLS:
            return True
        if isinstance(func, ast.Attribute) and func.attr in _STRING_METHODS:
            return True
    return False


def _max_depth(node: ast.AST, current: int = 0) -> int:
    nesting = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)
    best = current
    for child in ast.iter_child_nodes(node):
        step = current + 1 if isinstance(child, nesting) else current
        best = max(best, _max_depth(child, step))
    return best


async def parse_ast(ctx: ToolContext, args: dict) -> ToolResult:
    code = str(args.get("code") or "")
    if not code.strip():
        return ToolResult("Error: `code` was empty.", success=False)
    if len(code) > MAX_CODE_CHARS:
        return ToolResult(
            f"Error: {len(code):,} characters is beyond the {MAX_CODE_CHARS:,} "
            "limit. Pass the specific function or module you want audited.",
            success=False,
        )

    language = _language(str(args.get("language") or "python"))
    if language != "python":
        return ToolResult(
            f"AST parsing is implemented for Python only — `{language}` is not "
            "supported here. Use `lint_code` for that language, and base your "
            "structural comments on reading the code rather than on measurements "
            "you do not have. Say which you did.",
            success=False,
            meta={"language": language, "supported": ["python"]},
        )

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return ToolResult(
            output=(
                f"SyntaxError at line {exc.lineno}, column {exc.offset}: {exc.msg}\n"
                f"    {(exc.text or '').rstrip()}\n\n"
                "The code does not parse, so there is nothing to measure. Fix "
                "the syntax first — that is your first finding."
            ),
            success=False,
            meta={
                "syntax_error": {
                    "line": exc.lineno,
                    "column": exc.offset,
                    "message": exc.msg,
                    "text": (exc.text or "").rstrip(),
                }
            },
        )

    visitor = _FunctionVisitor()
    # Collect string-typed names before the main pass. Visiting alone would
    # work for the usual `acc = ""` above the loop, but not for an accumulator
    # assigned after the loop that reads it — and a detector whose result
    # depends on statement order would be a confusing thing to explain.
    visitor.prime_string_names(tree)
    visitor.visit(tree)

    functions = sorted(
        visitor.functions, key=lambda f: f["cyclomatic_complexity"], reverse=True
    )
    payload = {
        "language": "python",
        "lines": len(code.splitlines()),
        "functions": functions,
        "classes": visitor.classes,
        "imports": sorted(set(visitor.imports))[:60],
        "findings": sorted(
            visitor.findings,
            key=lambda f: {"high": 0, "medium": 1, "low": 2}.get(f["severity"], 3),
        ),
        "totals": {
            "functions": len(visitor.functions),
            "classes": len(visitor.classes),
            "findings": len(visitor.findings),
            "max_complexity": max((f["cyclomatic_complexity"] for f in functions), default=0),
        },
        "thresholds": {
            "cyclomatic_complexity": "flagged above 10, high above 20",
            "nesting_depth": "flagged above 4",
            "arguments": "flagged above 6",
        },
    }
    headline = (
        f"{len(visitor.functions)} function(s), {len(visitor.classes)} class(es), "
        f"{len(visitor.findings)} finding(s)."
    )
    return ToolResult(output=f"{headline}\n\n{as_json(payload)}", meta=payload)


# ---------------------------------------------------------------------------
# lint_code
# ---------------------------------------------------------------------------


async def lint_code(ctx: ToolContext, args: dict) -> ToolResult:
    code = str(args.get("code") or "")
    if not code.strip():
        return ToolResult("Error: `code` was empty.", success=False)

    language = _language(str(args.get("language") or "python"))
    spec = LANGUAGES.get(language)
    if spec is None:
        return ToolResult(
            f"Error: no linter wired for `{language}`. Supported: "
            f"{', '.join(LANGUAGES)}.",
            success=False,
        )

    filename = f"agent_lint.{spec['extension']}"
    path = f"/home/user/{filename}"

    try:
        sandbox = await sandbox_manager.get(ctx.session_id)
    except SandboxUnavailable as exc:
        # The sandbox is the real linter. Without it, Python at least gets a
        # genuine syntax check from the interpreter running this process —
        # a smaller true answer beats a fabricated complete one.
        return _local_fallback(code, language, str(exc))

    try:
        await sandbox.files.write(path, code)
    except Exception as exc:  # noqa: BLE001
        return _local_fallback(code, language, f"could not write to the sandbox ({exc})")

    for name, template in spec["lint"]:
        command = template.format(file=shlex.quote(path))
        stdout, stderr, exit_code, ran = await _run_in_sandbox(sandbox, command)
        if not ran:
            continue  # linter not installed in this image; try the next one

        output = "\n".join(p for p in (stdout, stderr) if p.strip()).strip()
        clean = exit_code == 0
        payload = {
            "linter": name,
            "language": language,
            "clean": clean,
            "exit_code": exit_code,
            "diagnostics": output[:MAX_OUTPUT_CHARS] or "(none)",
        }
        headline = (
            f"{name}: clean." if clean else f"{name}: {exit_code} — diagnostics below."
        )
        return ToolResult(
            output=f"{headline}\n\n{output[:MAX_OUTPUT_CHARS] or '(no output)'}",
            success=True,  # a lint failure is a *result*, not a tool error
            meta=payload,
        )

    return _local_fallback(
        code, language, f"no linter for {language} is installed in the sandbox image"
    )


def _local_fallback(code: str, language: str, reason: str) -> ToolResult:
    if language == "python":
        try:
            compile(code, "<submitted>", "exec")
        except SyntaxError as exc:
            return ToolResult(
                output=(
                    f"Sandbox linting unavailable ({reason}), so this is a "
                    f"syntax check only.\n\nSyntaxError line {exc.lineno}: {exc.msg}\n"
                    f"    {(exc.text or '').rstrip()}"
                ),
                success=True,
                meta={"linter": "python compile()", "clean": False, "degraded": True},
            )
        return ToolResult(
            output=(
                f"Sandbox linting unavailable ({reason}), so this is a syntax "
                "check only: the code compiles. Say in your audit that style and "
                "correctness lint rules were NOT run."
            ),
            success=True,
            meta={"linter": "python compile()", "clean": True, "degraded": True},
        )
    return ToolResult(
        output=(
            f"Could not lint: {reason}. There is no local fallback for "
            f"`{language}`. Say the code was not linted rather than implying it "
            "passed."
        ),
        success=False,
        meta={"degraded": True, "language": language},
    )


async def _run_in_sandbox(sandbox, command: str) -> tuple[str, str, int, bool]:
    """Run a command; ``ran`` is False when the executable is simply absent."""
    settings = get_settings()
    try:
        result = await sandbox.commands.run(
            command, cwd="/home/user", timeout=settings.bash_timeout_seconds
        )
        stdout, stderr, code = result.stdout or "", result.stderr or "", result.exit_code
    except Exception as exc:  # noqa: BLE001 - a non-zero exit raises in the SDK
        stdout = getattr(exc, "stdout", "") or ""
        stderr = getattr(exc, "stderr", "") or str(exc)
        code = getattr(exc, "exit_code", 1) or 1

    combined = f"{stdout}\n{stderr}".lower()
    missing = (
        "command not found" in combined
        or "no module named" in combined
        or (code == 127)
    )
    return stdout, stderr, code, not missing


# ---------------------------------------------------------------------------
# run_code
# ---------------------------------------------------------------------------


async def run_code(ctx: ToolContext, args: dict) -> ToolResult:
    """Execute code in the session's real sandbox and report what happened."""
    code = str(args.get("code") or "")
    if not code.strip():
        return ToolResult("Error: `code` was empty.", success=False)

    language = _language(str(args.get("language") or "python"))
    spec = LANGUAGES.get(language)
    if spec is None:
        return ToolResult(
            f"Error: cannot run `{language}`. Supported: {', '.join(LANGUAGES)}.",
            success=False,
        )

    filename = str(args.get("filename") or f"agent_run.{spec['extension']}").strip()
    # Basename only. The sandbox is isolated, but a tool that writes to an
    # arbitrary path on the model's say-so is a habit worth not forming.
    filename = filename.replace("\\", "/").split("/")[-1] or f"agent_run.{spec['extension']}"
    path = f"/home/user/{filename}"

    try:
        sandbox = await sandbox_manager.get(ctx.session_id)
    except SandboxUnavailable as exc:
        return ToolResult(
            output=(
                f"The sandbox is unavailable ({exc}), so this code was NOT run. "
                "Present the refactor as unverified and say what you would have "
                "executed — do not describe output you did not get."
            ),
            success=False,
            meta={"executed": False, "reason": str(exc)},
        )

    try:
        await sandbox.files.write(path, code)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(f"Error: could not write `{path}` ({exc}).", success=False)

    command = spec["run"].format(file=shlex.quote(path))
    stdin = args.get("stdin")
    if stdin:
        command = f"printf %s {shlex.quote(str(stdin))} | {command}"

    stdout, stderr, exit_code, _ = await _run_in_sandbox(sandbox, command)

    body = ""
    if stdout.strip():
        body += f"stdout:\n{stdout.strip()[:MAX_OUTPUT_CHARS]}"
    if stderr.strip():
        body += ("\n\n" if body else "") + f"stderr:\n{stderr.strip()[:MAX_OUTPUT_CHARS]}"
    if not body:
        body = "(no output)"

    passed = exit_code == 0
    return ToolResult(
        output=(
            f"exit code {exit_code} — {'passed' if passed else 'FAILED'}\n\n{body}\n\n"
            + (
                "Report this output verbatim in your Verification section."
                if passed
                else "It failed. Fix the cause and run it again; do not present "
                "this refactor as verified."
            )
        ),
        success=True,  # a failing run is a real result the model must read
        meta={
            "executed": True,
            "language": language,
            "exit_code": exit_code,
            "passed": passed,
            "file": path,
        },
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, dict] = {
    "parse_ast": {
        "name": "parse_ast",
        "description": (
            "Parse Python source into its AST and measure it: per-function "
            "cyclomatic complexity, nesting depth, argument counts, plus "
            "specific findings (mutable default arguments, bare excepts, O(n²) "
            "string building in a loop, linear membership tests, "
            "`range(len(x))`). Call it FIRST on any Python submitted for review "
            "— an audit should open with measurements, not impressions. Python "
            "only; it says so for other languages rather than guessing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The source to parse."},
                "language": {
                    "type": "string",
                    "description": "Currently only `python` is supported.",
                },
            },
            "required": ["code"],
        },
    },
    "lint_code": {
        "name": "lint_code",
        "description": (
            "Run a real linter over the code inside the session's sandbox — "
            "ruff or pyflakes for Python, `node --check` for JavaScript, "
            "`bash -n` for shell. Returns the linter's actual diagnostics. If "
            "no sandbox is available it degrades to a genuine syntax check and "
            "says so; report that degradation rather than implying a clean pass."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The source to lint."},
                "language": {
                    "type": "string",
                    "enum": list(LANGUAGES),
                    "description": "Defaults to python.",
                },
            },
            "required": ["code"],
        },
    },
    "run_code": {
        "name": "run_code",
        "description": (
            "Execute code in the session's isolated sandbox and get back real "
            "stdout, stderr and exit code. Use it to PROVE a refactor: include "
            "an assertion or a small test in what you run, because 'it executes' "
            "is a weaker claim than 'it produces the right answer'. Report the "
            "output verbatim, failures included. A 30-second timeout applies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The complete program to run."},
                "language": {
                    "type": "string",
                    "enum": list(LANGUAGES),
                    "description": "Defaults to python.",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional filename (basename only).",
                },
                "stdin": {"type": "string", "description": "Optional stdin to pipe in."},
            },
            "required": ["code"],
        },
    },
}

HANDLERS = {
    "parse_ast": parse_ast,
    "lint_code": lint_code,
    "run_code": run_code,
}
