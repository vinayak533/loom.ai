"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect } from "react";
import type { ModelToast } from "@/lib/useAgentSocket";
import { cn } from "@/lib/cn";
import { AnimationBoundary, SPRING_SNAP, useMotionOK } from "./Anim";

/**
 * The model-switch toast.
 *
 * A routing decision is a fact about the session, not a step in the
 * conversation, so it is announced beside the transcript and then withdrawn
 * rather than written into it. Three rules:
 *
 *  1. It never blocks. The viewport is `pointer-events-none` and only the card
 *     itself takes the pointer, so a toast over the context column costs the
 *     user nothing but a click-through they will not need.
 *  2. It never stacks. Auto can reroute twice in quick succession; the state
 *     holds exactly one announcement, and the card is absolutely positioned in
 *     its slot, so a replacement crossfades *through* the one it replaces
 *     instead of pushing a second card into view. Two switches in a second
 *     read as one card being replaced, which is what actually happened.
 *
 *     Deliberately not `AnimatePresence mode="wait"`, which would be the
 *     tidier way to say the same thing: an exiting child that never completes
 *     wedges that mode, and a wedged toast is a card stuck on screen rather
 *     than a missing flourish. Overlapping for one 220ms crossfade is the
 *     cheaper failure.
 *  3. It is small. Same hairline, same blur and same accent registers as every
 *     other floating surface — the reserved rare accent for the auto-router,
 *     because that is the one voice allowed to wear it.
 *
 * Placement is top-right, hung just below the 56px header so it clears the
 * status read-out rather than covering it. That is also the corner furthest
 * from the composer, where the model selector that triggered it lives — the
 * notification does not land on top of the control the user is still using.
 */

/**
 * How long a toast sits before it withdraws itself — the same three seconds
 * for every tone, a failed switch included. A switch that could not be honoured
 * is not lost when the card goes: the selector still shows what the session is
 * actually on, and Project Pulse still names the live model and how it was
 * chosen. The toast is the announcement, not the record.
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
  // the request on another one. Warn rather than error — the request succeeded,
  // so colouring it like a failure would misreport what happened; but it is not
  // a neutral routing decision either, and the user is entitled to notice that
  // the model they picked is not the one that answered.
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
    // The viewport is the live region — it outlives the cards that pass
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
              // Reduced motion keeps the toast — it just arrives and leaves as
              // an opacity change rather than travelling.
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
                  "min-w-0 font-sans text-[0.8125rem] leading-snug",
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
