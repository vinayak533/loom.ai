"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { TOOL_LABEL, toolSubtitle } from "@/lib/events";
import type { ChatItem } from "@/lib/useAgentSocket";
import { cn, formatDuration, shortPath } from "@/lib/cn";
import { SPRING, SPRING_SNAP, useMotionOK } from "./Anim";

type ToolItem = Extract<ChatItem, { kind: "tool" }>;

/**
 * A tool call, as it appears on the trace spine.
 *
 * The tool's identity now lives in the spine's gutter node, so the card itself
 * is free to be quiet: a single ruled row with the tool's name in the
 * interface voice and its argument in the machine voice, expanding to the raw
 * input and output. No icon, no heavy chrome — the gutter already said what
 * this is.
 *
 * While a call is in flight a single hairline runs along the card's base. It
 * replaces the old full-card sheen, which competed with the spine for the same
 * "something is happening" signal; one travelling line, sharing the spine's
 * vocabulary, says it once.
 */
export function ToolCallCard({ item }: { item: ToolItem }) {
  const running = item.status === "running";
  const failed = item.status === "error";
  const [open, setOpen] = useState(false);
  const motionOK = useMotionOK();

  const subtitle = toolSubtitle(item.tool, item.input);
  const label = TOOL_LABEL[item.tool] ?? item.tool;
  const duration =
    item.endedAt && item.startedAt ? formatDuration(item.endedAt - item.startedAt) : null;

  return (
    <motion.div
      layout={motionOK ? "position" : false}
      initial={motionOK ? { opacity: 0, y: 8, scale: 0.99 } : false}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={motionOK ? SPRING : { duration: 0 }}
      className={cn(
        "relative overflow-hidden rounded-card border bg-surface",
        failed ? "border-del/30" : running ? "border-warn/25" : "border-line",
      )}
      style={{ backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="relative flex w-full items-center gap-3 px-3.5 py-2.5 text-left"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="font-sans text-[0.8125rem] font-semibold tracking-[-0.01em] text-ink">
              {label}
            </span>
            {duration && (
              <span className="voice-machine text-2xs text-ink-faint">{duration}</span>
            )}
          </div>
          {subtitle && (
            <div className="voice-machine truncate text-ink-muted">
              {item.tool === "bash_execute" || item.tool === "web_search"
                ? subtitle
                : shortPath(subtitle)}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {running ? (
            // Quieter than the `done`/`failed` chips it replaces: a bare label
            // with a 3px dot breathing beside it. A running card should read as
            // "not finished yet", not as the loudest thing in the transcript.
            <span className="flex items-center gap-1.5">
              <motion.span
                aria-hidden
                className="sigil h-[3px] w-[3px] bg-warn"
                animate={motionOK ? { opacity: [0.35, 1, 0.35] } : { opacity: 1 }}
                transition={
                  motionOK
                    ? { duration: 2, repeat: Infinity, ease: "easeInOut" }
                    : { duration: 0 }
                }
              />
              <span className="voice-label text-warn/85">Running</span>
            </span>
          ) : failed ? (
            <span className="chip border-del/30 text-del">
              {item.exitCode !== undefined ? `exit ${item.exitCode}` : "failed"}
            </span>
          ) : (
            <span className="chip border-add/25 text-add">done</span>
          )}
          <motion.span
            animate={{ rotate: open ? 90 : 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            className="text-ink-faint"
            aria-hidden
          >
            ›
          </motion.span>
        </div>
      </button>

      {/* In-flight hairline. One composited transform, no repaint. Shorter,
          dimmer and slower than it was — it should be noticed on the second
          glance, not the first. */}
      {running && motionOK && (
        <motion.span
          aria-hidden
          className="pointer-events-none absolute bottom-0 left-0 h-px w-1/4"
          style={{
            background:
              "linear-gradient(90deg, transparent, rgb(var(--acc) / 0.45), transparent)",
            willChange: "transform",
          }}
          animate={{ x: ["-100%", "400%"] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
        />
      )}

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            initial={motionOK ? { height: 0, opacity: 0 } : false}
            animate={{ height: "auto", opacity: 1 }}
            exit={motionOK ? { height: 0, opacity: 0 } : { opacity: 0 }}
            transition={
              motionOK ? { ...SPRING, opacity: { duration: 0.15 } } : { duration: 0 }
            }
            className="overflow-hidden border-t border-line"
          >
            <div className="space-y-3 px-3.5 py-3">
              <Section title="input">
                <pre className="scroll-thin voice-machine max-h-48 overflow-auto whitespace-pre-wrap text-ink-muted">
                  {JSON.stringify(item.input, null, 2)}
                </pre>
              </Section>

              {item.results && item.results.length > 0 && (
                <Section title="results">
                  <ul className="space-y-2">
                    {item.results.map((r, i) => (
                      <li key={i}>
                        <a
                          href={r.url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-xs font-medium text-accent hover:text-accent-hover"
                        >
                          {r.title}
                        </a>
                        <p className="mt-0.5 line-clamp-2 text-xs text-ink-muted">
                          {r.snippet}
                        </p>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {item.output !== undefined && (
                <Section title="output">
                  <pre
                    className={cn(
                      "scroll-thin voice-machine max-h-72 overflow-auto whitespace-pre-wrap",
                      failed ? "text-del/90" : "text-ink/80",
                    )}
                  >
                    {item.output || "(empty)"}
                  </pre>
                </Section>
              )}

              {running && (
                <p className="voice-machine text-ink-faint">Waiting for result…</p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="voice-label mb-1.5 select-none">{title}</div>
      {children}
    </div>
  );
}
