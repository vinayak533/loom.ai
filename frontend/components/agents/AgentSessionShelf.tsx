"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useMemo } from "react";
import type { SessionRow } from "@/lib/api";
import type { AgentSummary } from "@/lib/agents";
import { cn } from "@/lib/cn";
import { SPRING_SNAP, useMotionOK } from "../Anim";
import type { HistoryAction } from "../SessionHistoryMenu";
import { SessionListItem } from "../SessionListItem";
import { AgentIcon } from "./AgentIcon";

/**
 * This specialist's past conversations.
 *
 * Scoped per agent rather than pooled: the backend returns only sessions whose
 * `agent_id` matches, so a list of ten specialists' threads never has to be
 * labelled or disambiguated — the header already says whose it is.
 *
 * The rows are the shared `SessionListItem`, which brings the shared
 * `SessionHistoryMenu` with it. That was the explicit requirement, and it is
 * also the reason pin, archive and delete behave identically here and in Chat:
 * there is one implementation of each.
 */
export function AgentSessionShelf({
  agent,
  sessions,
  activeId,
  open,
  onToggle,
  onSelect,
  onNew,
  onAction,
}: {
  agent: AgentSummary;
  sessions: SessionRow[];
  activeId: string | null;
  open: boolean;
  onToggle: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onAction: (id: string, action: HistoryAction) => void;
}) {
  const motionOK = useMotionOK();

  const { pinned, rest } = useMemo(
    () => ({
      pinned: sessions.filter((s) => s.is_pinned),
      rest: sessions.filter((s) => !s.is_pinned),
    }),
    [sessions],
  );

  return (
    <>
      {/* The handle. Always present, so the history is discoverable without
          having to know a shortcut — and out of the way, because most turns
          here are one-offs rather than resumed projects. */}
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-label={open ? "Hide this agent's history" : "Show this agent's history"}
        className={cn(
          "absolute right-3 top-3 z-20 grid h-8 w-8 place-items-center rounded-ctl",
          "border border-line text-ink-faint transition-colors duration-200",
          "hover:bg-elevated hover:text-ink",
          open && "bg-raised text-ink",
        )}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 7v5l3.5 2" />
          <path d="M3.5 12a8.5 8.5 0 1 0 2.2-5.7" />
          <path d="M3.5 5.5V10H8" />
        </svg>
      </button>

      <AnimatePresence>
        {open && (
          <motion.aside
            key="agent-shelf"
            initial={motionOK ? { opacity: 0, x: 16 } : { opacity: 0 }}
            animate={{ opacity: 1, x: 0 }}
            exit={motionOK ? { opacity: 0, x: 16 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            aria-label={`${agent.name} history`}
            className="glass absolute inset-y-3 right-3 z-10 flex w-[min(19rem,calc(100vw-2rem))]
                       flex-col overflow-hidden rounded-panel"
          >
            <div className="flex items-center gap-2.5 px-3.5 pb-2.5 pt-3.5">
              <span
                className="grid h-7 w-7 shrink-0 place-items-center rounded-ctl border"
                style={{
                  borderColor: `${agent.accent}38`,
                  backgroundColor: `${agent.accent}14`,
                  color: agent.accent,
                }}
              >
                <AgentIcon name={agent.icon} size={14} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="voice-label">history</p>
                <p className="truncate font-sans text-[0.8125rem] text-ink">
                  {agent.name}
                </p>
              </div>
              {/* Space for the handle, which floats above this panel. */}
              <span className="w-8 shrink-0" aria-hidden />
            </div>

            <div className="px-2.5 pb-2">
              <button
                type="button"
                onClick={onNew}
                className="flex h-8 w-full items-center gap-2 rounded-ctl border border-line
                           px-2.5 font-sans text-2xs text-ink-muted transition-colors
                           duration-200 hover:bg-raised hover:text-ink"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden>
                  <path d="M12 5v14M5 12h14" />
                </svg>
                New conversation
              </button>
            </div>

            <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 pb-3">
              {sessions.length === 0 && (
                <p className="px-2 py-6 text-center font-sans text-2xs leading-relaxed text-ink-faint">
                  Nothing yet. Conversations with {agent.name} appear here.
                </p>
              )}

              {pinned.length > 0 && (
                <>
                  <p className="voice-label px-2 pb-1 pt-2">pinned</p>
                  <ul className="space-y-0.5">
                    <AnimatePresence initial={false}>
                      {pinned.map((session) => (
                        <SessionListItem
                          key={session.id}
                          session={session}
                          active={session.id === activeId}
                          onSelect={onSelect}
                          onAction={onAction}
                        />
                      ))}
                    </AnimatePresence>
                  </ul>
                </>
              )}

              {rest.length > 0 && (
                <>
                  {pinned.length > 0 && (
                    <p className="voice-label px-2 pb-1 pt-3">recent</p>
                  )}
                  <ul className="space-y-0.5">
                    <AnimatePresence initial={false}>
                      {rest.map((session) => (
                        <SessionListItem
                          key={session.id}
                          session={session}
                          active={session.id === activeId}
                          onSelect={onSelect}
                          onAction={onAction}
                        />
                      ))}
                    </AnimatePresence>
                  </ul>
                </>
              )}
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}
