"use client";

import { useEffect, type RefObject } from "react";

/**
 * What `aria-modal="true"` promises, actually kept.
 *
 * Declaring a dialog modal tells assistive technology that the rest of the
 * page is inert. Six dialogs in this app said so and none of them made it
 * true: Tab walked straight out from under the scrim into the composer, and
 * on close focus landed on `<body>`, so the next Tab restarted at the top of
 * the app. This hook is the missing half of the contract.
 *
 * On open it remembers `document.activeElement`, focuses the first tabbable
 * child (or the container itself), and cycles Tab / Shift+Tab inside the
 * container. On close it hands focus back to whatever had it, which is what
 * makes a keyboard user's position survive opening Settings.
 *
 * `autoFocus: false` is for dialogs that already place focus deliberately
 * (the command palette focuses its input); the trap and the restore still
 * apply.
 */

const TABBABLE = [
  "a[href]",
  "button:not([disabled])",
  'input:not([disabled]):not([type="hidden"])',
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(", ");

function tabbables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(TABBABLE)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  { autoFocus = true }: { autoFocus?: boolean } = {},
) {
  useEffect(() => {
    if (!active) return;
    const root = ref.current;
    if (!root) return;

    const previous = document.activeElement as HTMLElement | null;

    if (!root.hasAttribute("tabindex")) root.setAttribute("tabindex", "-1");

    // The dialogs here mount through framer-motion, so their children are
    // laid out a frame after `open` flips. Focus after that frame, and only
    // if nothing inside has been focused by the dialog itself in between.
    let raf = 0;
    if (autoFocus) {
      raf = requestAnimationFrame(() => {
        if (root.contains(document.activeElement)) return;
        const first = tabbables(root)[0];
        (first ?? root).focus({ preventScroll: true });
      });
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const list = tabbables(root);
      if (list.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      const current = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (current === first || !root.contains(current)) {
          e.preventDefault();
          last.focus();
        }
      } else if (current === last || !root.contains(current)) {
        e.preventDefault();
        first.focus();
      }
    };

    // Focus that escapes by some route other than Tab (a click on the scrim's
    // edge, a programmatic focus) is pulled back in.
    const onFocusIn = (e: FocusEvent) => {
      if (root.contains(e.target as Node)) return;
      const first = tabbables(root)[0];
      (first ?? root).focus({ preventScroll: true });
    };

    document.addEventListener("keydown", onKey, true);
    document.addEventListener("focusin", onFocusIn);

    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("focusin", onFocusIn);
      // Restore unless focus has already been moved somewhere deliberate by
      // whatever closed the dialog.
      const now = document.activeElement;
      if (
        previous &&
        previous.isConnected &&
        (now === document.body || now === null || root.contains(now))
      ) {
        previous.focus({ preventScroll: true });
      }
    };
  }, [ref, active, autoFocus]);
}
