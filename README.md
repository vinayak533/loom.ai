<div align="center">

# Loom

**A web-based AI workspace: a coding agent with a real Linux sandbox, a grounded learning platform, and ten specialist agents — on one backend, one auth and one design system.**

[![lint](https://github.com/vinayak533/loom.ai/actions/workflows/lint.yml/badge.svg)](https://github.com/vinayak533/loom.ai/actions/workflows/lint.yml)
![Next.js](https://img.shields.io/badge/Next.js-14-000000?logo=nextdotjs&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-agent%20graph-1C3C3C)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Supabase](https://img.shields.io/badge/Supabase-Postgres%20%C2%B7%20Auth%20%C2%B7%20pgvector-3ECF8E?logo=supabase&logoColor=white)
![E2B](https://img.shields.io/badge/E2B-sandbox-FF8800)

[Overview](#-overview) ·
[Features](#-key-features) ·
[Screenshots](#-project-showcase) ·
[Architecture](#%EF%B8%8F-architecture) ·
[Quick start](#%EF%B8%8F-installation-and-setup) ·
[Docs](#-documentation)

<img src="portfolio-screenshots/previous-images/07-code-live-preview.png" alt="Loom Code workspace: the agent's step-by-step trace on the left and the app it built running in the live preview on the right" width="100%">

<sub>The Code workspace: every tool call streams into the trace, and the dev server the agent started runs in the live preview. (Scripted demo turn; see the <a href="#-project-showcase">note on capture</a>.)</sub>

</div>

---

## 🚀 Overview

Loom is a full-stack AI assistant split into four sections that share a backend, a websocket protocol, a model router and a design system, but behave like four different tools:

| Section | What it is |
|---|---|
| 💬 **Chat** | A general-purpose conversation. One model, no sandbox, no tools. |
| 📚 **Learn** | **Courses**: ten authored tracks with chapters, assessments, weak-area analysis and saved progress. **Notebooks**: upload PDFs, links or text and ask questions answered *only* from those sources, with citations. |
| 🧑‍💻 **Code** | The agent. It runs shell commands, reads and writes files, searches the web, runs dev servers and uses git, all in a real E2B Linux sandbox. Every step streams live into a three-panel trace UI. |
| 🧩 **Agents** | Ten specialist agents, each with its own persona and toolset: summariser, email copywriter, UI component designer, image prompt engineer, poster creator, SEO writer, researcher, workflow router, human-approval gatekeeper and code refactorer. |

```
frontend/   Next.js 14 (App Router) · TypeScript · Tailwind · Framer Motion · CodeMirror   → Vercel
backend/    FastAPI · LangGraph · OpenCode · OpenRouter · Groq · E2B · Exa                  → Render
            Supabase: Postgres + Auth + Storage + pgvector
```

---

## ✨ Key features

<table>
<tr>
<td width="50%" valign="top">

**🧑‍💻 Coding agent**
- LangGraph agent with tools: `bash_execute`, `read_file`, `write_file`, `edit_file`, `list_files`, `web_search`, `start_dev_server`, `stop_dev_server`, `git`, `analyze_project`, `create_artifact`, `update_artifact`
- **Live preview**: the agent starts a dev server, the port is forwarded from E2B, and the page renders in a resizable frame with desktop, tablet and phone viewports
- File tree, diff viewer, inline CodeMirror editor with autosave, and an **interactive terminal** in the same sandbox
- Import a local folder (with `node_modules`, `.git`, etc. skipped), and export the project as a `.zip`
- Git panel: staging, branches, merge, discard, model-suggested commit messages, and an opt-in **commit and push after each turn**

</td>
<td width="50%" valign="top">

**💬 Conversations**
- Streaming tokens and reasoning, rendered Markdown, tables and highlighted code
- **Edit forks, it never overwrites**: conversation branching with a `‹ 1/2 ›` switcher
- Regenerate on any model, copy, 👍 / 👎 feedback
- **Stop** keeps the partial answer, closes open tool calls cleanly, and bills only the tokens actually generated
- Session history with pin, archive, rename (with an AI-generated name and description) and search across titles and message bodies
- `Ctrl K` command palette, keyboard shortcuts, PDF and image attachments with thumbnails

</td>
</tr>
<tr>
<td width="50%" valign="top">

**📚 Learning**
- 10 authored courses (RAG, Prompt Engineering, Python, TypeScript, React/Next.js, FastAPI, PostgreSQL, Git, System Design, ML Basics), each with 8 or 9 chapters and two assessments
- Server-side grading; topics scoring under 70% come back as **weak areas** with chapters to review
- Notebooks: PDF, URL, YouTube transcript or pasted text → chunked → embedded → retrieved with cited answers
- Generate a structured course from a notebook's own sources

</td>
<td width="50%" valign="top">

**🧩 Workspace and platform**
- **Projects** with standing instructions and knowledge files; **memory** with custom instructions and learned facts
- **Artifacts**: versioned documents (Markdown, code, HTML, SVG) written beside the conversation
- Codebase scan, plus a model-written project narrative saved as `LOOM.md`
- Optional Supabase Auth (email magic link, Google OAuth), RLS policies, a per-user credit ledger
- Rate limits, origin checks, CSP and security headers, and a perimeter smoke test

</td>
</tr>
</table>

---

## 🧠 AI / ML capabilities

| Capability | How it is implemented | Where |
|---|---|---|
| **Agentic tool use** | A LangGraph `StateGraph`: the agent node streams a model call, and a conditional edge routes each pending `tool_use` to its tool node. Read-only tool batches run concurrently; batches that write run sequentially. | `backend/app/agent/graph.py` |
| **Multi-provider model router** | 8 models across **Groq**, **OpenRouter** and **OpenCode** behind one interface, with streaming, tool schemas, vision flags and per-call cost. | `backend/app/llm_router.py` |
| **Auto routing** | Each agent iteration is classified as `visual_structural`, `code_editing`, `complex_longhorizon` or `fast_simple`, and routed to the model registered for that class. | `backend/app/agent/task_classifier.py` |
| **Automatic fallback** | Rate limits, quota and 5xx failures retry on a different provider, at most two alternates. A toast tells you, and you are billed only for the model that answered. | `llm_router.stream_with_fallback` |
| **Retrieval-augmented generation** | Paragraph-aligned chunks (~1200 chars, 180 overlap), 768-d embeddings in pgvector, top-8 retrieval, and a grounded prompt that must cite `[n]` or say the sources do not cover it. | `backend/app/learn/` |
| **Generated curricula** | A notebook's sources become a 4–7 section course with multiple-choice check-ins. | `learn/tutor.py` |
| **Specialist agents** | One parameterised graph with ten personas and toolsets, human-in-the-loop approval (the run is genuinely suspended), and agent-to-agent handoff. | `backend/app/agents/` |
| **Learning memory** | After a turn, a cheap model proposes durable facts about the user. This is skipped for anonymous users, stopped turns, or when memory is switched off. | `backend/app/memory.py` |
| **Model-written metadata** | Session titles, project names and descriptions, commit messages, and codebase narratives. | `agent/llm.py`, `gitmsg.py`, `analysis.py` |

> [!NOTE]
> Notebook embeddings are **local hashed bag-of-words vectors**: no key and no network call. Retrieval is lexical, not semantic. See [Known limits](docs/known-limits.md).

---

## 🏗️ Architecture

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

- **One event contract.** `backend/app/events.py` and `frontend/lib/events.ts` mirror each other and are the only interface between the agent and the browser. The UI's whole animation system is a function of these events: `agent_token`, `tool_call_start`, `tool_output_chunk`, `file_changed`, `preview_ready`, `artifact_created`, `branches` and more.
- **Durable sessions.** LangGraph checkpoints the full graph state per session (SQLite locally, Postgres in production), so a session survives a cold start. Every tool call and result is written to Supabase, so the UI can replay a whole trace.
- **Learn is plain REST.** A notebook is a document workspace, not a conversation with the agent process, so it uses its own tables and endpoints. It can stay open while a Code run streams.
- **Keys never reach the browser.** Every vendor is called from `backend/`. The frontend reads exactly three variables, all `NEXT_PUBLIC_*`.

<details>
<summary><b>Request lifecycle for one Code turn</b></summary>

1. The browser sends `user_message` over `/ws/{session_id}`.
2. The agent node picks a model (manual or Auto), streams the call, and emits `agent_thinking_*` and `agent_token` as they arrive.
3. Pending `tool_use` blocks route to their tool nodes, which run in the session's E2B sandbox. `tool_output_chunk` frames stream stdout live.
4. File writes emit `file_changed`, which updates the tree, diff panel and preview. `start_dev_server` polls the port inside the sandbox before emitting `preview_ready`.
5. The loop returns to the agent node until there are no more tool calls, `MAX_AGENT_ITERATIONS` is reached, or the user stops it.
6. `usage` and `agent_done` close the turn. Message and usage writes are fire-and-forget so they never sit on the latency path.

</details>

Deeper write-ups: [Architecture](docs/architecture.md) · [Models and routing](docs/models.md) · [Performance](docs/performance.md)

---

## 🛠️ Tech stack

| Layer | Technologies |
|---|---|
| **Frontend** | Next.js 14 (App Router), React 18, TypeScript 5, Tailwind CSS 3, Framer Motion, CodeMirror 6, `pdfjs-dist`, Lucide icons |
| **Backend** | Python, FastAPI, Uvicorn, WebSockets, Pydantic Settings, `httpx` |
| **Agent orchestration** | LangGraph (`StateGraph`, SQLite / Postgres checkpointers) |
| **Models** | OpenCode (DeepSeek V4 Flash, MiniMax M3, Qwen 3.7 Plus, MiMo V2.5, MiMo V2.5 Pro), OpenRouter (Nemotron 3 Ultra, Llama 4 Scout), Groq (GPT-OSS 120B), all through the OpenAI-compatible client |
| **Sandbox and tools** | E2B (code execution, filesystem, port forwarding), Exa (web search), Tavily, Jina Reader, Stability AI, Resend (specialist-agent tools) |
| **Data** | Supabase: Postgres, Auth, Storage, `pgvector`; `pypdf` for PDF text; `tiktoken` for token counts |
| **Quality** | Ruff, ESLint, `tsc --noEmit`, a design-token class checker, and a GitHub Actions lint workflow |
| **Deployment** | Vercel (frontend), Render (backend) |

---

## 📸 Project showcase

All screens below are from the running application at the same window size.

> [!NOTE]
> **About capture.** Screens marked **†** stream *scripted* agent events through the real websocket UI. The default model provider was unavailable when they were captured, so the turns were scripted rather than generated. Every other screen shows live application state.

### 💬 Chat

<table>
<tr>
<td width="50%"><img src="portfolio-screenshots/previous-images/01-chat-home.png" alt="Chat home with greeting, composer, model selector and four starter cards"></td>
<td width="50%"><img src="portfolio-screenshots/previous-images/02-command-palette.png" alt="Command palette listing every model with its provider"></td>
</tr>
<tr>
<td><b>Chat home</b>: a composer with the model selector, and four starters that each say why they are there.</td>
<td><b>Command palette (<code>Ctrl K</code>)</b>: switch between the eight models across Groq, OpenRouter and OpenCode, or Auto.</td>
</tr>
<tr>
<td width="50%"><img src="portfolio-screenshots/previous-images/03-chat-answer-table.png" alt="A chat answer with a collapsible reasoning block and a comparison table"></td>
<td width="50%"><img src="portfolio-screenshots/previous-images/04-chat-answer-code.png" alt="A chat answer containing a highlighted Python code block"></td>
</tr>
<tr>
<td><b>Rich answers †</b>: a collapsible reasoning block, Markdown tables, and live token and cost counters in the header.</td>
<td><b>Code in chat †</b>: syntax-highlighted blocks with a line count and copy button.</td>
</tr>
</table>

### 🧑‍💻 Code

<table>
<tr>
<td width="50%"><img src="portfolio-screenshots/previous-images/05-code-workspace.png" alt="Empty Code workspace with starters and the Pulse context column"></td>
<td width="50%"><img src="portfolio-screenshots/previous-images/06-code-agent-session.png" alt="Agent trace with terminal, write and dev-server tool cards, and the file tree"></td>
</tr>
<tr>
<td><b>Code workspace</b>: starters including <i>Open a folder</i>, and the Pulse column with the pinned route, session counters, sandbox tree and terminal.</td>
<td><b>Agent session †</b>: each tool call is a card in the trace, with changed files flagged <code>A</code>/<code>M</code> and highlighted in the sandbox tree.</td>
</tr>
<tr>
<td width="50%"><img src="portfolio-screenshots/previous-images/07-code-live-preview.png" alt="Live preview of a Pomodoro timer served from the sandbox"></td>
<td width="50%"><img src="portfolio-screenshots/previous-images/08-code-editor.png" alt="Inline editor showing App.tsx with Diff, File and Edit modes"></td>
</tr>
<tr>
<td><b>Live preview †</b>: the forwarded dev-server port in a frame, with desktop, tablet and phone viewports, reload and open-in-tab.</td>
<td><b>Inline editor</b>: Diff, File and Edit views of any sandbox file, with autosave back to the sandbox.</td>
</tr>
</table>

### 📚 Learn

<table>
<tr>
<td width="50%"><img src="portfolio-screenshots/previous-images/09-learn-courses.png" alt="Course catalogue with progress stats and a resume banner"></td>
<td width="50%"><img src="portfolio-screenshots/previous-images/10-learn-course-overview.png" alt="RAG course overview with progress and learning outcomes"></td>
</tr>
<tr>
<td><b>Course catalogue</b>: progress, chapters done, assessments, average score, and a <i>pick up where you left off</i> banner.</td>
<td><b>Course overview</b>: level, chapters, assessments, duration, saved progress and outcomes.</td>
</tr>
</table>

<p align="center">
<img src="portfolio-screenshots/previous-images/11-learn-chapter.png" alt="A RAG chapter on retrieval with a reciprocal rank fusion code example" width="80%"><br>
<sub><b>Chapter reader</b>: authored content with worked code. Here, reciprocal rank fusion for hybrid retrieval.</sub>
</p>

### 🧩 Agents

<p align="center">
<img src="portfolio-screenshots/previous-images/06-agents-specialists.png" alt="Gallery of specialist agents grouped by Writing, Design and Research, each listing its tools" width="80%"><br>
<sub><b>Ten specialists</b>: each card shows the agent's role and the tools it can call. The credit balance sits beside the filter.</sub>
</p>

---

## ⚙️ Installation and setup

### Prerequisites

| Requirement | Notes |
|---|---|
| Python **3.11+** | CI runs 3.11 |
| Node.js **18+** | CI runs 20 |
| **E2B** API key | Required for the Code section's sandbox |
| At least one **model provider** key | `OPENCODE_API_KEY` (default models and Auto routing), `OPENROUTER_API_KEY` or `GROQ_API_KEY` |
| Supabase project | *Optional.* Without it, sessions, uploads and notebooks live in memory |

### 1. Clone

```bash
git clone https://github.com/vinayak533/loom.ai.git
cd loom.ai
```

### 2. Backend

```bash
cd backend
python -m venv .venv

# Windows
.venv/Scripts/python -m pip install -r requirements.txt
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt

cp .env.example .env        # then fill in your keys
```

### 3. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # defaults to ws://localhost:8000
```

### 4. Database (optional)

Run [`backend/schema.sql`](backend/schema.sql) in the Supabase SQL editor, or apply it and verify the result from the CLI:

```bash
cd backend
.venv/Scripts/python -m scripts.apply_schema
```

Every statement is idempotent, so you can re-run it safely on an existing database. Sign-in providers (email magic link, Google OAuth) are configured in the Supabase and Google dashboards. See **[docs/setup.md](docs/setup.md)** for the full walkthrough.

---

## ▶️ Running the project

Start the backend and the frontend in two terminals:

```bash
# terminal 1: API and websocket on :8000
cd backend
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

```bash
# terminal 2: web app on :3000
cd frontend
npm run dev
```

Open **http://localhost:3000**.

Check which integrations are wired up (it returns booleans, never key values):

```bash
curl http://localhost:8000/api/config
```

<details>
<summary><b>Verify the agent loop without the UI</b></summary>

Runs the full graph and prints every event the websocket would have sent: thinking, tokens, tool calls, streamed stdout, diffs and token usage.

```bash
cd backend
.venv/Scripts/python -m scripts.test_agent_loop "write fizzbuzz.py and run it"
```

</details>

<details>
<summary><b>Checks and tests</b></summary>

```bash
# Backend: offline, no keys needed
cd backend
.venv/Scripts/python scripts/test_task_routing.py
.venv/Scripts/python scripts/test_model_registry.py
.venv/Scripts/python scripts/test_model_fallback.py
.venv/Scripts/python scripts/smoke_test.py           # security perimeter check

# Backend lint (what CI runs)
pip install -r requirements-dev.txt && ruff check app scripts

# Frontend (what CI runs)
cd frontend
npm run lint
npm run typecheck
```

`backend/scripts/` also contains live end-to-end scripts (`test_live_models.py`, `test_auto_routing_e2e.py`, `test_agents_e2e.py` and others) that make real provider calls and spend tokens.

</details>

---

## 📂 Project structure

```
loom.ai/
├── backend/
│   ├── app/
│   │   ├── main.py              FastAPI app, CORS allowlist, lifespan
│   │   ├── config.py            every setting; public_summary() is the only safe export
│   │   ├── events.py            the websocket event contract (mirrored in frontend/lib/events.ts)
│   │   ├── llm_router.py        model registry, provider adapters, fallback, cost
│   │   ├── agent/               Code/Chat graph, runner, Auto task classifier, prompts
│   │   ├── agents/              the ten specialists: registry, shared graph, approvals, tools/
│   │   ├── tools/               E2B sandbox lifecycle, tool schemas and impls, preview, git, workspace
│   │   ├── learn/               ingest, chunking, embeddings, retrieval, tutor, courses/
│   │   ├── api/                 ws.py, agents_ws.py, rest.py, learn.py, agents.py, auth.py, ratelimit.py
│   │   ├── db/                  Supabase repositories
│   │   └── *.py                 artifacts, memory, projects, analysis, branching, cancel, credits, security, …
│   ├── sandbox/                 E2B template (e2b.Dockerfile) for the agent's sandbox image
│   ├── scripts/                 schema migration, smoke test, offline and live test scripts
│   ├── schema.sql               tables, RLS policies, pgvector, storage bucket
│   ├── requirements.txt         runtime dependencies
│   └── requirements-dev.txt     developer tools (ruff, pinned)
├── frontend/
│   ├── app/                     Next.js App Router shell (page.tsx, layout.tsx, globals.css)
│   ├── components/              ChatPanel, ToolCallCard, FileTree, DiffViewer, PreviewPanel, TerminalPanel,
│   │                            GitPanel, CommandPalette, … plus agents/, learn/, artifacts/, projects/
│   ├── lib/                     useAgentSocket (event → UI state machine), events.ts, API clients
│   └── scripts/                 pdf.js worker copy, design-token class checker
├── docs/                        in-depth design and operations documentation
├── design/                      design specification
├── portfolio-screenshots/       screenshots used in this README
└── .github/workflows/lint.yml   ruff + eslint + tsc on every push to main and every PR
```

---

## 🔧 Configuration

Backend settings live in `backend/.env` (template: [`backend/.env.example`](backend/.env.example)). Every value has a default in `app/config.py`.

| Variable | Required | Purpose |
|---|:---:|---|
| `E2B_API_KEY` | ✅ | Sandbox for the Code section |
| `OPENCODE_API_KEY` | ⭐ | Default models and the Auto routing pool |
| `OPENROUTER_API_KEY` | – | Nemotron 3 Ultra, Llama 4 Scout |
| `GROQ_API_KEY` | – | GPT-OSS 120B; also names sessions |
| `EXA_API_KEY` | – | `web_search` (the tool reports an error without it) |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY` | – | Persistence, auth, uploads, notebooks |
| `TEVILY_API_KEY`, `JINA_AI_READER_API_KEY`, `STABILITY_API_KEY`, `RESEND_API_KEY` | – | One tool each on the specialist agents (search, URL reader, image generation, email send) |
| `E2B_TEMPLATE` | – | Custom sandbox image built from `backend/sandbox/` |
| `GIT_PUSH_TOKEN` | – | Lets a sandbox `git push`; held server-side, never written into the sandbox |
| `REQUIRE_AUTH` | – | `1` requires a signed-in user |
| `ALLOWED_ORIGINS` | – | CORS and websocket origin allowlist (never `*`) |
| `DEFAULT_MODEL_*`, `AUTO_*` | – | Per-section default models and Auto-routing thresholds |
| `MAX_AGENT_ITERATIONS`, `BASH_TIMEOUT_SECONDS`, `SANDBOX_IDLE_TIMEOUT_SECONDS`, `RATE_LIMIT_*` | – | Guardrails (defaults: 50, 30 s, 900 s, 20 msgs/min) |
| `POSTGRES_CHECKPOINT_URL` | – | Postgres checkpointer for production (SQLite otherwise) |

✅ required · ⭐ required for the default configuration · – optional

The frontend reads only `frontend/.env.local`:

| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_BACKEND_WS_URL` | Backend websocket URL (`ws://localhost:8000` by default) |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase client for auth (the anon key is public by design and gated by RLS) |

> [!IMPORTANT]
> Never put a vendor key in a `NEXT_PUBLIC_*` variable: that prefix inlines the value into the client bundle. See [docs/security.md](docs/security.md).

---

## 💡 Usage examples

| Section | Try |
|---|---|
| 🧑‍💻 **Code** | *"Build a Pomodoro timer with React and Vite, then start it so I can try it."* The agent scaffolds, writes the files, starts the dev server and opens the Preview tab. |
| 🧑‍💻 **Code** | Use the composer's **+ → Open a folder** with your own repo, then ask *"Explain this codebase"*. The agent reads the tree first. |
| 🧑‍💻 **Code** | Paste a stack trace. The agent reproduces it in the sandbox before fixing it, and you can follow along in the terminal (`Ctrl J`). |
| 💬 **Chat** | *"What is the difference between a process and a thread?"* Then edit the question to fork the conversation, and switch between branches with `‹ 1/2 ›`. |
| 📚 **Learn** | Create a notebook, add a PDF or link, and ask *"Summarise these sources in five bullets"*. Click a `[n]` citation to see the passage behind it. |
| 📚 **Learn** | Open the **RAG** course, finish a chapter group, and take the assessment to get your weak areas. |
| 🧩 **Agents** | Ask the **Research & Fact-Checker** to verify a claim, or the **UI Component Designer** for a Tailwind component with a live preview. |

---

## 🛡️ Requirements and limitations

- **Provider keys are required for any AI feature.** With no working model provider, Chat, Code, Agents and notebook Q&A cannot answer. Without `OPENCODE_API_KEY`, Auto mode falls back to a fixed per-section route.
- **The Code section needs E2B.** Sandboxes are torn down after 15 idle minutes, and anything not exported or pushed goes with them.
- **Notebook retrieval is lexical.** Embeddings are local hashed vectors, and PDFs are read from their text layer only (no OCR).
- **Single-instance by default.** Rate limits, the sandbox registry and pending approvals are in-process. The default checkpointer is SQLite on local disk; set `POSTGRES_CHECKPOINT_URL` for real persistence.
- **One concurrent run per session.** Stop the current turn before sending another.
- **No license file has been added yet.** Until one is, default copyright applies.

The full, reasoned list is in **[docs/known-limits.md](docs/known-limits.md)**.

---

## 🗺️ Future improvements

These are gaps the documentation already names, not new promises:

- [ ] Persist `LOOM.md` on the project row so it outlives a sandbox's idle teardown
- [ ] Move rate limiting to a shared store (e.g. Redis) before scaling horizontally
- [ ] A semantic embedding model and OCR for scanned PDFs in Learn
- [ ] Cross-section history search
- [ ] A Mermaid renderer, so diagrams can become an artifact kind
- [ ] Near-duplicate detection for learned memories

---

## 📖 Documentation

| Document | Covers |
|---|---|
| [Architecture](docs/architecture.md) | Agent graph, websocket contract, live preview, sandbox editing, terminal, folder import, keyboard |
| [Interface behaviour](docs/interface.md) | Session history, message actions, branching, stopping, search, settings, attachments |
| [Learn](docs/learn.md) | Course platform, grading and weak areas, notebooks, retrieval |
| [Models and routing](docs/models.md) | Model selection, Auto routing, automatic fallback, refused vendors |
| [Projects, memory, git and artifacts](docs/features.md) | Projects, memory and the learning pass, codebase analysis, version control, artifacts |
| [Local setup](docs/setup.md) | Full setup, Supabase schema, sign-in providers |
| [Security](docs/security.md) | Key handling, guardrails, the perimeter around auth, smoke test |
| [Performance](docs/performance.md) | What may sit on the latency path, and measured results |
| [Deployment](docs/deployment.md) | Render and Vercel |
| [Known limits](docs/known-limits.md) | What the project deliberately does not do, or does not do yet |

---

<div align="center">
<sub>Built by <a href="https://github.com/vinayak533">vinayak533</a></sub>
</div>
