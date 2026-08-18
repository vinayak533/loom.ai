-- Supabase schema. Run this in the SQL editor of your Supabase project.
-- `users` is provided by Supabase Auth (auth.users) — we only reference it.

create extension if not exists "uuid-ossp";
create extension if not exists vector;   -- pgvector, powers Learn's notebook retrieval

-- ---------------------------------------------------------------- sessions
create table if not exists public.sessions (
  id          uuid primary key default uuid_generate_v4(),
  user_id     uuid references auth.users (id) on delete cascade,
  title       text        not null default 'New session',
  -- A router model id, or the literal 'auto' for task-based routing.
  model_id    text        not null default 'grok-4-5',
  sandbox_id  text,
  status      text        not null default 'idle',   -- idle | running | error
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
-- Add model_id to an existing table without recreating it.
alter table public.sessions
  add column if not exists model_id text not null default 'grok-4-5';
-- History management: a pinned session sorts above everything else; an
-- archived one leaves the default list without being destroyed. Both are
-- plain flags rather than a single `state` column because they are
-- independent — you can pin something and later archive it.
alter table public.sessions
  add column if not exists is_pinned boolean not null default false;
alter table public.sessions
  add column if not exists is_archived boolean not null default false;
-- When a session was pinned, so the Pinned group can order by recency of the
-- pin rather than by activity. Null for unpinned rows.
alter table public.sessions
  add column if not exists pinned_at timestamptz;
-- A one-line description of what the session's project *is*. `title` is
-- generated from the first message; this is written later — by hand or from
-- the transcript — once there is something to describe. Null until then, which
-- is what the history list keys off to decide whether to show a second line.
alter table public.sessions
  add column if not exists description text;
-- Which specialist agent owns this session, for the Agentic Loop section.
-- Null for every Chat and Code session, which is exactly what the default
-- session list filters on — so adding agent sessions cannot pollute the
-- history the other three sections already show.
alter table public.sessions
  add column if not exists agent_id text;
create index if not exists sessions_agent_idx
  on public.sessions (user_id, agent_id, is_archived, updated_at desc);

-- Which product surface created this session: 'chat' | 'code'.
--
-- `agent_id` above discriminates the Agentic Loop's ten specialists from
-- everything else, but it cannot separate Chat from Code — both leave it null,
-- so a list filtered on `agent_id is null` returns *both* sections' sessions.
-- That is the session-bleed bug: a conversation started in Chat appeared in
-- Code's history and vice versa. This column is the missing discriminator.
--
-- Null means "created before this column existed" and is not the same as
-- either value: those rows are backfilled below where their section can be
-- inferred, and left null — reported by `scripts/audit_session_sections.py` —
-- where it cannot, rather than being silently guessed into one section.
alter table public.sessions
  add column if not exists section text;

-- Backfill, once, for rows that predate the column.
--
-- A Code session is the only kind that ever gets a sandbox or writes files, so
-- either of those is positive evidence. Nothing distinguishes an old Chat
-- session from an old Code session that never ran anything, so those stay null
-- and are surfaced for review instead of being assigned by coin flip.
update public.sessions
   set section = 'code'
 where section is null
   and (
     sandbox_id is not null
     or exists (select 1 from public.files f where f.session_id = sessions.id)
   );

create index if not exists sessions_section_idx
  on public.sessions (user_id, section, agent_id, is_archived, updated_at desc);
create index if not exists sessions_user_updated_idx
  on public.sessions (user_id, updated_at desc);
-- The default list is "not archived, pinned first, then most recent".
create index if not exists sessions_user_shelf_idx
  on public.sessions (user_id, is_archived, is_pinned, updated_at desc);

-- ---------------------------------------------------------------- messages
-- Every turn is stored, including tool calls and tool results, so the UI can
-- replay a full trace rather than just the final answer.
create table if not exists public.messages (
  id          uuid primary key default uuid_generate_v4(),
  session_id  uuid not null references public.sessions (id) on delete cascade,
  role        text not null,        -- user | assistant | tool_use | tool_result
  content     text,
  tool_calls  jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists messages_session_created_idx
  on public.messages (session_id, created_at);

-- ------------------------------------------------------------------- files
create table if not exists public.files (
  id            uuid primary key default uuid_generate_v4(),
  session_id    uuid not null references public.sessions (id) on delete cascade,
  filename      text not null,
  storage_path  text not null,
  file_type     text not null,
  created_at    timestamptz not null default now()
);
create index if not exists files_session_idx on public.files (session_id);

-- ------------------------------------------------------------- token_usage
create table if not exists public.token_usage (
  id             uuid primary key default uuid_generate_v4(),
  session_id     uuid not null references public.sessions (id) on delete cascade,
  model          text not null,
  model_id       text,                 -- which router model powered this call
  routing_mode   text not null default 'manual',  -- manual | auto
  routing_hint   text,                 -- classifier hint, null when manual
  input_tokens   integer not null default 0,
  output_tokens  integer not null default 0,
  cost_estimate  numeric(12, 6) not null default 0,
  created_at     timestamptz not null default now()
);
alter table public.token_usage
  add column if not exists model_id text;
-- Added with auto routing: how the model was chosen, so the classifier's
-- decisions can be reviewed against what the turns actually needed.
alter table public.token_usage
  add column if not exists routing_mode text not null default 'manual';
alter table public.token_usage
  add column if not exists routing_hint text;
create index if not exists token_usage_session_idx on public.token_usage (session_id);
create index if not exists token_usage_model_idx on public.token_usage (model_id);
-- Supports "which hint routed to which model, and what did it cost" rollups.
create index if not exists token_usage_routing_idx
  on public.token_usage (routing_mode, routing_hint);

-- ============================================================================
--  Credits — the per-user meter every agent turn is charged against
-- ============================================================================
-- `token_usage` above records what a call *cost*; this records what a person
-- has left. They are separate on purpose: usage is an append-only analytics
-- log keyed by session, while a balance is a single mutable number per user
-- that a turn is gated on before it starts.
--
-- `user_id` is nullable-by-way-of-sentinel rather than a real null: auth is
-- optional in this project (REQUIRE_AUTH=0), and anonymous usage still has to
-- be metered, so it all lands in one well-known row instead of being free.

create table if not exists public.user_credits (
  user_id     text        primary key,   -- auth.users.id, or 'anonymous'
  balance     numeric(14, 4) not null default 0,
  granted     numeric(14, 4) not null default 0,   -- lifetime grants
  spent       numeric(14, 4) not null default 0,   -- lifetime debits
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- Every movement, so a balance can always be explained. Nothing here is ever
-- updated or deleted; a correction is another row.
create table if not exists public.credit_ledger (
  id          uuid primary key default uuid_generate_v4(),
  user_id     text        not null,
  session_id  text,                    -- not a FK: an agent turn may outlive
                                       -- its session row being deleted, and
                                       -- losing the audit trail is worse.
  agent_id    text,                    -- which specialist agent spent it
  kind        text        not null,    -- grant | llm | tool | refund
  -- Positive credits the account, negative debits it. Signed rather than a
  -- separate direction column so SUM(amount) is the balance by construction.
  amount      numeric(14, 4) not null,
  reason      text,
  model_id    text,
  created_at  timestamptz not null default now()
);
create index if not exists credit_ledger_user_idx
  on public.credit_ledger (user_id, created_at desc);
create index if not exists credit_ledger_agent_idx
  on public.credit_ledger (agent_id, created_at desc);

-- ============================================================================
--  Agentic Loop — approval gate audit (Agent 9)
-- ============================================================================
-- The live pause/resume happens in process (see app/agents/approvals.py) —
-- an approval is only meaningful while a socket is open to answer it. This
-- table is the record of what was asked and what the human decided, which is
-- the half that has to outlive the process.
create table if not exists public.agent_approvals (
  id           uuid primary key default uuid_generate_v4(),
  session_id   text        not null,
  agent_id     text        not null,
  action       text        not null,   -- e.g. 'send_email', 'generate_image'
  parameters   jsonb       not null default '{}',
  risk         text        not null default 'medium',  -- low | medium | high
  -- pending | approved | rejected | edited | expired
  decision     text        not null default 'pending',
  -- What the user actually approved, which is not always what was proposed:
  -- the edit path rewrites the parameters before the tool runs.
  final_parameters jsonb,
  requested_at timestamptz not null default now(),
  decided_at   timestamptz
);
create index if not exists agent_approvals_session_idx
  on public.agent_approvals (session_id, requested_at desc);

-- ============================================================================
--  Learn — notebooks, sources, retrieval, notes, lessons, progress
-- ============================================================================
-- A notebook is a study workspace: a bag of sources, a chat grounded in them,
-- free-form notes, and an optional generated course. It is deliberately not a
-- `session` — sessions belong to the agent loop and carry a checkpoint; a
-- notebook carries documents.

create table if not exists public.notebooks (
  id           uuid primary key default uuid_generate_v4(),
  user_id      uuid references auth.users (id) on delete cascade,
  title        text        not null default 'Untitled notebook',
  -- A seed string, not a URL. Covers are generated client-side from it so a
  -- notebook has an identity without an image pipeline behind it.
  cover_image  text,
  is_archived  boolean     not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists notebooks_user_updated_idx
  on public.notebooks (user_id, is_archived, updated_at desc);

-- ------------------------------------------------------------------ sources
-- `content` holds the extracted plain text for every type, including PDFs —
-- retrieval reads text, never the original bytes. `storage_path` points at the
-- original in the `uploads` bucket when there is one.
create table if not exists public.notebook_sources (
  id            uuid primary key default uuid_generate_v4(),
  notebook_id   uuid not null references public.notebooks (id) on delete cascade,
  source_type   text not null,          -- pdf | url | text
  title         text not null default 'Untitled source',
  url           text,
  storage_path  text,
  content       text,
  char_count    integer not null default 0,
  status        text not null default 'ready',   -- ready | failed
  error         text,
  added_at      timestamptz not null default now()
);
create index if not exists notebook_sources_notebook_idx
  on public.notebook_sources (notebook_id, added_at);

-- ------------------------------------------------------------------- chunks
-- The retrieval index. One row per chunk of one source, with its embedding.
-- 768 dimensions because that is what `app/learn/embeddings.py` emits in both
-- of its modes (hashed local vectors, or a provider call asked for 768) — one
-- column type means switching providers is not a migration.
create table if not exists public.notebook_chunks (
  id           uuid primary key default uuid_generate_v4(),
  notebook_id  uuid not null references public.notebooks (id) on delete cascade,
  source_id    uuid not null references public.notebook_sources (id) on delete cascade,
  chunk_index  integer not null default 0,
  content      text not null,
  embedding    vector(768),
  created_at   timestamptz not null default now()
);
create index if not exists notebook_chunks_notebook_idx
  on public.notebook_chunks (notebook_id);
-- HNSW, not IVFFlat.
--
-- The IVFFlat index this replaces was declared with `lists = 100` and silently
-- broke retrieval on every notebook. IVFFlat partitions the vectors into lists
-- and, at query time, scans only `ivfflat.probes` of them — which defaults to
-- 1. With a hundred lists over a personal-scale corpus of a few dozen chunks,
-- almost every list is empty, so a query would probe an empty one and return
-- *zero rows* while the chunks sat right there in the table. It was not a
-- ranking problem, it was a total retrieval failure, and it looked exactly
-- like "the source was never ingested": the notebook chat answered "there's
-- nothing in this notebook yet" over a fully indexed document.
--
-- HNSW has no such training/​occupancy requirement — it is a graph, correct
-- from the first row — so it is the right structure for a corpus that starts
-- at zero and may never grow past a few thousand chunks. Cosine to match the
-- normalised vectors the embedder emits.
drop index if exists public.notebook_chunks_embedding_idx;
create index if not exists notebook_chunks_embedding_hnsw_idx
  on public.notebook_chunks using hnsw (embedding vector_cosine_ops);

-- The retrieval call. Kept as a function so the backend sends one round trip
-- per question instead of pulling the whole corpus over the wire to score it
-- in Python. `app/learn/retrieval.py` falls back to doing exactly that if this
-- function is missing, so an un-migrated database still answers.
create or replace function public.match_notebook_chunks(
  p_notebook_id uuid,
  p_query_embedding vector(768),
  p_match_count int default 8
)
returns table (
  id uuid,
  source_id uuid,
  chunk_index int,
  content text,
  similarity float
)
language sql stable
as $$
  select c.id,
         c.source_id,
         c.chunk_index,
         c.content,
         1 - (c.embedding <=> p_query_embedding) as similarity
  from public.notebook_chunks c
  where c.notebook_id = p_notebook_id
    and c.embedding is not null
  order by c.embedding <=> p_query_embedding
  limit p_match_count;
$$;

-- -------------------------------------------------------------------- notes
create table if not exists public.notebook_notes (
  id           uuid primary key default uuid_generate_v4(),
  notebook_id  uuid not null references public.notebooks (id) on delete cascade,
  content      text not null default '',
  -- ai_generated notes were saved out of a chat answer; user_written ones were
  -- typed. The distinction is shown in the UI, so it is stored, not inferred.
  source       text not null default 'user_written',  -- ai_generated | user_written
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists notebook_notes_notebook_idx
  on public.notebook_notes (notebook_id, updated_at desc);

-- ------------------------------------------------------------------ lessons
create table if not exists public.notebook_lessons (
  id             uuid primary key default uuid_generate_v4(),
  notebook_id    uuid not null references public.notebooks (id) on delete cascade,
  section_title  text not null,
  section_order  integer not null default 0,
  summary        text,
  content        text,
  quiz_data      jsonb,
  created_at     timestamptz not null default now()
);
create index if not exists notebook_lessons_notebook_idx
  on public.notebook_lessons (notebook_id, section_order);

-- ----------------------------------------------------------------- progress
create table if not exists public.notebook_progress (
  id            uuid primary key default uuid_generate_v4(),
  notebook_id   uuid not null references public.notebooks (id) on delete cascade,
  user_id       uuid references auth.users (id) on delete cascade,
  section_id    uuid not null references public.notebook_lessons (id) on delete cascade,
  completed     boolean not null default false,
  completed_at  timestamptz
);
-- One progress row per person per section; the upsert on completion relies on it.
create unique index if not exists notebook_progress_unique_idx
  on public.notebook_progress (section_id, coalesce(user_id, '00000000-0000-0000-0000-000000000000'::uuid));
create index if not exists notebook_progress_notebook_idx
  on public.notebook_progress (notebook_id);

-- ============================================================================
--  Learn — the course platform
-- ============================================================================
-- Courses, chapters, resources and exam questions are *static content*, held in
-- `backend/app/learn/courses/`. Only what a person did with them is stored, so
-- a course can be rewritten, re-ordered or replaced without a migration — the
-- ids in these tables are the content's own string ids, not foreign keys.

create table if not exists public.course_progress (
  id             uuid primary key default uuid_generate_v4(),
  user_id        uuid references auth.users (id) on delete cascade,
  course_id      text        not null,
  chapter_id     text        not null,
  completed      boolean     not null default false,
  completed_at   timestamptz,
  -- Written when a chapter is opened, not only when it is finished: this is
  -- what "Continue learning" resumes from.
  last_accessed  timestamptz not null default now()
);
-- One row per person per chapter; the upsert in learn_repository relies on it.
create unique index if not exists course_progress_unique_idx
  on public.course_progress (
    course_id, chapter_id,
    coalesce(user_id, '00000000-0000-0000-0000-000000000000'::uuid)
  );
create index if not exists course_progress_user_idx
  on public.course_progress (user_id, course_id, last_accessed desc);

create table if not exists public.course_exam_attempts (
  id             uuid primary key default uuid_generate_v4(),
  user_id        uuid references auth.users (id) on delete cascade,
  course_id      text        not null,
  exam_id        text        not null,
  score          integer     not null default 0,   -- percent, 0-100
  correct_count  integer     not null default 0,
  total_count    integer     not null default 0,
  passed         boolean     not null default false,
  -- Denormalised on purpose: the analysis is what the results screen shows, and
  -- recomputing it later would need the exam content as it was *at the time*.
  weak_topics    jsonb       not null default '[]',
  topic_scores   jsonb       not null default '[]',
  answers        jsonb       not null default '{}',
  created_at     timestamptz not null default now()
);
create index if not exists course_exam_attempts_user_idx
  on public.course_exam_attempts (user_id, course_id, created_at desc);

-- ------------------------------------------------------------- RLS policies
-- The backend uses the service-role key and bypasses RLS. These policies exist
-- so the browser (anon key) can only ever read its own rows.
alter table public.sessions    enable row level security;
alter table public.messages    enable row level security;
alter table public.files       enable row level security;
alter table public.token_usage enable row level security;

drop policy if exists "own sessions" on public.sessions;
create policy "own sessions" on public.sessions
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "own messages" on public.messages;
create policy "own messages" on public.messages
  for select using (
    exists (select 1 from public.sessions s
            where s.id = messages.session_id and s.user_id = auth.uid())
  );

drop policy if exists "own files" on public.files;
create policy "own files" on public.files
  for select using (
    exists (select 1 from public.sessions s
            where s.id = files.session_id and s.user_id = auth.uid())
  );

drop policy if exists "own usage" on public.token_usage;
create policy "own usage" on public.token_usage
  for select using (
    exists (select 1 from public.sessions s
            where s.id = token_usage.session_id and s.user_id = auth.uid())
  );

-- Credits and the approval audit. The backend holds the service-role key and
-- bypasses all of these; they exist so the anon key can only ever read the
-- signed-in person's own balance and their own approval history. `user_id` is
-- text here (it carries the 'anonymous' sentinel), so the comparison casts.
alter table public.user_credits    enable row level security;
alter table public.credit_ledger   enable row level security;
alter table public.agent_approvals enable row level security;

drop policy if exists "own balance" on public.user_credits;
create policy "own balance" on public.user_credits
  for select using (auth.uid()::text = user_id);

drop policy if exists "own ledger" on public.credit_ledger;
create policy "own ledger" on public.credit_ledger
  for select using (auth.uid()::text = user_id);

drop policy if exists "own approvals" on public.agent_approvals;
create policy "own approvals" on public.agent_approvals
  for select using (
    exists (select 1 from public.sessions s
            where s.id::text = agent_approvals.session_id
              and s.user_id = auth.uid())
  );

-- Learn. Same shape: the backend holds the service-role key and bypasses these;
-- they exist so the anon key can never read another user's notebook.
alter table public.notebooks         enable row level security;
alter table public.notebook_sources  enable row level security;
alter table public.notebook_chunks   enable row level security;
alter table public.notebook_notes    enable row level security;
alter table public.notebook_lessons  enable row level security;
alter table public.notebook_progress enable row level security;

drop policy if exists "own notebooks" on public.notebooks;
create policy "own notebooks" on public.notebooks
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

do $$
declare
  t text;
begin
  foreach t in array array[
    'notebook_sources', 'notebook_chunks', 'notebook_notes',
    'notebook_lessons', 'notebook_progress'
  ]
  loop
    execute format('drop policy if exists "own %1$s" on public.%1$I', t);
    execute format(
      'create policy "own %1$s" on public.%1$I for select using ('
      '  exists (select 1 from public.notebooks n'
      '          where n.id = %1$I.notebook_id and n.user_id = auth.uid()))',
      t
    );
  end loop;
end $$;

-- The course platform. Rows are per-person, so the policy is a direct
-- ownership check rather than a join through a parent table.
alter table public.course_progress      enable row level security;
alter table public.course_exam_attempts enable row level security;

drop policy if exists "own course progress" on public.course_progress;
create policy "own course progress" on public.course_progress
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "own exam attempts" on public.course_exam_attempts;
create policy "own exam attempts" on public.course_exam_attempts
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ------------------------------------------------------------------ storage
-- Create a private bucket named `uploads` in Storage, or run:
insert into storage.buckets (id, name, public)
values ('uploads', 'uploads', false)
on conflict (id) do nothing;
