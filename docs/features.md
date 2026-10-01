# Projects, memory, codebase analysis, version control and artifacts

> The workspace features that sit around a conversation.
>
> [← Back to the README](../README.md)

## Projects and memory

Two kinds of "remember this", deliberately kept apart, because they answer
different questions and want different switches.

A **project** is the scoped half. The user makes one on purpose, files sessions
into it, and writes standing instructions that apply to those sessions and to
nothing else. It can also hold knowledge files — PDFs and text the agent should
treat as background. **Memory** is the unscoped half: two custom-instruction
boxes and a table of learned facts that apply to every section, every session,
which is exactly why it gets its own on/off control and its own audit list.

Both end up as text in front of the system prompt, composed by
`app/preamble.py` in one order and only one:

```
<the surface's own system prompt>      what the machinery is and how it works
<per-account memory and instructions>  who this person is, how they want answers
<the project's instructions and files> what this particular work needs
```

Least specific to most specific, so the narrower statement is read last. A
project that says "answer in French" beats an account preference for English,
because the project scope was set deliberately and more recently. The base
prompt goes first because it describes the tools and the sandbox, and no user
preference should be able to bury that.

### The injection budget

Knowledge files are uploads, so their size is whatever the user happened to
attach. A 400 kB document would quietly consume the context window and push the
actual conversation out of it, so there is a budget — and it is applied
*before* any content is read. That is what `project_files.char_count` is for:
the listing query returns the counts without the text, the budget is decided
against those, and only the files that fit have their content fetched at all.

Anything cut short says so, in the prompt, where the model can see it:

```
[... truncated: this file is longer than the space available ...]
```

A model that reads half a document and does not know it is reading half a
document will answer confidently from the half it got. That is worse than not
having the file, which is why the notice is not optional and why files left out
entirely are counted in the block rather than silently dropped.

### Learning

After a turn, a cheap model reads the exchange and proposes durable facts.
Three things make that safe enough to run without asking each time:

* it is **charged like any other model call** — there is no free model call
  anywhere in this project and this is not the first;
* it never runs for an anonymous caller, who has no account to remember
  against, and never when the switch is off;
* it is **skipped for a stopped turn**. A cut-off exchange is the worst
  possible source of a fact meant to persist.

Extraction failure is never surfaced. A turn that produced a good answer is not
a failed turn because the pass afterwards could not parse its own JSON, so
everything in `app/memory.py` logs and returns rather than raising. The prompt
that decides what counts as a fact is in that file and is written to be read —
it is the whole of the boundary, so it should not need explaining elsewhere.

The switch gates the *learned facts*, not the two instruction boxes. Turning
memory off means "stop learning things about me", not "discard what I typed
into Settings on purpose"; conflating those loses work the user did
deliberately.

### Scope, and what deleting means

`sessions.project_id` is `on delete set null`, not cascade. Deleting a project
keeps every conversation in it and leaves them unfiled. "Delete project" reads
like it takes the conversations with it, so the confirmation spells the
consequence out rather than just asking twice — a destructive action whose
blast radius the user has guessed wrong is the one case where the second click
has to say what it does.

Projects work signed out, on the anonymous shelf, exactly as sessions do.
Memory does not: the server refuses to store it without an account, because
pooling it under a shared sentinel would let one browser's stated preferences
steer another's answers.

---

## Reading a codebase

Two halves that cost very different things, so they are two functions, two
endpoints, and two visibly separate things in the UI.

`analysis.scan()` is **pure measurement**: languages, lines, entry points,
dependencies, a TODO census, whether tests and linting exist. No model call,
nothing charged, and the same tree always produces the same numbers. That is
what makes it something the panel can refresh whenever a file changes.

`analysis.summarise()` is **the narrative** — one model call that turns those
numbers into prose about what the project is. Charged like every other call,
and always asked for.

### One shell command, not a tree walk

The obvious implementation walks the tree through `sandbox.files.list`, which
is what the file sidebar does. For a real repository that is hundreds of round
trips over the E2B transport, and the sidebar's walker is depth-limited to 3
and budget-capped precisely because of it — it is built to draw a sidebar, not
to count a codebase. `scan` sends one script and parses its output: one round
trip, no depth limit, and `find | xargs wc -l` does the counting where the
files actually are. A ten-file sandbox measures in ~250 ms.

**A language's size is its lines, not its file count.** A project with one
4,000-line module and forty 20-line configs is a project in that first
language, and a file-count histogram says the opposite. The bar in the panel
is sized the same way.

### Neither half may create a sandbox

Both `scan` and `read_loom_file` probe `sandbox_manager.sandbox_id_for()` and
return empty when it is None. Without that guard:

* the health panel calls `scan` on mount, so **opening the Code tab** would
  cold-start an E2B sandbox, burn a slot and start the 15-minute idle clock for
  a session where nothing had been asked for;
* `read_loom_file` runs on the prompt path of *every* turn, so **every Chat
  message** would pay a cold create to read a file that cannot exist there.

Both were real, and the second is the more expensive mistake of the two.

### LOOM.md

`write_file: true` on the analysis endpoint stores the narrative as `LOOM.md`
at the sandbox root, and `preamble.compose` reads it back into the system
prompt on every later turn of that session. That is what makes an analysis
outlive the conversation that asked for it.

It is injected **last and labelled as reference**, not as instruction. It
describes what the code is; a description of a Rails app must not read as an
instruction to write Rails. Edit it by hand freely — nothing rewrites it except
an explicit regenerate.

---

## Version control

History now leaves the sandbox — deliberately, on request, and never with a
credential inside it.

A repository can be given **one remote**, `origin`, and it must be an `https://`
URL with no userinfo in it. `git.validate_remote_url` refuses
`https://user:token@host/...` before git ever sees it, because that is the one
shape that ends up written into `.git/config` in plain text where the sandbox
can read it back.

**The token is the server's and reaches exactly one process.** `GIT_PUSH_TOKEN`
lives in `backend/.env`, is handed to a single `git push` through that
command's environment plus a credential helper written inline on the command
line, and is never stored in the repository, the sandbox image, or any file in
the sandbox. `scripts/test_git_push.py` asserts that: it greps `.git/config`
after a real push and fails if anything token-shaped is there.

**Pushing at the end of a turn is opt-in, and off by default.** The Code
section can mirror Claude Code — finish a turn, commit what changed with a
generated message, push, and show the commit — but only for a repository whose
owner has ticked *Commit and push after each turn* in the History panel. The
reason it is not the default is that a turn is not always a deliverable: "just
try this" and "build the feature" look identical to the code that would have to
decide. So the explicit paths (the `git` tool's `commit` and `push`, the
panel's buttons) stay exactly as they were, and the automatic one is a switch a
person throws per repository. It is stored in the repository's own config
(`loom.autopush`), so it goes when the sandbox goes, beside the remote it
applies to.

When it fires, `app/autocommit.py` runs *before* `agent_done` — the composer
re-arms on that frame, and a commit landing after it would arrive under a turn
the user believes is finished. A stopped or failed turn is left uncommitted:
what it wrote is half of something. The result is a `turn_commit` frame, which
the transcript renders as a card carrying the short hash, the subject, the
branch, the file count and whether the push succeeded — and which flashes the
committed paths in the file tree, so the panel opens to what was just recorded.

A commit that lands but fails to push is **amber, not red**, and says why: the
work is safe locally and the next successful push carries it.

What the panel does now:

| | |
|---|---|
| **Staging** | Per-file checkboxes. An empty selection means *commit everything*, so the common case did not get worse to buy the rare one. |
| **Branches** | List, create, switch, merge. Merging is the secondary action on a row that is not the current branch, because merging *into* the branch you are looking at is what the word means. |
| **Discard** | The one operation nothing can undo — an untracked file removed here was never in the object store. It confirms, inline. |
| **Commit messages** | A cheap model writes one from the staged diff, into the box. Never applied. |
| **Remote** | One `origin`, https only, validated on the way in. `Push` sends the current branch and sets upstream. |
| **After each turn** | The opt-in above. Off by default, stored in the repository, shown as a checkbox under the remote. |

Two decisions worth stating.

**`commit(use_index=True)` exists because the checkboxes would otherwise be
decorative.** The old path always ran `git add -A` before committing, which
sweeps unticked files straight back in. The panel stages its selection, then
commits the index and nothing else.

**A merge conflict is a 200, not an error.** The merge really happened and the
working tree really does have markers in it; reporting a failure would leave
the caller looking at a repository whose state nobody told them about.
`conflicted` names the files, and both the UI and the agent's tool report it as
a result with a problem in it rather than as a call that did not happen.

**The commit message is suggested, never applied** — on the manual path. The
user still presses commit, because that press is the only moment anyone reads
what is about to be recorded permanently, and a wrong commit message is
permanent in a way a wrong chat reply is not. The end-of-turn path is the
deliberate exception, and it is exactly why that path is opt-in: turning it on
*is* the press, made once for the repository instead of once per commit. When
no model is available to write a message, the fallback is factual rather than
inventive (`Update 3 files`, with the names in the body) — a dull subject beats
a turn's work left uncommitted, and beats a subject the diff does not support.

---

## Artifacts

A document the model writes *beside* the conversation rather than into it: a
file, a page, a component, a draft. The transcript keeps a chip; the content
lives in its own panel.

The distinction that justifies the whole surface is whether a thing is **worked
on** or **said once**. A 300-line file pasted into a transcript is unreadable,
unscrollable, and gone the moment the next message arrives. The same file in a
panel can be read, edited, and revised across turns.

The Agents section has had this since it was built — a tool result can carry an
`artifact` payload. What was missing was any way for Chat and Code, the two
surfaces where people actually write documents, to produce one.

### Deliberate, not heuristic

The model creates an artifact by **calling a tool**. It does not get promoted
into a panel because a code fence crossed a line count.

A heuristic is wrong in both directions constantly: it promotes a long stack
trace nobody wants to edit, and it leaves the twelve-line config someone has
been iterating on for five turns stuck in the transcript. The model knows
whether it just wrote a *thing* or an *explanation*, and asking costs one
sentence in a tool description.

### Versions, never overwrites

Every write is a new row. `artifact_key` is the stable identity; `version`
counts up within it.

That settles the user-edit question, which is the one that decides whether
people trust the feature. **A person editing an artifact does not overwrite the
model's version — they add one**, attributed to them in `created_by`. Nothing
the model wrote is destroyed by someone tidying it up, the history stays
walkable, and the model sees the edit on its next turn because it reads the
latest.

The panel says so rather than assuming it is understood: the editor's status
line reads *"Autosaves to a new version"*, not the "to the sandbox" it says for
a real file. Both halves of that default would have been wrong — an artifact
never touches the sandbox, and "save" normally means overwrite.

Viewing an older version is read-only, and says why. Letting someone type into
a version that cannot be saved and only telling them at save time is worse than
not accepting the keystrokes.

### Four kinds, and one that is missing

| Kind | Rendered as |
|---|---|
| `markdown` | formatted prose |
| `code` | the CodeMirror editor, highlighted by `language` |
| `html` | a sandboxed iframe |
| `svg` | an image |

`mermaid` is deliberately **absent**. There is no mermaid renderer in this
frontend, so offering it would produce a "diagram" that displays as its own
source — a plausible-looking capability that does not work, which is the trade
this codebase refuses elsewhere (see the Deploy button in Project Pulse). Add
the renderer first, then add the kind.

### Two events, not one with a flag

`artifact_created` opens the panel. `artifact_updated` revises it in place and
does **not** steal focus. Stealing focus every time the model touches a
document someone is reading is the fastest way to make a canvas unusable, and
that difference is worth a type rather than a boolean.

Both carry the full content. The alternative is an id the client then fetches,
which is a round trip to display something the server already had in hand, on
the one path where the user is watching and waiting.

### Security notes

**HTML runs with `sandbox="allow-scripts allow-forms allow-popups"` and no
`allow-same-origin`.** The content is model-written and may contain scripts.
Those two flags *together* would be equivalent to not sandboxing at all — the
frame could reach this origin's storage and DOM. Apart, the page can be
interactive and still cannot touch anything of ours.

**SVG renders through an `<img>` data URL, not as injected markup.** An
`<svg>` written into the DOM can carry scripts and event handlers; the same
markup in an `<img>` cannot execute anything.
