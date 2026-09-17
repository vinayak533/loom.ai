"""Remote, push and the end-of-turn auto-commit, against a real git binary.

Same fake as `test_git_integration.py`: the sandbox is a temporary directory
and every command runs through a real shell, so `git push` here is a real
push — to a bare repository beside the working tree, which is the only
remote a test can have without a network. What that proves:

  * a URL with a token in it is refused before it reaches `.git/config`;
  * `push` sets upstream and the bare remote really receives the commit;
  * the credential helper is passed on the command line and through the
    command's environment only — nothing token-shaped lands in the repo;
  * the opt-in lives in the repository's config, and `commit_turn` does
    nothing without it, commits and pushes with it, and reports the
    push failing without taking the commit back.

Run: python scripts/test_git_push.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("E2B_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

import app.autocommit as autocommit  # noqa: E402
import app.gitmsg as gitmsg  # noqa: E402
import app.tools.git as git  # noqa: E402
import app.tools.sandbox as sb  # noqa: E402
from app.emitter import Emitter  # noqa: E402

results: list[bool] = []


def check(label: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))


class Result:
    def __init__(self, stdout: str, stderr: str, exit_code: int) -> None:
        self.stdout, self.stderr, self.exit_code = stdout, stderr, exit_code


class LocalCommands:
    """Runs the command for real, in `root`, through a POSIX shell.

    Records every command and the environment it was given, so the test can
    assert that the token travelled by `envs` and not by any other route.
    """

    def __init__(self, root: str) -> None:
        self.root = root
        self.calls: list[tuple[str, dict]] = []

    async def run(self, command: str, cwd: str | None = None, timeout: int = 60,
                  envs: dict | None = None, **kw):
        self.calls.append((command, dict(envs or {})))
        real_cwd = self.root
        if cwd and cwd != git.WORKDIR:
            rel = cwd[len(git.WORKDIR):].lstrip("/")
            real_cwd = os.path.join(self.root, rel) if rel else self.root
        proc = await asyncio.create_subprocess_exec(
            SHELL, "-c", command,
            cwd=real_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **(envs or {}), "GIT_CONFIG_NOSYSTEM": "1",
                 "HOME": self.root, "GIT_TERMINAL_PROMPT": "0"},
        )
        out, err = await proc.communicate()
        return Result(out.decode("utf-8", "replace"), err.decode("utf-8", "replace"),
                      proc.returncode or 0)


class LocalSandbox:
    def __init__(self, root: str) -> None:
        self.sandbox_id = "local"
        self.commands = LocalCommands(root)


def install(root: str) -> LocalSandbox:
    sandbox = LocalSandbox(root)

    async def fake_get(session_id: str):
        return sandbox

    sb.sandbox_manager.get = fake_get  # type: ignore[method-assign]
    sb.sandbox_manager.sandbox_id_for = lambda sid: "local"  # type: ignore[method-assign]
    return sandbox


def write(root: str, rel: str, text: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


async def drain(emitter: Emitter) -> list[dict]:
    # `Emitter.emit` enqueues via `call_soon_threadsafe`; yield once so
    # those callbacks run before the queue is read.
    await asyncio.sleep(0)
    out = []
    while True:
        try:
            item = emitter.queue.get_nowait()
        except asyncio.QueueEmpty:
            return out
        if item is not None:
            out.append(item)


SHELL = shutil.which("bash") or shutil.which("sh")
SID = "sess-push"


async def main() -> int:
    if SHELL is None or shutil.which("git") is None:
        print("SKIP: needs `git` and a POSIX shell on PATH.")
        return 0

    root = tempfile.mkdtemp(prefix="loom-push-")
    bare = tempfile.mkdtemp(prefix="loom-bare-")
    sandbox = install(root)
    try:
        # A bare repository is the remote. Its path is what `origin` points at.
        proc = await asyncio.create_subprocess_exec(
            "git", "init", "--bare", "-b", "main", bare,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        bare_url = bare.replace("\\", "/")

        print("1. the remote URL is validated before git sees it")
        for bad, why in [
            ("https://user:ghp_secret@github.com/a/b", "token in the URL"),
            ("git@github.com:a/b.git", "ssh"),
            ("http://github.com/a/b", "plain http"),
            ("https://github.com", "no repository path"),
            ("https://github.com/a/b; rm -rf /", "shell characters"),
            ("", "empty"),
        ]:
            try:
                git.validate_remote_url(bad)
                check(f"refuses {why}", False, bad)
            except git.GitError as exc:
                check(f"refuses {why}", True, str(exc)[:60])
        check("accepts a plain https repository URL",
              git.validate_remote_url("https://github.com/vinayak533/test")
              == "https://github.com/vinayak533/test")
        check("tolerates a trailing .git",
              git.validate_remote_url("https://github.com/a/b.git").endswith(".git"))

        print("\n2. remote and opt-in live in the repository")
        check("no repo: snapshot has no remote and no opt-in",
              (await git.snapshot(SID))["remote"] is None
              and (await git.snapshot(SID))["auto_push"] is False)
        git._ALLOW_FILE_REMOTES = True  # the test's bare repo is a path
        info = await git.set_remote(SID, bare_url)
        check("set_remote initialises the repo and records origin",
              await git.is_repo(SID) and info["remote"] == bare_url, str(info))
        check("remote_url reads it back", await git.remote_url(SID) == bare_url)
        again = await git.set_remote(SID, bare_url)
        check("setting the same URL twice is one remote", again["replaced"] == bare_url)
        check("auto_push is off by default", await git.auto_push(SID) is False)
        await git.set_auto_push(SID, True)
        check("set_auto_push records the opt-in", await git.auto_push(SID) is True)
        cfg = open(os.path.join(root, ".git", "config"), encoding="utf-8").read()
        check("the opt-in is in .git/config under its own key", "autopush = true" in cfg)
        snap = await git.snapshot(SID)
        check("snapshot carries both", snap["remote"] == bare_url and snap["auto_push"] is True)

        print("\n3. push with nothing to push is a reason, not an error")
        outcome = await git.push(SID)
        check("no commits -> pushed False with a reason",
              outcome["pushed"] is False and "no commits" in outcome["reason"].lower(),
              str(outcome))

        print("\n4. a real push to the bare remote")
        write(root, "app.py", "print('hi')\n")
        await git.commit(SID, "Add the entry point")
        sandbox.commands.calls.clear()
        outcome = await git.push(SID, token="tok_test_123")
        check("pushed", outcome.get("pushed") is True, str(outcome)[:160])
        check("upstream branch is main", outcome.get("branch") == "main")
        proc = await asyncio.create_subprocess_exec(
            "git", "--git-dir", bare, "log", "--oneline", "-1",
            stdout=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        check("the bare remote received the commit",
              b"Add the entry point" in out, out.decode().strip())

        push_calls = [(c, e) for c, e in sandbox.commands.calls if "git" in c and " push " in c]
        check("exactly one push command ran", len(push_calls) == 1, str(len(push_calls)))
        command, envs = push_calls[0]
        check("the token travelled in the command's environment only",
              envs.get(git._TOKEN_ENV) == "tok_test_123" and "tok_test_123" not in command)
        check("prompts are disabled so a bad credential fails fast",
              envs.get("GIT_TERMINAL_PROMPT") == "0")
        check("the helper is one-shot on the command line",
              "credential.helper=" in command and "-c credential.helper= " in command)
        cfg = open(os.path.join(root, ".git", "config"), encoding="utf-8").read()
        check("nothing token-shaped landed in .git/config",
              "tok_test_123" not in cfg and "credential" not in cfg)

        print("\n5. the end-of-turn auto-commit")

        async def fake_suggest(session_id, diff, user_id=None):
            return "Add a helper module\n\nWritten by the fake model."

        gitmsg.suggest = fake_suggest  # type: ignore[assignment]
        autocommit.gitmsg.suggest = fake_suggest  # type: ignore[assignment]

        emitter = Emitter()
        check("a clean tree commits nothing",
              await autocommit.commit_turn(SID, None, emitter) is None)
        write(root, "src/util.py", "X = 1\n")
        payload = await autocommit.commit_turn(SID, None, emitter)
        check("a dirty tree is committed", payload is not None and payload["sha"], str(payload)[:120])
        check("with the model's subject", payload and payload["subject"] == "Add a helper module")
        check("and pushed to the remote", payload and payload["pushed"] is True and not payload["error"])
        check("files are absolute sandbox paths",
              payload and payload["files"] == [f"{git.WORKDIR}/src/util.py"], str(payload and payload["files"]))
        frames = await drain(emitter)
        kinds = [f["type"] for f in frames]
        check("emits turn_commit then git_state", kinds[:2] == ["turn_commit", "git_state"], str(kinds))
        proc = await asyncio.create_subprocess_exec(
            "git", "--git-dir", bare, "log", "--oneline", "-1", stdout=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        check("the remote has the auto commit", b"Add a helper module" in out, out.decode().strip())

        print("\n6. opted out, nothing happens")
        await git.set_auto_push(SID, False)
        write(root, "ignored.txt", "x\n")
        check("commit_turn returns None when the opt-in is off",
              await autocommit.commit_turn(SID, None, Emitter()) is None)
        check("and the file stays uncommitted",
              any(e["path"] == "ignored.txt" for e in await git.status(SID)))

        print("\n7. a failed push keeps the commit and reports the error")
        await git.set_auto_push(SID, True)
        await git.set_remote(SID, bare_url)
        # Break the remote after the commit is made: point origin somewhere
        # that does not exist, so the push fails for a real git reason.
        gone = os.path.join(tempfile.gettempdir(), "loom-no-such-remote").replace("\\", "/")
        await sandbox.commands.run(f"git remote set-url origin {gone}", cwd=git.WORKDIR)
        emitter = Emitter()
        payload = await autocommit.commit_turn(SID, None, emitter)
        check("the commit still happened", payload is not None and payload["sha"], str(payload)[:100])
        check("pushed is False with git's reason",
              payload and payload["pushed"] is False and "push" in payload["error"].lower(),
              str(payload and payload["error"])[:100])
        check("the tree is clean afterwards", not await git.status(SID))

        print("\n8. no model -> a factual fallback message")
        async def no_model(session_id, diff, user_id=None):
            return ""
        autocommit.gitmsg.suggest = no_model  # type: ignore[assignment]
        await sandbox.commands.run(f"git remote set-url origin {bare_url}", cwd=git.WORKDIR)
        write(root, "a.txt", "1\n")
        write(root, "b.txt", "2\n")
        payload = await autocommit.commit_turn(SID, None, Emitter())
        check("subject names the file count", payload and payload["subject"] == "Update 2 files",
              str(payload and payload["subject"]))
        check("fallback for one file names it", autocommit.fallback_message(["src/x.py"]) == "Update x.py")

    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(bare, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
