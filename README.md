# Coding Agent

A web-based AI assistant in three sections, sharing one backend, one auth, one
design system — and behaving like three different tools, because they are.

| Section | What it is |
|---|---|
| **Chat** | The general-purpose conversation. One model, no sandbox, no tools. |
| **Learn** | Two surfaces. **Courses**: ten structured tracks with chapters, code examples, resources, assessments, weak-area analysis and persisted progress. **Notebooks**: upload PDFs, links and text; ask questions answered *only* from them. |
| **Code** | The agent. A real Linux sandbox — shell commands, file reads and writes, web search, PDFs and images — with every step streaming live into the three-panel trace UI. |

```
frontend/   Next.js 14 (App Router) · TypeScript · Tailwind · Framer Motion   → Vercel
backend/    FastAPI · LangGraph · OpenCode · OpenRouter · Groq · E2B · Exa    → Render
            Supabase: Postgres + Auth + Storage + pgvector
```

Chat and Code are conversations with the agent process and share the websocket,
the session list and the model router. Learn is not: a notebook is a document
workspace, so it is plain REST over its own tables, and it can be open while a
Code run is streaming without either touching the other.

---

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
  not awaited — see [Latency](#latency).

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

---

### Session history

Every session row in the Chat and Code lists carries an overflow menu with the
same four actions, from one shared component
(`components/SessionHistoryMenu.tsx`, used through `SessionListItem`):

| Action | Effect |
|---|---|
| Rename | Opens a dialog for the project's name and one-line description. **Generate** fills both from the transcript; the user still presses Save, so a name they dislike costs a keystroke rather than a round trip. |
| Pin | Moves the session into a **Pinned** group above everything else, ordered by when it was pinned. |
| Archive | Moves it to the **Archived** shelf, which the default list never shows. Nothing is deleted. |
| Delete | Confirms inline in the menu, then removes the session and cascades to its `messages`, `files` and `token_usage` rows. |

`title` is still generated automatically from the first message — that is cheap
and immediate. `description` is not: naming a *project* needs the transcript,
which costs a real model call, so it is on demand and the row stays one line
until someone asks.

Ordering is decided by the backend (`repository._shelf_order`), not by each
list, so Chat and Code cannot drift apart about what "most recent" means.
`is_pinned` / `is_archived` / `pinned_at` / `description` live on `sessions`;
run `schema.sql` again to add them to an existing database (every statement in
it is idempotent). Until you do, a rename still saves its title — the
description is dropped by a deliberate fallback rather than failing the write.

### Message actions: copy, edit, regenerate

Every message in Chat, Code and Agents carries a row of controls that appears
on hover and takes no layout — a transcript is something you read, and a
permanent row of buttons under every paragraph turns it into a form. They stay
in the DOM and stay focusable, so they are reachable by keyboard and to a
screen reader rather than being hover-only.

| Control | Where | What it does |
|---|---|---|
| Copy | Every message | Clipboard, with a tick for confirmation. Falls back to the `execCommand` path on an insecure origin, where `navigator.clipboard` throws. |
| Edit | User messages | Opens the message in place; `⏎` saves, `Esc` cancels. Saving re-runs the conversation from that point. |
| Regenerate | The most recent reply | Re-runs the last turn on **whichever model is selected now**, which need not be the one that answered the first time. |
| 👍 / 👎 | Assistant messages | Records a verdict. Pressing the thumb you already chose withdraws it. |

Regenerate is deliberately offered on the last reply only. Re-running an
earlier one is the same operation as editing the message above it without
changing the text, and offering it separately would be a second door onto one
room — with the added cost of silently discarding everything after it.

**Learn's notebook Q&A has none of these, on purpose.** Every control in that
table assumes a thread you can rewind: edit re-runs the conversation from a
point, regenerate discards what came after, branching keeps the version you
replaced. A notebook question is a single retrieval against a fixed set of
sources and a single answer — there is no checkpoint to fork and nothing after
a reply to discard, and the answer's interesting metadata is which passages it
used, which is why that panel spends its room on a citation strip instead. The
notebook chat is not even persisted (the notes panel is where a notebook keeps
things on purpose), so there would be nothing for a verdict to hang on either.

---

### Conversation branching

**An edit forks; it never overwrites.** Send a different version of your third
message and the conversation re-runs from there, but the replies the original
produced stay reachable behind a `‹ 1/2 ›` control on that message. Regenerate
does the same thing to a reply, so the answer you liked better is never one
click from being gone.

The data model was a real decision, and it is written up at length on
`public.message_branches` in `schema.sql`. The short version:

> A branch is the whole conversation **from turn N onwards**, stored as one row.

The alternative — a tree keyed by parent message — is more general and a worse
fit here. Nothing in this system has a per-message identity to *be* a parent:
what the agent reads is `AgentState["messages"]`, a flat list inside a LangGraph
checkpoint, and the rows in `public.messages` are an append-only log no reader
ever joins on. A tree would have meant minting stable ids in two stores and
keeping them in step. Versioned suffixes need neither: the checkpoint stays the
single live source of truth, and switching branches splices a stored suffix
back into it.

That last point is what makes the feature more than cosmetic. Switching
branches does not merely redraw the transcript — it puts the model back in the
state that branch left it in, so the next turn continues the branch you are
actually looking at. `scripts/test_message_actions.py` asserts exactly this by
reading the checkpoint back after a switch.

Turns are numbered by **user turn**, never by position in the messages array: a
`tool_result` carrier has `role == "user"` too, so array indices would shift the
moment a turn used a tool. `repository.user_turn_positions` and the client's own
count of `kind === "user"` rows are the same number by construction, which is
how both ends agree without exchanging ids.

`is_active` marks which version is spliced in. It is stored rather than
inferred, because on a cold page load the only evidence is the live
conversation itself, and comparing snapshots to guess would be expensive and
wrong the moment two versions began with the same message.

---

### Stopping a generation

The Stop control replaces Send while a reply is streaming, and `Esc` does the
same thing from the keyboard. What happens next is the interesting part.

A stop used to be `run_task.cancel()`, which is abrupt in three ways that all
matter:

* the partial answer already on screen was **thrown away** — it lived only in
  the local variables of a node that raised, and LangGraph does not checkpoint
  a node that raised;
* the tokens the provider had already produced were **never billed**, because
  the charge happens after the stream completes;
* a stop landing mid-tool-call left a `tool_use` block with **no matching
  `tool_result`**, which the provider rejects on the *next* turn — so the
  session was dead and the symptom appeared a long way from the cause.

So a stop is now a *request* rather than an interrupt (`app/cancel.py`). The
socket sets a flag; the graph reads it at the points where stopping is safe —
between stream frames and between tool calls — and then finishes the turn
normally:

* partial text and reasoning are appended to the conversation as a real
  assistant turn, marked `*(Stopped by the user.)*`, so a reload still shows it;
* a tool already running is **allowed to finish** — half a `file_write` cannot
  be undone, and one extra second beats a corrupt sandbox — while calls that
  never started are closed with a result, so nothing is orphaned;
* the turn is billed for the output actually generated, estimated from the
  streamed characters (`app/turnstop.py`) and recorded as an estimate. The
  provider only reports `usage` on a frame a stopped stream never receives, so
  the choice is between estimating and billing zero — and billing zero would
  make Stop a way to use the product free;
* the run ends with `agent_done.reason == "cancelled"`, not an error.

`scripts/test_stop_tools_credits.py` proves the tool case the only way worth
proving it: it stops mid-batch, checks every `tool_use` in the checkpoint has a
result, and then **sends another turn on the same session** — which a
conversation with an orphaned call cannot survive.

---

### History search

The sessions flyout has a search box over the current section's history. It
matches session titles *and message bodies*, and a body hit carries the line
that matched, because a row whose title has nothing to do with your query is
baffling until you can see why it is there.

It is a database query and nothing else: two indexed `ilike` scans and a merge
(`repository.search_sessions`). No model is in the path — a search box that
waits on a generative call is both slower and worse than one that does not.
The input debounces at 200ms and aborts every superseded request, so results
for "auth" cannot land after results for "authentication" and overwrite them.

Chat, Code and Agents each search their own history. The first two scope by
section; an agent scopes by its own id, because a conversation belongs to the
specialist it was started with. Same box, same debounce, same endpoint —
`components/SessionSearch.tsx`, used from both the flyout and the agent shelf,
because two copies of an abort-and-debounce dance is two places to get
out-of-order responses wrong.

Cross-section search is not implemented.

---

### Keyboard shortcuts panel

`?` opens a reference sheet, also reachable from Settings. One rule governs its
content: **every line in it is a binding that actually works.** A shortcuts
sheet is a promise, and one listing a key that does nothing teaches something
false and then makes the reader doubt the rest. It is a static list, so
`components/ShortcutsDialog.tsx` has to be edited when a binding is added — the
comment above the table says so.

---

### Settings

`SettingsDialog` has three groups:

* **Account** — who you are, what the session covers, and sign-out.
* **Preferences** — the default model, and the theme statement.
* **This build** — read-outs: which models are configured, how Auto routes,
  whether the sandbox is wired up.

The default model is stored **against the account**, not in `localStorage`
(`public.user_preferences`), which is the point: signing in on a second device
used to silently reset a choice made on the first — the one thing an account is
supposed to prevent. "No preference" stays distinguishable from "chose the
current default", so a user who never picked one follows the build's default
when it moves and a user who did does not.

On theme, the panel now says the thing rather than leaving it ambiguous: Loom
ships a single true-black theme, there is no light mode, and none is planned.
The `theme` column exists so that is a value in the data rather than an
assumption baked into the absence of one.

---

### Response feedback

👍 / 👎 on assistant replies, stored in `public.message_feedback`. Deliberately
inert: nothing reads it back, no model is retrained, no answer changes. It is a
record kept so that "which model and which route produce answers people
actually like" can one day be asked against real data instead of reconstructed
from nothing.

One verdict per person per turn, revisable and withdrawable. Two people looking
at the same transcript are entitled to disagree, and a control showing somebody
else's verdict as yours is simply wrong.

---

### Attachment previews

Composer chips show a real thumbnail for images and for PDFs whose first page
is a raster — which is what a scanned document is. A chip reading
`report.pdf · PDF` tells you what you already knew when you picked the file;
the first page tells you whether it is the *right* report.

`lib/thumbnail.ts` tries the cheap way first: it scans for a `/DCTDecode`
stream and hands the embedded JPEG to the browser's own decoder — no library,
no worker, a few hundred bytes of code, and it covers every scanned document.
A vector-only PDF (a LaTeX paper, a clean export) has no raster to find, and
that is what `pdfjs-dist` is for. It renders any PDF correctly and costs ~350 KB
plus a worker, which is why it is reached for *second* and behind a dynamic
`import()`: it is fetched the first time somebody attaches a PDF the byte scan
could not draw, and never by anyone who does not. The initial bundle does not
move.

Two details that are not obvious from the outside. The worker is copied into
`public/` at `predev`/`prebuild` rather than referenced through
`new URL(..., import.meta.url)` — the documented form, which Next hands to SWC,
which then parses an already-bundled worker as source and fails on its
`export`. And `render` is called with `intent: "print"`, which has nothing to do
with printing: it is the one option that stops pdf.js scheduling its work with
`requestAnimationFrame`, which browsers do not fire in a hidden tab. Without
it, attaching a PDF and switching away leaves the thumbnail unresolved and the
worker alive until you come back.

A PDF that is neither — corrupt, encrypted, an empty page — still falls back to
the type icon. Everything runs locally on the chosen file, before any upload.

---

### Empty states

Chat, Code and Learn each open on something usable rather than a blank
composer, at the bar the Agents gallery already set.

* **Chat** — four starters, each with the reason it is there.
* **Code** — the same, plus **Open a folder**, which *does* the thing rather
  than describing it: it reaches into the import pipeline the composer's `+`
  already owns.
* **Learn** — a first-run notebook library now points at the ten authored
  courses sitting one tab away, because someone opening Learn for the first
  time has no PDFs to drop in and no reason to know the courses exist.

`SECTION_META.suggestions` was removed rather than rewritten. The problem was
not that they were the wrong strings: a starter needs a reason attached, and
where a section has a real first action it needs to *be* that action — neither
fits a `string[]`.

---

### One-time data cleanup

`scripts/clean_session_debt.py` repairs rows left behind by two fixed bugs.
Run it once against an existing database:

```bash
.venv/Scripts/python -m scripts.clean_session_debt          # report only
.venv/Scripts/python -m scripts.clean_session_debt --apply  # write
```

It renames sessions whose title is the router's "the model produced no answer"
sentence, or a reasoning model's monologue about the title prompt, or an entire
markdown document — using the session's own first message, which is what the
title should have been. It also scans `messages` for raw provider error
payloads and redacts any it finds.

On the database this was written against, that scan found **zero** rows across
1,533 messages and 1,482 checkpoint entries: the 402 leak reached the socket as
a transient `error` frame and returned without appending an assistant message,
so it was rendered once and never written down. The scan stays in the script
because that reasoning is only as good as its evidence, and this is how the
evidence gets re-gathered on a database the author has not seen.

---

### Learn

Two surfaces under one section, switched at the top: **Courses** (authored
curriculum, the default) and **Notebooks** (your own sources).

#### Courses

Ten structured tracks — RAG, Prompt Engineering, Python, TypeScript,
React/Next.js, FastAPI, PostgreSQL, Git, System Design, ML Basics — each with
eight or nine chapters and two assessments. The loop is:

```
catalogue ─▶ course ─▶ chapter ─▶ mark complete ─▶ assessment unlocks
                ▲                                        │
                │                                        ▼
          review chapter ◀── recommendations ◀── weak-area analysis
```

Course content is **static data**, not database rows: one Python module per
course under `backend/app/learn/courses/`, normalised by that package's
registry. Adding an eleventh course is a new module plus one line in `MODULES`
— no endpoint, no component, no migration. Only what a *person* did with a
course is stored (`course_progress`, `course_exam_attempts`), keyed by the
content's own string ids, so a course can be rewritten without a data
migration.

Grading does more than count: every question carries a topic and the chapter it
came from, so topics scoring under 70% come back as **weak areas**, each with
the chapter to revisit, that chapter's resources, and practice prompts drawn
from its own takeaways. Answer keys never leave the server — the exam endpoint
strips `answer` and `explanation` from every question until a submission is
graded.

Three endpoints, split by payload size rather than by resource, which is what
keeps the catalogue from shipping eighty chapters of prose to draw ten cards:

| Endpoint | Returns | Cached |
|---|---|---|
| `GET /api/learn/courses` | card metadata + progress | 30s, dropped on write |
| `GET /api/learn/courses/{id}` | chapter titles + exam state | 30s, dropped on write |
| `GET /api/learn/courses/{id}/chapters/{id}` | one chapter's body | for the session |

Chapter bodies carry no per-user state, so they are identical for everyone and
cached permanently client-side; the next chapter is prefetched when one is
opened. Videos are click-to-load facades — nothing is requested from YouTube
until someone presses play. Progress writes are optimistic and the route is
persisted, so closing the app mid-chapter and returning reopens that chapter.

#### Notebooks

A **notebook** holds sources, a chat scoped to them, notes, and optionally a
course. Three ways in: a PDF (text layer only — there is no OCR), a URL the
backend fetches and reduces to readable text, or pasted text. A YouTube URL is
recognised and resolved to its transcript rather than scraped, reusing
`app/sources.py`.

```
source ──▶ extract ──▶ chunk (~1200 chars, paragraph-aligned, 180 overlap)
                          │
                          └──▶ embed (768d) ──▶ notebook_chunks.embedding
                                                        │
question ──▶ embed ──▶ match_notebook_chunks(...) ──┴──▶ top 8 passages
                                                             │
                                          grounded prompt ───┴──▶ llm_router
```

Answers cite passages as `[1]`, `[2]`; the UI resolves each marker back to the
source and shows the passage behind it, so an answer can be disbelieved
cheaply. When the retrieved passages do not cover the question, the prompt
requires the model to say so rather than fall back on what it knows.

**Embeddings** have two modes and one output shape, both 768-dimensional
because the `vector(768)` column is declared once:

* Not set (or three consecutive failures) → a hashed bag-of-words vector
  computed in-process. Cosine over these is weighted lexical overlap: a decent
  retriever for a personal notebook, and it needs no key. `/api/learn/config`
  reports which mode is live.

The two vector spaces are not comparable — if you add a key after indexing
sources, re-add those sources.

**Structured mode** generates a curriculum from the notebook's sources: 4–7
sequential sections, each with explanatory content and a short multiple-choice
check-in, plus per-section progress. Regenerating replaces the course and its
progress, since progress against a differently-shaped curriculum is a number
about nothing.

Without Supabase, Learn keeps notebooks in the backend's memory and the library
says so — the section still runs end to end locally, it just does not survive a
restart.

---

## Local setup

### Prerequisites

Python 3.11+, Node 18+, and API keys for OpenCode and E2B at minimum.

### 1. Backend

A virtualenv already exists at `backend/.venv` with everything installed. If you
need to recreate it:

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux
```

Then create your env file and fill it in:

```bash
cp .env.example .env
```

| Key | Required | What it does |
|---|---|---|
| `OPENCODE_API_KEY` | **yes** | the default model (Qwen 3.7 Plus) plus the 4 models Auto routes between, and a 5th selectable by hand |
| `E2B_API_KEY` | **yes** | the sandbox the agent works in |
| `EXA_API_KEY` | no | `web_search`; the tool reports an error without it |
| `OPENROUTER_API_KEY` | no | 2 selectable models |
| `GROQ_API_KEY` | no | 1 selectable model (GPT-OSS 120B); also names sessions when present |
| `SUPABASE_*` | no | persistence, auth, uploads — the app degrades to in-memory |
| `GIT_PUSH_TOKEN` | no | lets a sandbox `git push`. Without it commits still happen and pushes report the missing credential |
| `E2B_TEMPLATE` | no | the sandbox image built from `backend/sandbox/`. Unset, E2B's base image is used and tools that need more say so |

Run it:

```bash
cd backend
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

`GET /api/config` reports which integrations are wired up (booleans only, never
key values).

#### Dependencies, and where each one is allowed to live

Three files, and the split is the point — a tool added for one of them must not
silently change the other two:

| File | For | Installed by |
|---|---|---|
| `backend/requirements.txt` | what the service imports at runtime | the deploy, the venv |
| `backend/requirements-dev.txt` | tools for people working on this repo (ruff, pinned) | a developer, and CI's lint job |
| `backend/sandbox/e2b.Dockerfile` | what a **user's sandbox** contains | `e2b template build`, once |

`ruff` used to be in `requirements.txt`, and `lint_code` used to `pip install`
it into a live sandbox the first time it was asked to lint Python. Both are
gone. A runtime install changes what a session can do depending on which tool
happened to run first, cannot be reproduced from the repository, and does
nothing at all in a sandbox with no network — so the linter is baked into the
image at the same pinned version this repository is checked with, and
`SANDBOX_RUNTIME_LINTER_INSTALL=1` is the documented escape hatch for a
deployment that cannot build a template. With neither, the tool reports a
syntax-only check as degraded, which is the honest answer.

Building the image is four commands and is written down in
`backend/sandbox/README.md`.

### Model selection: manual, or Auto

Every model call goes through `app/llm_router.py`. The selector in the composer
offers two things:

* **A specific model** — eight of them, across Groq, OpenRouter and OpenCode.
  The session stays on it until you change it, *unless* that model starts
  failing — see "Automatic fallback" below.
* **Auto (recommended)** — the router classifies each turn and picks for
  itself. `app/agent/task_classifier.py` reads the live graph state and returns
  one hint, in this priority order:

  | signal in the turn | hint | model |
  |---|---|---|
  | an image or document is attached | `visual_structural` | Qwen 3.7 Plus |
  | the agent is writing or editing files | `code_editing` | Minimax M2.7 |
  | big context, many iterations, or a long plan | `complex_longhorizon` | MiMo V2.5 |
  | anything else | `fast_simple` | DeepSeek V4 Flash |

  Classification re-runs on every agent iteration, so a thread that turns into
  a series of file edits moves onto the editing model when it starts editing.
  Switches are announced inline in the chat trace.

All the thresholds live in `app/config.py` as `AUTO_*` settings — retune them
there rather than in the router. Without `OPENCODE_API_KEY`, startup logs an
explicit error and Auto falls back to the per-section table (`AUTO_ROUTE_*`).

### Automatic fallback

A model that is *currently unusable* should not cost you your request. When a
call fails for a reason that belongs to the provider, the router retries the
same request against another model and tells you it did:

> ⚠︎ Qwen 3.7 Plus hit a rate limit — switched to MiMo V2.5.

* **Which failures qualify.** Rate limits and quota (429, and Groq's 413 TPM
  refusal), transient server errors (5xx), and "model unavailable" (404).
  Deliberately *not*: malformed requests (400/422), auth failures (401/403),
  and content-policy refusals. Those will fail identically on the next model,
  so retrying there would burn a second call and hide a real bug behind a model
  switch.
* **Where it goes.** Each registry entry declares a `fallback_chain`. Chains
  lead with a *different provider*, because a 429 is usually provider-wide, and
  a model that can read images falls back to another that can.
* **How far.** At most `MAX_FALLBACK_ATTEMPTS` alternates (2), so three models
  total. A systemic outage fails in bounded time instead of walking the
  registry.
* **A manual pick is not exempt.** You chose a model, but you asked a question;
  finishing the request wins. The toast is how you find out.
* **Never mid-answer.** Once a token has been streamed to you, the answer is
  yours — a failure after that point is surfaced, not restarted, because
  retrying would duplicate or truncate what you are already reading. In
  practice nothing is lost: 429s and 503s happen at request time.
* **You are billed once.** Credits are debited from reported usage after a
  successful call, and a failed attempt produces none. The charge is priced
  against the model that actually answered, not the one that refused.

Every fallback logs one `llm_fallback` line with the model, the reason, the
status code and where it went, so a model that fails often is visible in the
logs rather than only in the toasts.

Covered by `scripts/test_model_fallback.py` (mocked adapters, no live calls).

### Refused vendors

Removing a model from `MODEL_REGISTRY` is not on its own enough to make it
unreachable. Every entry's wire id comes from settings — `OPENCODE_MODEL_FAST`,
`GROQ_MODEL_A`, and so on — so one `.env` line can repoint an innocently-named
entry at any model the upstream gateway happens to serve, and the gateways do
serve models this app has deliberately dropped.

`BANNED_MODEL_SUBSTRINGS` in `app/llm_router.py` closes that. It is checked in
`is_available()`, which every selection and dispatch path already goes through,
so a banned slug is:

* hidden from the model selector,
* refused at dispatch (with an error naming the real reason, not "add your
  API key"),
* skipped as an automatic-fallback target, and
* reported as an error at startup, per offending entry.

Matching is case-insensitive on substrings of the *resolved* wire id, so it
targets a vendor rather than one version of one model. Add a vendor to the
tuple to drop it; there is nothing else to change.

Covered by `scripts/test_model_registry.py`, which repoints a live slot at a
banned model and asserts every one of those routes closes.

`token_usage` records `routing_mode` and `routing_hint` per call, so you can
check afterwards whether Auto has been choosing sensibly.

Routing, registry and fallback tests:

```bash
cd backend
.venv/Scripts/python scripts/test_task_routing.py       # offline, no keys
.venv/Scripts/python scripts/test_model_registry.py     # offline, no keys
.venv/Scripts/python scripts/test_model_fallback.py     # offline, mocked adapters
.venv/Scripts/python scripts/test_live_models.py        # live, spends tokens
.venv/Scripts/python scripts/test_opencode_models.py    # live, per model
.venv/Scripts/python -m scripts.test_auto_routing_e2e   # live, through the graph
```

### 2. Verify the agent loop before touching the UI

```bash
cd backend
.venv/Scripts/python -m scripts.test_agent_loop "write fizzbuzz.py and run it"
```

This runs the full graph and prints every event the websocket would have sent —
thinking, tokens, tool calls, streamed stdout, diffs, token usage.

### 3. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # optional: defaults to ws://localhost:8000
npm run dev
```

Open http://localhost:3000.

### 4. Supabase (optional but recommended)

1. Create a project.
2. Run `backend/schema.sql` in the SQL editor. It creates `sessions`,
   `messages`, `files`, `token_usage`, Learn's six notebook tables
   (`notebooks`, `notebook_sources`, `notebook_chunks`, `notebook_notes`,
   `notebook_lessons`, `notebook_progress`) and the course platform's two
   (`course_progress`, `course_exam_attempts`), enables `pgvector`, defines the
   `match_notebook_chunks` retrieval function, adds RLS policies, and creates a
   private `uploads` storage bucket.

   Or apply it from here, which also *verifies* it. Set the connection in
   `backend/.env` (Project Settings → Database) and:

   > **Use the pooler, not the direct host.** `db.<ref>.supabase.co` publishes
   > an `AAAA` record only, so it does not resolve on an IPv4-only machine —
   > the failure looks like `could not translate host name`, not like a
   > network-stack problem. The session-mode pooler
   > (`aws-0-<region>.pooler.supabase.com:5432`) is IPv4 and takes the user
   > `postgres.<ref>`. `SUPABASE_DB_HOST` / `_PORT` / `_USER` / `_PASSWORD` are
   > read as discrete parts precisely so a password containing `@`, `+` or `=`
   > does not have to be percent-encoded into a URI; `SUPABASE_DB_URL` still
   > works if you prefer one.

   ```bash
   cd backend && .venv/Scripts/python -m scripts.apply_schema
   ```

   It runs `schema.sql`, then reads the live catalogue back and names anything
   still missing, because "the script ran" and "the columns are there" are not
   the same claim. `--check` verifies without applying. The REST API cannot run
   DDL, so this or the SQL editor are the only two routes.

   **Run it again on an existing database.** Every statement is idempotent
   (`create … if not exists`, `alter table … add column if not exists`), and it
   is what adds `is_pinned` / `is_archived` / `pinned_at` to `sessions`, the
   Learn tables, and the two course tables. Until it has run, pinning and
   archiving silently do nothing, the Learn library stays empty and course
   progress does not persist — the backend treats persistence failures as
   non-fatal by design, so nothing crashes to tell you.
3. Fill `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY` in
   `backend/.env`, and `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   in `frontend/.env.local`.
4. Set `REQUIRE_AUTH=1` in `backend/.env` to require a signed-in user.

### 5. Sign-in providers (manual — cannot be scripted)

Both of these are configured in dashboards, not in this repo. Nothing in
`backend/.env` or `frontend/.env.local` can substitute for them, and with them
unset the sign-in screen renders but every attempt fails.

**Email OTP (magic link).** Supabase dashboard → *Authentication → Providers →
Email*. Enable it. Under *Authentication → URL Configuration*, set **Site URL**
to `http://localhost:3000` (and add your deployed origin for production). The
link in the email returns to that origin, so a wrong value here sends users to a
page that cannot complete the sign-in. No key goes into `.env` for this one.

**Google OAuth.** Two dashboards, in this order:

1. **Google Cloud Console** → *APIs & Services → Credentials → Create
   credentials → OAuth client ID*, type *Web application*.
   - *Authorised JavaScript origins*: `http://localhost:3000`
   - *Authorised redirect URI*:
     `https://<your-project-ref>.supabase.co/auth/v1/callback`

     Note this points at **Supabase**, not at the app — Supabase performs the
     exchange and then returns the browser to your Site URL. Pointing it at
     `localhost:3000` is the usual mistake and fails with `redirect_uri_mismatch`.
   - Copy the **Client ID** and **Client Secret**.
2. **Supabase dashboard** → *Authentication → Providers → Google*. Enable it and
   paste the Client ID and Client Secret from step 1. They are stored by
   Supabase; they are **not** environment variables of this project and must
   never be put in `frontend/.env.local`, which ships to the browser.

The app degrades honestly if you skip Google: `AuthPanel` checks which providers
are actually enabled and shows *"Google is not enabled on this Supabase project
yet — use the magic link below"* rather than offering a button that cannot work.

---

## Projects and memory

Two kinds of "remember this", deliberately kept apart, because they answer
different questions and want different switches.

A **project** is the scoped half. The user makes one on purpose, files sessions
into it, and writes standing instructions that apply to those sessions and to
nothing else. It can also hold knowledge files — PDFs and text the agent should
treat as background. **Memory** is the unscoped half: two custom-instruction
boxes and a table of learned facts that apply to every section, every session,
which is exactly why it gets its own on/off control and its own audit list.

Both end up as text in front of the system prompt, composed by
`app/preamble.py` in one order and only one:

```
<the surface's own system prompt>      what the machinery is and how it works
<per-account memory and instructions>  who this person is, how they want answers
<the project's instructions and files> what this particular work needs
```

Least specific to most specific, so the narrower statement is read last. A
project that says "answer in French" beats an account preference for English,
because the project scope was set deliberately and more recently. The base
prompt goes first because it describes the tools and the sandbox, and no user
preference should be able to bury that.

### The injection budget

Knowledge files are uploads, so their size is whatever the user happened to
attach. A 400 kB document would quietly consume the context window and push the
actual conversation out of it, so there is a budget — and it is applied
*before* any content is read. That is what `project_files.char_count` is for:
the listing query returns the counts without the text, the budget is decided
against those, and only the files that fit have their content fetched at all.

Anything cut short says so, in the prompt, where the model can see it:

```
[... truncated: this file is longer than the space available ...]
```

A model that reads half a document and does not know it is reading half a
document will answer confidently from the half it got. That is worse than not
having the file, which is why the notice is not optional and why files left out
entirely are counted in the block rather than silently dropped.

### Learning

After a turn, a cheap model reads the exchange and proposes durable facts.
Three things make that safe enough to run without asking each time:

* it is **charged like any other model call** — there is no free model call
  anywhere in this project and this is not the first;
* it never runs for an anonymous caller, who has no account to remember
  against, and never when the switch is off;
* it is **skipped for a stopped turn**. A cut-off exchange is the worst
  possible source of a fact meant to persist.

Extraction failure is never surfaced. A turn that produced a good answer is not
a failed turn because the pass afterwards could not parse its own JSON, so
everything in `app/memory.py` logs and returns rather than raising. The prompt
that decides what counts as a fact is in that file and is written to be read —
it is the whole of the boundary, so it should not need explaining elsewhere.

The switch gates the *learned facts*, not the two instruction boxes. Turning
memory off means "stop learning things about me", not "discard what I typed
into Settings on purpose"; conflating those loses work the user did
deliberately.

### Scope, and what deleting means

`sessions.project_id` is `on delete set null`, not cascade. Deleting a project
keeps every conversation in it and leaves them unfiled. "Delete project" reads
like it takes the conversations with it, so the confirmation spells the
consequence out rather than just asking twice — a destructive action whose
blast radius the user has guessed wrong is the one case where the second click
has to say what it does.

Projects work signed out, on the anonymous shelf, exactly as sessions do.
Memory does not: the server refuses to store it without an account, because
pooling it under a shared sentinel would let one browser's stated preferences
steer another's answers.

---

## Reading a codebase

Two halves that cost very different things, so they are two functions, two
endpoints, and two visibly separate things in the UI.

`analysis.scan()` is **pure measurement**: languages, lines, entry points,
dependencies, a TODO census, whether tests and linting exist. No model call,
nothing charged, and the same tree always produces the same numbers. That is
what makes it something the panel can refresh whenever a file changes.

`analysis.summarise()` is **the narrative** — one model call that turns those
numbers into prose about what the project is. Charged like every other call,
and always asked for.

### One shell command, not a tree walk

The obvious implementation walks the tree through `sandbox.files.list`, which
is what the file sidebar does. For a real repository that is hundreds of round
trips over the E2B transport, and the sidebar's walker is depth-limited to 3
and budget-capped precisely because of it — it is built to draw a sidebar, not
to count a codebase. `scan` sends one script and parses its output: one round
trip, no depth limit, and `find | xargs wc -l` does the counting where the
files actually are. A ten-file sandbox measures in ~250 ms.

**A language's size is its lines, not its file count.** A project with one
4,000-line module and forty 20-line configs is a project in that first
language, and a file-count histogram says the opposite. The bar in the panel
is sized the same way.

### Neither half may create a sandbox

Both `scan` and `read_loom_file` probe `sandbox_manager.sandbox_id_for()` and
return empty when it is None. Without that guard:

* the health panel calls `scan` on mount, so **opening the Code tab** would
  cold-start an E2B sandbox, burn a slot and start the 15-minute idle clock for
  a session where nothing had been asked for;
* `read_loom_file` runs on the prompt path of *every* turn, so **every Chat
  message** would pay a cold create to read a file that cannot exist there.

Both were real, and the second is the more expensive mistake of the two.

### LOOM.md

`write_file: true` on the analysis endpoint stores the narrative as `LOOM.md`
at the sandbox root, and `preamble.compose` reads it back into the system
prompt on every later turn of that session. That is what makes an analysis
outlive the conversation that asked for it.

It is injected **last and labelled as reference**, not as instruction. It
describes what the code is; a description of a Rails app must not read as an
instruction to write Rails. Edit it by hand freely — nothing rewrites it except
an explicit regenerate.

---

## Version control

History now leaves the sandbox — deliberately, on request, and never with a
credential inside it.

A repository can be given **one remote**, `origin`, and it must be an `https://`
URL with no userinfo in it. `git.validate_remote_url` refuses
`https://user:token@host/...` before git ever sees it, because that is the one
shape that ends up written into `.git/config` in plain text where the sandbox
can read it back.

**The token is the server's and reaches exactly one process.** `GIT_PUSH_TOKEN`
lives in `backend/.env`, is handed to a single `git push` through that
command's environment plus a credential helper written inline on the command
line, and is never stored in the repository, the sandbox image, or any file in
the sandbox. `scripts/test_git_push.py` asserts that: it greps `.git/config`
after a real push and fails if anything token-shaped is there.

**Pushing at the end of a turn is opt-in, and off by default.** The Code
section can mirror Claude Code — finish a turn, commit what changed with a
generated message, push, and show the commit — but only for a repository whose
owner has ticked *Commit and push after each turn* in the History panel. The
reason it is not the default is that a turn is not always a deliverable: "just
try this" and "build the feature" look identical to the code that would have to
decide. So the explicit paths (the `git` tool's `commit` and `push`, the
panel's buttons) stay exactly as they were, and the automatic one is a switch a
person throws per repository. It is stored in the repository's own config
(`loom.autopush`), so it goes when the sandbox goes, beside the remote it
applies to.

When it fires, `app/autocommit.py` runs *before* `agent_done` — the composer
re-arms on that frame, and a commit landing after it would arrive under a turn
the user believes is finished. A stopped or failed turn is left uncommitted:
what it wrote is half of something. The result is a `turn_commit` frame, which
the transcript renders as a card carrying the short hash, the subject, the
branch, the file count and whether the push succeeded — and which flashes the
committed paths in the file tree, so the panel opens to what was just recorded.

A commit that lands but fails to push is **amber, not red**, and says why: the
work is safe locally and the next successful push carries it.

What the panel does now:

| | |
|---|---|
| **Staging** | Per-file checkboxes. An empty selection means *commit everything*, so the common case did not get worse to buy the rare one. |
| **Branches** | List, create, switch, merge. Merging is the secondary action on a row that is not the current branch, because merging *into* the branch you are looking at is what the word means. |
| **Discard** | The one operation nothing can undo — an untracked file removed here was never in the object store. It confirms, inline. |
| **Commit messages** | A cheap model writes one from the staged diff, into the box. Never applied. |
| **Remote** | One `origin`, https only, validated on the way in. `Push` sends the current branch and sets upstream. |
| **After each turn** | The opt-in above. Off by default, stored in the repository, shown as a checkbox under the remote. |

Two decisions worth stating.

**`commit(use_index=True)` exists because the checkboxes would otherwise be
decorative.** The old path always ran `git add -A` before committing, which
sweeps unticked files straight back in. The panel stages its selection, then
commits the index and nothing else.

**A merge conflict is a 200, not an error.** The merge really happened and the
working tree really does have markers in it; reporting a failure would leave
the caller looking at a repository whose state nobody told them about.
`conflicted` names the files, and both the UI and the agent's tool report it as
a result with a problem in it rather than as a call that did not happen.

**The commit message is suggested, never applied** — on the manual path. The
user still presses commit, because that press is the only moment anyone reads
what is about to be recorded permanently, and a wrong commit message is
permanent in a way a wrong chat reply is not. The end-of-turn path is the
deliberate exception, and it is exactly why that path is opt-in: turning it on
*is* the press, made once for the repository instead of once per commit. When
no model is available to write a message, the fallback is factual rather than
inventive (`Update 3 files`, with the names in the body) — a dull subject beats
a turn's work left uncommitted, and beats a subject the diff does not support.

---

## Artifacts

A document the model writes *beside* the conversation rather than into it: a
file, a page, a component, a draft. The transcript keeps a chip; the content
lives in its own panel.

The distinction that justifies the whole surface is whether a thing is **worked
on** or **said once**. A 300-line file pasted into a transcript is unreadable,
unscrollable, and gone the moment the next message arrives. The same file in a
panel can be read, edited, and revised across turns.

The Agents section has had this since it was built — a tool result can carry an
`artifact` payload. What was missing was any way for Chat and Code, the two
surfaces where people actually write documents, to produce one.

### Deliberate, not heuristic

The model creates an artifact by **calling a tool**. It does not get promoted
into a panel because a code fence crossed a line count.

A heuristic is wrong in both directions constantly: it promotes a long stack
trace nobody wants to edit, and it leaves the twelve-line config someone has
been iterating on for five turns stuck in the transcript. The model knows
whether it just wrote a *thing* or an *explanation*, and asking costs one
sentence in a tool description.

### Versions, never overwrites

Every write is a new row. `artifact_key` is the stable identity; `version`
counts up within it.

That settles the user-edit question, which is the one that decides whether
people trust the feature. **A person editing an artifact does not overwrite the
model's version — they add one**, attributed to them in `created_by`. Nothing
the model wrote is destroyed by someone tidying it up, the history stays
walkable, and the model sees the edit on its next turn because it reads the
latest.

The panel says so rather than assuming it is understood: the editor's status
line reads *"Autosaves to a new version"*, not the "to the sandbox" it says for
a real file. Both halves of that default would have been wrong — an artifact
never touches the sandbox, and "save" normally means overwrite.

Viewing an older version is read-only, and says why. Letting someone type into
a version that cannot be saved and only telling them at save time is worse than
not accepting the keystrokes.

### Four kinds, and one that is missing

| Kind | Rendered as |
|---|---|
| `markdown` | formatted prose |
| `code` | the CodeMirror editor, highlighted by `language` |
| `html` | a sandboxed iframe |
| `svg` | an image |

`mermaid` is deliberately **absent**. There is no mermaid renderer in this
frontend, so offering it would produce a "diagram" that displays as its own
source — a plausible-looking capability that does not work, which is the trade
this codebase refuses elsewhere (see the Deploy button in Project Pulse). Add
the renderer first, then add the kind.

### Two events, not one with a flag

`artifact_created` opens the panel. `artifact_updated` revises it in place and
does **not** steal focus. Stealing focus every time the model touches a
document someone is reading is the fastest way to make a canvas unusable, and
that difference is worth a type rather than a boolean.

Both carry the full content. The alternative is an id the client then fetches,
which is a round trip to display something the server already had in hand, on
the one path where the user is watching and waiting.

### Security notes

**HTML runs with `sandbox="allow-scripts allow-forms allow-popups"` and no
`allow-same-origin`.** The content is model-written and may contain scripts.
Those two flags *together* would be equivalent to not sandboxing at all — the
frame could reach this origin's storage and DOM. Apart, the page can be
interactive and still cannot touch anything of ours.

**SVG renders through an `<img>` data URL, not as injected markup.** An
`<svg>` written into the DOM can carry scripts and event handlers; the same
markup in an `<img>` cannot execute anything.

---

## Latency

An agent turn is mostly waiting — on the model, on E2B, on Supabase — so the
rules here are about what is allowed to sit *between* the user's message and the
model's next token. If you add something to that path, know why.

| Where | Rule |
|---|---|
| `repository.fire(...)` | Message and usage writes are fired, never awaited, inside a run. Nothing downstream reads them. The one exception is the `sessions` insert on connect — `messages` has a foreign key onto it. |
| Tool nodes | One node visit drains the whole `pending` batch. Read-only batches run concurrently; anything that writes to the filesystem stays sequential. |
| `impl._refresh_tree_later` | The sidebar tree walk is a background task. It is UI garnish the model never sees, and it walks the sandbox over the network. |
| `sandbox_manager.get` | `set_timeout` is renewed once per quarter-window, not once per tool call. |
| `ws._writer` | Drains the emitter queue and merges adjacent text/thinking/output fragments into one frame. Nothing is ever *held back* waiting for more — it only batches what has already piled up. |
| `page.tsx` handlers | `useCallback`, so the `memo` on `ChatPanel`'s items, `SessionSidebar`, `TerminalPanel` and `DiffViewer` actually holds. Every streamed token re-renders `page.tsx`. |

Measured on a mid-loop conversation of ~80k tokens (4 tool rounds of real file
contents), median time-to-first-token for the next iteration went from **8.9s to
5.0s**, and the spread narrowed from 4.6–22.0s to 3.4–5.1s — the whole
transcript is served from cache instead of re-read.

The one knob deliberately left alone is `OPENCODE_MAX_TOKENS=8000`. It is the
single largest remaining lever on response time, and unlike everything above it
is a real quality trade — turn it down only if you have decided you want
shallower reasoning, not because you want a faster benchmark. (`GROQ_MAX_TOKENS`
is *not* in that category: it must stay under Groq's tokens-per-minute cap, and
at 8000 on the free tier every Groq call failed with a 413. See `.env.example`.)

## Security

**The browser never holds a vendor key.** OpenCode, OpenRouter, Groq, E2B and Exa are
called only from `backend/`, and the only secret the frontend sees is the
Supabase **anon** key — which is designed to be public and is gated by the RLS
policies in `schema.sql`.

Two rules keep it that way:

* Nothing sensitive may be named `NEXT_PUBLIC_*` — that prefix inlines the value
  into the client bundle.
* `frontend/.env.local.example` lists every variable the browser is allowed to
  see. If a key is not in that file, it does not belong in the frontend.

To verify after a change, check that the client reads nothing but
`NEXT_PUBLIC_*` — grepping the built bundle for key *names* gives false
positives, because the setup banner renders them as UI copy:

```bash
cd frontend && grep -rhoE "process\.env\.[A-Z_]+" app lib components | grep -v NEXT_PUBLIC_
```

That must print nothing. Currently the client reads exactly three variables:
`NEXT_PUBLIC_BACKEND_WS_URL`, `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY`.

### Guardrails

| Guardrail | Where | Default |
|---|---|---|
| Max agent iterations per task | `MAX_AGENT_ITERATIONS` | 50, then a user-facing stop message |
| Sandbox idle teardown | `SANDBOX_IDLE_TIMEOUT_SECONDS` | 900s (15 min), refreshed on each use |
| Per-command bash timeout | `BASH_TIMEOUT_SECONDS` | 30s |
| Websocket message rate limit | `RATE_LIMIT_MESSAGES_PER_MINUTE` | 20/min per session |
| Upload rate limit | `RATE_LIMIT_UPLOADS_PER_MINUTE` | 10/min per session |
| Token usage logging | `token_usage` table | every model call |
| CORS | `ALLOWED_ORIGINS` | explicit allowlist, never `*` |
| Websocket origin check | `ALLOWED_ORIGINS` | same list; a socket from any other page is closed with 4403 |
| Per-address ceilings | `app/security.py` | 30 new sessions/min, 60 socket connects/min, 3× the upload limit |
| Request body ceiling | `app/security.py` | 21 MB, refused from `Content-Length` before the body is read |
| Response headers | `app/security.py` | `nosniff`, `frame-ancestors 'none'`, `no-store`, HSTS over https |

### The perimeter, beyond auth

`app/security.py` is the layer around authentication and ownership. Three
of its decisions are worth knowing about before deploying:

* **The access token does not travel in the websocket URL.** A browser cannot
  set headers on a websocket handshake, and `?token=` lands in every access
  log between the browser and the process. The client offers the token as a
  subprotocol (`Sec-WebSocket-Protocol: loom.bearer, <jwt>`) and the server
  selects `loom.bearer` back. `?token=` is still honoured for older clients.
* **CORS does not apply to websockets**, so the socket handshake checks its
  `Origin` against `ALLOWED_ORIGINS` itself. A non-browser client sends no
  Origin and is allowed through; a browser on a foreign page is not.
* **The rate limiters are keyed two ways.** The per-session limits above bound
  a session, and a session id is minted by the client, so on their own they
  bound nothing a caller could not sidestep with a fresh id. The per-address
  limits bound the caller. Both honour one hop of `X-Forwarded-For`.

The frontend sends its own headers from `next.config.mjs`: a content-security
policy derived from `NEXT_PUBLIC_BACKEND_WS_URL` and the Supabase URL,
`frame-ancestors 'none'`, HSTS, and no `X-Powered-By`. Agent-rendered
components and artifacts run in sandboxed iframes without `allow-same-origin`;
the Markdown renderer only links `http`, `https` and `mailto`.

### Smoke test

Before pointing users at a deployment, run the perimeter check against the
real configuration. It boots the app in-process, never starts a model turn,
and exits non-zero on the first failure:

```bash
cd backend && python scripts/smoke_test.py
```

It covers boot, the response headers, that `/api/config` leaks no key value,
session creation and its per-address ceiling, the body-size gate, upload
filename sanitising, the websocket origin check and the bearer subprotocol.
On the frontend, `npm run build` is the equivalent: it type-checks, lints,
and runs `scripts/check-classes.mjs`, which fails on any class that reaches
past the design tokens.

### Interface primitives

The shared components that keep the interface on its own design system live
in `frontend/components/`: `Tooltip` (and the `data-tip` layer that replaces
every native `title`), `PanelHeader` with the `--bar-h` / `--bar-h-sub` /
`--gutter` tokens, `Skeleton`, `Meter`, `Sparkline`, and the action toast
channel in `Toast.tsx` (`useToast().notify` for outcomes, `useToast().undoable`
for reversible deletion). Every `aria-modal` dialog runs `useFocusTrap`.

---

## Deploying

### Render (backend)

* Build: `pip install -r requirements.txt`
* Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
* Env: everything from `.env.example`, plus
  * `ALLOWED_ORIGINS=https://your-app.vercel.app`
  * `POSTGRES_CHECKPOINT_URL=<Supabase connection string>` and add
    `langgraph-checkpoint-postgres` to `requirements.txt` — otherwise checkpoints
    live on the instance's ephemeral disk and are lost on redeploy.

### Vercel (frontend)

* Root directory: `frontend`
* Env: `NEXT_PUBLIC_BACKEND_WS_URL=wss://your-service.onrender.com`,
  `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`

---

## Layout

```
backend/
  app/
    main.py               FastAPI app, CORS allowlist, lifespan
    config.py             every setting; `public_summary()` is the only safe export
    events.py             THE websocket event contract
    emitter.py            thread-safe event plumbing from graph nodes to the socket
    files.py              PDF/image uploads -> document/image content blocks
    llm_router.py         every model call: registry, provider adapters, cost
    agent/
      graph.py            the StateGraph: agent node, routing, tool nodes
      runner.py           checkpointer lifecycle + one-turn execution
      llm.py              cost estimation + session titles, via the router
      task_classifier.py  Auto mode's decision: turn state -> routing hint
      prompts.py          system prompt
      state.py            checkpointed state shape
    tools/
      sandbox.py          E2B lifecycle, idle reaper
      schemas.py          tool definitions given to the model
      impl.py             bash / read / write / edit / list / search
    learn/
      ingest.py           PDF / URL / text -> readable text
      chunking.py         text -> overlapping, paragraph-aligned passages
      embeddings.py       768d vectors: provider, or local hashing fallback
      retrieval.py        index a source; retrieve passages for a question
      tutor.py            the two grounded model calls: answer, course outline
      courses/
        __init__.py       registry: normalise, project, grade, recommend
        rag.py … ml_basics.py   one module per course — the whole catalogue
    db/
      repository.py       sessions, messages, files, token_usage
      learn_repository.py notebooks + course progress and exam attempts
    analysis.py           one shell pass over the sandbox; the narrative; LOOM.md
    artifacts.py          documents written beside the conversation, versioned
    gitmsg.py             a commit message from a staged diff
    projects.py           a project's instructions + knowledge -> prompt text
    memory.py             per-account instructions, facts, and the learning pass
    preamble.py           composes base + memory + project, in that order
    api/
      ws.py               /ws/{session_id}
      rest.py             /api/sessions, /api/upload, /api/config
      learn.py            /api/learn/*  — notebooks, and the course platform
      auth.py             optional Supabase Auth
      ratelimit.py        sliding-window limiter
  scripts/test_agent_loop.py
  schema.sql

frontend/
  app/page.tsx            the shell: rail nav, section switch, sessions flyout
  lib/
    events.ts             mirror of backend/app/events.py
    useAgentSocket.ts     the event -> UI state machine
    sections.ts           Chat / Learn / Code identity
    api.ts, learn.ts, supabase.ts
    courses.ts            course API client + the content/state cache split
    artifacts.ts          artifacts: read, version history, save an edit
    projects.ts           projects, knowledge files, memory
    useProjects.ts        project state, kept out of page.tsx
  components/
    SectionNav  SessionSidebar  SessionListItem  SessionHistoryMenu  AuthPanel
    ChatPanel  ToolCallCard  FileTree  DiffViewer  TerminalPanel
    GitPanel  ProjectPulse  ProjectHealth  PersonalizationGroup
    artifacts/
      ArtifactPanel       the document surface: render, edit, version switcher
    projects/
      ProjectStrip        the chips at the head of the sessions flyout
      ProjectPanel        instructions, knowledge files, the sessions inside
    FileUploadZone  StatusIndicator  Markdown
    learn/
      LearnSection        the Courses <-> Notebooks switch
      CoursePlatform      course routing, fetching, optimistic progress
      CourseCatalog  CoursePage  ChapterView  ExamView  ExamResults
      NotebookLibrary     the grid, filters, search, sort, view toggle
      NotebookWorkspace   sources | chat | notes, and the Q&A/Course toggle
      SourcesPanel  NotebookChat  NotesPanel  CourseView  NotebookCover
```

---

## Known limits

* **Uploads without Supabase** are kept in a bounded in-process cache, so they
  do not survive a backend restart. Configure Supabase for durable storage.
* **Rate limiting is in-process.** Fine for one Render instance; move to Redis
  before scaling horizontally.
* **The checkpointer defaults to SQLite on local disk.** On Render that disk is
  ephemeral — set `POSTGRES_CHECKPOINT_URL` for real persistence.
* **One concurrent run per session.** Sending a second message while the agent
  is working returns an error event; cancel first.
* **Learn reads text layers, not pixels.** A scanned PDF has no text to chunk,
  and there is no OCR step — the source is stored with an explanatory error
  rather than silently indexing nothing.
* **Local embeddings are lexical.** Retrieval matches
  on shared terms rather than on meaning: it will find the passage that uses
  your words, not the one that makes your point differently. Fine for a
  personal notebook; set the key for anything larger.
* **A notebook's chat is not persisted.** Notes are. That is deliberate — the
  notes panel is where a notebook keeps things on purpose.
* **Course content is fixed at deploy time.** The catalogue is Python modules,
  so adding a course is a code change, not an admin screen. That is the trade
  for making a course reorderable and rewritable without a data migration.
* **Some chapter videos are YouTube *searches*, not embeds.** A pinned video id
  rots when a channel re-uploads or re-titles; where no canonical video is
  stable, the card opens a search instead so the link cannot break.
* **A stopped turn's token count is an estimate.** The provider reports `usage`
  only on the final frame, which a stopped stream never receives, so the charge
  is derived from the characters actually streamed at four per token. The
  alternative is billing nothing for output that was really produced. Both turn
  loops estimate the same way, through `app/turnstop.py` — the agents' loop used
  to bill a hardcoded zero for input, which on a specialist is most of the turn.
* **`count_tokens` is approximate for most of the roster.** tiktoken ships
  OpenAI's vocabularies, so the count is exact for `gpt-oss` and an
  approximation elsewhere — Llama, Qwen, DeepSeek, MiniMax and Nemotron each
  use a vocabulary it does not have. The result says which it gave you and sets
  `exact_for_model`; expect 10-20% either way on the approximate path.
* **`lint_code` needs the project's sandbox template, and says so when it is
  absent.** E2B's base image has neither ruff nor pyflakes. The linter is
  installed by `backend/sandbox/e2b.Dockerfile` at build time; with
  `E2B_TEMPLATE` unset the tool falls back to a syntax-only check and reports
  it as degraded rather than returning a "clean" it did not earn. Nothing is
  installed into a running sandbox unless
  `SANDBOX_RUNTIME_LINTER_INSTALL=1` is set deliberately.
* **A sandbox push needs `GIT_PUSH_TOKEN` on the server.** There is no way to
  authenticate from inside the sandbox otherwise, and the token is never put
  there — so with it unset, `push` reports the missing credential and the
  end-of-turn auto-commit records the commit with `pushed: false` rather than
  failing the turn.
* **The end-of-turn commit is per repository, not per account.** The opt-in
  lives in the sandbox's git config, so a reaped sandbox takes it with it. That
  is intentional — it applies to a history that no longer exists — but it does
  mean re-ticking the box after a long idle gap.
* **A PDF thumbnail costs a 350 KB download the first time.** Only for a
  vector PDF, only once per session, and only for someone who attaches one —
  the byte scan handles scanned documents with no library at all. A PDF that
  neither path can draw keeps its type icon.
* **History search is per section and literal.** Chat, Code and Agents each
  search their own history; there is no cross-section search. Matching is
  substring, not fuzzy and not semantic.
* **Learn's notebook Q&A has no message actions.** No copy row, no edit, no
  branching, no thumbs — see **Message actions** for why those controls do not
  describe a single grounded retrieval.
* **Response feedback feeds nothing.** It is captured and stored, and that is
  all it does today.
* **Project knowledge is injected whole, not retrieved.** Every ready file goes
  into the prompt up to the budget, in the order they were added — there is no
  relevance ranking, so a project with more knowledge than fits will always
  drop the same files. Learn's notebooks do the retrieval version of this
  (pgvector, `learn/retrieval.py`); a project deliberately does not, because
  standing background is not the same shape of problem as a question.
* **The scan measures; it does not read source.** Languages, sizes, manifests
  and TODO counts come from `find` and `wc`, so the narrative built on them
  reasons about shape rather than about what the code does. It hedges where the
  numbers do not settle something, which is the honest behaviour, but it is not
  a substitute for reading the files.
* **LOOM.md lives in the sandbox, which is ephemeral.** It survives the
  conversation but not the sandbox's idle teardown. Regenerating it is one
  click and one model call; persisting it across sandboxes would mean storing
  it on the project row, which is worth doing and is not done yet.
* **An artifact is not a file.** It lives in the database, not the sandbox, so
  it cannot be run, imported or served. The tool description says so, but a
  model that ignores it will produce something the user cannot execute — in a
  Code session `write_file` is the right tool and an artifact is not.
* **No mermaid.** There is no renderer for it, so the kind is not offered
  rather than offered and shown as source.
* **Artifacts are per session.** They are not shared across a project, and
  reopening a different conversation does not carry them over.
* **Git is sandbox-only, permanently.** No remote, no push, no clone. A
  session's history leaves as part of the exported zip or not at all.
* **Duplicate memories are only caught on an exact match.** A rephrasing of a
  fact already known is stored again. Near-duplicate detection needs
  embeddings, and the failure mode of a slightly redundant list is much better
  than the failure mode of a fuzzy match, which is silently dropping a real
  new fact.
* **The account-level default model needs an account.** Anonymous sessions are
  per-device by definition, so the control is disabled when signed out rather
  than writing to a shared row.
