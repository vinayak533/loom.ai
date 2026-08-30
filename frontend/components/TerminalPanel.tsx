"use client";

import { AnimatePresence, motion } from "framer-motion";
import { memo, useEffect, useRef } from "react";
import type { TerminalLine } from "@/lib/useAgentSocket";
import { cn } from "@/lib/cn";
import { SPRING_SOFT, useMotionOK } from "./Anim";

/**
 * The sandbox terminal, docked to the foot of the context column.
 *
 * Collapsed it is a slim rail carrying only what you need at a glance — line
 * count, and whether something is running. Expanded it lifts: a darker inset
 * bed under a blur, so it reads as a well cut into the column rather than
 * another panel stacked onto it.
 *
 * Lines arrive one at a time from `tool_output_chunk` and land individually.
 * Auto-scroll sticks to the bottom, but backs off the moment the user scrolls
 * up — nothing is more annoying than a log that yanks you back down.
 *
 * Memoized: this drawer holds up to 2000 lines, and its props do not change
 * while the model is streaming prose. Without the guard it re-rendered every
 * one of them on every token of every answer.
 */
export const TerminalPanel = memo(function TerminalPanel({
  lines,
  busy,
  open,
  onToggle,
  onClear,
}: {
  lines: TerminalLine[];
  busy: boolean;
  open: boolean;
  onToggle: () => void;
  onClear: () => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const motionOK = useMotionOK();

  useEffect(() => {
    const el = scroller.current;
    if (!el || !pinned.current) return;
    el.scrollTop = el.scrollHeight;
  }, [lines, open]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  return (
    <motion.section
      initial={false}
      animate={{ height: open ? 244 : 40 }}
      transition={motionOK ? SPRING_SOFT : { duration: 0 }}
      className="relative z-10 shrink-0 overflow-hidden border-t border-line bg-inset"
      style={{ backdropFilter: "blur(18px)", WebkitBackdropFilter: "blur(18px)" }}
    >
      <header className="flex h-10 touch:h-12 items-center gap-3 px-4">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="group flex h-full min-w-[44px] items-center gap-2 text-ink-muted transition-colors hover:text-ink"
        >
          <motion.span
            animate={{ rotate: open ? 90 : 0 }}
            transition={motionOK ? { duration: 0.18 } : { duration: 0 }}
            className="text-ink-faint"
            aria-hidden
          >
            ›
          </motion.span>
          <span className="voice-label group-hover:text-ink-muted">Terminal</span>
        </button>

        {busy && (
          // A soft breathe rather than a blink — the same restraint as the
          // transcript's live tail, so the two in-progress cues in view at
          // once share a rhythm instead of competing.
          <span className="flex items-center gap-1.5 font-mono text-2xs text-warn/85">
            <motion.span
              className="sigil h-1 w-1 bg-warn"
              animate={motionOK ? { opacity: [0.35, 1, 0.35] } : { opacity: 1 }}
              transition={
                motionOK
                  ? { duration: 2, repeat: Infinity, ease: "easeInOut" }
                  : { duration: 0 }
              }
              aria-hidden
            />
            running
          </span>
        )}

        <span className="voice-machine ml-auto text-ink-faint">
          {lines.length}
        </span>
        {lines.length > 0 && (
          <button
            type="button"
            onClick={onClear}
            className="voice-label text-ink-faint transition-colors hover:text-ink-muted"
          >
            Clear
          </button>
        )}
      </header>

      <div
        ref={scroller}
        onScroll={onScroll}
        className="scroll-thin h-[204px] overflow-auto px-4 pb-3 font-mono text-[0.6875rem] leading-[1.7]"
      >
        {lines.length === 0 ? (
          <p className="pt-1 text-ink-faint">
            Shell output from the sandbox streams here.
          </p>
        ) : (
          <AnimatePresence initial={false}>
            {lines.map((line) => (
              <motion.div
                key={line.id}
                initial={motionOK ? { opacity: 0, x: -3 } : false}
                animate={{ opacity: 1, x: 0 }}
                transition={motionOK ? { duration: 0.12 } : { duration: 0 }}
                className={cn(
                  "whitespace-pre-wrap break-all",
                  line.stream === "stderr"
                    ? "text-del/90"
                    : line.stream === "meta"
                      ? "text-accent"
                      : "text-ink/70",
                )}
              >
                {line.text || " "}
              </motion.div>
            ))}
          </AnimatePresence>
        )}
        {busy && (
          <span className="inline-block h-3 w-[6px] translate-y-[2px] animate-caret bg-accent" />
        )}
      </div>
    </motion.section>
  );
});
