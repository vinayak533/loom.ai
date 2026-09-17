"""The end-of-turn commit and push for Code sessions that asked for it.

Claude Code's workflow, transplanted: when the agent finishes a turn that
changed files, the change is committed with a generated message and pushed,
and the user is shown the commit rather than having to go and look for it.

Three decisions worth knowing before changing anything here:

* **Opt-in, per repository, off by default.** A turn that says "just try
  this" must not end up on a remote, and there is no way to tell that turn
  from "build the feature" by reading it. So nothing is committed unless the
  repository has been told to (`git.set_auto_push`), and that switch lives in
  the repository's own config beside the remote it applies to. The explicit
  paths — the `git` tool's `commit` and `push`, the panel's buttons — are
  unchanged and remain the default.

* **The message costs one cheap model call.** `gitmsg.suggest` writes it
  from the staged diff, charged to the user like the panel's "Write one"
  button. When no model is available the fallback is a plain message built
  from the file list, because a commit with a dull subject is better than a
  turn's work left uncommitted over a wording problem.

* **It runs inside the turn, before `agent_done`.** The composer re-arms on
  that frame and the socket's "still working" guard is the run task itself;
  finishing the commit first means a message sent the instant the answer
  lands cannot race the push, and the transcript ends with the commit card
  rather than the card arriving under a turn the user believes is over.
"""

from __future__ import annotations

import logging
import posixpath

from app import events as ev
from app import gitmsg
from app.config import get_settings
from app.emitter import Emitter
from app.tools import git
from app.tools.impl import _refresh_tree_later
from app.tools.sandbox import SandboxUnavailable, sandbox_manager

log = logging.getLogger(__name__)

#: How many paths the fallback message lists before saying "and N more".
_MAX_LISTED = 6


def fallback_message(paths: list[str]) -> str:
    """A commit message from nothing but the changed paths.

    Used when no model can write one. Deliberately factual rather than
    inventive — "Update 3 files" is a worse subject than a model's, but it is
    never a *wrong* one, and a commit whose subject claims something the diff
    does not support is the failure this fallback must not introduce.
    """
    names = [posixpath.basename(p) or p for p in paths]
    if not names:
        return "Update project"
    if len(names) == 1:
        return f"Update {names[0]}"
    listed = ", ".join(names[:_MAX_LISTED])
    more = len(names) - _MAX_LISTED
    subject = f"Update {len(names)} files"
    body = listed + (f", and {more} more" if more > 0 else "")
    return f"{subject}\n\n{body}"


async def commit_turn(
    session_id: str,
    user_id: str | None,
    emitter: Emitter | None,
) -> dict | None:
    """Commit and push what the turn left in the working tree, if opted in.

    Returns the `turn_commit` payload that was emitted, or ``None`` when
    nothing happened — no sandbox, no repository, not opted in, or a clean
    tree. Never raises: a failure here is reported on the socket as a
    `turn_commit` carrying `error`, or logged, and the turn ends normally
    either way. The user's answer is already on screen and must not be
    retracted over a git problem.
    """
    # Only a session that already has a sandbox can have a repository. Asking
    # `sandbox_manager.get` here would *create* one for a Code turn that never
    # touched the filesystem, which is a slot burned for nothing.
    if sandbox_manager.sandbox_id_for(session_id) is None:
        return None
    try:
        if not await git.is_repo(session_id):
            return None
        if not await git.auto_push(session_id):
            return None
        entries = await git.status(session_id)
        if not entries:
            return None

        root = git.repo_root()
        files = [posixpath.join(root, e["path"]) for e in entries]

        # Stage everything first so the message is written from exactly the
        # diff that will be recorded, then commit from the index.
        await git.stage(session_id, [e["path"] for e in entries])
        diff = await git.staged_diff(session_id)
        message = ""
        try:
            message = await gitmsg.suggest(session_id, diff, user_id=user_id)
        except Exception:  # noqa: BLE001 - the fallback below is the plan
            log.debug("Auto-commit message generation failed", exc_info=True)
        if not message.strip():
            message = fallback_message([e["path"] for e in entries])

        result = await git.commit(session_id, message, use_index=True)
        if not result.get("committed"):
            return None
        commit = result.get("commit") or {}

        pushed = False
        error = ""
        remote = await git.remote_url(session_id)
        if remote:
            try:
                outcome = await git.push(
                    session_id, token=get_settings().git_push_token or None
                )
                pushed = bool(outcome.get("pushed"))
                if not pushed:
                    error = str(outcome.get("reason") or "Push did not happen.")
            except git.GitError as exc:
                error = str(exc)
        else:
            error = "No remote is configured, so the commit was not pushed."

        payload = ev.turn_commit(
            sha=commit.get("sha") or "",
            short=commit.get("short") or "",
            subject=commit.get("subject") or message.splitlines()[0],
            branch=result.get("branch") or "",
            files=files,
            pushed=pushed,
            remote=remote,
            error=error,
        )
        if emitter is not None:
            emitter.emit(payload)
            try:
                snap = await git.snapshot(session_id)
                emitter.emit(
                    ev.git_state(
                        repo=snap["repo"],
                        branch=snap["branch"],
                        status=snap["status"],
                        log=snap["log"],
                        path=snap["path"],
                        remote=snap.get("remote"),
                        auto_push=bool(snap.get("auto_push")),
                    )
                )
            except Exception:  # noqa: BLE001 - the panel going stale is not fatal
                log.debug("git_state after auto-commit failed", exc_info=True)
            # The tree is what the user is shown opened to the committed files.
            try:
                sandbox = await sandbox_manager.get(session_id)
                _refresh_tree_later(sandbox, session_id, emitter)
            except SandboxUnavailable:
                pass
        return payload
    except SandboxUnavailable:
        return None
    except git.GitError as exc:
        log.warning("Auto-commit failed for session %s: %s", session_id, exc)
        if emitter is not None:
            emitter.emit(
                ev.turn_commit(
                    sha="", short="", subject="", branch="", files=[],
                    pushed=False, remote=None, error=f"Auto-commit failed: {exc}",
                )
            )
        return None
    except Exception:  # noqa: BLE001 - never let git end a finished turn badly
        log.exception("Auto-commit raised for session %s", session_id)
        return None
