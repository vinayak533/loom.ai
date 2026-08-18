"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";
import { LoomMark } from "./LoomMark";
import { useMotionOK } from "./Anim";

/**
 * The Code section's welcome.
 *
 * Built as a **brand logo animation** — the reference supplied for this pass
 * was a pin from a board of them — rather than as a generic loader, which is
 * why it resolves into the mark instead of into a progress figure.
 *
 * The figure is the product's own name taken literally: warp threads fall,
 * weft threads cross them, the two interlace into a woven field, and the field
 * condenses into the mark. It is the one moment in the app that gets to use
 * the brand's gold (`--gold`), because it is the one moment that is about the
 * brand rather than about the work; three seconds later the section accent has
 * the screen back.
 *
 * Four constraints shaped the timing, and all four came from the brief:
 *
 *  · **Once per session.** `sessionStorage`, not a persisted user flag — a new
 *    browser session earns the moment again, navigating back into Code does
 *    not. The key is written the instant it plays, so a second entry in the
 *    same session is already a no-op before the first frame renders.
 *  · **Short.** 2.5s door to door. Long enough to land, short enough that a
 *    returning user mid-task is not held.
 *  · **Skippable.** Any click, any key. It dismisses on the same 420ms exit it
 *    would have used anyway, so skipping is a shortcut rather than a cut.
 *  · **Reduced motion.** No weave at all — the mark and wordmark fade in over
 *    180ms and the whole thing is gone in under a second. Same brand beat, no
 *    travel, consistent with how the rest of the app answers the query.
 */

const SEEN_KEY = "loom:code-intro:v1";

/** Threads per axis. Odd, so one lands dead centre behind the mark. */
const THREADS = 9;

/** Door to door, in ms. The exit transition runs inside this. */
const RUN_MS = 2500;
const RUN_MS_REDUCED = 900;

/**
 * Whether the intro should play, and how to end it early.
 *
 * Lives here rather than in the component so the page can dim the interface
 * *underneath* the overlay in step with it — that synchronisation is what
 * makes the hand-off read as one movement instead of a curtain being yanked.
 */
export function useCodeIntro(active: boolean) {
  const [playing, setPlaying] = useState(false);
  /** Guards against a re-entry racing the sessionStorage write. */
  const decided = useRef(false);

  useEffect(() => {
    if (!active || decided.current) return;
    decided.current = true;

    try {
      if (sessionStorage.getItem(SEEN_KEY) === "1") return;
      sessionStorage.setItem(SEEN_KEY, "1");
    } catch {
      // Private mode, or storage disabled. Playing once for this mount is a
      // better failure than either never playing or playing on every entry.
    }
    setPlaying(true);
  }, [active]);

  const dismiss = useCallback(() => setPlaying(false), []);
  return { playing, dismiss };
}

export function CodeIntro({
  playing,
  onDismiss,
}: {
  playing: boolean;
  onDismiss: () => void;
}) {
  const motionOK = useMotionOK();

  // Auto-dismiss, and the two skip affordances. One effect so the timer is
  // torn down by the same cleanup that removes the listeners — a skip must not
  // leave a timer running that fires into an already-closed intro.
  useEffect(() => {
    if (!playing) return;

    const timer = setTimeout(onDismiss, motionOK ? RUN_MS : RUN_MS_REDUCED);
    const skip = () => onDismiss();

    window.addEventListener("pointerdown", skip);
    window.addEventListener("keydown", skip);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("pointerdown", skip);
      window.removeEventListener("keydown", skip);
    };
  }, [playing, motionOK, onDismiss]);

  return (
    <AnimatePresence>
      {playing && (
        <motion.div
          key="code-intro"
          // Decorative and self-dismissing: a screen reader should hear the
          // Code section, not a 2.5s animation it cannot act on.
          aria-hidden
          initial={{ opacity: 1 }}
          animate={{ opacity: 1 }}
          // The veil lifts *away* from the viewer rather than dissolving in
          // place — the scale is what stops this reading as a hard cut.
          exit={
            motionOK
              ? { opacity: 0, scale: 1.06 }
              : { opacity: 0 }
          }
          transition={
            motionOK
              ? { duration: 0.42, ease: [0.2, 0, 0, 1] }
              : { duration: 0.2 }
          }
          className="fixed inset-0 z-[60] grid place-items-center bg-base"
        >
          {motionOK ? <Weave /> : null}

          <div className="relative flex flex-col items-center gap-5">
            <motion.div
              initial={motionOK ? { opacity: 0, scale: 0.86 } : { opacity: 0 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={
                motionOK
                  ? { type: "spring", stiffness: 240, damping: 26, mass: 0.9, delay: 1 }
                  : { duration: 0.18 }
              }
              className="relative"
            >
              {/* The mark arrives lit — the weave has just collapsed into it,
                  so it should look like the threads went somewhere. */}
              <span
                aria-hidden
                className="pointer-events-none absolute -inset-10 -z-10"
                style={{
                  background:
                    "radial-gradient(closest-side, rgb(var(--gold) / 0.14), transparent 70%)",
                }}
              />
              <LoomMark size={76} ring={false} />
            </motion.div>

            <motion.div
              initial={motionOK ? { opacity: 0, y: 6 } : { opacity: 0 }}
              animate={{ opacity: 1, y: 0 }}
              transition={
                motionOK
                  ? { duration: 0.5, ease: [0.2, 0, 0, 1], delay: 1.28 }
                  : { duration: 0.18 }
              }
              className="flex flex-col items-center gap-2"
            >
              <span className="text-[1.375rem] font-semibold tracking-[-0.02em] text-ink">
                Loom
              </span>
              {/* The section accent's one appearance here: it names where you
                  have just arrived, which is the only thing on this screen that
                  is about the app rather than the brand. */}
              <span className="voice-label text-accent/80">Code workspace</span>
            </motion.div>
          </div>

          <motion.span
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={
              motionOK ? { duration: 0.4, delay: 1.9 } : { duration: 0.18 }
            }
            className="absolute bottom-10 select-none font-sans text-2xs text-ink-dim"
          >
            Press any key to skip
          </motion.span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/**
 * The weave.
 *
 * Warp falls, weft crosses, both retire. Every thread animates `scaleX`/
 * `scaleY` off a fixed 1px element with a set transform origin — nothing here
 * animates a width, a height or a gradient position, so the whole field is a
 * compositor job no matter how many threads it holds.
 *
 * Weft alternates its origin left/right. That alternation is the entire reason
 * it reads as *weaving* rather than as a grid being drawn: threads that all
 * enter from one side are a wipe, threads that enter from both are a shuttle.
 */
function Weave() {
  const lines = Array.from({ length: THREADS }, (_, i) => i);
  /** Where the field starts giving way to the mark. */
  const RETIRE = 1.15;

  return (
    <span
      aria-hidden
      className="pointer-events-none absolute h-[min(74vw,300px)] w-[min(74vw,300px)]"
    >
      {lines.map((i) => {
        const at = `${((i + 0.5) / THREADS) * 100}%`;
        return (
          <motion.span
            key={`warp-${i}`}
            className="absolute top-0 bottom-0 w-px origin-top"
            style={{
              left: at,
              background:
                "linear-gradient(to bottom, transparent, rgb(var(--gold) / 0.5), transparent)",
            }}
            initial={{ scaleY: 0, opacity: 0 }}
            animate={{ scaleY: 1, opacity: [0, 1, 1, 0] }}
            transition={{
              scaleY: { duration: 0.52, ease: [0.2, 0, 0, 1], delay: i * 0.04 },
              opacity: {
                duration: RETIRE + 0.5,
                times: [0, 0.18, 0.72, 1],
                ease: "linear",
                delay: i * 0.04,
              },
            }}
          />
        );
      })}

      {lines.map((i) => {
        const at = `${((i + 0.5) / THREADS) * 100}%`;
        const fromLeft = i % 2 === 0;
        return (
          <motion.span
            key={`weft-${i}`}
            className={`absolute left-0 right-0 h-px ${
              fromLeft ? "origin-left" : "origin-right"
            }`}
            style={{
              top: at,
              background:
                "linear-gradient(to right, transparent, rgb(var(--gold) / 0.5), transparent)",
            }}
            initial={{ scaleX: 0, opacity: 0 }}
            animate={{ scaleX: 1, opacity: [0, 1, 1, 0] }}
            transition={{
              scaleX: {
                duration: 0.52,
                ease: [0.2, 0, 0, 1],
                delay: 0.34 + i * 0.04,
              },
              opacity: {
                duration: RETIRE + 0.5,
                times: [0, 0.18, 0.66, 1],
                ease: "linear",
                delay: 0.34 + i * 0.04,
              },
            }}
          />
        );
      })}
    </span>
  );
}
