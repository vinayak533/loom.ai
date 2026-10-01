# Interface behaviour

> Session history, message actions, branching, stopping, search, settings and the other conversation-level behaviours.
>
> [← Back to the README](../README.md)

## Session history

Every session row in the Chat and Code lists carries an overflow menu with the
same four actions, from one shared component
(`components/SessionHistoryMenu.tsx`, used through `SessionListItem`):

| Action | Effect |
|---|---|
| Rename | Opens a dialog for the project's name and one-line description. **Generate** fills both from the transcript; the user still presses Save, so a name they dislike costs a keystroke rather than a round trip. |
| Pin | Moves the session into a **Pinned** group above everything else, ordered by when it was pinned. |
| Archive | Moves it to the **Archived** shelf, which the default list never shows. Nothing is deleted. |
| Delete | Confirms inline in the menu, then removes the session and cascades to its `messages`, `files` and `token_usage` rows. |

`title` is still generated automatically from the first message — that is cheap
and immediate. `description` is not: naming a *project* needs the transcript,
which costs a real model call, so it is on demand and the row stays one line
until someone asks.

Ordering is decided by the backend (`repository._shelf_order`), not by each
list, so Chat and Code cannot drift apart about what "most recent" means.
`is_pinned` / `is_archived` / `pinned_at` / `description` live on `sessions`;
run `schema.sql` again to add them to an existing database (every statement in
it is idempotent). Until you do, a rename still saves its title — the
description is dropped by a deliberate fallback rather than failing the write.

## Message actions: copy, edit, regenerate

Every message in Chat, Code and Agents carries a row of controls that appears
on hover and takes no layout — a transcript is something you read, and a
permanent row of buttons under every paragraph turns it into a form. They stay
in the DOM and stay focusable, so they are reachable by keyboard and to a
screen reader rather than being hover-only.

| Control | Where | What it does |
|---|---|---|
| Copy | Every message | Clipboard, with a tick for confirmation. Falls back to the `execCommand` path on an insecure origin, where `navigator.clipboard` throws. |
| Edit | User messages | Opens the message in place; `⏎` saves, `Esc` cancels. Saving re-runs the conversation from that point. |
| Regenerate | The most recent reply | Re-runs the last turn on **whichever model is selected now**, which need not be the one that answered the first time. |
| 👍 / 👎 | Assistant messages | Records a verdict. Pressing the thumb you already chose withdraws it. |

Regenerate is deliberately offered on the last reply only. Re-running an
earlier one is the same operation as editing the message above it without
changing the text, and offering it separately would be a second door onto one
room — with the added cost of silently discarding everything after it.

**Learn's notebook Q&A has none of these, on purpose.** Every control in that
table assumes a thread you can rewind: edit re-runs the conversation from a
point, regenerate discards what came after, branching keeps the version you
replaced. A notebook question is a single retrieval against a fixed set of
sources and a single answer — there is no checkpoint to fork and nothing after
a reply to discard, and the answer's interesting metadata is which passages it
used, which is why that panel spends its room on a citation strip instead. The
notebook chat is not even persisted (the notes panel is where a notebook keeps
things on purpose), so there would be nothing for a verdict to hang on either.

---

## Conversation branching

**An edit forks; it never overwrites.** Send a different version of your third
message and the conversation re-runs from there, but the replies the original
produced stay reachable behind a `‹ 1/2 ›` control on that message. Regenerate
does the same thing to a reply, so the answer you liked better is never one
click from being gone.

The data model was a real decision, and it is written up at length on
`public.message_branches` in `schema.sql`. The short version:

> A branch is the whole conversation **from turn N onwards**, stored as one row.

The alternative — a tree keyed by parent message — is more general and a worse
fit here. Nothing in this system has a per-message identity to *be* a parent:
what the agent reads is `AgentState["messages"]`, a flat list inside a LangGraph
checkpoint, and the rows in `public.messages` are an append-only log no reader
ever joins on. A tree would have meant minting stable ids in two stores and
keeping them in step. Versioned suffixes need neither: the checkpoint stays the
single live source of truth, and switching branches splices a stored suffix
back into it.

That last point is what makes the feature more than cosmetic. Switching
branches does not merely redraw the transcript — it puts the model back in the
state that branch left it in, so the next turn continues the branch you are
actually looking at. `scripts/test_message_actions.py` asserts exactly this by
reading the checkpoint back after a switch.

Turns are numbered by **user turn**, never by position in the messages array: a
`tool_result` carrier has `role == "user"` too, so array indices would shift the
moment a turn used a tool. `repository.user_turn_positions` and the client's own
count of `kind === "user"` rows are the same number by construction, which is
how both ends agree without exchanging ids.

`is_active` marks which version is spliced in. It is stored rather than
inferred, because on a cold page load the only evidence is the live
conversation itself, and comparing snapshots to guess would be expensive and
wrong the moment two versions began with the same message.

---

## Stopping a generation

The Stop control replaces Send while a reply is streaming, and `Esc` does the
same thing from the keyboard. What happens next is the interesting part.

A stop used to be `run_task.cancel()`, which is abrupt in three ways that all
matter:

* the partial answer already on screen was **thrown away** — it lived only in
  the local variables of a node that raised, and LangGraph does not checkpoint
  a node that raised;
* the tokens the provider had already produced were **never billed**, because
  the charge happens after the stream completes;
* a stop landing mid-tool-call left a `tool_use` block with **no matching
  `tool_result`**, which the provider rejects on the *next* turn — so the
  session was dead and the symptom appeared a long way from the cause.

So a stop is now a *request* rather than an interrupt (`app/cancel.py`). The
socket sets a flag; the graph reads it at the points where stopping is safe —
between stream frames and between tool calls — and then finishes the turn
normally:

* partial text and reasoning are appended to the conversation as a real
  assistant turn, marked `*(Stopped by the user.)*`, so a reload still shows it;
* a tool already running is **allowed to finish** — half a `file_write` cannot
  be undone, and one extra second beats a corrupt sandbox — while calls that
  never started are closed with a result, so nothing is orphaned;
* the turn is billed for the output actually generated, estimated from the
  streamed characters (`app/turnstop.py`) and recorded as an estimate. The
  provider only reports `usage` on a frame a stopped stream never receives, so
  the choice is between estimating and billing zero — and billing zero would
  make Stop a way to use the product free;
* the run ends with `agent_done.reason == "cancelled"`, not an error.

`scripts/test_stop_tools_credits.py` proves the tool case the only way worth
proving it: it stops mid-batch, checks every `tool_use` in the checkpoint has a
result, and then **sends another turn on the same session** — which a
conversation with an orphaned call cannot survive.

---

## History search

The sessions flyout has a search box over the current section's history. It
matches session titles *and message bodies*, and a body hit carries the line
that matched, because a row whose title has nothing to do with your query is
baffling until you can see why it is there.

It is a database query and nothing else: two indexed `ilike` scans and a merge
(`repository.search_sessions`). No model is in the path — a search box that
waits on a generative call is both slower and worse than one that does not.
The input debounces at 200ms and aborts every superseded request, so results
for "auth" cannot land after results for "authentication" and overwrite them.

Chat, Code and Agents each search their own history. The first two scope by
section; an agent scopes by its own id, because a conversation belongs to the
specialist it was started with. Same box, same debounce, same endpoint —
`components/SessionSearch.tsx`, used from both the flyout and the agent shelf,
because two copies of an abort-and-debounce dance is two places to get
out-of-order responses wrong.

Cross-section search is not implemented.

---

## Keyboard shortcuts panel

`?` opens a reference sheet, also reachable from Settings. One rule governs its
content: **every line in it is a binding that actually works.** A shortcuts
sheet is a promise, and one listing a key that does nothing teaches something
false and then makes the reader doubt the rest. It is a static list, so
`components/ShortcutsDialog.tsx` has to be edited when a binding is added — the
comment above the table says so.

---

## Settings

`SettingsDialog` has three groups:

* **Account** — who you are, what the session covers, and sign-out.
* **Preferences** — the default model, and the theme statement.
* **This build** — read-outs: which models are configured, how Auto routes,
  whether the sandbox is wired up.

The default model is stored **against the account**, not in `localStorage`
(`public.user_preferences`), which is the point: signing in on a second device
used to silently reset a choice made on the first — the one thing an account is
supposed to prevent. "No preference" stays distinguishable from "chose the
current default", so a user who never picked one follows the build's default
when it moves and a user who did does not.

On theme, the panel now says the thing rather than leaving it ambiguous: Loom
ships a single true-black theme, there is no light mode, and none is planned.
The `theme` column exists so that is a value in the data rather than an
assumption baked into the absence of one.

---

## Response feedback

👍 / 👎 on assistant replies, stored in `public.message_feedback`. Deliberately
inert: nothing reads it back, no model is retrained, no answer changes. It is a
record kept so that "which model and which route produce answers people
actually like" can one day be asked against real data instead of reconstructed
from nothing.

One verdict per person per turn, revisable and withdrawable. Two people looking
at the same transcript are entitled to disagree, and a control showing somebody
else's verdict as yours is simply wrong.

---

## Attachment previews

Composer chips show a real thumbnail for images and for PDFs whose first page
is a raster — which is what a scanned document is. A chip reading
`report.pdf · PDF` tells you what you already knew when you picked the file;
the first page tells you whether it is the *right* report.

`lib/thumbnail.ts` tries the cheap way first: it scans for a `/DCTDecode`
stream and hands the embedded JPEG to the browser's own decoder — no library,
no worker, a few hundred bytes of code, and it covers every scanned document.
A vector-only PDF (a LaTeX paper, a clean export) has no raster to find, and
that is what `pdfjs-dist` is for. It renders any PDF correctly and costs ~350 KB
plus a worker, which is why it is reached for *second* and behind a dynamic
`import()`: it is fetched the first time somebody attaches a PDF the byte scan
could not draw, and never by anyone who does not. The initial bundle does not
move.

Two details that are not obvious from the outside. The worker is copied into
`public/` at `predev`/`prebuild` rather than referenced through
`new URL(..., import.meta.url)` — the documented form, which Next hands to SWC,
which then parses an already-bundled worker as source and fails on its
`export`. And `render` is called with `intent: "print"`, which has nothing to do
with printing: it is the one option that stops pdf.js scheduling its work with
`requestAnimationFrame`, which browsers do not fire in a hidden tab. Without
it, attaching a PDF and switching away leaves the thumbnail unresolved and the
worker alive until you come back.

A PDF that is neither — corrupt, encrypted, an empty page — still falls back to
the type icon. Everything runs locally on the chosen file, before any upload.

---

## Empty states

Chat, Code and Learn each open on something usable rather than a blank
composer, at the bar the Agents gallery already set.

* **Chat** — four starters, each with the reason it is there.
* **Code** — the same, plus **Open a folder**, which *does* the thing rather
  than describing it: it reaches into the import pipeline the composer's `+`
  already owns.
* **Learn** — a first-run notebook library now points at the ten authored
  courses sitting one tab away, because someone opening Learn for the first
  time has no PDFs to drop in and no reason to know the courses exist.

`SECTION_META.suggestions` was removed rather than rewritten. The problem was
not that they were the wrong strings: a starter needs a reason attached, and
where a section has a real first action it needs to *be* that action — neither
fits a `string[]`.

---

## One-time data cleanup

`scripts/clean_session_debt.py` repairs rows left behind by two fixed bugs.
Run it once against an existing database:

```bash
.venv/Scripts/python -m scripts.clean_session_debt          # report only
.venv/Scripts/python -m scripts.clean_session_debt --apply  # write
```

It renames sessions whose title is the router's "the model produced no answer"
sentence, or a reasoning model's monologue about the title prompt, or an entire
markdown document — using the session's own first message, which is what the
title should have been. It also scans `messages` for raw provider error
payloads and redacts any it finds.

On the database this was written against, that scan found **zero** rows across
1,533 messages and 1,482 checkpoint entries: the 402 leak reached the socket as
a transient `error` frame and returned without appending an assistant message,
so it was rendered once and never written down. The scan stays in the script
because that reasoning is only as good as its evidence, and this is how the
evidence gets re-gathered on a database the author has not seen.
