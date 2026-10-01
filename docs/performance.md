# Performance

> What is allowed to sit between a message and the model's next token.
>
> [← Back to the README](../README.md)

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

The one knob deliberately left alone is `OPENCODE_MAX_TOKENS=8000`. It is the
single largest remaining lever on response time, and unlike everything above it
is a real quality trade — turn it down only if you have decided you want
shallower reasoning, not because you want a faster benchmark. (`GROQ_MAX_TOKENS`
is *not* in that category: it must stay under Groq's tokens-per-minute cap, and
at 8000 on the free tier every Groq call failed with a 413. See `.env.example`.)
