# Models and routing

> Manual model selection, Auto routing, automatic fallback and refused vendors.
>
> [← Back to the README](../README.md)

## Model selection: manual, or Auto

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

## Automatic fallback

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

## Refused vendors

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
