"""Git integration, exercised against a real git binary.

The sandbox is faked, but the commands are not: the fake runs each one with a
real shell in a real temporary directory, so `git status --porcelain`, the
`--pretty` log format and the diff output are parsed from git's genuine output
rather than from a fixture someone typed out. That is the point — the parsing
is the part that breaks, and a fixture would only ever prove the fixture.

Run: python scripts/test_git_integration.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("E2B_API_KEY", "test-key")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

import app.tools.git as git  # noqa: E402
import app.tools.sandbox as sb  # noqa: E402

results: list[bool] = []


def check(label: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))


class Result:
    def __init__(self, stdout: str, stderr: str, exit_code: int) -> None:
        self.stdout, self.stderr, self.exit_code = stdout, stderr, exit_code


class LocalCommands:
    """Runs the command for real, in `root`, through a POSIX shell."""

    def __init__(self, root: str) -> None:
        self.root = root

    async def run(self, command: str, cwd: str | None = None, timeout: int = 60, **kw):
        # The module addresses everything under the sandbox WORKDIR; map that
        # onto the temp directory standing in for it.
        real_cwd = self.root
        if cwd and cwd != git.WORKDIR:
            rel = cwd[len(git.WORKDIR):].lstrip("/")
            real_cwd = os.path.join(self.root, rel) if rel else self.root

        proc = await asyncio.create_subprocess_exec(
            SHELL, "-c", command,
            cwd=real_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": self.root,
                 "GIT_TERMINAL_PROMPT": "0"},
        )
        out, err = await proc.communicate()
        return Result(out.decode("utf-8", "replace"), err.decode("utf-8", "replace"),
                      proc.returncode or 0)


class LocalSandbox:
    def __init__(self, root: str) -> None:
        self.sandbox_id = "local"
        self.commands = LocalCommands(root)


def install(root: str) -> None:
    sandbox = LocalSandbox(root)

    async def fake_get(session_id: str):
        return sandbox

    sb.sandbox_manager.get = fake_get  # type: ignore[method-assign]


def write(root: str, rel: str, text: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


SHELL = shutil.which("bash") or shutil.which("sh")
SID = "sess-git"


async def main() -> int:
    if SHELL is None or shutil.which("git") is None:
        print("SKIP: needs `git` and a POSIX shell on PATH.")
        return 0

    root = tempfile.mkdtemp(prefix="loom-git-")
    install(root)
    try:
        print("1. init")
        check("a fresh directory is not a repo yet", not await git.is_repo(SID))
        info = await git.init(SID)
        check("init creates a repository", info["created"] is True, str(info))
        check("it is a repo now", await git.is_repo(SID))
        check("on a named branch", bool(info["branch"]), info["branch"])
        check("a .gitignore was written", os.path.exists(os.path.join(root, ".gitignore")))
        again = await git.init(SID)
        check("init is idempotent", again["created"] is False, str(again))

        print("\n2. status parses real porcelain output")
        write(root, "app.py", "print('hello')\n")
        write(root, "src/util.py", "X = 1\n")
        write(root, "a file with spaces.txt", "spaces\n")
        entries = await git.status(SID)
        paths = {e["path"] for e in entries}
        check("untracked files are listed", {"app.py", "src/util.py"} <= paths, str(sorted(paths)))
        check("a path containing spaces survives NUL parsing",
              "a file with spaces.txt" in paths, str(sorted(paths)))
        check("they are marked untracked",
              all(e["untracked"] for e in entries if e["path"] == "app.py"))
        check("node_modules would be ignored",
              "node_modules/" in open(os.path.join(root, ".gitignore")).read())

        print("\n3. commit")
        result = await git.commit(SID, "Add the entry point and a helper")
        check("the commit is made", result["committed"] is True, str(result)[:120])
        check("it reports the new commit", bool((result.get("commit") or {}).get("short")),
              str(result.get("commit")))
        check("subject round-trips exactly",
              (result.get("commit") or {}).get("subject") == "Add the entry point and a helper",
              str((result.get("commit") or {}).get("subject")))
        check("the tree is clean afterwards", not await git.status(SID),
              str(await git.status(SID)))

        empty = await git.commit(SID, "Nothing changed")
        check("committing a clean tree is reported, not raised",
              empty["committed"] is False, str(empty))
        check("and says why", "clean" in empty["reason"].lower(), empty["reason"])

        print("\n4. diff")
        write(root, "app.py", "print('hello world')\n")
        text = await git.diff(SID)
        check("a working-tree change produces a diff", "hello world" in text, text[:80])
        check("the diff names the file", "app.py" in text)
        scoped = await git.diff(SID, path="app.py")
        check("a path-scoped diff works", "hello world" in scoped)

        write(root, "brand_new.py", "NEW = True\n")
        untracked_diff = await git.diff(SID, path="brand_new.py")
        check("an untracked file still shows a diff", "NEW = True" in untracked_diff,
              untracked_diff[:80] or "(empty)")

        print("\n5. log")
        await git.commit(SID, "Broaden the greeting")
        commits = await git.log(SID)
        check("history is returned newest first",
              [c["subject"] for c in commits][:2]
              == ["Broaden the greeting", "Add the entry point and a helper"],
              str([c["subject"] for c in commits]))
        check("each commit carries sha/short/author/date",
              all(c["sha"] and c["short"] and c["author"] and c["date"] for c in commits))
        check("the limit is honoured", len(await git.log(SID, limit=1)) == 1)

        print("\n6. commit with an explicit path list")
        write(root, "one.txt", "1\n")
        write(root, "two.txt", "2\n")
        partial = await git.commit(SID, "Add only the first file", paths=["one.txt"])
        check("the scoped commit succeeded", partial["committed"] is True)
        remaining = {e["path"] for e in await git.status(SID)}
        check("the unlisted file is still uncommitted", "two.txt" in remaining,
              str(sorted(remaining)))
        check("the listed file is not", "one.txt" not in remaining)

        print("\n7. a message that would break a shell")
        nasty = 'Fix "quoting"; $(rm -rf /) & `backticks` and \'single\''
        write(root, "three.txt", "3\n")
        risky = await git.commit(SID, nasty)
        check("it commits without the shell interpreting it", risky["committed"] is True)
        check("and the message is stored verbatim",
              (risky.get("commit") or {}).get("subject") == nasty,
              repr((risky.get("commit") or {}).get("subject")))
        check("nothing was destroyed", os.path.exists(os.path.join(root, "app.py")))

        print("\n8. snapshot: one call for the whole panel")
        write(root, "dirty.txt", "x\n")
        snap = await git.snapshot(SID)
        check("reports repo=True", snap["repo"] is True)
        check("carries the branch", bool(snap["branch"]), str(snap["branch"]))
        check("carries the working-tree status", any(e["path"] == "dirty.txt"
                                                     for e in snap["status"]))
        check("carries the history", len(snap["log"]) >= 3, str(len(snap["log"])))

        print("\n9. an empty message is refused")
        try:
            await git.commit(SID, "   ")
            check("a blank message raises GitError", False)
        except git.GitError as exc:
            check("a blank message raises GitError", True, str(exc))

        print("\n10. snapshot on a directory with no repo")
        other = tempfile.mkdtemp(prefix="loom-nogit-")
        install(other)
        try:
            snap = await git.snapshot(SID)
            check("reports repo=False rather than raising", snap["repo"] is False, str(snap))
            check("with empty history", snap["log"] == [] and snap["status"] == [])
        finally:
            shutil.rmtree(other, ignore_errors=True)

    finally:
        shutil.rmtree(root, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
