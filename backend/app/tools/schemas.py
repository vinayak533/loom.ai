"""Tool definitions handed to the model.

Descriptions are prescriptive about *when* to reach for each tool, not just
what it does — that is what actually drives correct tool selection.
"""

from __future__ import annotations

WORKDIR = "/home/user"

BASH_EXECUTE = "bash_execute"
READ_FILE = "read_file"
WRITE_FILE = "write_file"
EDIT_FILE = "edit_file"
WEB_SEARCH = "web_search"
LIST_FILES = "list_files"
START_DEV_SERVER = "start_dev_server"
STOP_DEV_SERVER = "stop_dev_server"
GIT = "git"

# Maps a tool name to the LangGraph node that executes it.
#
# The dev-server tools are shell operations, so they route to the same node
# every other shell operation does. That is the whole integration: no new node,
# no new edge, and the batching and ordering rules the graph already applies to
# `bash_execute` apply to them unchanged.
TOOL_NODE = {
    BASH_EXECUTE: "bash_tool",
    READ_FILE: "file_read",
    WRITE_FILE: "file_write",
    EDIT_FILE: "file_edit",
    WEB_SEARCH: "search_tool",
    LIST_FILES: "file_read",
    START_DEV_SERVER: "bash_tool",
    STOP_DEV_SERVER: "bash_tool",
    # Git is shell work like the rest, so it routes to the same node and
    # inherits its batching and ordering rules unchanged.
    GIT: "bash_tool",
}

TOOLS: list[dict] = [
    {
        "name": BASH_EXECUTE,
        "description": (
            "Run a shell command inside the session's isolated Linux sandbox. "
            f"The working directory is {WORKDIR}. Use this to install packages, "
            "run tests, start builds, inspect processes, or use git. Commands "
            "time out after 30 seconds by default, so avoid long-running "
            "foreground processes — background them or split the work up. "
            "Returns stdout, stderr, and the exit code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run, e.g. `pytest -q`.",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": READ_FILE,
        "description": (
            "Read a file from the sandbox filesystem. Call this before editing "
            "any file so your edit is based on the current contents. Returns "
            "the file with 1-indexed line numbers prefixed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": f"Absolute path, or relative to {WORKDIR}.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": WRITE_FILE,
        "description": (
            "Write a file, replacing its entire contents (and creating parent "
            "directories as needed). Use this for brand-new files. For changes "
            f"to an existing file prefer {EDIT_FILE}, which is cheaper and much "
            "less likely to clobber content you did not intend to touch."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to write."},
                "content": {
                    "type": "string",
                    "description": "Full file contents.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": EDIT_FILE,
        "description": (
            "Make a targeted edit by replacing an exact string in a file. "
            "`old_str` must appear EXACTLY ONCE in the file, including "
            "whitespace and indentation — the edit fails loudly otherwise. "
            "Include enough surrounding context to make the match unique. To "
            "delete code, pass an empty `new_str`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to edit."},
                "old_str": {
                    "type": "string",
                    "description": "Exact text to replace. Must be unique in the file.",
                },
                "new_str": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
    {
        "name": LIST_FILES,
        "description": (
            "List a directory in the sandbox as a tree. Call this when you need "
            "to orient yourself in an unfamiliar project before reading files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": f"Directory to list. Defaults to {WORKDIR}.",
                }
            },
            "required": [],
        },
    },
    {
        "name": START_DEV_SERVER,
        "description": (
            "Start a long-running dev server and show the user the live site. "
            "Use this — NOT `bash_execute` — for anything that serves a web "
            "page: `npm run dev`, `next dev`, `vite`, `python -m http.server`, "
            "`flask run`, and so on. It backgrounds the process (so it is not "
            "subject to the 30-second shell timeout), waits until the port is "
            "actually accepting connections, exposes it on a public URL, and "
            "opens the user's Preview panel on it.\n\n"
            "Call it once the project can actually serve something — after the "
            "install and the first files exist, not before. `port` must be the "
            "port the server really binds; pass the flag that pins it "
            "(`--port 3000`) rather than guessing what the tool defaults to. "
            "The server must listen on 0.0.0.0, not 127.0.0.1, or the "
            "forwarded URL will not reach it — add `--host 0.0.0.0` for Vite, "
            "`-H 0.0.0.0` for Next.js.\n\n"
            "Starting a second server replaces the first. If the command fails "
            "to bind the port, the error you get back includes the server's own "
            "output — read it and fix the cause rather than retrying blindly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "The command to run, e.g. `npm run dev -- --host 0.0.0.0 "
                        "--port 3000`. Runs from the working directory; prefix "
                        "with `cd sub/dir &&` for a project in a subfolder."
                    ),
                },
                "port": {
                    "type": "integer",
                    "description": "The port the server listens on, e.g. 3000.",
                },
            },
            "required": ["command", "port"],
        },
    },
    {
        "name": STOP_DEV_SERVER,
        "description": (
            "Stop the running dev server and close the user's preview. Use it "
            "when the preview is no longer relevant — you are about to "
            "restructure the project, or the user asked you to shut it down. "
            f"You do NOT need this before another {START_DEV_SERVER}, which "
            "replaces the running server by itself."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": GIT,
        "description": (
            "Version-control the project in the sandbox. Real git — the commits "
            "are real and the user can see the history in their Code panel.\n\n"
            "Use it deliberately, not constantly:\n"
            "- `init` once, when starting a project the user will want history "
            "for. It also writes a sensible .gitignore. Safe to call twice.\n"
            "- `commit` at meaningful checkpoints — a feature works, a bug is "
            "fixed, a refactor lands. NOT after every file write. Write the "
            "message the way a careful engineer would: what changed and why, "
            "imperative mood, no 'Updated files'.\n"
            "- `status` to see what is uncommitted before deciding to commit.\n"
            "- `diff` to review your own changes before committing them.\n"
            "- `log` to see what has already been committed this session.\n\n"
            "There is no remote and nothing is ever pushed. Do not attempt to "
            f"push, add a remote, or authenticate — use {BASH_EXECUTE} only if "
            "the user explicitly asks for a git operation not listed here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["init", "status", "diff", "commit", "log"],
                    "description": "Which git operation to run.",
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Commit message. Required for `commit`, ignored "
                        "otherwise. A concise imperative subject line; add a "
                        "blank line and detail below it when the change "
                        "warrants explanation."
                    ),
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "For `commit`: stage only these paths. Omit to commit "
                        "everything that changed, which is usually what you want."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "For `diff`: limit the diff to one file.",
                },
                "staged": {
                    "type": "boolean",
                    "description": (
                        "For `diff`: show the staged changes instead of the "
                        "working tree. Defaults to false."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "For `log`: how many commits. Defaults to 30.",
                },
            },
            "required": ["operation"],
        },
    },
    {
        "name": WEB_SEARCH,
        "description": (
            "Search the web. Call this when the answer depends on information "
            "you do not have — current library versions, recent releases, an "
            "unfamiliar API, or anything the user flags as time-sensitive. Do "
            "not use it for questions you can answer from the sandbox contents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "num_results": {
                    "type": "integer",
                    "description": "How many results to return (1-10, default 5).",
                },
            },
            "required": ["query"],
        },
    },
]
