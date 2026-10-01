# Known limits

> What the project deliberately does not do, or does not do yet.
>
> [← Back to the README](../README.md)

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
  personal notebook; a semantic embedding model is not wired in.
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
* **Duplicate memories are only caught on an exact match.** A rephrasing of a
  fact already known is stored again. Near-duplicate detection needs
  embeddings, and the failure mode of a slightly redundant list is much better
  than the failure mode of a fuzzy match, which is silently dropping a real
  new fact.
* **The account-level default model needs an account.** Anonymous sessions are
  per-device by definition, so the control is disabled when signed out rather
  than writing to a shared row.
