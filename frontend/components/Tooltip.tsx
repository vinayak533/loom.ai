"use client";

import {
  cloneElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/cn";

/**
 * The tooltip primitive, replacing the native `title` attribute.
 *
 * `title` was the only surface left in the app that ignored the design
 * system: a grey OS bubble, a full second late, never on touch, and not
 * dismissable with Escape (WCAG 1.4.13). This is the same information on the
 * app's own terms: a `.glass` chip in the label voice, 400ms to open, and
 * 0ms between neighbours so a hand moving along the rail reads a label per
 * glyph rather than waiting on each one.
 *
 * It is suppressed on coarse pointers. There is no hover on a phone and a
 * tooltip that opens on tap sits under the finger that opened it; touch
 * surfaces carry visible labels instead (the mobile rail does).
 *
 * The child gets `aria-describedby` pointing at the bubble, so the label is
 * announced whether or not it is visible. The child should still carry its
 * own `aria-label` if it is icon-only: a tooltip describes, it does not name.
 */

const OPEN_DELAY = 400;
/** How long after one tooltip closes the next one opens instantly. */
const GROUP_GRACE = 300;

let lastClosedAt = 0;

/**
 * The delegated form of the same tooltip, for the forty-odd sites that used
 * to carry a native `title`.
 *
 * Mounted once at the app root, it watches the document for hover and focus
 * on any element with a `data-tip` attribute and shows the same themed bubble
 * the component form does, with the same delay, grouping, touch suppression
 * and Escape. Migrating a site is a one-word change (`title=` becomes
 * `data-tip=`), which is what made it possible to retire `title` everywhere
 * at once rather than wrapping each element by hand.
 *
 * `data-tip-side` picks the side (`top` by default).
 */
export function TooltipLayer() {
  const [tip, setTip] = useState<{ text: string; x: number; y: number; side: Side } | null>(null);
  const timer = useRef<number>(0);
  const anchor = useRef<Element | null>(null);
  const coarse = useRef(false);

  useEffect(() => {
    const mql = window.matchMedia("(pointer: coarse)");
    coarse.current = mql.matches;
    const onChange = (e: MediaQueryListEvent) => (coarse.current = e.matches);
    mql.addEventListener("change", onChange);

    const hide = () => {
      window.clearTimeout(timer.current);
      if (anchor.current) {
        anchor.current.removeAttribute("aria-describedby");
        anchor.current = null;
        lastClosedAt = Date.now();
      }
      setTip(null);
    };

    const show = (el: Element) => {
      const text = el.getAttribute("data-tip");
      if (!text || coarse.current) return;
      window.clearTimeout(timer.current);
      const delay = Date.now() - lastClosedAt < GROUP_GRACE ? 0 : OPEN_DELAY;
      timer.current = window.setTimeout(() => {
        if (!el.isConnected) return;
        const r = el.getBoundingClientRect();
        const side = (el.getAttribute("data-tip-side") as Side | null) ?? "top";
        const gap = 8;
        const x =
          side === "left" ? r.left - gap : side === "right" ? r.right + gap : r.left + r.width / 2;
        const y =
          side === "top" ? r.top - gap : side === "bottom" ? r.bottom + gap : r.top + r.height / 2;
        anchor.current = el;
        el.setAttribute("aria-describedby", "loom-tip");
        setTip({ text, x, y, side });
      }, delay);
    };

    const onOver = (e: Event) => {
      const el = (e.target as Element | null)?.closest?.("[data-tip]");
      if (!el) return;
      if (el === anchor.current) return;
      show(el);
    };
    const onOut = (e: Event) => {
      const el = (e.target as Element | null)?.closest?.("[data-tip]");
      if (!el) return;
      const to = (e as MouseEvent).relatedTarget as Node | null;
      if (to && el.contains(to)) return;
      hide();
    };
    const onFocusIn = (e: Event) => {
      const el = (e.target as Element | null)?.closest?.("[data-tip]");
      if (el) show(el);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && hide();

    document.addEventListener("mouseover", onOver);
    document.addEventListener("mouseout", onOut);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("focusout", hide);
    document.addEventListener("pointerdown", hide, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      mql.removeEventListener("change", onChange);
      document.removeEventListener("mouseover", onOver);
      document.removeEventListener("mouseout", onOut);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("pointerdown", hide, true);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
      window.clearTimeout(timer.current);
    };
  }, []);

  if (!tip) return null;
  const transform =
    tip.side === "top"
      ? "translate(-50%, -100%)"
      : tip.side === "bottom"
        ? "translate(-50%, 0)"
        : tip.side === "left"
          ? "translate(-100%, -50%)"
          : "translate(0, -50%)";

  return createPortal(
    <span
      id="loom-tip"
      role="tooltip"
      style={{ left: tip.x, top: tip.y, transform }}
      className="glass pointer-events-none fixed z-[90] max-w-[18rem] whitespace-pre-line rounded-inner
                 px-2 py-1 font-sans text-2xs leading-snug text-ink-muted"
    >
      {tip.text}
    </span>,
    document.body,
  );
}

type Side = "top" | "bottom" | "left" | "right";

type AnchorProps = {
  onMouseEnter?: (e: React.MouseEvent) => void;
  onMouseLeave?: (e: React.MouseEvent) => void;
  onFocus?: (e: React.FocusEvent) => void;
  onBlur?: (e: React.FocusEvent) => void;
  onPointerDown?: (e: React.PointerEvent) => void;
};

export function Tooltip({
  label,
  side = "top",
  children,
  className,
}: {
  label: ReactNode;
  side?: Side;
  /** One focusable element. It receives the hover/focus handlers. */
  children: ReactElement<AnchorProps>;
  className?: string;
}) {
  const id = useId();
  const anchor = useRef<HTMLElement | null>(null);
  const timer = useRef<number>(0);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [coarse, setCoarse] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia("(pointer: coarse)");
    setCoarse(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setCoarse(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  const place = useCallback(() => {
    const el = anchor.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 8;
    const x =
      side === "left" ? r.left - gap : side === "right" ? r.right + gap : r.left + r.width / 2;
    const y =
      side === "top" ? r.top - gap : side === "bottom" ? r.bottom + gap : r.top + r.height / 2;
    setPos({ x, y });
  }, [side]);

  const show = useCallback(() => {
    if (coarse) return;
    window.clearTimeout(timer.current);
    const delay = Date.now() - lastClosedAt < GROUP_GRACE ? 0 : OPEN_DELAY;
    timer.current = window.setTimeout(() => {
      place();
      setOpen(true);
    }, delay);
  }, [coarse, place]);

  const hide = useCallback(() => {
    window.clearTimeout(timer.current);
    setOpen((was) => {
      if (was) lastClosedAt = Date.now();
      return false;
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && hide();
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [open, hide]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const props = children.props;
  const child = cloneElement(children, {
    // A callback ref composes with whatever ref the child already carries.
    ref: (node: HTMLElement | null) => {
      anchor.current = node;
      const prev = (children as unknown as { ref?: unknown }).ref;
      if (typeof prev === "function") prev(node);
      else if (prev && typeof prev === "object") {
        (prev as { current: unknown }).current = node;
      }
    },
    "aria-describedby": open ? id : undefined,
    onMouseEnter: (e: React.MouseEvent) => {
      props.onMouseEnter?.(e);
      show();
    },
    onMouseLeave: (e: React.MouseEvent) => {
      props.onMouseLeave?.(e);
      hide();
    },
    onFocus: (e: React.FocusEvent) => {
      props.onFocus?.(e);
      show();
    },
    onBlur: (e: React.FocusEvent) => {
      props.onBlur?.(e);
      hide();
    },
    onPointerDown: (e: React.PointerEvent) => {
      props.onPointerDown?.(e);
      hide();
    },
  } as Partial<AnchorProps> & { ref: (node: HTMLElement | null) => void });

  const transform =
    side === "top"
      ? "translate(-50%, -100%)"
      : side === "bottom"
        ? "translate(-50%, 0)"
        : side === "left"
          ? "translate(-100%, -50%)"
          : "translate(0, -50%)";

  return (
    <>
      {child}
      {open &&
        pos &&
        typeof document !== "undefined" &&
        createPortal(
          <span
            id={id}
            role="tooltip"
            style={{ left: pos.x, top: pos.y, transform }}
            className={cn(
              "glass pointer-events-none fixed z-[90] max-w-[18rem] whitespace-pre-line rounded-inner",
              "px-2 py-1 font-sans text-2xs leading-snug text-ink-muted",
              className,
            )}
          >
            {label}
          </span>,
          document.body,
        )}
    </>
  );
}
