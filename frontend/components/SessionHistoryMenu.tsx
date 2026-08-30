"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { SPRING_SNAP, useMotionOK } from "./Anim";

export type HistoryAction = "rename" | "pin" | "archive" | "delete";

/**
 * The per-session overflow menu — pin, archive, delete.
 *
 * Written once and mounted in every session list in the app. Chat and Code
 * have different layouts around their history, but a session is a session:
 * two implementations of "archive" would eventually disagree about what
 * archiving does, and the user would be the one to find out.
 *
 * Delete confirms *inside the menu* rather than in a modal. A modal for one
 * destructive row-level action is a disproportionate amount of ceremony — it
 * takes over the screen to ask about something the pointer is already resting
 * on. The second click is deliberately in a different place from the first, so
 * a double-click cannot carry through it.
 */
export function SessionHistoryMenu({
  pinned,
  archived,
  onAction,
  label = "session",
  align = "right",
  alwaysVisible = false,
  actions = ["rename", "pin", "archive", "delete"],
  projectOptions,
  currentProjectId = null,
  onMoveToProject,
}: {
  pinned: boolean;
  archived: boolean;
  onAction: (action: HistoryAction) => void;
  /** Named in the aria labels, e.g. "session" / "notebook". */
  label?: string;
  align?: "left" | "right";
  /** Touch has no hover, so the trigger cannot be hover-revealed there. */
  alwaysVisible?: boolean;
  /**
   * Which entries this list supports. Notebooks archive and delete but do not
   * pin — the library is a grid, and there is no "top" for a pin to move a
   * card to. Offering an action the surface cannot honour is worse than
   * offering one fewer.
   */
  actions?: HistoryAction[];
  /**
   * Projects this session can be filed into. Optional: the notebook library
   * mounts this same menu and has no project concept, and an entry that
   * cannot be honoured is worse than one fewer.
   */
  projectOptions?: { id: string; name: string }[];
  currentProjectId?: string | null;
  onMoveToProject?: (projectId: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  /**
   * The project picker replaces the menu body in place, the same way the
   * delete confirm does. A flyout submenu inside a 172px panel that already
   * lives in a scrolling container is a clipping problem with extra steps.
   */
  const [picking, setPicking] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const motionOK = useMotionOK();

  // Close on an outside click or on Escape. Both, because a menu that only
  // closes one way is a menu you end up with two of.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // A menu that reopens still holding a primed delete — or still in the
  // project picker — is a trap.
  useEffect(() => {
    if (!open) {
      setConfirming(false);
      setPicking(false);
    }
  }, [open]);

  const run = (action: HistoryAction) => {
    setOpen(false);
    onAction(action);
  };

  return (
    <div ref={root} className="relative shrink-0">
      <button
        type="button"
        aria-label={`More actions for this ${label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          // The row underneath is a navigation control; opening its menu must
          // not also navigate to it.
          e.stopPropagation();
          e.preventDefault();
          setOpen((o) => !o);
        }}
        className={cn(
          "grid h-7 w-7 place-items-center rounded-ctl text-ink-faint",
          "transition-all duration-200 hover:bg-raised hover:text-ink",
          "focus-visible:opacity-100",
          open
            ? "bg-raised text-ink opacity-100"
            : alwaysVisible
              ? "opacity-100"
              : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
        )}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <circle cx="12" cy="5" r="1.6" />
          <circle cx="12" cy="12" r="1.6" />
          <circle cx="12" cy="19" r="1.6" />
        </svg>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            initial={motionOK ? { opacity: 0, scale: 0.94, y: -4 } : { opacity: 0 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={motionOK ? { opacity: 0, scale: 0.96, y: -2 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            // Anchored to the trigger and above everything: these lists live
            // inside scrolling containers, and a menu clipped by its own
            // scroller is worse than no menu.
            style={{ transformOrigin: align === "right" ? "top right" : "top left" }}
            // Opaque, not `.glass`. A translucent panel is the house style for
            // things that float over the *floor*, but this floats over
            // whatever the row is — including a notebook's cover art — and a
            // 4% white tint over a lit gradient is not a readable menu. This
            // is the documented case for the solid surface tokens.
            className={cn(
              "absolute top-8 z-50 w-[172px] overflow-hidden rounded-ctl border border-line-strong",
              "bg-overlay p-1 shadow-lift",
              align === "right" ? "right-0" : "left-0",
            )}
          >
            {picking ? (
              <div className="max-h-56 overflow-y-auto">
                <p className="px-2 pb-1 pt-1 text-2xs text-ink-faint">Move to</p>
                <MenuItem
                  onClick={() => {
                    setOpen(false);
                    onMoveToProject?.(null);
                  }}
                  icon={<span className="w-[15px]" />}
                  label="No project"
                  muted={currentProjectId === null}
                />
                {(projectOptions ?? []).map((p) => (
                  <MenuItem
                    key={p.id}
                    onClick={() => {
                      setOpen(false);
                      onMoveToProject?.(p.id);
                    }}
                    icon={<span className="w-[15px]" />}
                    label={p.name}
                    muted={currentProjectId === p.id}
                  />
                ))}
              </div>
            ) : (
            <>
            {actions.includes("rename") && (
              <MenuItem
                onClick={() => run("rename")}
                icon={<RenameIcon />}
                label="Rename"
              />
            )}
            {actions.includes("pin") && (
              <MenuItem
                onClick={() => run("pin")}
                icon={<PinIcon filled={pinned} />}
                label={pinned ? "Unpin" : "Pin"}
              />
            )}
            {actions.includes("archive") && (
              <MenuItem
                onClick={() => run("archive")}
                icon={<ArchiveIcon />}
                label={archived ? "Unarchive" : "Archive"}
              />
            )}

            {onMoveToProject && projectOptions && (
              <MenuItem
                onClick={() => setPicking(true)}
                icon={<FolderIcon />}
                label="Move to project"
              />
            )}

            {actions.includes("delete") && actions.length > 1 && (
              <div className="my-1 h-px bg-line" />
            )}

            {!actions.includes("delete") ? null : confirming ? (
              // The confirm replaces the row in place — the menu stays the
              // same size and the pointer does not have to travel.
              <div className="px-2 pb-1 pt-0.5">
                <p className="pb-1.5 text-2xs leading-snug text-ink-muted">
                  Delete permanently?
                </p>
                <div className="flex gap-1">
                  <button
                    type="button"
                    onClick={() => run("delete")}
                    className="h-7 flex-1 rounded-ctl bg-del/15 text-2xs font-medium text-del
                               transition-colors duration-150 hover:bg-del/25"
                  >
                    Delete
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(false)}
                    className="h-7 flex-1 rounded-ctl text-2xs text-ink-muted
                               transition-colors duration-150 hover:bg-raised hover:text-ink"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <MenuItem
                onClick={() => setConfirming(true)}
                icon={<TrashIcon />}
                label="Delete"
                danger
              />
            )}
            </>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  danger,
  muted,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
  /**
   * This entry is where the session already is. Still clickable — re-choosing
   * it is a harmless no-op, and disabling it would remove the one row that
   * answers "which project is this in?" from the keyboard order.
   */
  muted?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={cn(
        "flex h-8 w-full items-center gap-2.5 rounded-ctl px-2.5 text-left text-[0.8125rem]",
        "transition-colors duration-150",
        danger
          ? "text-ink-muted hover:bg-del/12 hover:text-del"
          : "text-ink-muted hover:bg-raised hover:text-ink",
        muted && "text-ink",
      )}
    >
      <span className="shrink-0 opacity-80">{icon}</span>
      <span className="min-w-0 truncate">{label}</span>
      {muted && (
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="ml-auto shrink-0 text-accent"
          aria-hidden
        >
          <path d="M20 6 9 17l-5-5" />
        </svg>
      )}
    </button>
  );
}

export function PinIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M9 3h6l-.7 5.2 3 3.1V14H6.7v-2.7l3-3.1L9 3Z" />
      <path d="M12 14v7" fill="none" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M3 7a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.7.9l.8 1.2H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
    </svg>
  );
}

function RenameIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 16.5V20h3.5L18 9.5 14.5 6 4 16.5Z" />
      <path d="m13.2 7.3 3.5 3.5" />
    </svg>
  );
}

function ArchiveIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 6.5h18V9H3z" />
      <path d="M5 9v9.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V9" />
      <path d="M10 13h4" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 7h16" />
      <path d="M9.5 7V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2V7" />
      <path d="M6.5 7l.8 12.1A1.5 1.5 0 0 0 8.8 20.5h6.4a1.5 1.5 0 0 0 1.5-1.4L17.5 7" />
    </svg>
  );
}
