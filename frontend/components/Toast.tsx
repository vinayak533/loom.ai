"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ModelToast } from "@/lib/useAgentSocket";
import { cn } from "@/lib/cn";
import { AnimationBoundary, SPRING_SNAP, useMotionOK } from "./Anim";

/**
 * Two toast channels, deliberately different.
 *
 * **The routing toast** (`ToastHost`) is single-slot and announces model
 * switches. A routing decision is a fact about the session, not a step in the
 * conversation, so it is announced beside the transcript and then withdrawn
 * rather than written into it. It never blocks, never stacks (a replacement
 * crossfades through the card it replaces), and wears the reserved accent
 * because the auto-router is the one voice allowed to.
 *
 * **The action toast** (`ActionToastHost`, reached through `useToast`) is the
 * general feedback channel the product did not have: a session deleted, an
 * export that failed, a commit that landed. It stacks to three, sits
 * bottom-left away from the composer, and carries an optional action slot,
 * which is what makes deletion *reversible* rather than merely confirmed:
 * the row leaves immediately and the toast holds an Undo for six seconds,
 * committing the deletion only when that window closes. The intentional case
 * got faster and the accidental case became recoverable.
 */

/* ------------------------------------------------------------- routing */

/**
 * How long a routing toast sits before it withdraws itself. The toast is the
 * announcement, not the record: the selector still shows what the session is
 * on, and Project Pulse still names the live model.
 */
const DWELL = 3000;

const TONE: Record<ModelToast["tone"], { card: string; mark: string; text: string }> = {
  // The auto-router. The reserved accent appears here and nowhere else.
  route: {
    card: "border-rare-line bg-accent-rare/[0.06]",
    mark: "bg-accent-rare",
    text: "text-accent-rare",
  },
  // A pick the user made themselves.
  manual: {
    card: "border-accent-line bg-accent/[0.06]",
    mark: "bg-accent",
    text: "text-ink",
  },
  // An involuntary switch: the chosen model errored and the router completed
  // the request on another one. Warn rather than error: the request
  // succeeded, but the user is entitled to notice that the model they picked
  // is not the one that answered.
  fallback: {
    card: "border-warn/35 bg-[rgba(240,181,74,0.08)]",
    mark: "bg-warn",
    text: "text-warn",
  },
  error: {
    card: "border-del/35 bg-del-bg",
    mark: "bg-del",
    text: "text-del",
  },
};

export function ToastHost({
  toast,
  onDismiss,
}: {
  toast: ModelToast | null;
  onDismiss: (id: string) => void;
}) {
  const motionOK = useMotionOK();
  const id = toast?.id;

  // Keyed on the id, so a switch that lands while one is showing restarts the
  // clock for the new message instead of inheriting the old one's remainder.
  useEffect(() => {
    if (!id) return;
    const timer = setTimeout(() => onDismiss(id), DWELL);
    return () => clearTimeout(timer);
  }, [id, onDismiss]);

  return (
    // The viewport is the live region: it outlives the cards that pass
    // through it, which is what lets a screen reader announce each new
    // announcement rather than re-announcing the region every time one mounts.
    <div
      aria-live="polite"
      className="pointer-events-none fixed right-4 top-[4.25rem] z-50 w-[min(21rem,calc(100vw-2rem))]"
    >
      <AnimationBoundary>
        <AnimatePresence>
          {toast && (
            <motion.div
              key={toast.id}
              initial={motionOK ? { opacity: 0, y: -10, scale: 0.97 } : { opacity: 0 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={
                motionOK
                  ? { opacity: 0, y: -8, scale: 0.98, transition: { duration: 0.22 } }
                  : { opacity: 0, transition: { duration: 0.12 } }
              }
              transition={motionOK ? SPRING_SNAP : { duration: 0 }}
              onClick={() => onDismiss(toast.id)}
              className={cn(
                // Absolute so a replacement lands in the same slot rather than
                // beside or below the card it is replacing.
                "pointer-events-auto absolute right-0 top-0 flex cursor-default items-center",
                "gap-2.5 rounded-ctl border px-3 py-2 shadow-lift backdrop-blur-xl",
                TONE[toast.tone].card,
              )}
              style={{ WebkitBackdropFilter: "blur(20px)" }}
            >
              <span
                aria-hidden
                className={cn("sigil h-[7px] w-[7px] shrink-0", TONE[toast.tone].mark)}
              />
              <span
                className={cn(
                  "min-w-0 font-sans text-ui leading-snug",
                  TONE[toast.tone].text,
                )}
              >
                {toast.text}
              </span>
            </motion.div>
          )}
        </AnimatePresence>
      </AnimationBoundary>
    </div>
  );
}

/* -------------------------------------------------------------- actions */

export type ActionToastTone = "success" | "warning" | "failure";

export type ActionToast = {
  id: string;
  text: string;
  tone: ActionToastTone;
  /** A single affordance: Undo, Retry, Open. */
  action?: { label: string; onClick: () => void };
  /** Milliseconds before the toast withdraws. Undo toasts hold longer. */
  dwell: number;
  /** Runs when the toast expires *without* its action having been taken. */
  onExpire?: () => void;
};

type Notify = Omit<ActionToast, "id" | "dwell"> & { dwell?: number };

type ToastApi = {
  notify: (toast: Notify) => string;
  dismiss: (id: string) => void;
  /**
   * An optimistic, reversible deletion. `revert` puts the thing back;
   * `commit` performs the real deletion and runs only if nobody pressed Undo
   * before the window closed (or the page was left, see `flush`).
   */
  undoable: (text: string, handlers: { revert: () => void; commit: () => void | Promise<void> }) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

const MAX_VISIBLE = 3;
const ACTION_DWELL = 4000;
const UNDO_DWELL = 6000;

let counter = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ActionToast[]>([]);
  // Pending commits, so a page that is left mid-window still performs the
  // deletion instead of leaving the row to reappear after a reload.
  const pending = useRef(new Map<string, () => void | Promise<void>>());

  const dismiss = useCallback((id: string) => {
    setToasts((all) => all.filter((t) => t.id !== id));
  }, []);

  const notify = useCallback((toast: Notify) => {
    const id = `t${++counter}`;
    const dwell = toast.dwell ?? (toast.action ? UNDO_DWELL : ACTION_DWELL);
    setToasts((all) => [...all, { ...toast, id, dwell }].slice(-MAX_VISIBLE * 2));
    return id;
  }, []);

  const undoable = useCallback<ToastApi["undoable"]>(
    (text, { revert, commit }) => {
      const id = `t${++counter}`;
      let settled = false;
      const run = () => {
        if (settled) return;
        settled = true;
        pending.current.delete(id);
        void commit();
      };
      pending.current.set(id, run);
      setToasts((all) => [
        ...all,
        {
          id,
          text,
          tone: "success",
          dwell: UNDO_DWELL,
          action: {
            label: "Undo",
            onClick: () => {
              if (settled) return;
              settled = true;
              pending.current.delete(id);
              revert();
              setToasts((cur) => cur.filter((t) => t.id !== id));
            },
          },
          onExpire: run,
        },
      ]);
    },
    [],
  );

  useEffect(() => {
    const flush = () => {
      for (const run of Array.from(pending.current.values())) run();
    };
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, []);

  const api = useMemo(() => ({ notify, dismiss, undoable }), [notify, dismiss, undoable]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ActionToastHost toasts={toasts.slice(-MAX_VISIBLE)} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) {
    throw new Error("useToast must be used inside <ToastProvider>");
  }
  return api;
}

const ACTION_TONE: Record<ActionToastTone, { mark: string; text: string; card: string }> = {
  success: { mark: "bg-add", text: "text-ink", card: "border-line" },
  warning: { mark: "bg-warn", text: "text-warn", card: "border-warn/35" },
  failure: { mark: "bg-del", text: "text-del", card: "border-del/35" },
};

function ActionToastHost({
  toasts,
  onDismiss,
}: {
  toasts: ActionToast[];
  onDismiss: (id: string) => void;
}) {
  return (
    // Bottom-left beside the rail on a desktop, where it is furthest from the
    // composer. On a phone the composer *is* the bottom, so the stack hangs
    // under the header instead: a toast over the field you are typing into
    // is the one placement worse than none.
    <div
      aria-live="polite"
      className="pointer-events-none fixed left-4 top-[4.25rem] z-50 flex w-[min(22rem,calc(100vw-2rem))]
                 flex-col gap-2 sm:bottom-4 sm:left-[calc(var(--rail-w)+1rem)] sm:top-auto sm:flex-col-reverse"
    >
      <AnimationBoundary>
        <AnimatePresence initial={false}>
          {toasts.map((t) => (
            <ActionToastCard key={t.id} toast={t} onDismiss={onDismiss} />
          ))}
        </AnimatePresence>
      </AnimationBoundary>
    </div>
  );
}

function ActionToastCard({
  toast,
  onDismiss,
}: {
  toast: ActionToast;
  onDismiss: (id: string) => void;
}) {
  const motionOK = useMotionOK();
  const { id, dwell, onExpire } = toast;
  const expire = useRef(onExpire);
  expire.current = onExpire;

  useEffect(() => {
    const timer = setTimeout(() => {
      expire.current?.();
      onDismiss(id);
    }, dwell);
    return () => clearTimeout(timer);
  }, [id, dwell, onDismiss]);

  return (
    <motion.div
      layout={motionOK}
      initial={motionOK ? { opacity: 0, y: 10, scale: 0.98 } : { opacity: 0 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={
        motionOK
          ? { opacity: 0, y: 8, scale: 0.98, transition: { duration: 0.18 } }
          : { opacity: 0, transition: { duration: 0.12 } }
      }
      transition={motionOK ? SPRING_SNAP : { duration: 0 }}
      role="status"
      className={cn(
        "glass pointer-events-auto flex items-center gap-2.5 rounded-ctl px-3 py-2",
        ACTION_TONE[toast.tone].card,
      )}
    >
      <span aria-hidden className={cn("sigil h-[7px] w-[7px] shrink-0", ACTION_TONE[toast.tone].mark)} />
      <span className={cn("min-w-0 flex-1 font-sans text-ui leading-snug", ACTION_TONE[toast.tone].text)}>
        {toast.text}
      </span>
      {toast.action && (
        <button
          type="button"
          onClick={toast.action.onClick}
          className="shrink-0 rounded-inner border border-accent-line bg-accent/[0.08] px-2 py-0.5
                     font-sans text-2xs font-semibold text-accent transition-colors duration-200
                     hover:bg-accent/[0.14]"
        >
          {toast.action.label}
        </button>
      )}
      <button
        type="button"
        onClick={() => onDismiss(id)}
        aria-label="Dismiss"
        className="grid h-6 w-6 shrink-0 place-items-center rounded-inner text-ink-faint
                   transition-colors duration-200 hover:bg-raised hover:text-ink"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
          <path d="m6 6 12 12M18 6 6 18" />
        </svg>
      </button>
    </motion.div>
  );
}
