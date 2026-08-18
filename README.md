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
backend/    FastAPI · LangGraph · xAI · OpenRouter · E2B · Exa                → Render
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
| `preview_ready` | Preview tab opens on the running site |
| `preview_error` | error banner over the frame (`fatal: false`) or the stopped state (`true`) |
| `preview_stopped` | preview closes; `reason: "absent"` is the connect-time reconcile |
| `usage` / `agent_done` / `max_iterations` / `error` | status chips and notices |

Change a `type` in one file and you must change it in the other.

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
| `Esc` | Closes the topmost surface only |

The global layer never fires while the user is typing (the palette and `Esc`
excepted — they are how you leave a field) and never fires in Learn, which is
not a session and has none of these surfaces.

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

Python 3.11+, Node 18+, and API keys for xAI and E2B at minimum.

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
| `XAI_API_KEY` | **yes** | the agent's model (Grok 4.5, the default everywhere) |
| `E2B_API_KEY` | **yes** | the sandbox the agent works in |
| `EXA_API_KEY` | no | `web_search`; the tool reports an error without it |
| `OPENROUTER_API_KEY` | no | 2 selectable models |
| `GROQ_API_KEY` | no | 1 selectable model; also names sessions when present |
| `OPENCODE_API_KEY` | no | the 4 models Auto routes between (see below) |
| `SUPABASE_*` | no | persistence, auth, uploads — the app degrades to in-memory |

Run it:

```bash
cd backend
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

`GET /api/config` reports which integrations are wired up (booleans only, never
key values).

### Model selection: manual, or Auto

Every model call goes through `app/llm_router.py`. The selector in the composer
offers two things:

* **A specific model** — eight of them, across xAI, Groq, OpenRouter and
  OpenCode. The session stays on it until you change it.
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

`token_usage` records `routing_mode` and `routing_hint` per call, so you can
check afterwards whether Auto has been choosing sensibly.

Routing tests:

```bash
cd backend
.venv/Scripts/python scripts/test_task_routing.py       # offline, no keys
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

The one knob deliberately left alone is `XAI_MAX_TOKENS=16000`. It is the
single largest remaining lever on response time, and unlike everything above it
is a real quality trade — turn it down only if you have decided you want
shallower reasoning, not because you want a faster benchmark.

## Security

**The browser never holds a vendor key.** xAI, OpenRouter, E2B and Exa are
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
  components/
    SectionNav  SessionSidebar  SessionListItem  SessionHistoryMenu  AuthPanel
    ChatPanel  ToolCallCard  FileTree  DiffViewer  TerminalPanel
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
