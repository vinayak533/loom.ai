"use client";

import { motion } from "framer-motion";
import { memo } from "react";
import type { SessionRow } from "@/lib/api";
import { cn } from "@/lib/cn";
import { SPRING, useMotionOK } from "./Anim";
import { PinIcon, SessionHistoryMenu, type HistoryAction } from "./SessionHistoryMenu";

/**
 * One row in a session list.
 *
 * Shared by every history list in the app. The row owns its status dot, its
 * pin marker and its overflow menu; the list above it owns grouping and
 * ordering. Anything section-specific belongs in the list, not here — the
 * whole point is that a session looks and behaves the same wherever it is
 * listed.
 */
export const SessionListItem = memo(function SessionListItem({
  session,
  active,
  touch,
  onSelect,
  onAction,
  projectOptions,
  onMoveToProject,
}: {
  session: SessionRow;
  active: boolean;
  /** Touch has no hover state, so the menu trigger stays visible there. */
  touch?: boolean;
  onSelect: (id: string) => void;
  onAction: (id: string, action: HistoryAction) => void;
  /** Passed straight through to the menu. Absent where there are no projects. */
  projectOptions?: { id: string; name: string }[];
  onMoveToProject?: (sessionId: string, projectId: string | null) => void;
}) {
  const motionOK = useMotionOK();

  return (
    <motion.li
      layout={motionOK}
      initial={motionOK ? { opacity: 0, x: -8 } : false}
      animate={{ opacity: 1, x: 0 }}
      exit={motionOK ? { opacity: 0, height: 0 } : { opacity: 0 }}
      transition={motionOK ? SPRING : { duration: 0 }}
      className="group relative"
    >
      <button
        type="button"
        onClick={() => onSelect(session.id)}
        className={cn(
          "flex w-full items-start gap-2.5 rounded-ctl py-2 pl-2.5 pr-9 text-left text-[0.8125rem]",
          "transition-colors duration-200",
          active
            ? "bg-accent-dim text-ink"
            : "text-ink-muted hover:bg-elevated hover:text-ink",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "sigil mt-[6px] h-1.5 w-1.5 shrink-0",
            session.status === "running" ? "bg-warn" : "bg-ink-dim",
          )}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate">
              {session.title || "Untitled"}
            </span>
            {session.is_pinned && (
              // The group heading already says "Pinned", so this is a marker,
              // not a label — it survives when the row is read out of its group.
              <span className="shrink-0 text-accent/70" aria-label="Pinned">
                <PinIcon filled />
              </span>
            )}
          </span>
          {/* The second line only exists once the session has been named as a
              project. A row with nothing to say here stays one line rather than
              reserving space for a description that may never arrive. */}
          {session.description && (
            <span className="mt-0.5 block truncate font-sans text-2xs leading-snug text-ink-faint">
              {session.description}
            </span>
          )}
        </span>
      </button>

      {/* Centred with `inset-y-0` + flex rather than `-translate-y-1/2`. A
          transform creates a stacking context, and the dropdown inside this
          div is `z-50` *within it* — which left the open menu painting at this
          row's depth and every row below it drawing over the top, so Pin,
          Archive and Delete could be seen but not clicked. */}
      <div className="absolute inset-y-0 right-1 flex items-center">
        <SessionHistoryMenu
          pinned={Boolean(session.is_pinned)}
          archived={Boolean(session.is_archived)}
          alwaysVisible={touch}
          onAction={(action) => onAction(session.id, action)}
          projectOptions={projectOptions}
          currentProjectId={session.project_id ?? null}
          onMoveToProject={
            onMoveToProject
              ? (projectId) => onMoveToProject(session.id, projectId)
              : undefined
          }
        />
      </div>
    </motion.li>
  );
});
