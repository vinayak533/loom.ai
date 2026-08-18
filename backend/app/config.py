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
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model_a: str = "llama-3.3-70b-versatile"
    groq_max_tokens: int = 8000

    # xAI (Grok, direct API — NOT via OpenRouter). This is now the primary
    # provider: Grok 4.5 is the default model for every section.
    #
    # `grok-4.5` is the id xAI's own model docs publish (aliases: grok-4.5-latest,
    # grok-build-latest). 500k context, tool calling and image input both
    # supported, structured outputs supported. Do not "tidy" this string into
    # `grok-4-5` — the registry *key* uses dashes, the wire id uses a dot.
    xai_api_key: str = ""
    xai_base_url: str = "https://api.x.ai/v1"
    xai_model_grok_45: str = "grok-4.5"
    # Raised from the old 8000: Grok 4.5 is now the agent's main driver and a
    # tool loop needs headroom to write a real answer after its calls.
    xai_max_tokens: int = 16000

    # OpenCode Go (OpenAI-compatible gateway — verified against the live API,
    # see OpenCodeAdapter). The Go plan's base URL differs from Zen's
    # (`/zen/v1`): Go serves its own curated catalogue under `/zen/go/v1`.
    opencode_api_key: str = ""
    opencode_base_url: str = "https://opencode.ai/zen/go/v1"
    opencode_model_fast: str = "deepseek-v4-flash"
    opencode_model_edit: str = "minimax-m2.7"
    opencode_model_visual: str = "qwen3.7-plus"
    opencode_model_complex: str = "mimo-v2.5"
    opencode_max_tokens: int = 8000

    # Which model_id a new session starts with unless the user picks otherwise.
    default_model_id: str = "grok-4-5"

    # --- Auto router ------------------------------------------------------
    # Auto mode classifies the live turn (see app.agent.task_classifier) and
    # maps the resulting hint onto an OpenCode model. When OpenCode is not
    # configured it degrades to the older section-based table below.
    auto_route_chat: str = "grok-4-5"
    auto_route_learning: str = "nemotron-3"
    auto_route_code: str = "llama-4-scout"

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
    def xai_enabled(self) -> bool:
        return bool(self.xai_api_key)

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

    def public_summary(self) -> dict:
        """Booleans only. Safe to return over HTTP — never the values."""
        return {
            "openrouter": self.openrouter_enabled,
            "groq": self.groq_enabled,
            "xai": self.xai_enabled,
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
            "credits_enabled": self.credits_enabled,
            "default_model_id": self.default_model_id,
            "auto_routes": self.auto_routes,
            # True once OpenCode is configured: Auto classifies each turn
            # instead of using the section table above.
            "auto_task_routing": self.opencode_enabled,
            "max_iterations": self.max_agent_iterations,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
