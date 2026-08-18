"use client";

import { motion } from "framer-motion";
import { useEffect } from "react";
import type { AgentStatus } from "@/lib/useAgentSocket";
import { AnimationBoundary, useMotionOK } from "./Anim";

/**
 * The ambient field — the layer the whole app is painted on.
 *
 * Two very large, very soft radial poles drifting against each other on long,
 * offset cycles, over the flat black bed in `globals.css`. They are white, not
 * accent-coloured, and sit at 1–2% alpha: the background stays black, and the
 * poles only keep it from banding across a large screen.
 *
 * The field also *listens*. When the agent starts working, the tracking pole
 * brightens and pulls toward where the work is happening; when it goes idle it
 * dims and drifts back. That is the "faint glow anchored to where the agent is
 * active" — it is the slowest, quietest member of the working-state animation
 * system, and the only one you are not supposed to consciously notice.
 *
 * Cost: two composited layers animating `transform` and `opacity` only. No
 * canvas, no per-frame JS, nothing that repaints. On a low-end profile this is
 * two extra compositor layers and no main-thread work at all.
 */

/** Where the accent pole sits, per state, and how strongly it burns. */
const ANCHOR: Record<AgentStatus, { x: string; y: string; strength: number }> = {
  // Idle states sit high and cool.
  connecting: { x: "50%", y: "18%", strength: 0.2 },
  offline: { x: "50%", y: "18%", strength: 0.12 },
  idle: { x: "62%", y: "22%", strength: 0.3 },
  // Working states pull toward the conversation and lift a little. A white
  // pole reads further on a true-black floor than it did on the old
  // near-black, so the working strengths are pulled back to compensate — the
  // field should still be the thing you never consciously notice.
  thinking: { x: "44%", y: "38%", strength: 0.6 },
  streaming: { x: "40%", y: "46%", strength: 0.7 },
  // Tool work pulls right, toward the sandbox column.
  executing: { x: "76%", y: "52%", strength: 0.65 },
  error: { x: "50%", y: "30%", strength: 0.3 },
};

function Field({ status }: { status: AgentStatus }) {
  const motionOK = useMotionOK();
  const anchor = ANCHOR[status] ?? ANCHOR.idle;

  // The bed gradient in `globals.css` reads these, so the pole and the bed
  // move together rather than sliding past one another.
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty("--field-x", anchor.x);
    root.style.setProperty("--field-y", anchor.y);
    root.style.setProperty("--field-strength", String(anchor.strength));
  }, [anchor.x, anchor.y, anchor.strength]);

  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      {/* The pole that tracks the agent. */}
      <motion.div
        className={motionOK ? "field-layer animate-drift-a absolute" : "field-layer absolute"}
        style={{
          left: `calc(${anchor.x} - 45vmax)`,
          top: `calc(${anchor.y} - 45vmax)`,
          width: "90vmax",
          height: "90vmax",
          background:
            "radial-gradient(circle at center, rgb(255 255 255 / 0.022) 0%, rgb(255 255 255 / 0.008) 38%, transparent 68%)",
          filter: "blur(28px)",
          willChange: "transform, opacity",
          transition: "left 1100ms cubic-bezier(.4,0,.2,1), top 1100ms cubic-bezier(.4,0,.2,1)",
        }}
        animate={{ opacity: 0.28 + anchor.strength * 0.5 }}
        transition={motionOK ? { duration: 0.9, ease: [0.4, 0, 0.2, 1] } : { duration: 0 }}
      />

      {/* Counter-pole — cool, static in intent, drifting on a longer cycle so
          the two never sync up into a visible beat. */}
      <div
        className={motionOK ? "field-layer animate-drift-b absolute" : "field-layer absolute"}
        style={{
          left: "-30vmax",
          bottom: "-40vmax",
          width: "88vmax",
          height: "88vmax",
          background:
            "radial-gradient(circle at center, rgb(255 255 255 / 0.014) 0%, rgb(255 255 255 / 0.005) 40%, transparent 70%)",
          filter: "blur(34px)",
          willChange: "transform",
        }}
      />

      {/* A single hairline horizon. Costs nothing, and gives the eye something
          to read the two poles as sitting behind. */}
      <div
        className="absolute inset-x-0 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, rgba(255,255,255,0.07) 30%, rgba(255,255,255,0.07) 70%, transparent)",
        }}
      />
    </div>
  );
}

export function AmbientField({ status }: { status: AgentStatus }) {
  return (
    <AnimationBoundary>
      <Field status={status} />
    </AnimationBoundary>
  );
}
