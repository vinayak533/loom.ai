"""Real git, inside the session's sandbox.

The sandbox already runs arbitrary shell commands, so this adds no new
execution capability — what it adds is *structure*. `bash_execute` could run
`git commit` today, but the output would be an opaque blob the UI cannot read,
the model would have to remember the porcelain flags, and nothing would tell
the client that history had moved. So the operations worth surfacing get a
declared schema, parsed output, and an event the UI can render.

Design notes a future maintainer will want:

* **One tool, not five.** `git` takes an ``operation`` enum rather than
  shipping `git_init` / `git_status` / `git_diff` / `git_commit` / `git_log` as
  separate entries. Every tool schema is re-sent on every turn of every Code
  session, and five near-identical entries is a real prompt-token cost for no
  gain in selection accuracy — the operations share one argument shape.

* **Parsed, not scraped.** `status` uses ``--porcelain=v1 -z`` and `log` uses a
  ``--pretty`` format with unit separators. Both are stable, machine-readable
  formats with an explicit compatibility promise, unlike the human-facing
  output which changes between git versions and localises.

* **Identity is set at init.** A sandbox has no git identity, and `git commit`
  hard-fails with "Please tell me who you are" rather than defaulting. It is
  set repo-locally so nothing leaks between sessions.

* **Nothing is pushed, ever.** There is no remote, no credential, and no
  network path out of this module. Version control here means local history a
  session can produce and the user can inspect or clone out — not publishing.
"""

from __future__ import annotations

import logging
import posixpath
import shlex
from dataclasses import dataclass

from app.tools.sandbox import WORKDIR, sandbox_manager

log = logging.getLogger(__name__)

#: Identity used for commits made inside a sandbox. Local to the repo.
GIT_USER_NAME = "Loom Agent"
GIT_USER_EMAIL = "agent@loom.local"

#: How many commits the history panel asks for by default.
DEFAULT_LOG_LIMIT = 30
MAX_LOG_LIMIT = 200

#: Field separator inside `git log --pretty`. Unit Separator, because it cannot
#: appear in a commit subject and needs no escaping.
_FS = "\x1f"
#: Record separator between commits.
_RS = "\x1e"

_LOG_FORMAT = _FS.join(["%H", "%h", "%an", "%aI", "%s"]) + _RS

#: Files never worth committing from a sandbox build. Written as a .gitignore
#: at init so the first commit is the project, not its dependencies.
#:
#: The shell dotfiles at the top are not paranoia. The sandbox workdir *is*
#: ``/home/user``, so a repository rooted there sees `.bashrc`, `.profile` and
#: friends as untracked, and the first commit would carry the sandbox image's
#: shell config as though it were project source. Verified against a live E2B
#: sandbox, not assumed.
_DEFAULT_IGNORE = """\
.bashrc
.profile
.bash_logout
.bash_history
.cache/
.local/
.npm/
.config/
node_modules/
.next/
dist/
build/
__pycache__/
*.pyc
.venv/
venv/
.env
.env.local
.DS_Store
"""


class GitError(RuntimeError):
    """A git operation failed in a way worth reporting verbatim."""


@dataclass
class Run:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def text(self) -> str:
        out = self.stdout.strip()
        err = self.stderr.strip()
        if out and err:
            return f"{out}\n{err}"
        return out or err


async def _run(session_id: str, command: str, cwd: str | None = None) -> Run:
    """Run one command in the sandbox, without raising on a non-zero exit.

    Git uses exit status as information — `diff --quiet` returns 1 for "there
    are changes", `rev-parse` returns 128 for "not a repo" — so a non-zero exit
    is routinely the answer rather than a failure.
    """
    sandbox = await sandbox_manager.get(session_id)
    try:
        result = await sandbox.commands.run(command, cwd=cwd or WORKDIR, timeout=60)
        return Run(result.stdout or "", result.stderr or "", result.exit_code or 0)
    except Exception as exc:  # noqa: BLE001 - the SDK raises on non-zero exit
        return Run(
            getattr(exc, "stdout", "") or "",
            getattr(exc, "stderr", "") or str(exc),
            getattr(exc, "exit_code", 1) or 1,
        )


def _repo_path(path: str | None) -> str:
    path = (path or "").strip() or WORKDIR
    if not path.startswith("/"):
        path = posixpath.join(WORKDIR, path)
    return posixpath.normpath(path)


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------


async def is_repo(session_id: str, repo: str | None = None) -> bool:
    run = await _run(
        session_id, "git rev-parse --is-inside-work-tree", cwd=_repo_path(repo)
    )
    return run.ok and run.stdout.strip() == "true"


async def init(session_id: str, repo: str | None = None) -> dict:
    """Create a repository, set a local identity, and stage a .gitignore.

    Idempotent: initialising an existing repo re-asserts the identity and
    reports the repo it found rather than failing.
    """
    cwd = _repo_path(repo)
    already = await is_repo(session_id, cwd)
    if not already:
        run = await _run(session_id, "git init -b main", cwd=cwd)
        if not run.ok:
            raise GitError(f"`git init` failed: {run.text}")

    # Repo-local, so nothing is written to a shared global config.
    await _run(session_id, f"git config user.name {shlex.quote(GIT_USER_NAME)}", cwd=cwd)
    await _run(
        session_id, f"git config user.email {shlex.quote(GIT_USER_EMAIL)}", cwd=cwd
    )

    if not already:
        # Heredoc rather than an echo chain so the content is exact.
        await _run(
            session_id,
            "cat > .gitignore <<'LOOM_EOF'\n" + _DEFAULT_IGNORE + "LOOM_EOF",
            cwd=cwd,
        )

    return {"repo": cwd, "created": not already, "branch": await branch(session_id, cwd)}


async def branch(session_id: str, repo: str | None = None) -> str:
    run = await _run(session_id, "git rev-parse --abbrev-ref HEAD", cwd=_repo_path(repo))
    name = run.stdout.strip()
    # A repo with no commits yet reports the symbolic ref, not "HEAD".
    if not run.ok or name == "HEAD":
        run = await _run(
            session_id, "git symbolic-ref --short HEAD", cwd=_repo_path(repo)
        )
        name = run.stdout.strip()
    return name or "main"


#: `git status --porcelain` two-letter codes, mapped to something a UI can
#: label. Index status is the first char, worktree status the second.
_STATUS_LABEL = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "conflicted",
    "?": "untracked",
    "!": "ignored",
}


async def status(session_id: str, repo: str | None = None) -> list[dict]:
    """Parse `git status --porcelain=v1 -z` into structured entries.

    NUL-separated because a path with a space or a newline in it is legal and
    the space-separated form quotes it in a way that then has to be unquoted.
    """
    cwd = _repo_path(repo)
    # `-uall` matters: the default collapses an untracked directory to a single
    # `src/` entry, so a panel listing changed *files* would show a folder and
    # hide everything the agent just wrote inside it.
    run = await _run(session_id, "git status --porcelain=v1 -z -uall", cwd=cwd)
    if not run.ok:
        return []

    entries: list[dict] = []
    records = [r for r in run.stdout.split("\0") if r]
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 3:
            continue
        index_code, tree_code, path = record[0], record[1], record[3:]
        # A rename record is followed by its source path as the next NUL field.
        old_path = None
        if "R" in (index_code, tree_code) and index < len(records):
            old_path = records[index]
            index += 1
        entries.append(
            {
                "path": path,
                "old_path": old_path,
                "index": _STATUS_LABEL.get(index_code, "") if index_code != " " else "",
                "worktree": _STATUS_LABEL.get(tree_code, "") if tree_code != " " else "",
                "staged": index_code not in (" ", "?"),
                "untracked": index_code == "?" or tree_code == "?",
            }
        )
    return entries


async def log(
    session_id: str, repo: str | None = None, limit: int = DEFAULT_LOG_LIMIT
) -> list[dict]:
    """Commit history, newest first. Empty for a repo with no commits."""
    cwd = _repo_path(repo)
    limit = max(1, min(int(limit or DEFAULT_LOG_LIMIT), MAX_LOG_LIMIT))
    run = await _run(
        session_id,
        f"git log --max-count={limit} --pretty=format:{shlex.quote(_LOG_FORMAT)}",
        cwd=cwd,
    )
    if not run.ok:
        # An empty repo is not an error worth reporting as one.
        return []

    commits: list[dict] = []
    for record in run.stdout.split(_RS):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(_FS)
        if len(parts) < 5:
            continue
        commits.append(
            {
                "sha": parts[0],
                "short": parts[1],
                "author": parts[2],
                "date": parts[3],
                "subject": parts[4],
            }
        )
    return commits


async def diff(
    session_id: str,
    repo: str | None = None,
    path: str | None = None,
    staged: bool = False,
) -> str:
    """Unified diff of the working tree (or the index with ``staged``).

    Untracked files are included via ``--no-index`` against /dev/null when a
    specific path is asked for, because "show me the diff" on a brand new file
    returning nothing is the single most confusing thing git does to someone
    reading a diff panel.
    """
    cwd = _repo_path(repo)
    flag = "--staged" if staged else ""
    target = f"-- {shlex.quote(path)}" if path else ""
    run = await _run(session_id, f"git diff {flag} {target}".strip(), cwd=cwd)
    body = run.stdout

    if not body.strip() and path and not staged:
        probe = await _run(
            session_id,
            f"git ls-files --error-unmatch -- {shlex.quote(path)}",
            cwd=cwd,
        )
        if not probe.ok:  # untracked
            untracked = await _run(
                session_id,
                f"git diff --no-index -- /dev/null {shlex.quote(path)}",
                cwd=cwd,
            )
            body = untracked.stdout
    return body


async def commit(
    session_id: str,
    message: str,
    repo: str | None = None,
    paths: list[str] | None = None,
) -> dict:
    """Stage and commit. Returns the new commit, or explains why there is none.

    Committing nothing is not an error the caller should have to special-case,
    so a clean tree comes back as ``{"committed": False, ...}`` rather than an
    exception.
    """
    cwd = _repo_path(repo)
    message = (message or "").strip()
    if not message:
        raise GitError("A commit needs a message.")

    if not await is_repo(session_id, cwd):
        await init(session_id, cwd)

    if paths:
        spec = " ".join(shlex.quote(p) for p in paths)
        staged = await _run(session_id, f"git add -- {spec}", cwd=cwd)
    else:
        staged = await _run(session_id, "git add -A", cwd=cwd)
    if not staged.ok:
        raise GitError(f"`git add` failed: {staged.text}")

    pending = await _run(session_id, "git diff --cached --quiet", cwd=cwd)
    if pending.ok:  # exit 0 from --quiet means no staged changes
        return {
            "committed": False,
            "reason": "Nothing to commit — the working tree is clean.",
            "branch": await branch(session_id, cwd),
        }

    run = await _run(
        session_id, f"git commit -m {shlex.quote(message)}", cwd=cwd
    )
    if not run.ok:
        raise GitError(f"`git commit` failed: {run.text}")

    head = await log(session_id, cwd, limit=1)
    return {
        "committed": True,
        "commit": head[0] if head else None,
        "branch": await branch(session_id, cwd),
        "output": run.text,
    }


async def snapshot(
    session_id: str, repo: str | None = None, limit: int = DEFAULT_LOG_LIMIT
) -> dict:
    """Everything the UI panel needs, in one round trip.

    The panel refreshes after every agent turn, so this is deliberately one
    call rather than three: three sequential shell commands over the E2B
    transport is most of a second.
    """
    cwd = _repo_path(repo)
    if not await is_repo(session_id, cwd):
        return {"repo": False, "path": cwd, "branch": None, "status": [], "log": []}
    return {
        "repo": True,
        "path": cwd,
        "branch": await branch(session_id, cwd),
        "status": await status(session_id, cwd),
        "log": await log(session_id, cwd, limit),
    }
