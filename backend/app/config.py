"""Central configuration.

Every vendor key lives here and here only. Nothing in this module is ever
serialised into a websocket event or an HTTP response body — see
`Settings.public_summary()` for the only thing that is safe to expose.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLMs -------------------------------------------------------------
    # OpenRouter (an OpenAI-compatible gateway; the vendor itself is not used).
    openrouter_api_key: str = ""
    # Two selectable slugs. `_b` has been removed; `_c` keeps its name so
    # existing .env files carry on working unchanged.
    openrouter_model_a: str = "nvidia/nemotron-3-ultra-550b-a55b"
    # Llama 4 Scout is served here, not by Groq — Groq has retired it and its
    # /models list no longer includes any llama-4 variant.
    openrouter_model_c: str = "meta-llama/llama-4-scout"
    openrouter_max_tokens: int = 8000

    # Groq (OpenAI-compatible, direct API)
    #
    # `llama-3.3-70b-versatile` was retired: Groq's live /models list no longer
    # includes any llama-3.x chat model, and calling it returns HTTP 404
    # `model_not_found`, which surfaced as a failed title generation on every
    # first message. `openai/gpt-oss-120b` is the largest general chat model
    # Groq currently serves and was verified against the live API for both
    # plain completion and tool calling before being put here.
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model_a: str = "openai/gpt-oss-120b"
    # 4000, not 8000, and the difference is the whole reason Groq worked at all
    # here. Groq's free `on_demand` tier caps at 8000 *tokens per minute*, and
    # it counts `max_tokens` toward that budget rather than the tokens actually
    # produced. At 8000 the budget was exhausted by the request's own ceiling
    # before a single prompt token was counted, so every call — even "say
    # PONG" — came back:
    #
    #   413 rate_limit_exceeded: Request too large ... on tokens per minute
    #   (TPM): Limit 8000, Requested 8074
    #
    # 4000 leaves 4000 tokens of prompt headroom under the same cap. A paid
    # tier lifts the limit and this can go back up; gpt-oss-120b itself allows
    # 65,536 completion tokens, so nothing about the model constrains it.
    groq_max_tokens: int = 4000

    # The providers above and below are the whole list. A removed provider's
    # variables left over in a deployment's .env are ignored rather than
    # rejected (`extra="ignore"` above), so they are harmless and can simply be
    # deleted.
    #
    # Note that the model slugs below are the *only* thing deciding which model
    # each registry entry serves, which is why a slug alone is not trusted:
    # `llm_router.BANNED_MODEL_SUBSTRINGS` refuses a set of vendors outright,
    # whatever entry they are configured under. Read that note before assuming
    # an .env change is enough to switch a slot to any model you like.

    # OpenCode Go (OpenAI-compatible gateway — verified against the live API,
    # see OpenCodeAdapter). The Go plan's base URL differs from Zen's
    # (`/zen/v1`): Go serves its own curated catalogue under `/zen/go/v1`.
    opencode_api_key: str = ""
    opencode_base_url: str = "https://opencode.ai/zen/go/v1"
    opencode_model_fast: str = "deepseek-v4-flash"
    # `minimax-m2.7` used to be here and had to go: it is still on OpenCode
    # Go's /models list and it answers HTTP 500 to *everything* — probed 9/9
    # failures, including "reply with exactly PONG" with no tools attached, so
    # it is the model and not this project's request shape. It is Auto's
    # `code_editing` target, which made every auto-routed code edit open with a
    # guaranteed failed call. `minimax-m3` is the same family, live, and
    # verified here for streaming and tool calling against the real coding
    # toolset. (`minimax-m2.5` also works if a more conservative step back from
    # 2.7 is ever wanted.)
    opencode_model_edit: str = "minimax-m3"
    opencode_model_visual: str = "qwen3.7-plus"
    opencode_model_complex: str = "mimo-v2.5"
    # A *different model* from the one above, not a newer label for it. Both
    # ids are on OpenCode Go's live /models list and they differ in a way that
    # matters: `mimo-v2.5` reads images, `mimo-v2.5-pro` rejects them.
    opencode_model_complex_pro: str = "mimo-v2.5-pro"
    # 32000, not 8000, and the reason is the same class of failure as Groq's
    # cap above — read in the other direction. A single-file deliverable ("a
    # one-page site") is 12-20k output tokens, so at 8000 the model was cut
    # off mid-`file_write`, every time, on a request it was otherwise
    # answering well. The truncated arguments then failed to parse and the run
    # retried the same doomed call until it ran out of iterations. The parse
    # failure is now caught and explained (`agent.graph._broken_arguments`),
    # but a ceiling below the size of the thing being asked for is its own
    # bug. Nothing on this gateway required 8000: 16000, 32000 and 64000 were
    # all probed against the live API, and 32000 specifically against every
    # one of the five models these settings serve. Unlike Groq, the ceiling is
    # not itself billed — usage is reported from tokens produced — so the
    # headroom is free until it is used.
    opencode_max_tokens: int = 32000

    # Which model_id a new session starts with unless the user picks otherwise.
    #
    # `qwen3_7_plus` is the roster-wide pick because it is the only model that
    # is *verified against the live API* for both halves of what a default has
    # to do: real tool calling, and actually reading an attached image. It
    # remains the answer for any surface that does not name its own default
    # below, and the stand-in whenever a section's own pick has no key.
    default_model_id: str = "qwen3_7_plus"

    # --- Per-section defaults ---------------------------------------------
    # A new session opens on the default for *the section that created it*,
    # not on one number for the whole product. The sections do genuinely
    # different work and the right opening model differs accordingly:
    #
    #   code      MiMo V2.5 — long-horizon coding is its stated speciality,
    #             and it reads images, so a screenshot pasted into a Code
    #             session still lands. Verified end to end on the "build me a
    #             page and serve it" flow.
    #   chat      DeepSeek V4 Flash — the cheapest and fastest entry in the
    #             pool, which is what a conversational surface should open on.
    #             Text only; an attachment degrades to a note, and Auto still
    #             reroutes a turn carrying an image to a model that can see.
    #   learning  MiMo V2.5, matching `learn.tutor.LEARN_MODELS`, which had
    #             already chosen it independently.
    #   agents    left on the roster default: the ten specialists span every
    #             kind of work and one section-wide pick would be wrong for
    #             most of them.
    #
    # Each is only a *preference*. `llm_router.section_default_model()` runs
    # every one of these through `first_available()`, so a section whose pick
    # has no configured key opens on something that works instead of on a dead
    # first message.
    default_model_chat: str = "deepseek_v4_flash"
    default_model_code: str = "mimo_v2_5"
    default_model_learning: str = "mimo_v2_5"
    default_model_agents: str = ""

    # --- Auto router ------------------------------------------------------
    # Auto mode classifies the live turn (see app.agent.task_classifier) and
    # maps the resulting hint onto an OpenCode model. When OpenCode is not
    # configured it degrades to the older section-based table below.
    #
    # These now mirror the per-section defaults above rather than pointing at
    # three unrelated models. The table is the *degraded* path — it is what
    # Auto resolves to when there is no task-routing pool — and answering with
    # something other than the section's own default there is a difference the
    # user cannot see the reason for. `auto_route_code` in particular pointed
    # at `llama-4-scout`, an OpenRouter entry, which put the Code section's
    # degraded route on a different provider from everything else it does.
    auto_route_chat: str = "deepseek_v4_flash"
    auto_route_learning: str = "mimo_v2_5"
    auto_route_code: str = "mimo_v2_5"

    # --- Auto router thresholds -------------------------------------------
    # Every tunable the classifier reads lives here, so its rules can be
    # retuned without touching routing or dispatch code.
    #
    # Context size at which a turn counts as "large" and escalates to the
    # long-horizon model. Estimated at ~4 characters per token.
    auto_complex_context_tokens: int = 24_000
    # Agent iterations already spent in this task before it counts as
    # long-horizon (a run that keeps going is, empirically, a hard one).
    auto_complex_iteration_count: int = 6
    # Distinct numbered/bulleted steps in the agent's own plan before the turn
    # counts as a long-horizon multi-step plan.
    auto_complex_plan_steps: int = 5
    # How many trailing messages the classifier inspects for images and for
    # recent file-editing tool calls.
    auto_lookback_messages: int = 6

    # --- Embeddings (Learn section retrieval) -----------------------------
    # There is no embeddings provider key any more. Notebook chunks are
    # embedded in-process by the hashed bag-of-words vectoriser in
    # app/learn/embeddings.py, which emits the 768 dimensions the
    # `notebook_chunks.embedding` column is declared as. See that module's
    # docstring for why that is a reasonable retriever at notebook scale.

    # --- Tools ------------------------------------------------------------
    e2b_api_key: str = ""
    exa_api_key: str = ""
    # Which E2B template every session sandbox is created from. Empty means
    # E2B's stock base image, which is what this project has always run on.
    #
    # The point of a *named* template is that anything a tool needs beyond
    # that base — ruff for `lint_code` is the current case — is baked into an
    # image built from `backend/sandbox/e2b.Dockerfile`, versioned and pinned
    # there, rather than `pip install`ed into a user's live sandbox at the
    # moment a tool discovers it is missing. A runtime install changes what a
    # session can do depending on which tool happened to run first, and it
    # cannot be reproduced from the repository. Build the template once with
    # `e2b template build` (see `backend/sandbox/README.md`), then set this to
    # its name or id.
    e2b_template: str = ""
    # The escape hatch for a deployment that cannot build a template: let
    # `lint_code` run `pip install ruff` inside the sandbox on first use. Off
    # by default on purpose — see the note above — and when it is off the tool
    # reports a syntax-only check as degraded rather than installing anything.
    sandbox_runtime_linter_install: bool = False

    # --- Terminal ---------------------------------------------------------
    # How long a command typed into the Code section's terminal panel may
    # run. Longer than `bash_timeout_seconds` (the agent's own ceiling): the
    # agent is told to background anything slow and split its work up, but a
    # person typing `npm install` expects to wait for it, and 30 seconds is
    # not enough for that on a cold sandbox.
    terminal_timeout_seconds: int = 120

    # --- Git push ---------------------------------------------------------
    # The credential the sandbox pushes with, held by the *server* and handed
    # to exactly one `git push` at a time through that command's environment.
    # It is never written into the sandbox image, the repository's config, or
    # any file in the sandbox, and it never leaves this process in an event.
    #
    # A fine-grained GitHub token with `contents: write` on the target repos
    # is the intended value; any https remote that accepts token-as-password
    # works the same way. `GITHUB_TOKEN` is accepted as an alias because that
    # is what CI environments already export. With neither set, `push` reports
    # that no credential is configured and does nothing.
    git_push_token: str = Field(default="", validation_alias=AliasChoices(
        "GIT_PUSH_TOKEN", "GITHUB_TOKEN", "git_push_token", "github_token"
    ))

    # --- Agentic Loop: the ten specialist agents --------------------------
    # Each of these powers exactly one tool on one agent. Every one is
    # optional: a missing key disables that single tool and the agent reports
    # a "not configured" state for it rather than fabricating a result. See
    # `app/agents/tool_registry.py`, where every tool declares `requires_key`.
    #
    # Note the spelling of `TEVILY_API_KEY` — it is how the key was named in
    # this project's .env, and renaming it would silently unconfigure the
    # search tool on an existing deployment. The alias below accepts the
    # correct spelling too, so a future .env can use either.
    tevily_api_key: str = Field(default="", validation_alias=AliasChoices(
        "TEVILY_API_KEY", "TAVILY_API_KEY", "tevily_api_key", "tavily_api_key"
    ))
    # Jina Reader — URL -> Markdown. Agent 7's scraper.
    jina_ai_reader_api_key: str = ""
    # Stability AI — Agent 5's image generation. `core` is the cheapest model
    # on the v2beta endpoint (3 Stability credits per image); see
    # `app/agents/tools/imagery.py` before changing it, because the request
    # shape differs per model family.
    stability_api_key: str = ""
    image_gen_provider: str = "stability"
    stability_model: str = "core"
    # Resend — Agent 2's real send path. Wired, but every send goes through
    # Agent 9's approval gate first; nothing is ever sent unprompted.
    resend_api_key: str = ""
    resend_from_email: str = "onboarding@resend.dev"
    # LangSmith tracing. Off unless `langsmith_tracing` is also true, so
    # simply having the key present does not start shipping traces.
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "atlas-agentic-loop"

    # --- Credits ----------------------------------------------------------
    # See app/credits.py. `credit_usd` is what one credit is worth, which is
    # what converts the router's per-call cost estimate into a debit.
    credits_enabled: bool = True
    credit_usd: float = 0.001
    credit_starting_balance: float = 1000.0
    # Refuse to start a turn with less than this left, so a run cannot open
    # with a balance it is certain to overdraw on its first model call.
    credit_minimum_to_start: float = 1.0
    # How the meter behaves when the durable store is unreachable. It falls
    # back to an in-process ledger and keeps charging, then retries the store
    # on this interval (doubling up to the max) and flushes everything it
    # buffered once the store answers. Nothing charged while degraded is
    # discarded — see `credit_spill_path`.
    credit_store_retry_seconds: float = 30.0
    credit_store_retry_max_seconds: float = 300.0
    # Where degraded-window movements are written so a restart cannot lose
    # them. Replayed at startup and flushed on the first successful probe.
    # This file existing means there is unreconciled usage.
    credit_spill_path: str = "./data/credit_spill.jsonl"
    # The most one turn may spend before it is cut off. `credit_minimum_to_start`
    # only guards the *opening* of a turn, which is why a balance of 19.64 could
    # end a single turn at -28.27: nothing was watching while the turn ran. This
    # is checked between iterations, in the same spirit as Agent 7's tool-call
    # budget — the turn gets one final, tool-free call to write up what it has
    # rather than being severed mid-thought.
    credit_turn_ceiling: float = 150.0
    # How long the balance the turn-opening gate reads may be, before it goes
    # back to the database. The gate is the one credit call sitting directly
    # between Enter and the model, and it cost a full Supabase round trip
    # (151 ms warm) on every turn. See `credits.ensure_can_start` for why a
    # snapshot is safe here and where it deliberately is not used. Set to 0 to
    # read through on every turn.
    credit_gate_cache_seconds: float = 20.0

    # --- Supabase ---------------------------------------------------------
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""

    # --- Runtime ----------------------------------------------------------
    allowed_origins: str = "http://localhost:3000"
    max_agent_iterations: int = 50
    sandbox_idle_timeout_seconds: int = 900  # 15 minutes
    bash_timeout_seconds: int = 30
    rate_limit_messages_per_minute: int = 20
    rate_limit_uploads_per_minute: int = 10
    checkpoint_db_path: str = "./data/checkpoints.sqlite"
    postgres_checkpoint_url: str = ""
    require_auth: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def supabase_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def openrouter_enabled(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def opencode_enabled(self) -> bool:
        return bool(self.opencode_api_key)

    @property
    def tavily_enabled(self) -> bool:
        return bool(self.tevily_api_key)

    @property
    def jina_enabled(self) -> bool:
        return bool(self.jina_ai_reader_api_key)

    @property
    def image_gen_enabled(self) -> bool:
        """True when Agent 5 can actually reach an image provider.

        Deliberately provider-aware rather than "is any key set": the tool
        dispatches on ``image_gen_provider``, so a Stability key with the
        provider set to something else is *not* configured, and the UI should
        say so instead of failing at call time.
        """
        if self.image_gen_provider == "stability":
            return bool(self.stability_api_key)
        return False

    @property
    def resend_enabled(self) -> bool:
        return bool(self.resend_api_key)

    @property
    def auto_routes(self) -> dict[str, str]:
        return {
            "chat": self.auto_route_chat,
            "learning": self.auto_route_learning,
            "code": self.auto_route_code,
        }

    @property
    def section_defaults(self) -> dict[str, str]:
        """Configured opening model per section. Preferences, not promises.

        An empty string means "no opinion, use `default_model_id`". Resolving
        these to something runnable is `llm_router.section_default_model()`'s
        job, not this property's — settings state intent, the router states
        what will actually happen.
        """
        return {
            "chat": self.default_model_chat,
            "code": self.default_model_code,
            "learning": self.default_model_learning,
            "agents": self.default_model_agents,
        }

    def public_summary(self) -> dict:
        """Booleans only. Safe to return over HTTP — never the values."""
        return {
            "openrouter": self.openrouter_enabled,
            "groq": self.groq_enabled,
            "opencode": self.opencode_enabled,
            "e2b": bool(self.e2b_api_key),
            "exa": bool(self.exa_api_key),
            "supabase": self.supabase_enabled,
            # Agentic Loop integrations. Booleans only, same rule as above —
            # the Agents UI reads these to decide which tools to show as
            # "not configured" before the user spends a turn discovering it.
            "tavily": self.tavily_enabled,
            "jina": self.jina_enabled,
            "image_gen": self.image_gen_enabled,
            "image_gen_provider": self.image_gen_provider,
            "resend": self.resend_enabled,
            # Whether a `git push` from a sandbox can authenticate. The panel
            # uses it to say "no credential configured" before someone sets a
            # remote and wonders why the push fails.
            "git_push": bool(self.git_push_token),
            "credits_enabled": self.credits_enabled,
            "default_model_id": self.default_model_id,
            # Per-section opening models. The client needs the whole map, not
            # just its own section's entry: it holds one selection per section
            # and restores all four before the user has picked a surface.
            "default_model_ids": self.section_defaults,
            "auto_routes": self.auto_routes,
            # True once OpenCode is configured: Auto classifies each turn
            # instead of using the section table above.
            "auto_task_routing": self.opencode_enabled,
            "max_iterations": self.max_agent_iterations,
            # Not a secret, and the client cannot behave correctly without it:
            # it decides whether signing out leaves a way back into the app
            # without an account, or a wall.
            "require_auth": self.require_auth,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
