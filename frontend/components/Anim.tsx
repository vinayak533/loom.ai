"use client";

import { useReducedMotion, type Transition } from "framer-motion";
import { Component, useEffect, useState, type ReactNode } from "react";

/**
 * The motion vocabulary, in one place.
 *
 * Everything tied to the agent actually doing work moves on a spring — never
 * on an eased duration. A duration says "this takes 260ms"; a spring says
 * "this has mass and it just arrived", and that difference is the whole reason
 * the working state reads as alive rather than as a progress bar.
 *
 * Three tunings, and only three:
 *   SPRING       the default arrival — cards, nodes, rows
 *   SPRING_SOFT  larger bodies that should settle rather than snap — panels
 *   SPRING_SNAP  small confirmations that must feel instant — toggles, chips
 */
export const SPRING: Transition = {
  type: "spring",
  stiffness: 380,
  damping: 32,
  mass: 0.8,
};

export const SPRING_SOFT: Transition = {
  type: "spring",
  stiffness: 210,
  damping: 30,
  mass: 1,
};

export const SPRING_SNAP: Transition = {
  type: "spring",
  stiffness: 620,
  damping: 38,
  mass: 0.5,
};

/**
 * The working state's own tuning.
 *
 * Everything that idles while the agent is busy — the shimmer on the label,
 * the reasoning dot, the composing hairline, the sandbox bars, every node ring
 * on the spine — now breathes on ONE period and ONE curve. Before this pass
 * they were each pitched by hand (2.1s, 1.8s, 1.7s, 1.9s, 2.4s, 3.4s), which
 * is the difference between an interface that is occupied and one that is
 * merely busy: a handful of near-but-not-quite tempos beat against each other
 * and read as several unrelated widgets rather than one state.
 *
 * `EASE_BREATH` is a sine in/out rather than framer's `easeInOut`. At the
 * turnarounds — which is where a slow two-second loop is actually looked at —
 * easeInOut holds noticeably longer at the extremes, and the pause is what
 * makes a pulse read as a blink.
 */
export const BREATH = 2.4;
/** Typed as a bezier tuple, not `as const` — framer's `Easing` rejects readonly. */
export const EASE_BREATH: [number, number, number, number] = [0.37, 0, 0.63, 1];

/** Stagger between members of one cue (bars, threads, dots). */
export const CUE_STAGGER = 0.12;

/**
 * Swapping one cue for another — reasoning → sandbox → composing.
 *
 * Stiff enough to feel like a substitution rather than a transition, soft
 * enough that it does not snap. This is what the states were missing: each one
 * used to mount and unmount independently, so a status change popped.
 */
export const SPRING_CUE: Transition = {
  type: "spring",
  stiffness: 520,
  damping: 34,
  mass: 0.45,
};

/** What a spring becomes when the user has asked for less motion. */
export const INSTANT: Transition = { duration: 0 };

/**
 * Whether it is safe to animate.
 *
 * Two things are folded in here, and both have to be, because this value
 * decides *class names* as well as transitions:
 *
 *  1. `useReducedMotion` returns `null` until the media query has been read,
 *     so `=== false` treats "not yet known" as "reduce" — the safer default.
 *
 *  2. It stays `false` until after mount. The server cannot know a user's
 *     motion preference, so any class chosen from it would differ between the
 *     server's HTML and React's first client render — a hydration mismatch.
 *     Deferring past mount makes those two renders identical by construction,
 *     and everything that mounts later (every transcript row, every tool card)
 *     is created after the flag has settled, so it animates normally. The only
 *     thing given up is an entrance animation on content that was already in
 *     the HTML — which should not animate on hydration anyway.
 */
export function useMotionOK(): boolean {
  const reduced = useReducedMotion();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted && reduced === false;
}

/**
 * Picks between a spring and an instant transition. Use this rather than
 * branching at every call site.
 */
export function useSpring(base: Transition = SPRING): Transition {
  return useMotionOK() ? base : INSTANT;
}

/**
 * Containment for anything decorative.
 *
 * The brief's hard rule: if the animation logic fails, the underlying content
 * must still render. Every generative/animated component in this app is
 * therefore mounted inside one of these, and every one of them is a *sibling*
 * of the content it decorates rather than a parent of it — so the worst case
 * is a missing flourish, never a missing tool result.
 */
export class AnimationBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    // Decorative failures are worth knowing about but never worth surfacing.
    if (process.env.NODE_ENV !== "production") {
      console.warn("[loom] animation layer failed, content unaffected:", error);
    }
  }

  render() {
    if (this.state.failed) return this.props.fallback ?? null;
    return this.props.children;
  }
}
