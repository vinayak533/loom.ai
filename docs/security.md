# Security

> Key handling, guardrails, the perimeter around auth, and the smoke test.
>
> [← Back to the README](../README.md)

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

## Guardrails

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

## The perimeter, beyond auth

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

## Smoke test

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

## Interface primitives

The shared components that keep the interface on its own design system live
in `frontend/components/`: `Tooltip` (and the `data-tip` layer that replaces
every native `title`), `PanelHeader` with the `--bar-h` / `--bar-h-sub` /
`--gutter` tokens, `Skeleton`, `Meter`, `Sparkline`, and the action toast
channel in `Toast.tsx` (`useToast().notify` for outcomes, `useToast().undoable`
for reversible deletion). Every `aria-modal` dialog runs `useFocusTrap`.
