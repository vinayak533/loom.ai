"use client";

import { motion } from "framer-motion";
import { useState } from "react";
import type { AgentSummary } from "@/lib/agents";
import type { ChatItem } from "@/lib/useAgentSocket";
import { cn } from "@/lib/cn";
import { SPRING, useMotionOK } from "../Anim";
import { AgentIcon } from "./AgentIcon";

type HandoffItem = Extract<ChatItem, { kind: "handoff" }>;

/**
 * A routing directive, offered as an action.
 *
 * This card is the human-in-the-loop requirement made concrete: the backend
 * emits the directive and does nothing else, so the *only* way the next agent
 * ever runs is that a person presses this button. There is deliberately no
 * auto-accept, no countdown, and no setting to turn it off — autonomous
 * chaining is out of scope for this pass, and this is the seam where it would
 * otherwise creep in.
 *
 * The carried context is shown before it travels, not after. Handing the next
 * agent something the user never saw would make the chain harder to trust,
 * not easier to use.
 */
export function HandoffCard({
  item,
  target,
  onAccept,
}: {
  item: HandoffItem;
  /** The receiving agent's metadata, if the catalogue has loaded. */
  target: AgentSummary | undefined;
  onAccept: (agentId: string, context: string) => void;
}) {
  const motionOK = useMotionOK();
  const [showContext, setShowContext] = useState(false);
  const [taken, setTaken] = useState(false);

  const accent = target?.accent ?? "#B28AFF";

  return (
    <motion.div
      layout={motionOK ? "position" : false}
      initial={motionOK ? { opacity: 0, y: 8 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={motionOK ? SPRING : { duration: 0 }}
      className="overflow-hidden rounded-card border border-line bg-surface"
      style={{ backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)" }}
    >
      <div className="flex items-start gap-3 p-4">
        <span
          className="grid h-9 w-9 shrink-0 place-items-center rounded-ctl border"
          style={{
            borderColor: `${accent}38`,
            backgroundColor: `${accent}14`,
            color: accent,
          }}
        >
          <AgentIcon name={target?.icon ?? "GitBranch"} size={17} />
        </span>

        <div className="min-w-0 flex-1">
          <p className="voice-label text-ink-faint">Suggested handoff</p>
          <p className="mt-1 font-sans text-[0.875rem] font-semibold leading-snug text-ink">
            {item.nextAgentName}
            {target && (
              <span className="ml-2 font-normal text-ink-faint">{target.role}</span>
            )}
          </p>
          <p className="mt-1.5 font-sans text-[0.8125rem] leading-relaxed text-ink-muted">
            {item.reason}
          </p>

          {item.context && (
            <div className="mt-2.5">
              <button
                type="button"
                onClick={() => setShowContext((s) => !s)}
                aria-expanded={showContext}
                className="voice-label flex items-center gap-1.5 transition-colors hover:text-ink-muted"
              >
                <motion.span
                  animate={{ rotate: showContext ? 90 : 0 }}
                  transition={motionOK ? { duration: 0.16 } : { duration: 0 }}
                  aria-hidden
                >
                  ›
                </motion.span>
                What travels across
              </button>
              {showContext && (
                <pre className="scroll-thin voice-machine mt-2 max-h-44 overflow-auto whitespace-pre-wrap
                                rounded-ctl border border-line bg-inset p-3 text-ink/80">
                  {item.context}
                </pre>
              )}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setTaken(true);
                onAccept(item.nextAgent, item.context);
              }}
              disabled={taken}
              className={cn(
                "h-8 rounded-ctl px-3.5 font-sans text-2xs font-semibold transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200",
                "text-accent-ink hover:brightness-110 active:scale-[0.97]",
                "disabled:pointer-events-none disabled:opacity-50",
              )}
              style={{ background: `linear-gradient(135deg, ${accent}, ${accent}bb)` }}
            >
              Hand off to {item.nextAgentName}
            </button>
            <span className="font-sans text-2xs text-ink-faint">
              Opens a new session, pre-seeded. Nothing runs until you click.
            </span>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
