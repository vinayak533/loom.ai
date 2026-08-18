# Atlas — design spec

Companion to `index.html` (self-contained HTML+CSS prototype, no build step, no
dependencies). Open the file directly in a browser.

**Deliverable format chosen: HTML + CSS prototype.** The mascot brief is ~70%
motion specification — state machine, easing, loop timing, reduced-motion
fallback. A static spec can assert those numbers; a prototype proves them.

---

## 1. Concept

One product, four surfaces, **one design system**. Sections differentiate through
three levers only — **accent, icon language, layout emphasis**. Everything else
(surface ramp, type scale, spacing rhythm, shadow ramp, motion curve) is shared,
so switching modes reads as *the same app changing register*, not four apps.

**Bit**, the robot mascot, now lives **only in the Code section** — seated at a
desk on the composer's top edge. Chat and Learning use a neutral three-dot
processing indicator instead (§6a). The model selector sits inside the composer
in all three sections (§6b), and Learning gains source ingestion (§6c).

---

## 2. Color tokens

### Base surfaces — tonal elevation, not borders

| Token | Hex | Role |
|---|---|---|
| `--s-0` | `#0F0F10` | App canvas |
| `--s-1` | `#141517` | Sidebar / rail |
| `--s-2` | `#1A1B1E` | Cards, composer, message bubbles |
| `--s-3` | `#212327` | Hover, raised card, user bubble |
| `--s-4` | `#292B30` | Popover, menu (highest) |
| `--s-inset`| `#0B0B0C` | Wells — code blocks, mascot visor |

Elevation is a **tone step first**; shadow only reinforces it. Hairlines are
`rgba(255,255,255,.055)` — never a hard 1px white border.

### Text — contrast measured against `--s-0`

| Token | Hex | Ratio | Use |
|---|---|---|---|
| `--tx-hi` | `#EDEEF0` | 16.5:1 | Body, headings |
| `--tx-mid` | `#A9ADB5` | 8.5:1 | Secondary, nav rest state |
| `--tx-low` | `#7C8089` | 4.8:1 | Meta, captions, placeholder |
| `--tx-dis` | `#4E5158` | 2.2:1 | Disabled — **non-text only** (icons in disabled controls, gutter digits) |

All four accents clear AA for normal text at every size.

### Section accents

| Section | `--acc` | Ratio | `--acc-2` | Ratio | Gradient |
|---|---|---|---|---|---|
| **Chat** | `#8AA9FF` | 8.4:1 | `#B190FF` | 7.6:1 | `135deg` blue → violet |
| **Learning** | `#F0B54A` | 10.4:1 | `#3BC9B4` | 9.3:1 | `135deg` amber → teal |
| **Code** | `#5BE0A0` | 11.5:1 | `#54D2E8` | 10.7:1 | `135deg` green → cyan |
| **Login** | `--brand` `#8AA9FF` | 8.4:1 | — (grayscale) | — | none — flat CTA only |

Per-accent derivatives, generated the same way in every section:

```
--acc-soft   accent @ 9–11% alpha    fills, active nav, chips
--acc-line   accent @ 22–24% alpha   focused borders, avatar rings
--acc-ring   accent @ 40–42% alpha   focus ring outer
--acc-glow   accent @ 28–30% alpha   mascot LED drop-shadow
--acc-ink    near-black, hue-matched text on accent fills (8.4:1 on chat blue)
```

**Login deliberately has no gradient.** Account UI is grayscale plus exactly one
brand fill (Upgrade CTA), so it never competes with whichever mode is active.

### Syntax palette (Code wells + mascot laptop screen)

`--syn-key #7BD8F0` · `--syn-str #9BE3A8` · `--syn-num #F0B54A` ·
`--syn-fn #8AA9FF` · `--syn-com #666B74` · `--syn-pun #8E949E`

---

## 3. Type, space, radius, motion

**Type** — Google Sans → Inter → Söhne → system. Scale `12 / 14 / 16 / 20 / 24 / 32`.
Body 14/1.55, message text 16/1.65, greeting 32/1.2 at `-0.02em`.
Code and all Code-section input: `--mono` at 13–14px / 1.75.

**Space** — 4px base: `4 / 8 / 12 / 16 / 24 / 32`. Sidebar padding 12, card padding
16, section gap 24, block gap 32.

**Radius** — token-swapped per section, which is a large part of the identity shift:

| | Chat | Learning | Code |
|---|---|---|---|
| `--r-card` cards, composer | 16px | 16px | **8px** |
| `--r-ctl` buttons, nav | 8px | 8px | **6px** |
| `--r-bubble` messages | 18px | 18px | **10px** |
| avatars, pills | full | full | full |

Icon stroke follows: `1.75` round-cap in Chat/Learning, `1.6` butt-cap miter-join
in Code.

**Motion** — `--dur-fast 150ms` (press) · `--dur 200ms` (hover/color) ·
`--dur-slow 250ms` (layout, section switch). Easing `cubic-bezier(.2,0,0,1)`
(ease-out) for entrances, `cubic-bezier(.4,0,.2,1)` for reversible states. Press
feedback is `scale(.985)` on large targets, `scale(.94)` on icon buttons. No
bounce, no spring, nothing over 320ms except mascot idle loops.

---

## 4. Component states

Focus is uniform everywhere: `0 0 0 2px var(--s-0), 0 0 0 4px var(--acc-ring)` —
a ring floated off the surface, so it reads on any elevation. `:focus-visible`
only; mouse clicks never show it.

| Component | Default | Hover | Active / pressed | Focus | Disabled |
|---|---|---|---|---|---|
| **New chat** | `--s-3`, `--e-1` | `--s-4`, `--e-2` | `scale(.985)`, back to `--s-3` | ring | — |
| **Nav item** | transparent, `--tx-mid` | `--s-2`, `--tx-hi` | `scale(.985)` | ring | — |
| **Nav item (current)** | `--nav-soft` + 3px accent bar, `--tx-hi` | no change | — | ring | — |
| **Recent row** | transparent, `--tx-mid` | `--s-2`, `--tx-hi` | — | ring | — |
| **Composer** | `--s-2`, `--line` | border → `--line-2` | — | border `--acc-line` + `0 0 0 3px --acc-soft` | — |
| **Send** | `--s-3`, `--tx-dis`, `disabled` | — | — | ring | `opacity .55`, `not-allowed` |
| **Send (armed)** | accent gradient, `--acc-ink` | `brightness(1.08)` | `scale(.92)` | ring | — |
| **Tool chip** | `--tx-low` | `--s-3`, `--tx-hi` | — | ring | — |
| **Tool chip (on)** | `--acc-soft`, `--acc` | — | — | ring | — |
| **Suggestion** | `--s-2` + `--line` | `--s-3`, border `--acc-line` | `scale(.98)` | ring | — |
| **Icon button** | `--tx-mid` | `--s-2`, `--tx-hi` | `scale(.94)` | ring | — |
| **Account row** | transparent | `--s-2` | — | ring | — |
| **Account (open)** | `--s-3`, chevron 180° | — | — | ring | — |
| **Popover item** | `--tx-mid` | `rgba(255,255,255,.055)`, `--tx-hi` | — | ring | — |
| **Upgrade CTA** | `--brand` fill | `brightness(1.08)` | `scale(.985)` + `brightness(.96)` | ring | — |
| **Copy button** | `--tx-low` | `rgba(255,255,255,.06)` | — | ring | `.ok` → accent, "copied", 1.6s |
| **Lesson step** | `--s-2` + `--line` | border `--line-2` | `[open]` rotates caret 180° | ring | `[data-done]` fills tick with accent |

The send button carries the only disabled state in the UI — everything else stays
enabled and gives feedback instead.

---

## 5. Responsive

| Range | Sidebar | Behavior |
|---|---|---|
| **≥ 1024px** | 264px, full | Labels, Recent list, account meta all visible. Manual collapse toggle → 72px rail. |
| **768–1023px** | 72px icon rail | Labels, Recent, wordmark, account meta hidden. Nav icons centered; the current-item accent bar shifts to the rail edge. Account popover flips to `left: 100% + 10px`. |
| **< 768px** | Slide-over drawer | Sidebar becomes `position: fixed`, `translateX(-100%)`, full 264px. Hamburger in the top bar opens it; scrim (`rgba(0,0,0,.6)` + 2px blur) or Escape closes it. All rail overrides are explicitly undone — this was the one place the cascade bit during build. |

Content column: 760px max (860px in Code, where lines are longer). Under 768px the
greeting drops 32 → 24px, bubbles widen to 88%, and the mascot shrinks 96 → 72px.

The greeting reserves right padding equal to the mascot's perch width so text
never runs beneath it, and scrolled thread content gets 96px bottom clearance plus
a `--s-0` fade into the dock.

---

## 6a. Processing indicator — Chat + Learning

A single shared component, `.proc`. Three 7px dots on the section accent, mounted
in the response area behind the same AI avatar the reply will use — so the
indicator occupies the exact position the answer appears in, and the swap is a
substitution rather than a jump.

| | Value |
|---|---|
| Loop | `procPulse` — opacity `.32 → 1 → .32`, scale `.78 → 1 → .78` |
| Duration | 750ms ease-in-out, infinite |
| Stagger | 0 / 130 / 260ms |
| Mount | on submit |
| Unmount | on first token — `.leaving` fades out over 140ms, then removes |
| Properties animated | `opacity`, `transform` only |
| Reduced motion | no pulse; dots hold at opacity `.85` with a static `box-shadow` accent glow |

Code does **not** use this — the mascot's thinking state is its indicator, and
running both would double up.

## 6b. Model selector

Lives in the composer's bottom-left in every section, as `.model-pill`. There is
deliberately **no model control in the top bar** — the header chip that used to
sit there was removed.

**Auto is the default on load**, per section, and is the only routing logic:

```js
const AUTO_ROUTE = {
  chat:     "claude-sonnet",
  learning: "nemotron-3",
  code:     "llama-4-scout"
};
```

Section/task type is the sole input — no complexity scoring, no history. `MODELS`
and `AUTO_ROUTE` are plain objects; changing a default is a one-line edit.

| Model | Provider | Slug |
|---|---|---|
| Claude Sonnet | Claude | `claude-opus-5` |
| Llama 70B | Groq | `llama-3.3-70b-versatile` |
| Llama 4 Scout `17B-16E-Instruct` | **OpenRouter** ⚠ | `meta-llama/llama-4-scout` |
| Nemotron 3 Ultra | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b` |
| DeepSeek V4 Flash | OpenCode | `deepseek-v4-flash` |
| Minimax M2.7 | OpenCode | `minimax-m2.7` |
| Qwen 3.7 Plus | OpenCode | `qwen3.7-plus` |
| MiMo V2.5 | OpenCode | `mimo-v2.5` |

GPT-OSS-20B (`openai/gpt-oss-20b`) was in this list and has been removed.

The four OpenCode models are the pool Auto routes between; the rest are manual
picks. See the routing table in the README.

⚠ **Deviation from spec.** Llama 4 Scout was specified under Groq. Groq has
retired every `llama-4` variant — its `/models` endpoint returns none, and the
call 404s with `model_not_found`. OpenRouter still serves it, so it is routed and
labelled there; the group label has to match where the call actually goes. If
Groq restores it, move `llama-4-scout` back to `provider: "groq"` in
`MODEL_REGISTRY` and point it at a `GROQ_MODEL_*` setting. Every model in the
table was verified with real completions.

The popover groups by provider with an uppercase `.mp-label` per group, model
name as primary label. The `Auto` row sits above the groups, separated by a rule,
and shows what it currently resolves to ("Routed by section → Claude Sonnet").

**Auto indicator:** a 7px accent orb with a 2.6s breathing opacity loop. On manual
override the orb goes flat grey with no motion, so the two modes are
distinguishable without reading the label. Hovering the pill in Auto mode expands
`.routed` from `max-width: 0` to reveal "Auto · Claude Sonnet".

Manual choice is stored per section in `modelChoice` and persists for the session
until set back to Auto — switching Chat to Claude Sonnet leaves Learning and Code
on Auto. Closes on selection, outside click, and Escape.

## 6c. Learning — source ingestion

**PDF**: `.only-learning` icon button beside the model selector, opening a hidden
`input[type=file][accept=.pdf][multiple]`.

**YouTube**: `YT_RE` matches `youtu.be/ID`, `watch?v=ID`, `/shorts/ID`, and
`/embed/ID`. On input the URL is stripped from the textarea and replaced with a
chip carrying the real `i.ytimg.com` thumbnail (with an inline play-glyph
fallback via `onerror`), so the input never shows a raw URL.

Sources stack as removable chips above the textarea, dedupe by key, and multiple
can attach to one session. Attaching a source alone arms the send button — text
is not required. On submit the chips render into the user message and the tray
clears.

A sourced turn returns two `.out-card` actions:

- **Generate Study Notes** → `.notes`: accent uppercase section headers, bulleted
  body, key terms in `.term` (accent-tinted chip, `box-decoration-break: clone` so
  wrapped terms keep their shape).
- **Generate Slides** → `.deck`: 16:9 stage, one `.slide` per entry, title + 3–5
  bullets. Prev/next with disabled bounds, tabular slide counter, and clickable
  position dots. Slides cross-fade with a 12px X-translate; under reduced motion
  the translate is dropped and it's a pure fade.

Choosing one format disables only that card — the other stays available, so a
user can generate both from the same source.

## 6. Mascot "Bit" — technical spec (Code section only)

**Format: inline SVG + CSS animations.** Not Lottie. Reasons: accent color comes
from the same CSS custom properties as the rest of the UI (a Lottie JSON bakes its
palette, so per-section recolor means three exported files); total cost is ~2KB of
markup with no runtime; and `prefers-reduced-motion` is handled by the same
cascade as everything else instead of by JS teardown.

### Geometry

`viewBox="0 0 100 128"`. Rendered at 64×82 at rest, 96×122 in the empty state,
72×92 below 768px. The silhouette is the angular variant throughout: head
`rx: 10`, visor `rx: 6`, body `rx: 6`, 8×8 rounded-square LED eyes, and desk and
chair props at pure right angles — matching the section's 6–8px radius grammar.

`.mascot` is `display: none` by default and only becomes `block` under
`[data-section="code"]`. The extra bottom padding on `.col` and the greeting's
right-hand reserve are likewise scoped to Code, so Chat and Learning get their
full content width back.

### States

Set `data-state` on `#mascot`; CSS does the rest. Selectors are
`.mascot[data-state="idle"|"thinking"|"answering"]` — the `.mascot-idle` /
`.mascot-thinking` / `.mascot-answering` naming from the brief, expressed as one
attribute so states are mutually exclusive by construction.

| | Idle | Thinking | Answering |
|---|---|---|---|
| **Body** | `bob` — 5px Y, 2.8s, ease-in-out, infinite | `leanIn` → `rotate(7deg)`, 300ms, bob continues | `straighten` → 7° → −3° → 0, 320ms |
| **Eyes** | steady, accent glow | `eyePulse` opacity .6→1→.6, 800ms | `eyeFlash` scale 1→1.55→1, 300ms |
| **Above head** | — | 3 dots, `dotRise` 1.4s staggered 0/.22/.44s | 4-point spark, `sparkPop` 400ms ease-out, one-shot |
| **Desk props** | `tapL`/`tapR` hands 2.5px counterphase, 360ms; `synShift` swaps two screen-line layers every 2.6s; `glowPulse` 3.4s | tap → **190ms**, glow → **900ms** | hands pause, `checkPop` green check on screen 900ms |

### Trigger contract

```
idle      → thinking    on user message submit
thinking  → answering   on first token/chunk received
answering → idle        on stream completion (or 1.5s after last chunk)
```

In the prototype: `setMascot(state)` in the `submit()` / `stream()` lifecycle,
guarded by `section === "code"`. Chat and Learning run `showProc()` / `hideProc()`
off the **same three lifecycle points**, so both indicators share one contract —
wiring either to a real stream means calling from `onSubmit`, `onFirstChunk`,
`onDone`. The prototype's 1400ms delay is a stand-in for first-token latency.

### Performance

Every animation touches **`transform` and `opacity` only**. No animated `width`,
`height`, `box-shadow`, or `filter`. SVG children use
`transform-box: view-box` with explicit `transform-origin` in viewBox units, so
rotations pivot correctly without wrapper `<g>` hacks. The "syntax re-highlight"
on the laptop screen is two stacked line groups cross-faded by opacity rather than
an animated `fill`.

### Reduced motion

Under `prefers-reduced-motion: reduce`, all loops (`bob`, typing, glow, syntax
shift, dot rise) are cancelled and transforms reset. State changes become a 120ms
linear opacity crossfade: thinking dims the eyes to `.65` with no pulse, answering
shows the spark and check statically. The character still communicates state — it
just stops moving.

The same block also flattens the new components: `.proc-dots` hold a static glow,
the Auto orb stops breathing but keeps its glow, and `.slide` drops its
X-translate so the deck cross-fades in place.

---

## 7. What's deliberately absent

No purple-on-black hero gradient. No card grid on the empty state. No emoji
placeholders — every icon is a hand-drawn path on the section's stroke grammar.
No glassmorphism except the one scroll-state top bar blur and the scrim. No
decorative illustration anywhere; the mascot is the only non-functional element,
and it stays at 64px with a fixed perch so it never becomes the subject.

---

## 8. Porting to the Next.js app

`frontend/` is Next 14 + Tailwind + Framer Motion, so:

- **Tokens** → `tailwind.config.ts` under `theme.extend.colors`, keeping the CSS
  custom properties as the source of truth (`bg-[var(--s-2)]`) so the section
  accent swap stays a single `data-section` attribute on the root instead of
  conditional class strings in every component.
- **Section switch** → `data-section` on the layout root, driven by route segment.
  All accent/radius changes follow from the attribute selectors; no per-component
  branching.
- **Mascot** → one `<Mascot section state />` component. Keep it CSS-animated;
  Framer Motion buys nothing here and would re-run the loops on every re-render.
  Drive `state` from the existing `agent_token` / stream-completion events.
- **Message rendering** → the Learning and Code blocks in this prototype are the
  target markup for your streamed content renderers.

---

## 9. Prototype controls

The pill in the bottom-right is scaffolding, not product UI. It forces the Code
mascot's states, runs a scripted turn per section (submit → 1.4s processing →
streamed reply), and `source turn` seeds a YouTube source in Learning so the two
output modes are reachable in one click. Delete `.proto` and its script block when
porting.
