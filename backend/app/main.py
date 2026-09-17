from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.agent import runner  # noqa: E402
from app.agents import runner as agents_runner  # noqa: E402
from app.api import agents as agents_api, agents_ws, learn, rest, ws  # noqa: E402
from app import credits  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import repository  # noqa: E402
from app.llm_router import validate_keys  # noqa: E402
from app.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware  # noqa: E402
from app.tools.preview import preview_manager  # noqa: E402
from app.tools.sandbox import sandbox_manager  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("Config: %s", settings.public_summary())
    validate_keys()
    validate_agent_keys()
    validate_credit_pricing()
    # Anything the last process buffered while the credit store was down is
    # picked back up here, before a single turn runs. Without this, a restart
    # during an outage is exactly where metered usage disappears.
    recovered = credits.load_spill()
    if recovered:
        log.error(
            "Credits: %d movement(s) from a previous degraded window are "
            "waiting to be flushed. They will be written as soon as the store "
            "answers; the credits endpoint reports the backlog until then.",
            recovered,
        )
    await runner.startup()
    await agents_runner.startup()
    sandbox_manager.start_reaper()
    try:
        yield
    finally:
        # Persistence writes are fired off rather than awaited during a run,
        # so give the in-flight ones a moment to land before the loop closes.
        await repository.drain()
        # Sandbox teardown already discards each session's preview; this is the
        # backstop for a preview whose sandbox entry has since been replaced.
        await preview_manager.shutdown()
        await sandbox_manager.shutdown()
        await runner.shutdown()
        await agents_runner.shutdown()


def validate_credit_pricing() -> None:
    """Refuse to boot on a pricing table that cannot charge correctly.

    A wrong surcharge is worse than a missing one: it looks metered and bills
    the wrong amount for as long as nobody audits the ledger. So this is fatal
    at boot, where it costs nothing to fix — the alternative is discovering it
    in a reconciliation months later.

    Raises :class:`RuntimeError` when the tables disagree.
    """
    problems = credits.validate_pricing()
    if not problems:
        log.info(
            "Credits: image generation priced at %.0f credits per "
            "`%s` image, derived from the provider's published rate.",
            credits.image_surcharge(),
            get_settings().stability_model,
        )
        return
    for problem in problems:
        log.error("CREDIT PRICING: %s", problem)

    # Fatal, not a warning. This used to log and carry on, which meant the
    # process started, served, accepted image requests, and failed every one of
    # them at the moment of charging — with an exception surfaced to a user who
    # had already waited for a render. Every one of those failures traces back
    # to a single line of configuration that was wrong before the first request
    # arrived, and the boot log said so in a line nobody was watching.
    #
    # Refusing to start turns that into an unmissable failure at the one moment
    # it is trivially fixable, and costs nothing legitimate: the only way here
    # is a STABILITY_MODEL that is not one of the priced models, or an endpoint
    # added to `imagery.py` without a price beside it.
    raise RuntimeError(
        "Credit pricing is inconsistent and the server will not start:\n  - "
        + "\n  - ".join(problems)
        + "\n\nSet STABILITY_MODEL to one of "
        + ", ".join(sorted(credits.STABILITY_CREDITS_PER_IMAGE))
        + ", or add the missing per-image price to "
        "`credits.STABILITY_CREDITS_PER_IMAGE` from "
        "platform.stability.ai/pricing."
    )


def validate_agent_keys() -> None:
    """Say at startup which specialist tools are unavailable, and what that costs.

    The same reasoning as `llm_router.validate_keys`: a missing optional key
    should be a clear line in the boot log, not a surprise the user discovers
    by spending a turn on a tool that cannot run. Nothing here is fatal — every
    one of the ten agents starts either way, minus the affected tool.
    """
    from app.agents.registry import ordered_agents
    from app.agents.tool_registry import REQUIREMENTS, is_configured

    unconfigured: list[str] = []
    for tool, requirement in REQUIREMENTS.items():
        if requirement["flag"] == "_always" or is_configured(tool):
            continue
        unconfigured.append(tool)
        log.warning(
            "Agents: tool `%s` is NOT configured — `%s` is unset. %s",
            tool,
            requirement["env"],
            requirement["loses"],
        )

    degraded = {
        agent.name: [t for t in agent.tools if t in unconfigured]
        for agent in ordered_agents()
    }
    affected = {name: tools for name, tools in degraded.items() if tools}
    if affected:
        log.warning(
            "Agents: %d of 10 specialists are missing a tool: %s. They will "
            "still run and will report the gap rather than faking a result.",
            len(affected),
            "; ".join(f"{n} ({', '.join(t)})" for n, t in affected.items()),
        )
    else:
        log.info("Agents: all 10 specialists have every tool configured.")


app = FastAPI(title="Coding Agent API", version="1.0.0", lifespan=lifespan)

# CORS is an explicit allowlist. Do NOT use "*" here — set ALLOWED_ORIGINS to
# your Vercel domain in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    # PUT is here for the inline editor's file save. The list is an allowlist of
    # the verbs this API actually serves, so a verb added to a route has to be
    # added here too — a missing one fails as an opaque "Failed to fetch" in the
    # browser, with the preflight rejection never reaching application code.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # The two headers the client actually sends. `["*"]` with credentials on
    # is broader than any route here needs.
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Retry-After"],
)

# Middleware runs in reverse order of registration: the body-size gate is
# added last so it runs first, before CORS has done any work on a request
# that is about to be refused anyway.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)

app.include_router(rest.router)
app.include_router(learn.router)
app.include_router(ws.router)
# The Agentic Loop. Its own routers rather than additions to `rest`/`ws`, so
# the three existing sections' surfaces are untouched.
app.include_router(agents_api.router)
app.include_router(agents_api.credits_router)
app.include_router(agents_api.approvals_router)
app.include_router(agents_ws.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
