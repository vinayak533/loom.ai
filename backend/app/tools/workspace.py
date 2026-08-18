"""Direct, user-driven operations on a session's sandbox.

Everything here is invoked by the *person* rather than by the agent: opening a
file in the inline editor, saving an edit back, importing a folder from their
own machine, renaming and deleting things in the tree, and downloading the
project as a zip. The agent's own filesystem access stays in `app.tools.impl`,
which additionally emits trace events and feeds tool results back into the
model — none of which applies to a user clicking Save.

These functions take the same session-scoped sandbox as the agent, so an edit
made in the browser and an edit made by the agent land in exactly one place.
"""

from __future__ import annotations

import difflib
import io
import logging
import posixpath
import tarfile
import time

from app.tools.sandbox import WORKDIR, sandbox_manager

log = logging.getLogger(__name__)


class WorkspaceError(RuntimeError):
    """A user-facing failure — surfaced as an HTTP error, not a tool result."""


#: Refuse to open or save anything larger than this in the inline editor. The
#: content travels as JSON through the browser; a minified bundle or a binary
#: blob is not something the editor can usefully show anyway.
MAX_EDIT_BYTES = 2_000_000

#: Cap on the exported archive, so a project that accidentally vendored a
#: toolchain cannot pull hundreds of megabytes through the backend.
MAX_EXPORT_BYTES = 80_000_000

#: Never packaged. These are reproducible from the manifest, and including them
#: turns a 40 KB project into a 400 MB download.
EXPORT_EXCLUDE_DIRS = (
    "node_modules",
    ".git",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "target",
    ".gradle",
    ".idea",
    ".vscode-server",
)

#: Files that belong to the sandbox image rather than to the project. The
#: working directory *is* the sandbox user's home, so a plain walk picks up the
#: shell dotfiles E2B ships and packages them as if the agent had written them.
#: Named individually rather than excluding all dotfiles, because `.env`,
#: `.gitignore` and `.npmrc` are genuinely part of what was built.
EXPORT_EXCLUDE_FILES = (
    ".bashrc",
    ".bash_logout",
    ".bash_history",
    ".profile",
    ".sudo_as_admin_successful",
    ".viminfo",
    ".wget-hsts",
    ".python_history",
    ".node_repl_history",
)


#: The same list, under the name the import path uses. A folder the user opens
#: from their machine is filtered in the *browser* — the point of excluding
#: `node_modules` is not to avoid writing it, it is to avoid uploading it — so
#: `frontend/lib/folder.ts` mirrors this list. It is enforced here too, because
#: the client's copy can drift and a request can be hand-made.
IMPORT_EXCLUDE_DIRS = EXPORT_EXCLUDE_DIRS

#: Per-file ceiling on an imported folder. Anything larger is almost certainly
#: an asset, a lockfile-sized artefact or a checked-in binary; it is skipped and
#: named in the summary rather than silently dropped.
MAX_IMPORT_FILE_BYTES = 2_000_000

#: Ceiling on one import request. The client uploads a large folder as several
#: batches, so this bounds a single round trip rather than the whole folder.
MAX_IMPORT_BATCH_BYTES = 48_000_000

#: Total files one folder may put into the sandbox. A tree bigger than this is
#: not a project, it is a disk.
MAX_IMPORT_FILES = 4000


def _abs(path: str) -> str:
    """Resolve a client-supplied path, confined to the working directory.

    The confinement is the point: `path` arrives from the browser, and
    `..` segments would otherwise reach the sandbox's own home directory and
    E2B's runtime alongside it.
    """
    raw = (path or "").strip()
    if not raw:
        raise WorkspaceError("No path given.")
    joined = raw if raw.startswith("/") else posixpath.join(WORKDIR, raw)
    resolved = posixpath.normpath(joined)
    if resolved != WORKDIR and not resolved.startswith(WORKDIR + "/"):
        raise WorkspaceError(f"`{path}` is outside the workspace.")
    return resolved


def _unified_diff(old: str, new: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a{path}",
            tofile=f"b{path}",
            n=3,
        )
    )


# --- reading ---------------------------------------------------------------


async def read_text(session_id: str, path: str) -> dict:
    """Read one file for the inline editor."""
    resolved = _abs(path)
    sandbox = await sandbox_manager.get(session_id)
    try:
        content = await sandbox.files.read(resolved)
    except Exception as exc:  # noqa: BLE001 - "missing" is the common case
        raise WorkspaceError(f"Could not read `{resolved}`: {exc}") from exc

    if not isinstance(content, str):
        content = str(content)
    if len(content.encode("utf-8", "ignore")) > MAX_EDIT_BYTES:
        raise WorkspaceError(
            f"`{resolved}` is too large to open in the editor "
            f"({MAX_EDIT_BYTES // 1000} KB limit)."
        )
    if "\x00" in content:
        raise WorkspaceError(f"`{resolved}` looks like a binary file.")

    return {"path": resolved, "content": content}


# --- writing ---------------------------------------------------------------


async def write_text(session_id: str, path: str, content: str) -> dict:
    """Save an inline edit back to the sandbox.

    Returns the same shape as the agent's `file_changed` event so the client can
    fold a manual save into the identical state it already keeps for agent
    edits — one "changed files" list, one diff viewer, one flash animation.
    """
    resolved = _abs(path)
    if len(content.encode("utf-8", "ignore")) > MAX_EDIT_BYTES:
        raise WorkspaceError("That file is too large to save from the editor.")

    sandbox = await sandbox_manager.get(session_id)
    try:
        previous = await sandbox.files.read(resolved)
        if not isinstance(previous, str):
            previous = str(previous)
    except Exception:  # noqa: BLE001 - a new file is a legitimate save
        previous = None

    try:
        await sandbox.files.write(resolved, content)
    except Exception as exc:  # noqa: BLE001
        raise WorkspaceError(f"Could not write `{resolved}`: {exc}") from exc

    return {
        "path": resolved,
        "content": content,
        "diff": _unified_diff(previous or "", content, resolved),
        "change": "modified" if previous is not None else "created",
        "ts": int(time.time() * 1000),
    }


# --- importing a folder ----------------------------------------------------


def _relative(path: str) -> str:
    """Normalise a browser-supplied *relative* path, or reject it.

    Folder imports arrive as `webkitRelativePath`-style strings — `src/app.ts`,
    never absolute, never with `..`. Anything else is refused rather than
    coerced: this string decides where bytes land in the sandbox.
    """
    raw = (path or "").strip().replace("\\", "/").lstrip("/")
    if not raw:
        raise WorkspaceError("A file arrived with no path.")
    normalised = posixpath.normpath(raw)
    if normalised.startswith("..") or normalised.startswith("/") or normalised == ".":
        raise WorkspaceError(f"`{path}` is not a valid path inside the project.")
    parts = normalised.split("/")
    if any(part in IMPORT_EXCLUDE_DIRS for part in parts[:-1]):
        raise _Excluded(normalised)
    return normalised


class _Excluded(Exception):
    """A path the client should have filtered out. Skipped, not fatal."""


async def import_files(
    session_id: str, files: list[tuple[str, bytes]], dest: str = ""
) -> dict:
    """Write a batch of uploaded files into the sandbox, keeping the structure.

    The batch travels as one tar archive rather than as N writes. E2B's
    filesystem API is one HTTP round trip per file, and a modest React project
    is several hundred files — the difference between "a moment" and "a minute
    and a half" is entirely this. The archive is unpacked inside the sandbox and
    both the archive and the extraction are cleaned up afterwards.
    """
    if not files:
        return {"written": 0, "bytes": 0, "skipped": []}
    if len(files) > MAX_IMPORT_FILES:
        raise WorkspaceError(
            f"That folder has more than {MAX_IMPORT_FILES} files. Open a "
            "subfolder, or let the agent clone it instead."
        )

    root = _abs(dest) if dest else WORKDIR

    buffer = io.BytesIO()
    skipped: list[dict] = []
    written = 0
    total = 0

    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for raw_path, data in files:
            try:
                rel = _relative(raw_path)
            except _Excluded as exc:
                skipped.append({"path": str(exc), "reason": "excluded"})
                continue
            except WorkspaceError as exc:
                skipped.append({"path": raw_path, "reason": str(exc)})
                continue

            if len(data) > MAX_IMPORT_FILE_BYTES:
                skipped.append({"path": rel, "reason": "too large"})
                continue
            if total + len(data) > MAX_IMPORT_BATCH_BYTES:
                skipped.append({"path": rel, "reason": "batch limit"})
                continue

            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            info.mtime = int(time.time())
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
            written += 1
            total += len(data)

    if not written:
        return {"written": 0, "bytes": 0, "skipped": skipped}

    sandbox = await sandbox_manager.get(session_id)
    stamp = int(time.time() * 1000)
    archive_path = f"/tmp/_atlas_import_{stamp}.tar"

    try:
        await sandbox.files.write(archive_path, buffer.getvalue())
    except Exception as exc:  # noqa: BLE001
        raise WorkspaceError(f"Could not upload the files: {exc}") from exc

    try:
        result = await sandbox.commands.run(
            f"mkdir -p {_shell_quote(root)} && "
            f"tar -xf {archive_path} -C {_shell_quote(root)}",
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        raise WorkspaceError(f"Could not unpack the files: {exc}") from exc
    finally:
        try:
            await sandbox.commands.run(f"rm -f {archive_path}", timeout=15)
        except Exception:  # noqa: BLE001
            log.debug("Import cleanup failed", exc_info=True)

    if result.exit_code != 0:
        raise WorkspaceError(
            f"Could not unpack the files: {result.stderr or 'unknown error'}"
        )

    return {"written": written, "bytes": total, "skipped": skipped, "root": root}


# --- tree operations -------------------------------------------------------


async def list_tree(session_id: str) -> dict:
    """The sandbox tree, on demand.

    The agent's own runs push `file_tree` events as a side effect of working.
    This is the same walk for the times nothing is running — after a folder
    import, after a rename — so the sidebar does not have to wait for the next
    agent turn to tell the truth.
    """
    # Imported from the agent's tools rather than reimplemented: one walker, one
    # set of rules about what the sidebar shows.
    from app.tools.impl import _list_tree

    sandbox = await sandbox_manager.get(session_id)
    nodes = await _list_tree(sandbox, WORKDIR)
    return {"path": WORKDIR, "nodes": nodes or []}


async def create_entry(session_id: str, path: str, kind: str) -> dict:
    """Create an empty file or a directory."""
    resolved = _abs(path)
    sandbox = await sandbox_manager.get(session_id)

    try:
        if await sandbox.files.exists(resolved):
            raise WorkspaceError(f"`{posixpath.basename(resolved)}` already exists.")
    except WorkspaceError:
        raise
    except Exception:  # noqa: BLE001 - a failing existence check is not fatal
        log.debug("exists() failed for %s", resolved, exc_info=True)

    try:
        if kind == "dir":
            await sandbox.files.make_dir(resolved)
        else:
            parent = posixpath.dirname(resolved)
            if parent and parent != WORKDIR:
                await sandbox.files.make_dir(parent)
            await sandbox.files.write(resolved, "")
    except Exception as exc:  # noqa: BLE001
        raise WorkspaceError(f"Could not create `{resolved}`: {exc}") from exc

    return {"path": resolved, "type": kind}


async def rename_entry(session_id: str, path: str, name: str) -> dict:
    """Rename a file or folder in place. `name` is a bare name, not a path."""
    resolved = _abs(path)
    clean = (name or "").strip().strip("/")
    if not clean or "/" in clean or clean in {".", ".."}:
        raise WorkspaceError("A name cannot be empty or contain a slash.")
    if resolved == WORKDIR:
        raise WorkspaceError("The workspace root cannot be renamed.")

    target = posixpath.join(posixpath.dirname(resolved), clean)
    sandbox = await sandbox_manager.get(session_id)
    try:
        await sandbox.files.rename(resolved, target)
    except Exception as exc:  # noqa: BLE001
        raise WorkspaceError(f"Could not rename `{resolved}`: {exc}") from exc

    return {"path": target, "previous_path": resolved}


async def delete_entry(session_id: str, path: str) -> dict:
    """Delete a file or folder. Recursive for folders — as `rm -r` is."""
    resolved = _abs(path)
    if resolved == WORKDIR:
        raise WorkspaceError("The workspace root cannot be deleted.")

    sandbox = await sandbox_manager.get(session_id)
    try:
        await sandbox.files.remove(resolved)
    except Exception as exc:  # noqa: BLE001
        raise WorkspaceError(f"Could not delete `{resolved}`: {exc}") from exc

    return {"path": resolved}


# --- export ----------------------------------------------------------------

#: Written into the sandbox and run there. Zipping in-sandbox rather than
#: pulling the tree over the wire file-by-file is the difference between one
#: round trip and one per file — a small React project is already several
#: hundred.
_ZIP_SCRIPT = '''
import json, os, sys, zipfile

root, out, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
excluded = set(json.loads(sys.argv[4]))
excluded_files = set(json.loads(sys.argv[5]))

total = 0
count = 0
skipped = []
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in excluded]
        for name in files:
            # Only at the top level: a project may legitimately ship a file
            # with one of these names further down.
            if base == root and name in excluded_files:
                continue
            full = os.path.join(base, name)
            if os.path.islink(full):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if total + size > limit:
                skipped.append(os.path.relpath(full, root))
                continue
            try:
                zf.write(full, os.path.relpath(full, root))
            except OSError:
                skipped.append(os.path.relpath(full, root))
                continue
            total += size
            count += 1

print(json.dumps({"files": count, "bytes": total, "skipped": skipped[:20]}))
'''


async def export_zip(session_id: str) -> tuple[bytes, dict]:
    """Package the session's project. Returns `(zip_bytes, summary)`."""
    import json

    sandbox = await sandbox_manager.get(session_id)
    stamp = int(time.time())
    script_path = f"/tmp/_atlas_export_{stamp}.py"
    archive_path = f"/tmp/_atlas_export_{stamp}.zip"

    try:
        await sandbox.files.write(script_path, _ZIP_SCRIPT)
        result = await sandbox.commands.run(
            "python3 {script} {root} {out} {limit} {dirs} {files}".format(
                script=script_path,
                root=WORKDIR,
                out=archive_path,
                limit=MAX_EXPORT_BYTES,
                dirs=_shell_quote(json.dumps(list(EXPORT_EXCLUDE_DIRS))),
                files=_shell_quote(json.dumps(list(EXPORT_EXCLUDE_FILES))),
            ),
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        raise WorkspaceError(f"Could not package the project: {exc}") from exc

    if result.exit_code != 0:
        raise WorkspaceError(
            f"Could not package the project: {result.stderr or 'unknown error'}"
        )

    try:
        summary = json.loads((result.stdout or "{}").strip().splitlines()[-1])
    except (ValueError, IndexError):
        summary = {}

    if not summary.get("files"):
        raise WorkspaceError(
            "There is nothing to export yet — the sandbox workspace is empty."
        )

    try:
        data = await sandbox.files.read(archive_path, format="bytes")
    except Exception as exc:  # noqa: BLE001
        raise WorkspaceError(f"Could not read the archive: {exc}") from exc
    finally:
        # Best effort: the sandbox is ephemeral, but a user who exports
        # repeatedly should not be filling its disk with old archives.
        try:
            await sandbox.commands.run(
                f"rm -f {script_path} {archive_path}", timeout=15
            )
        except Exception:  # noqa: BLE001
            log.debug("Export cleanup failed", exc_info=True)

    return bytes(data), summary


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
