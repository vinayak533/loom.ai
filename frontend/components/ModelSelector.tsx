"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ModelOption } from "@/lib/api";
import { cn } from "@/lib/cn";

/**
 * Lives inside the composer, bottom-left, in every section. There is
 * deliberately no model control in the top bar.
 *
 * `currentId === null` means Auto. With `taskRouted` set, the backend picks a
 * model per turn from what the turn actually contains — an attached image, an
 * edit in flight, a long plan — so there is no fixed target to display and the
 * pill shows whatever the router last landed on (`activeName`). Without it,
 * Auto falls back to the older per-section table and resolves to
 * `autoTargetId`. A manual pick persists for that section until the user sets
 * it back to Auto.
 */
export function ModelSelector({
  models,
  currentId,
  autoTargetId,
  taskRouted = false,
  activeName,
  activeId,
  onSelect,
}: {
  models: ModelOption[];
  /** null = Auto mode */
  currentId: string | null;
  /** What Auto resolves to for the active section, in fallback mode. */
  autoTargetId: string;
  /** True when Auto routes per turn by task instead of by section. */
  taskRouted?: boolean;
  /** The model Auto is currently on, as announced by the backend. */
  activeName?: string;
  /**
   * Id the backend says is active. Paired with `activeName` so the pill can
   * name a model that is *registered but unavailable* — one whose key is not
   * set is filtered out of `models`, and without this the pill fell back to
   * printing the raw slug ("qwen3_7_plus") instead of "Qwen 3.7 Plus".
   */
  activeId?: string;
  onSelect: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  /**
   * How tall the panel may be, measured rather than assumed.
   *
   * A fixed `max-h` in rem is the same bug one viewport further along: this
   * menu is anchored to the *top* of a control that sits near the bottom of
   * the window, so the room it has is whatever is above that control, and only
   * the DOM knows what that is. Measured on open — the one moment it can
   * change without a resize — and again on resize.
   */
  const [roomAbove, setRoomAbove] = useState<number | null>(null);

  useEffect(() => {
    if (!open) return;
    const measure = () => {
      const trigger = root.current?.firstElementChild;
      if (!trigger) return;
      // 8px for the panel's own offset from the control, 12px so it never
      // reads as jammed against the top of the window.
      setRoomAbove(Math.max(160, trigger.getBoundingClientRect().top - 20));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const isAuto = currentId === null;
  const resolvedId = currentId ?? autoTargetId;
  const manualModel = models.find((m) => m.id === resolvedId);
  // In task-routed Auto the backend is the only thing that knows the current
  // model, so the live announcement wins over any client-side guess.
  const resolved =
    isAuto && taskRouted
      ? models.find((m) => m.name === activeName)
      : manualModel;
  const label = isAuto
    ? "Auto"
    : manualModel?.name ??
      // Only trust the announced name when it is describing this same model.
      (activeId === resolvedId && activeName ? activeName : resolvedId);

  /** The models Auto can choose between, for the dropdown's subtitle. */
  const autoPool = useMemo(
    () => models.filter((m) => m.routing_hint),
    [models],
  );

  // Deliberately *not* grouped by provider any more. Which gateway serves a
  // model is an implementation detail of this deployment; sorting the user's
  // choice by it asks them to care about something they cannot act on, and
  // pushed the list past the height of an ordinary window. One flat list, in
  // the registry's own order.

  const pick = (id: string | null) => {
    onSelect(id);
    setOpen(false); // closes on selection
  };

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        // The name and nothing else. Who serves the model is our plumbing, not
        // a fact about the choice being made — and the tooltip was the last
        // place the provider was still leaking through, including into the
        // control's accessible name.
        title={
          isAuto
            ? taskRouted
              ? resolved
                ? `Auto · currently on ${resolved.name}`
                : "Auto · picks a model for each turn"
              : `Auto · routed to ${resolved?.name ?? resolvedId}`
            : (manualModel?.name ?? resolvedId)
        }
        className={cn(
          "group flex h-[30px] max-w-[13rem] items-center gap-2 rounded-full pl-2 pr-2.5",
          "touch:h-11 touch:pl-3 touch:pr-3.5",
          "text-xs font-medium text-ink-muted transition-colors duration-200 ease-out",
          "hover:bg-raised hover:text-ink",
          open && "bg-raised text-ink",
        )}
      >
        <span
          className={cn(
            "auto-orb h-[7px] w-[7px] shrink-0 rounded-full",
            isAuto
              ? "animate-auto-breathe bg-accent shadow-[0_0_6px_rgb(var(--acc)/0.5)]"
              : "bg-ink-faint",
          )}
        />
        <span className="truncate">{label}</span>
        {/* Hover reveals what Auto actually routed to. */}
        {isAuto && resolved && (
          <span
            className="max-w-0 overflow-hidden whitespace-nowrap text-ink-faint opacity-0
                       transition-all duration-250 ease-out
                       group-hover:max-w-[9rem] group-hover:opacity-100"
          >
            · {resolved.name}
          </span>
        )}
        <motion.span
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.2 }}
          className="shrink-0 text-ink-faint"
          aria-hidden
        >
          <ChevronGlyph />
        </motion.span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="listbox"
            aria-label="Model"
            initial={{ opacity: 0, y: 6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.98 }}
            transition={{ duration: 0.2, ease: [0.2, 0, 0, 1] }}
            /**
             * Bounded, and split into a pinned head and a scrolling body.
             *
             * This panel used to be whatever height its contents wanted — 552px
             * with eight models — and it opens *upwards* from a composer that
             * sits near the bottom of the window. On any viewport shorter than
             * about 850px its top ran off the top of the screen, taking the
             * first row with it. That row is Auto. So the recommended option
             * was not missing from the menu, it was rendered 141px above the
             * top of the window with no way to scroll to it: present in the
             * DOM, unreachable with a mouse.
             *
             * Auto now sits outside the scroll area, so it is visible whatever
             * the window height and however many models the registry grows to.
             */
            className="absolute bottom-full left-0 z-40 mb-2 flex w-[17.5rem] max-w-[calc(100vw-2rem)]
                       origin-bottom-left flex-col overflow-hidden rounded-[14px] border border-line
                       bg-overlay p-2 shadow-lift"
            style={{
              transformOrigin: "bottom left",
              maxHeight: roomAbove ? `min(26rem, ${roomAbove}px)` : "26rem",
            }}
          >
            <button
              type="button"
              role="option"
              aria-selected={isAuto}
              onClick={() => pick(null)}
              className="mb-1 flex w-full shrink-0 items-center gap-2.5 rounded-ctl border-b border-line
                         px-2 pb-2.5 pt-1.5 text-left text-sm text-ink-muted
                         transition-colors duration-200 hover:bg-white/[0.055] hover:text-ink"
            >
              <span className="auto-orb h-[7px] w-[7px] shrink-0 animate-auto-breathe rounded-full bg-accent" />
              <span className="min-w-0 flex-1">
                <span className="block text-ink">
                  Auto {taskRouted && <span className="text-ink-faint">(recommended)</span>}
                </span>
                <span className="block truncate text-2xs text-ink-faint">
                  {taskRouted
                    ? resolved
                      ? `Best model per task · now on ${resolved.name}`
                      : `Best model per task · ${autoPool.length} models`
                    : `Routed by section → ${
                        models.find((m) => m.id === autoTargetId)?.name ?? autoTargetId
                      }`}
                </span>
              </span>
              {isAuto && <CheckGlyph />}
            </button>

            <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
              {models.length === 0 && (
                <p className="px-2 py-2 text-2xs text-ink-faint">
                  No models configured. Add API keys in backend/.env.
                </p>
              )}

              {models.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  role="option"
                  aria-selected={currentId === m.id}
                  onClick={() => pick(m.id)}
                  // The Auto pool's models say what they are for. They stay
                  // hand-pickable; Auto is just the better default.
                  title={m.description ?? undefined}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-ctl px-2 py-2 text-left text-sm",
                    "transition-colors duration-200",
                    currentId === m.id
                      ? "text-ink"
                      : "text-ink-muted hover:bg-white/[0.055] hover:text-ink",
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{m.name}</span>
                    {m.detail && (
                      <span className="block truncate text-2xs text-ink-faint">
                        {m.detail}
                      </span>
                    )}
                    {!m.supports_tools && (
                      <span className="block text-2xs text-warn">text only</span>
                    )}
                  </span>
                  {currentId === m.id && <CheckGlyph />}
                </button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ChevronGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function CheckGlyph() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0 text-accent"
    >
      <path d="m5 12 5 5L19 8" />
    </svg>
  );
}
