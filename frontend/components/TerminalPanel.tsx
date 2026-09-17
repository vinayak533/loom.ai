"use client";

import { AnimatePresence, motion } from "framer-motion";
import { memo, useEffect, useRef, useState } from "react";
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
 * It is a terminal, not a log: the prompt line at the foot takes a command
 * and runs it in the same sandbox the agent works in, with the same streamed
 * output. Up and down arrows walk the commands typed this session. One
 * command at a time — the input locks while one runs, because the panel is
 * a single terminal and two commands interleaving in it would be unreadable.
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
  onRun,
  running = false,
}: {
  lines: TerminalLine[];
  /** Something — the agent's command or the user's — is producing output. */
  busy: boolean;
  open: boolean;
  onToggle: () => void;
  onClear: () => void;
  /**
   * Run a command the user typed. Returns false when it could not be sent
   * (socket down), in which case the text stays in the input. Absent when
   * there is no session to run in — the panel is then output-only.
   */
  onRun?: (command: string) => boolean;
  /** A user-typed command is still running; the prompt is locked until it exits. */
  running?: boolean;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const motionOK = useMotionOK();
  const input = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState("");
  /** Commands typed this session, oldest first. */
  const [history, setHistory] = useState<string[]>([]);
  /** Where the up/down arrows are in `history`; null means "the draft". */
  const [cursor, setCursor] = useState<number | null>(null);

  useEffect(() => {
    const el = scroller.current;
    if (!el || !pinned.current) return;
    el.scrollTop = el.scrollHeight;
  }, [lines, open, running]);

  // Opening the drawer is opening a terminal; the cursor belongs in it.
  useEffect(() => {
    if (open && onRun && !running) input.current?.focus();
  }, [open, onRun, running]);

  const submit = () => {
    const command = draft.trim();
    if (!command || running || !onRun) return;
    if (!onRun(command)) return;
    setHistory((h) => [...h.filter((c) => c !== command), command].slice(-100));
    setDraft("");
    setCursor(null);
    pinned.current = true;
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === "ArrowUp" && history.length) {
      event.preventDefault();
      const next = cursor === null ? history.length - 1 : Math.max(0, cursor - 1);
      setCursor(next);
      setDraft(history[next]);
      return;
    }
    if (event.key === "ArrowDown" && cursor !== null) {
      event.preventDefault();
      const next = cursor + 1;
      if (next >= history.length) {
        setCursor(null);
        setDraft("");
      } else {
        setCursor(next);
        setDraft(history[next]);
      }
    }
  };

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
      <header className="flex h-bar-sub items-center gap-3 px-3.5">
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
            {onRun
              ? "A shell in the sandbox. The agent's commands stream here; type your own below."
              : "Shell output from the sandbox streams here."}
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
        {onRun && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
            className="mt-1 flex items-center gap-2"
          >
            <span className="shrink-0 text-accent" aria-hidden>
              $
            </span>
            <input
              ref={input}
              value={draft}
              disabled={running}
              onChange={(event) => {
                setDraft(event.target.value);
                setCursor(null);
              }}
              onKeyDown={onKeyDown}
              placeholder={running ? "running…" : "Type a command and press Enter"}
              aria-label="Terminal command"
              spellCheck={false}
              autoComplete="off"
              autoCapitalize="off"
              className="min-w-0 flex-1 bg-transparent font-mono text-[0.6875rem] text-ink
                         outline-none placeholder:text-ink-faint disabled:opacity-60"
            />
          </form>
        )}
      </div>
    </motion.section>
  );
});
