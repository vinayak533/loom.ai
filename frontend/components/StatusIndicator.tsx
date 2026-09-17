"use client";

import { motion } from "framer-motion";
import type { AgentStatus } from "@/lib/useAgentSocket";
import { cn } from "@/lib/cn";
import { BREATH, EASE_BREATH, useMotionOK } from "./Anim";
import { ScanBars } from "./TraceSpine";

/**
 * The header's read-out.
 *
 * It speaks the same visual language as the trace spine rather than inventing
 * a second one: the same rhombus, the same expanding ring for reasoning, the
 * same bar figure for sandbox work. Reasoning breathes, tool work scans — so
 * the two are told apart by motion, and the words are confirmation rather than
 * the only signal.
 */

const COPY: Record<AgentStatus, string> = {
  connecting: "Connecting",
  offline: "Disconnected",
  idle: "Ready",
  thinking: "Thinking",
  streaming: "Responding",
  executing: "Running tool",
  error: "Error",
};

const TONE: Record<AgentStatus, string> = {
  connecting: "bg-ink-faint",
  offline: "bg-ink-faint",
  idle: "bg-add",
  thinking: "bg-accent",
  streaming: "bg-accent",
  executing: "bg-warn",
  error: "bg-del",
};

/**
 * The mark, breathing. Used wherever the agent is thinking or answering.
 *
 * The halo is deliberately kept inside the header's own line height: this is a
 * status read-out in a 56px bar, and an indicator that pulses out to twice the
 * height of the text beside it stops being a read-out.
 */
export function ThinkingDot({ className }: { className?: string }) {
  const motionOK = useMotionOK();
  return (
    <span className={cn("relative inline-grid h-2 w-2 place-items-center", className)}>
      {motionOK && (
        <motion.span
          aria-hidden
          className="sigil absolute h-2 w-2 bg-accent"
          animate={{ scale: [0.9, 1.6, 0.9], opacity: [0.35, 0, 0.35] }}
          transition={{ duration: BREATH, repeat: Infinity, ease: EASE_BREATH }}
        />
      )}
      <span className="sigil relative h-1.5 w-1.5 bg-accent" aria-hidden />
    </span>
  );
}

/** Kept as a named export — `ScanBars` is the same figure under its own name. */
export { ScanBars as Waveform };

export function StatusIndicator({
  status,
  iterations,
  usage,
  showCost = true,
}: {
  status: AgentStatus;
  iterations: number;
  usage: { input: number; output: number; cost: number };
  /**
   * Whether the estimated dollar cost sits beside the token counts.
   *
   * False in the Code section, which reports tokens and nothing else — see
   * the note on `ProjectPulse`'s usage block. The value is still on `usage`
   * either way; this only decides whether it is drawn.
   */
  showCost?: boolean;
}) {
  const motionOK = useMotionOK();

  return (
    <div className="flex items-center gap-3 text-xs text-ink-muted">
      <div className="flex items-center gap-2">
        {status === "thinking" || status === "streaming" ? (
          <ThinkingDot />
        ) : status === "executing" ? (
          <ScanBars />
        ) : (
          <span className={cn("sigil h-2 w-2", TONE[status])} aria-hidden />
        )}
        {/* Keyed on `status` so each label re-mounts and plays its entrance.
            Deliberately NOT wrapped in AnimatePresence mode="wait": the key can
            change within the mount commit, which wedges the exiting child and
            leaves a stale label on screen. */}
        <motion.span
          key={status}
          initial={motionOK ? { opacity: 0, y: 4 } : false}
          animate={{ opacity: 1, y: 0 }}
          transition={motionOK ? { duration: 0.18 } : { duration: 0 }}
          className="voice-label text-ink-muted"
        >
          {COPY[status]}
        </motion.span>
      </div>

      {iterations > 0 && (
        <span className="chip hidden sm:inline-flex">
          {iterations} step{iterations === 1 ? "" : "s"}
        </span>
      )}
      {usage.output > 0 && (
        <span
          className="chip hidden md:inline-flex"
          data-tip={
            showCost
              ? "Tokens this session (input / output) and estimated cost"
              : "Tokens this session (input / output)"
          }
        >
          {usage.input.toLocaleString()}↓ {usage.output.toLocaleString()}↑
          {showCost && <> · ${usage.cost.toFixed(3)}</>}
        </span>
      )}
    </div>
  );
}
