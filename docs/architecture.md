# Architecture

> The agent graph, the websocket contract, live preview, and how the sandbox is shared between the agent and the person.
>
> [← Back to the README](../README.md)

## How it works

```
browser ──WebSocket──▶ FastAPI ──▶ LangGraph StateGraph
                                     │
                    agent ──(route)──┼──▶ bash_tool    ──┐
                      ▲              ├──▶ file_read    ──┤
                      │              ├──▶ file_write   ──┼── E2B sandbox
                      │              ├──▶ file_edit    ──┤
                      │              └──▶ search_tool  ──┘   (Exa)
                      └──────────────────────┘

   iframe ◀──forwarded port── E2B ◀── dev server (start_dev_server)
```

* The **`agent` node** streams a model call (tool schemas + streamed thinking
  where the provider supports it)
  and emits `agent_token` / `agent_thinking_delta` events as they arrive.
* The **conditional edge** routes to the tool node matching the model's first
  pending `tool_use` block. A model can request several tools per turn, and that
  node drains the whole `pending` list in one visit — concurrently when every
  call in the batch is read-only (`read_file`, `list_files`, `web_search`), in
  strict order otherwise — then returns to `agent`, which flushes all buffered
  `tool_result`s into the single user turn the API requires.
* **Checkpointing** (SQLite locally, Postgres on Render) persists the full graph
  state per `thread_id == session_id`, so a session survives a cold start.
  LangGraph writes a checkpoint after every node, which is why the tool node
  drains a batch rather than looping once per call.
* Every tool call *and* its result is written to the `messages` table, not just
  the final answer, so the UI can replay a whole trace. Those writes are fired,
  not awaited — see [Performance](performance.md).

### The websocket contract

`backend/app/events.py` and `frontend/lib/events.ts` are mirrors of each other
and are the only interface between agent and browser. The frontend's entire
animation system is a pure function of these event types:

| Event | What the UI does |
|---|---|
| `agent_thinking_start` / `_delta` / `_end` | pulsing dot, collapsible reasoning block |
| `agent_token` | token-by-token text with a blinking caret |
| `tool_call_start` | tool card springs into the feed, sheen animation while pending |
| `tool_output_chunk` | line streams into the terminal drawer, auto-scrolling |
| `tool_call_result` | card result expands with a height transition |
| `file_changed` | file-tree node flashes; diff panel springs open; a live preview schedules a debounced reload |
| `file_tree` | sidebar tree re-renders |
| `artifact_created` | the artifact panel opens on the new document |
| `artifact_updated` | the panel revises in place, and does *not* steal focus |
| `preview_ready` | Preview tab opens on the running site |
| `preview_error` | error banner over the frame (`fatal: false`) or the stopped state (`true`) |
| `preview_stopped` | preview closes; `reason: "absent"` is the connect-time reconcile |
| `usage` / `agent_done` / `max_iterations` / `error` | status chips and notices |
| `branches` | draws the `‹ 1/2 ›` switcher on any user turn that has been edited |
| `history_replaced` | the conversation on the server is no longer the one on screen |

Change a `type` in one file and you must change it in the other.

`history_replaced` carries the turn that was cut and the text replacing it,
and that is not redundancy. The obvious design — "the history changed, go and
fetch it" — is a race the client loses: an edit truncates the checkpoint and
*immediately* starts the re-run, so a fetch issued on that frame reads a
checkpoint mid-rewrite and comes back either empty or still holding the turns
just removed. Both were observed during this work; the second is worse, because
the new reply then streams in underneath the old one and the thread shows the
question twice. Naming the cut lets the client truncate its own transcript at
the same place. A *branch switch* has no run to race, so it omits both fields
and the client does refetch — that path replaces an arbitrary suffix, which is
not expressible as a truncation.

Client frames added alongside them: `edit_message`, `regenerate` and
`switch_branch`. `cancel` is unchanged on the wire but no longer means "tear
the run down" — see **Stopping a generation**.

---

### Live preview

`start_dev_server(command, port)` is a tool like any other — it maps to
`bash_tool` in `TOOL_NODE` and changes nothing about the graph. What it adds is
in `backend/app/tools/preview.py`:

1. runs the command with `background=True`, so it is not subject to the
   30-second shell timeout every other command has;
2. polls the port *from inside the sandbox* until something is listening — a
   server that never binds fails here, loudly, with its own stderr attached,
   rather than producing an iframe pointed at nothing;
3. asks E2B for the forwarded host and emits `preview_ready`;
4. keeps reading the process's output. A build error that leaves the server
   serving becomes `preview_error(fatal=False)` — a banner over a live frame —
   while the process exiting becomes `preview_error(fatal=True)`.

One preview per session, and starting a second replaces the first. Teardown
hangs off the existing sandbox lifecycle rather than beside it: `SandboxManager
.destroy` discards the preview first, so the idle reaper, an explicit session
delete and shutdown all tear down forwarding without a separate code path.

The registry is process-local, like the sandbox registry it mirrors. On connect
the socket therefore states the truth in both directions — re-announcing a live
preview so a page reload does not lose it, and sending
`preview_stopped(reason="absent")` when there is none so a client cannot keep
showing a preview the backend no longer has.

In the browser (`components/PreviewPanel.tsx`) the frame is laid out at the
selected viewport's real width and scaled to fit the column, so a page under the
Tablet preset genuinely lays out at 834px and its media queries fire — clamping
the iframe instead would make every preset render identically. Reloads are
keyed remounts, because the frame is cross-origin and `contentWindow` is not
reachable; `file_changed` events while live are debounced into one reload rather
than one per file.

### Working in the sandbox by hand

The agent is not the only thing with write access. `backend/app/tools/
workspace.py` serves the user's own edits:

| Route | What it does |
|---|---|
| `GET /api/sessions/{id}/file?path=` | Read any file for the inline editor — not just the ones the agent touched |
| `PUT /api/sessions/{id}/file` | Save a manual edit, returning a `file_changed`-shaped body |
| `GET /api/sessions/{id}/tree` | Walk the sandbox now, rather than waiting for the agent's next turn |
| `POST /api/sessions/{id}/files` | Import a batch of local files, preserving their relative paths |
| `POST /api/sessions/{id}/fs` | `rename` / `delete` / `new_file` / `new_dir` on one path |
| `GET /api/sessions/{id}/export` | Zip the project (built inside the sandbox, `node_modules` and friends excluded) |
| `POST /api/sessions/{id}/name` | Name and describe the session from its transcript |

Every path is confined to the working directory — `path` arrives from the
browser, and `..` would otherwise reach the sandbox's own home.

A save comes back over HTTP rather than the socket on purpose: the browser that
saved already has the content, and echoing it would land in the editor the user
is still typing in. The client folds the response into the same `changed` map
the agent's edits use, so there is one changed-files list and one diff viewer
rather than two.

The inline editor **autosaves**: typing stops for 900ms and the buffer goes to
the sandbox, with the save bar reporting each state. ⌘S and the Save button
remain as accelerators. The agent may write the same file while a buffer is
dirty; that is handled where it happens — the agent's version is adopted into a
*clean* buffer only, and a dirty one is left alone and says so.

### The terminal takes commands

The panel at the foot of the context column was output-only: it rendered
`tool_output_chunk` frames and had nowhere to type. The socket agreed —
anything that was not `user_message`, `cancel`, `set_model`, `edit_message`,
`regenerate`, `switch_branch` or `ping` came back as
`Unknown client frame`, which `scripts/test_terminal.py` records as the repro.

It is a shell now. A `terminal_command` frame runs in **the same sandbox the
agent's tools use, through the same `sandbox_manager.get`** — so the same lazy
creation, the same keepalive, the same idle reaper, and the same path
confinement. Nothing here is a second lifecycle, which is the whole reason it
routes through `tools/impl.py` rather than opening its own connection.

Three differences from an agent command, all deliberate:

* **It is not a tool call.** Output streams as `tool_output_chunk` on a
  `term_…` call id, bracketed by `terminal_started` / `terminal_exit` rather
  than `tool_call_start` / `tool_call_result`. A command the user typed must
  not appear in the transcript as something the agent did.
* **It gets longer than 30 seconds.** `TERMINAL_TIMEOUT_SECONDS` defaults to
  120. The agent is told to background anything slow and split its work up; a
  person typing `npm install` expects to wait for it.
* **One at a time, per socket.** A second command while one is running is
  refused with an error rather than queued — the panel is a single terminal,
  and two commands interleaving their output in it would be unreadable. It is
  *not* gated on the agent: running `ls` while a turn is in flight is the
  normal case, and the sandbox handles both.

Up and down walk the commands typed this session. The prompt locks while a
command runs and unlocks on `terminal_exit`, including the timeout and
sandbox-unavailable paths — which is why those emit one.

### Opening a folder — the composer's `+`

One entry point for everything you can add to a session, in the order people
reach for it: a folder from the machine, loose files, and a PDF or image. The
first two land in the **sandbox filesystem**, so the agent sees them exactly as
it sees anything it wrote itself; the third goes to the upload pipeline and
rides along with the next message as a content block, because those are for the
model to *look at* rather than for the project to contain.

```
showDirectoryPicker() ──▶ walk, pruning excluded dirs ──▶ batches of ≤40 files
        │                                                        │
   (or <input webkitdirectory> where unsupported)          POST …/files
                                                                 │
                                              tar in memory ──▶ sandbox ──▶ tar -xf
```

* The browser will not hand out a directory without the person choosing it in
  the OS picker. There is no path here that reads a folder the user did not
  select, and no attempt to work around that.
* `node_modules`, `.git`, `dist`, `build`, `__pycache__` and the rest of
  `IMPORT_EXCLUDE_DIRS` are pruned **during the walk**, before their contents
  are ever read — which is the difference between skipping a 900 MB
  `node_modules` and reading it to throw it away. `frontend/lib/folder.ts`
  mirrors that list; `app/tools/workspace.py` enforces it again server-side.
  What was skipped is reported under the progress bar.
* A batch travels as **one tar archive**, unpacked inside the sandbox. E2B's
  filesystem API is one round trip per file and a modest project is several
  hundred; this is the difference between a moment and a minute and a half.
* Batching is also what makes the progress bar honest — it moves on requests
  that have landed, not on a timer.

### The tree acts, as well as reports

Right-click any row (or the `⋯` that appears on hover) for **rename**,
**delete**, and — on a folder — **new file** / **new folder**; the workspace
root carries the same two create buttons. Naming happens inline rather than in a
dialog, because the row it replaces is the answer to "which thing am I naming".

A rename or delete moves a path the rest of the UI is keyed by, so the client
moves its own state with it (`pathMoved` in `useAgentSocket`) before re-reading
the tree — otherwise the diff panel would spend a round trip pointed at a file
that no longer exists, and could save an edit back to it.

### Keyboard

| Keys | Action |
|---|---|
| `⌘K` / `Ctrl+K` | Command palette — commands and files, ranked together |
| `⌘J` / `Ctrl+J` | Terminal drawer |
| `⌘B` / `Ctrl+B` | Sessions flyout |
| `⌘\` / `Ctrl+\` | Context column (below `xl`, where it floats) |
| `⌘⇧O` / `Ctrl+Shift+O` | New session |
| `⌘S` / `Ctrl+S` | Save, inside the inline editor |
| `?` | Keyboard shortcuts panel |
| `Esc` | Closes the topmost surface — or, with nothing left to close, stops a streaming reply |

The global layer never fires while the user is typing (the palette and `Esc`
excepted — they are how you leave a field) and never fires in Learn, which is
not a session and has none of these surfaces.

`Esc` is last in its own chain deliberately: a panel open over a streaming
answer closes on the first press rather than silently killing the generation
behind it. Only when nothing is left to close does it stop the run. The panel
that lists all of this is `?`, and every line in it is a binding that really
works — see **Keyboard shortcuts panel** below.
