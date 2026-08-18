"use client";

import { AnimatePresence, motion } from "framer-motion";
import type { LearningSource } from "@/lib/api";
import { cn } from "@/lib/cn";

/**
 * Attached Learning sources, shown above the composer. Multiple sources can be
 * attached to one session; each is removable. A source whose text could not be
 * extracted still shows — degraded, with its reason — rather than vanishing.
 */
export function SourceChips({
  sources,
  onRemove,
  readOnly = false,
  className,
}: {
  sources: LearningSource[];
  onRemove?: (id: string) => void;
  readOnly?: boolean;
  className?: string;
}) {
  if (sources.length === 0) return null;

  return (
    <div className={cn("flex flex-wrap gap-2", className)}>
      <AnimatePresence initial={false}>
        {sources.map((s) => (
          <motion.div
            key={s.id}
            layout
            initial={{ opacity: 0, y: 6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            transition={{ duration: 0.2, ease: [0.2, 0, 0, 1] }}
            className={cn(
              "flex max-w-[16rem] items-center gap-2.5 rounded-[12px] border bg-raised p-1.5 pr-2",
              s.error ? "border-warn/35" : "border-line",
            )}
          >
            <span className="relative grid h-[26px] w-9 shrink-0 place-items-center overflow-hidden rounded-md bg-inset">
              {s.kind === "youtube" && s.thumbnail ? (
                <img
                  src={s.thumbnail}
                  alt=""
                  className="h-full w-full object-cover"
                  onError={(e) => {
                    (e.currentTarget as HTMLImageElement).style.display = "none";
                  }}
                />
              ) : (
                <PdfGlyph />
              )}
            </span>

            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-2xs font-medium text-ink">
                {s.title}
              </span>
              <span
                className={cn(
                  "truncate text-[11px]",
                  s.error ? "text-warn" : "text-ink-faint",
                )}
              >
                {s.error
                  ? "no transcript"
                  : s.kind === "youtube"
                    ? `YouTube · ${fmtChars(s.chars)}`
                    : `PDF · ${fmtChars(s.chars)}`}
              </span>
            </span>

            {!readOnly && onRemove && (
              <button
                type="button"
                onClick={() => onRemove(s.id)}
                aria-label={`Remove ${s.title}`}
                className="grid h-[22px] w-[22px] shrink-0 place-items-center rounded-md text-ink-faint
                           transition-colors duration-200 hover:bg-white/[0.07] hover:text-ink"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function fmtChars(chars?: number): string {
  if (!chars) return "attached";
  if (chars < 1000) return `${chars} chars`;
  return `${(chars / 1000).toFixed(1)}k chars`;
}

function PdfGlyph() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="text-accent"
    >
      <path d="M6 3h7l5 5v13H6z" />
      <path d="M13 3v5h5" />
    </svg>
  );
}
