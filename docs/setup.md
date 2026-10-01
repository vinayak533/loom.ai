# Local setup

> Full setup, including Supabase, schema migration and sign-in providers.
>
> [← Back to the README](../README.md)

## Prerequisites

Python 3.11+, Node 18+, and API keys for OpenCode and E2B at minimum.

## 1. Backend

Create a virtualenv in `backend/.venv` and install the runtime dependencies:

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

### Dependencies, and where each one is allowed to live

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

---

## 2. Verify the agent loop before touching the UI

```bash
cd backend
.venv/Scripts/python -m scripts.test_agent_loop "write fizzbuzz.py and run it"
```

This runs the full graph and prints every event the websocket would have sent —
thinking, tokens, tool calls, streamed stdout, diffs, token usage.

## 3. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # optional: defaults to ws://localhost:8000
npm run dev
```

Open http://localhost:3000.

## 4. Supabase (optional but recommended)

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

## 5. Sign-in providers (manual — cannot be scripted)

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
