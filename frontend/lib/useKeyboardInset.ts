"use client";

import { useEffect } from "react";

/**
 * Keep the composer above the on-screen keyboard.
 *
 * `h-dvh` and `interactive-widget=resizes-content` between them handle Android
 * Chrome. iOS Safari honours neither: the keyboard slides *over* the page
 * without changing the layout viewport at all, so a bottom-anchored composer
 * ends up underneath it and the user is typing into something they cannot see.
 *
 * The only thing on iOS that knows the keyboard is there is the VisualViewport
 * API — the gap between `window.innerHeight` and the visual viewport's height
 * and offset is exactly the keyboard. That gap is published here as a CSS
 * variable so layout can react to it in CSS rather than through React state,
 * which matters because this fires on every frame of the keyboard animation
 * and a `setState` per frame would re-render the whole transcript.
 *
 * On a desktop browser `visualViewport` exists but never reports a gap, so the
 * variable stays at 0 and nothing about the layout changes.
 */
export function useKeyboardInset(): void {
  useEffect(() => {
    const vv = typeof window !== "undefined" ? window.visualViewport : null;
    if (!vv) return;

    let frame = 0;
    const apply = () => {
      frame = 0;
      // How much of the layout viewport the keyboard is covering. `offsetTop`
      // is included because iOS scrolls the visual viewport up as well as
      // shrinking it, and both move the bottom edge.
      const covered = Math.max(
        0,
        window.innerHeight - vv.height - vv.offsetTop,
      );
      // Sub-pixel jitter during the animation would repaint for nothing.
      document.documentElement.style.setProperty(
        "--kb-inset",
        `${Math.round(covered)}px`,
      );
    };
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(apply);
    };

    apply();
    vv.addEventListener("resize", schedule);
    vv.addEventListener("scroll", schedule);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      vv.removeEventListener("resize", schedule);
      vv.removeEventListener("scroll", schedule);
      document.documentElement.style.removeProperty("--kb-inset");
    };
  }, []);
}
