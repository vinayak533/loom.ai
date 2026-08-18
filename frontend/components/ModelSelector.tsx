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
   * printing the raw slug ("grok-4-5") instead of "Grok 4.5".
   */
  activeId?: string;
  onSelect: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

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

  // Group by provider, preserving the backend's registry order.
  const groups = useMemo(() => {
    const out: { group: string; items: ModelOption[] }[] = [];
    for (const m of models) {
      const bucket = out.find((g) => g.group === m.group);
      if (bucket) bucket.items.push(m);
      else out.push({ group: m.group, items: [m] });
    }
    return out;
  }, [models]);

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
        title={
          isAuto
            ? taskRouted
              ? resolved
                ? `Auto · currently on ${resolved.name}`
                : "Auto · picks a model for each turn"
              : `Auto · routed to ${resolved?.name ?? resolvedId}`
            : `${manualModel?.name ?? resolvedId} · ${manualModel?.group ?? ""}`
        }
        className={cn(
          "group flex h-[30px] max-w-[13rem] items-center gap-2 rounded-full pl-2 pr-2.5",
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
            style={{ transformOrigin: "bottom left" }}
            className="absolute bottom-full left-0 z-40 mb-2 w-[17.5rem] origin-bottom-left
                       rounded-[14px] border border-line bg-overlay p-2 shadow-lift"
          >
            <button
              type="button"
              role="option"
              aria-selected={isAuto}
              onClick={() => pick(null)}
              className="mb-1 flex w-full items-center gap-2.5 rounded-ctl border-b border-line
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

            {models.length === 0 && (
              <p className="px-2 py-2 text-2xs text-ink-faint">
                No models configured. Add API keys in backend/.env.
              </p>
            )}

            {groups.map(({ group, items }) => (
              <div key={group} className="mt-2 first:mt-1">
                <p className="px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.07em] text-ink-faint">
                  {group}
                </p>
                {items.map((m) => (
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
                      "flex w-full items-center gap-2.5 rounded-ctl px-2 py-[7px] text-left text-sm",
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
            ))}
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
