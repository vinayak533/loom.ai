"use client";

import { AnimatePresence, motion } from "framer-motion";
import type { ReactNode } from "react";
import type { AgentStatus, ToolStatus } from "@/lib/useAgentSocket";
import { cn } from "@/lib/cn";
import {
  AnimationBoundary,
  BREATH,
  CUE_STAGGER,
  EASE_BREATH,
  INSTANT,
  SPRING,
  SPRING_CUE,
  SPRING_SOFT,
  useMotionOK,
} from "./Anim";

/**
 * The trace spine — the app's signature.
 *
 * A transcript is normally a stack of unrelated boxes scrolling past. This
 * turns it into one continuous thread: every step the agent takes — reason,
 * tool call, result, reason again — is a node on a single line that runs down
 * the left gutter and *draws itself* as the work happens. While the agent is
 * still going, the leading end of the thread carries a travelling light, so
 * the path visibly reaches downward into empty space toward whatever comes
 * next. The task reads as a journey with a shape, not as a log.
 *
 * Three rules govern everything here:
 *
 *  1. The spine is a sibling of the content, never its parent. Every row is a
 *     two-cell grid — gutter, then content — and the content cell renders
 *     unconditionally. The entire gutter is wrapped in an `AnimationBoundary`,
 *     so if any of this throws, the transcript loses a decoration and keeps
 *     every word.
 *
 *  2. Nothing here gates reading. No entrance animation delays text; the
 *     thread draws behind content that is already on screen, and the
 *     travelling head is pure decoration on empty space.
 *
 *  3. Transforms and opacity only. Two composited properties per element, no
 *     canvas, no layout thrash, no per-frame JavaScript. The whole system is
 *     a handful of springs and one repeating keyframe.
 *
 * Node shape is the rhombus used by the logo and the agent avatar — same mark
 * at four sizes, so the thread is unmistakably this product's and not a
 * generic timeline widget.
 */

/** Gutter width, and where a node's centre sits inside a row. */
export const TRACE_GUTTER = 38;
const NODE_TOP = 13;

export type TraceKind = "user" | "agent" | "tool" | "notice" | "pending" | "gate";

// ---------------------------------------------------------------- the nodes

const TOOL_GLYPH: Record<string, string> = {
  bash_execute: "$",
  git: "⎇",
  web_search: "⌕",
  edit_file: "±",
  write_file: "+",
  read_file: "≡",
  list_files: "⊞",
};

/**
 * The mark. One rhombus, drawn as a rotated square so its edges stay crisp at
 * 1px, with an optional upright glyph floating inside it.
 */
function Rhombus({
  size,
  tone,
  filled,
  glyph,
  className,
}: {
  size: number;
  tone: string;
  filled?: boolean;
  glyph?: string;
  className?: string;
}) {
  return (
    <span
      className={cn("relative grid shrink-0 place-items-center", className)}
      style={{ width: size, height: size }}
    >
      <span
        className={cn("absolute inset-[14%] rotate-45 rounded-[2px] border", tone)}
        // A hollow node is filled with the floor, not with a tint, so the
        // thread behind it is occluded rather than tinted through.
        style={filled ? undefined : { background: "rgb(0 0 0 / 0.72)" }}
      />
      {glyph && (
        <span className="relative font-mono text-[10px] font-medium leading-none">
          {glyph}
        </span>
      )}
    </span>
  );
}

function NodeMark({
  kind,
  tool,
  status,
  live,
  level,
}: {
  kind: TraceKind;
  tool?: string;
  status?: ToolStatus;
  live?: boolean;
  level?: "error" | "warn";
}) {
  const motionOK = useMotionOK();

  switch (kind) {
    // The user's turn is the quietest node on the thread: small, solid, no
    // colour. It marks where the agent was handed something.
    case "user":
      return <Rhombus size={9} tone="border-ink-dim bg-ink-dim" filled />;

    // The agent speaking. Hollow, accent, with a ring that breathes out of it
    // while tokens are still landing — small enough to stay a cue rather than
    // a halo; the gutter is a margin note, not a display.
    case "agent":
      return (
        <span className="relative grid place-items-center">
          {live && motionOK && (
            <motion.span
              aria-hidden
              className="sigil absolute h-3 w-3 bg-accent"
              animate={{ scale: [0.7, 1.55], opacity: [0.32, 0] }}
              transition={{ duration: BREATH, repeat: Infinity, ease: EASE_BREATH }}
            />
          )}
          <Rhombus size={15} tone="border-accent-line bg-accent-soft" />
        </span>
      );

    // A tool call. Same mark, one size up, carrying the tool's glyph — so the
    // gutter alone tells you the shape of the run: think, shell, think, edit.
    case "tool": {
      const failed = status === "error";
      const running = status === "running";
      return (
        <span className="relative grid place-items-center">
          {running && motionOK && (
            <motion.span
              aria-hidden
              className="sigil absolute h-3.5 w-3.5 bg-warn"
              animate={{ scale: [0.8, 1.5], opacity: [0.3, 0] }}
              transition={{ duration: BREATH, repeat: Infinity, ease: EASE_BREATH }}
            />
          )}
          <Rhombus
            size={21}
            tone={cn(
              failed
                ? "border-del/45"
                : running
                  ? "border-warn/55"
                  : "border-line-strong",
            )}
            glyph={TOOL_GLYPH[tool ?? ""] ?? "◇"}
            className={cn(
              failed ? "text-del" : running ? "text-warn" : "text-ink-faint",
            )}
          />
        </span>
      );
    }

    case "notice":
      return (
        <Rhombus
          size={11}
          tone={level === "error" ? "border-del/60 bg-del/25" : "border-warn/60 bg-warn/20"}
        />
      );

    // Agents section: a point where the thread stops and waits for a person —
    // an approval card, or a handoff offered but not taken.
    //
    // The largest node on the spine, and the only one drawn as a *doubled*
    // rhombus. That is the point: everything else on the thread is something
    // the agent did, and this is the one place it could not proceed alone. It
    // breathes while `live`, using the same slow cycle as the pending head so
    // "waiting on you" and "working" read as the same family of state rather
    // than two unrelated animations.
    case "gate":
      return (
        <span className="relative grid place-items-center">
          {live && motionOK && (
            <motion.span
              aria-hidden
              className="sigil absolute h-4 w-4 bg-accent-rare"
              animate={{ scale: [0.75, 1.6], opacity: [0.34, 0] }}
              transition={{ duration: BREATH, repeat: Infinity, ease: EASE_BREATH }}
            />
          )}
          <Rhombus size={23} tone="border-rare-line bg-rare-soft" />
          <Rhombus
            size={11}
            tone="border-accent-rare/70 bg-accent-rare/25"
            className="absolute"
            filled
          />
        </span>
      );

    // The live end of the thread. A small dot that breathes — the state it is
    // in is told by the cue beside the label in `TraceTail`, not by making the
    // node itself into a graphic.
    case "pending":
      return <PendingMark />;
  }
}

/**
 * The live head of the thread, at rest.
 *
 * This used to be a full-size rhombus rotating inside an expanding ring — a
 * small spinner, but a spinner, and the largest moving thing on the screen
 * while the agent worked. It is now the quietest node on the spine: a 7px mark
 * that breathes on a slow, soft cycle and does nothing else. Working is not an
 * event that deserves a graphic; it is a state that deserves a heartbeat.
 */
function PendingMark() {
  const motionOK = useMotionOK();
  if (!motionOK) return <Rhombus size={9} tone="border-accent bg-accent-soft" />;

  return (
    // `block` is load-bearing: the gutter hands this to a plain wrapper, and a
    // bare inline span takes no width or height from its size classes.
    <motion.span
      aria-hidden
      className="sigil block h-[7px] w-[7px] bg-accent"
      animate={{ opacity: [0.35, 1, 0.35], scale: [0.9, 1.1, 0.9] }}
      transition={{ duration: BREATH, repeat: Infinity, ease: EASE_BREATH }}
    />
  );
}

// -------------------------------------------------------------- the gutter

/**
 * The thread itself, plus the node, for one row.
 *
 * The line is a single 1px element spanning the row's full height — including
 * the row's own bottom padding — which is why transcript rows sit flush
 * against each other and own their spacing internally. That is what keeps the
 * thread continuous instead of dashed.
 */
function Gutter({
  kind,
  tool,
  status,
  level,
  first,
  last,
  live,
  extending,
}: {
  kind: TraceKind;
  tool?: string;
  status?: ToolStatus;
  level?: "error" | "warn";
  first?: boolean;
  last?: boolean;
  live?: boolean;
  /** The agent is still working, so the thread reaches past the final node. */
  extending?: boolean;
}) {
  const motionOK = useMotionOK();

  return (
    <div
      className="relative shrink-0 select-none"
      style={{ width: TRACE_GUTTER }}
      aria-hidden
    >
      {/* Thread above the node — absent on the very first row, so the spine
          begins at a point rather than running off the top edge. */}
      {!first && (
        <span
          className="absolute left-1/2 w-px -translate-x-1/2 bg-line-strong"
          style={{ top: 0, height: NODE_TOP }}
        />
      )}

      {/* Thread below the node. This is the part that draws itself: it scales
          from its top edge on a spring as each new step lands, which is what
          makes the path read as being *extended* rather than revealed. */}
      {(!last || extending) && (
        <motion.span
          className="absolute left-1/2 w-px -translate-x-1/2 origin-top"
          style={{
            top: NODE_TOP + 11,
            bottom: 0,
            background:
              "linear-gradient(to bottom, rgba(255,255,255,0.12), rgba(255,255,255,0.05))",
          }}
          initial={motionOK ? { scaleY: 0, opacity: 0 } : false}
          animate={{ scaleY: 1, opacity: 1 }}
          transition={motionOK ? SPRING_SOFT : { duration: 0 }}
        />
      )}

      {/* The travelling head. Only ever on the final row, only while the agent
          is working, and only over empty gutter — it never crosses text. Kept
          short and dim: it is a hint that the thread continues, not a beam. */}
      {extending && last && motionOK && (
        <motion.span
          className="trace-head absolute left-1/2 w-px -translate-x-1/2"
          style={{
            top: NODE_TOP + 10,
            height: 18,
            background:
              "linear-gradient(to bottom, transparent, rgb(var(--acc) / 0.55), transparent)",
            willChange: "transform, opacity",
          }}
          animate={{ y: [0, 34], opacity: [0, 0.9, 0] }}
          transition={{ duration: BREATH, repeat: Infinity, ease: EASE_BREATH }}
        />
      )}

      {/* The node, floated over the thread. */}
      <span
        className="absolute left-1/2"
        style={{ top: NODE_TOP, transform: "translate(-50%, -50%)" }}
      >
        <motion.span
          className="block"
          initial={motionOK ? { scale: 0.4, opacity: 0 } : false}
          animate={{ scale: 1, opacity: 1 }}
          transition={motionOK ? SPRING : { duration: 0 }}
        >
          <NodeMark kind={kind} tool={tool} status={status} live={live} level={level} />
        </motion.span>
      </span>
    </div>
  );
}

// ----------------------------------------------------------------- the row

/**
 * Vertical rhythm, per kind of step.
 *
 * Every row used to carry a flat 24px, which is the single biggest reason a
 * long run read as a list of boxes: a tool call and the prose that introduced
 * it were as far apart as two separate turns. Tool steps now sit tighter than
 * prose, so a think→run→run→think sequence clusters into one visible unit, and
 * the wider gaps fall where the conversation actually changes hands.
 *
 * The numbers are the app's own 4px scale — 20 / 24 / 28 — not new values.
 */
const ROW_GAP: Record<TraceKind, string> = {
  user: "pb-7", // 28 — a turn boundary
  agent: "pb-7", // 28
  tool: "pb-5", // 20 — a step inside a turn
  notice: "pb-6", // 24
  gate: "pb-6", // 24
  pending: "pb-6", // 24
};

/**
 * One step on the thread.
 *
 * Content renders whether or not the gutter does — that is the contract. The
 * row itself carries no entrance animation; individual children may, but the
 * text is in the DOM and readable from the first frame regardless.
 */
export function TraceRow({
  kind,
  tool,
  status,
  level,
  first,
  last,
  live,
  extending,
  align = "start",
  children,
}: {
  kind: TraceKind;
  tool?: string;
  status?: ToolStatus;
  level?: "error" | "warn";
  first?: boolean;
  last?: boolean;
  live?: boolean;
  extending?: boolean;
  /** `end` right-aligns the content cell — used for the user's own turns. */
  align?: "start" | "end";
  children: ReactNode;
}) {
  return (
    <div className="relative flex">
      <AnimationBoundary
        fallback={<div style={{ width: TRACE_GUTTER }} className="shrink-0" aria-hidden />}
      >
        <Gutter
          kind={kind}
          tool={tool}
          status={status}
          level={level}
          first={first}
          last={last}
          live={live}
          extending={extending}
        />
      </AnimationBoundary>

      <div
        className={cn(
          "min-w-0 flex-1 pt-px",
          ROW_GAP[kind],
          align === "end" && "flex justify-end",
        )}
      >
        {children}
      </div>
    </div>
  );
}

// ------------------------------------------------------------ the live tail

const PENDING_COPY: Partial<Record<AgentStatus, string>> = {
  thinking: "Reasoning",
  executing: "Working the sandbox",
  streaming: "Composing",
};

/**
 * The row that exists only while the agent is between steps.
 *
 * The whole of it is one line of small caps with a shimmer running through the
 * words and a cue no taller than the text beside them. Nothing here is larger
 * than a caption, and nothing moves quickly: while the agent is working the
 * interface should feel *occupied*, not animated at.
 *
 * The state distinctions are intact — they have simply been moved into the
 * cue's rhythm rather than its size. Reasoning breathes, composing draws a
 * line, sandbox work scans. Each is a different motion at the same tiny scale,
 * which is what lets them be told apart without reading the label.
 */
export function TraceTail({
  status,
  label,
  first,
}: {
  status: AgentStatus;
  label?: string;
  first?: boolean;
}) {
  const copy = label ?? PENDING_COPY[status] ?? "Working";
  const motionOK = useMotionOK();

  return (
    <TraceRow kind="pending" first={first} last extending>
      <div role="status" aria-live="polite" className="flex h-[22px] items-center gap-2">
        {/* Keyed on the copy so a status change replays the entrance rather
            than swapping the glyphs underneath a static block. Deliberately
            not wrapped in AnimatePresence — the label's width changes with the
            word, and a `mode="wait"` exit would hold the old width for its
            whole duration and shunt the cue sideways twice per transition. */}
        <motion.span
          key={copy}
          initial={motionOK ? { opacity: 0, y: 3 } : false}
          animate={{ opacity: 1, y: 0 }}
          transition={motionOK ? { duration: 0.24, ease: [0.2, 0, 0, 1] } : INSTANT}
        >
          <ShimmerWords text={copy} />
        </motion.span>
        <WorkCue status={status} />
      </div>
    </TraceRow>
  );
}

/**
 * A few words with a slow highlight passing through them.
 *
 * Text that is quietly alive costs no space at all, which is exactly why it is
 * carrying most of the "still working" signal now. With reduced motion it is
 * simply the label — legible, and still the same words.
 */
export function ShimmerWords({ text, className }: { text: string; className?: string }) {
  const motionOK = useMotionOK();

  if (!motionOK) {
    return <span className={cn("voice-label text-ink-muted", className)}>{text}</span>;
  }

  // The gradient, the text clipping and the motion all live together in
  // `.work-shimmer`, so reduced motion can retire the whole treatment in one
  // move rather than stopping the animation under transparent glyphs.
  return <span className={cn("voice-label work-shimmer", className)}>{text}</span>;
}

/**
 * The state cue: one small figure per act, each with its own rhythm.
 *
 * Two things changed in this pass, and neither of them is the size — the
 * figures are the same 4px dot, 20×1px hairline and 8px bars they were.
 *
 * **They now share a footprint.** All three are centred in one fixed 20×8 box,
 * so swapping between them cannot move the label beside them. Previously each
 * cue was its own inline element of its own width, and every status change
 * nudged the row.
 *
 * **They now cross-fade.** Each act used to mount and unmount independently:
 * the dot vanished, then bars appeared in a different place at a different
 * size. One `AnimatePresence` on a shared spring turns that into a
 * substitution — the thing the interface is doing changes, the indicator does
 * not restart. That, and the single shared period underneath all three loops,
 * is the whole of the "make it feel crafted, not bigger" brief.
 */
function WorkCue({ status }: { status: AgentStatus }) {
  const motionOK = useMotionOK();
  const act =
    status === "executing" ? "sandbox" : status === "streaming" ? "compose" : "reason";

  return (
    <span className="relative grid h-2 w-5 shrink-0 place-items-center" aria-hidden>
      <AnimatePresence initial={false} mode="wait">
        <motion.span
          key={act}
          className="absolute inset-0 grid place-items-center"
          initial={motionOK ? { opacity: 0, scale: 0.6 } : false}
          animate={{ opacity: 1, scale: 1 }}
          exit={motionOK ? { opacity: 0, scale: 0.6 } : { opacity: 0 }}
          transition={motionOK ? SPRING_CUE : INSTANT}
        >
          {act === "sandbox" ? (
            <ScanBars />
          ) : act === "compose" ? (
            <ComposeRule />
          ) : (
            <ReasonDot />
          )}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

/** Composing: a hairline that draws itself, the width of a couple of glyphs. */
function ComposeRule() {
  const motionOK = useMotionOK();

  if (!motionOK) return <span className="h-px w-5 bg-accent/50" />;

  return (
    <motion.span
      className="h-px w-5 origin-left"
      style={{
        background: "linear-gradient(90deg, rgb(var(--acc) / 0.6), transparent)",
      }}
      animate={{ scaleX: [0.35, 1, 0.35], opacity: [0.45, 0.9, 0.45] }}
      transition={{ duration: BREATH, repeat: Infinity, ease: EASE_BREATH }}
    />
  );
}

/** Reasoning: a single dot, breathing. */
function ReasonDot() {
  const motionOK = useMotionOK();

  if (!motionOK) return <span className="sigil h-1 w-1 bg-accent/70" />;

  return (
    <motion.span
      className="sigil h-1 w-1 bg-accent"
      animate={{ opacity: [0.3, 0.85, 0.3] }}
      transition={{ duration: BREATH, repeat: Infinity, ease: EASE_BREATH }}
    />
  );
}

/**
 * Tool work gets bars, not a breath — a different rhythm for a different act.
 * Eight pixels tall, hairline-wide: it sits inside the cap height of the label
 * next to it rather than beside it as a figure in its own right.
 *
 * The bars animate `scaleY` off a fixed 8px element rather than animating
 * `height` between percentages. This file's third rule is "transforms and
 * opacity only", and the old version was the one place in the app that broke
 * it: an animated `height` is a layout property, so three of these ran the
 * layout and paint stages every frame, for the entire duration of every tool
 * call. Same figure, same 8px, now composited.
 */
export function ScanBars({ className }: { className?: string }) {
  const motionOK = useMotionOK();

  if (!motionOK) {
    return (
      <span className={cn("inline-flex h-2 items-end gap-[2px]", className)} aria-hidden>
        {[0, 1, 2].map((i) => (
          <span key={i} className="h-1.5 w-px rounded-full bg-warn/60" />
        ))}
      </span>
    );
  }

  return (
    <span className={cn("inline-flex h-2 items-end gap-[2px]", className)} aria-hidden>
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="h-2 w-px origin-bottom rounded-full bg-warn/80"
          animate={{ scaleY: [0.3, 1, 0.5, 0.8, 0.3] }}
          transition={{
            duration: BREATH,
            repeat: Infinity,
            ease: EASE_BREATH,
            delay: i * CUE_STAGGER,
          }}
        />
      ))}
    </span>
  );
}
