"use client";

import { coverHues, type SourceType } from "@/lib/learn";
import { cn } from "@/lib/cn";

/**
 * A notebook's face.
 *
 * Drawn, not fetched. Two hues derived from the notebook's seed make a soft
 * gradient, and the app's rhombus is stamped over it at low contrast so a wall
 * of covers still reads as one product rather than as a colour swatch chart.
 * The section's own accent stays on top of it as the badge, which is what ties
 * a Learn card to the Learn accent instead of to whatever hue it drew.
 */
export function NotebookCover({
  seed,
  id,
  className,
  compact,
}: {
  seed: string | null;
  id: string;
  className?: string;
  compact?: boolean;
}) {
  const [a, b] = coverHues(seed, id);
  return (
    <div
      aria-hidden
      className={cn("relative overflow-hidden", className)}
      style={{
        // Low saturation and low lightness: these sit on a near-black floor
        // and must not out-shout the section accent or the title above them.
        // A wall of covers is decoration, and decoration that competes with
        // the amber accent for attention makes the accent stop meaning
        // anything.
        backgroundImage: `linear-gradient(140deg,
          hsl(${a} 32% 22%) 0%,
          hsl(${a} 24% 13%) 46%,
          hsl(${b} 30% 17%) 100%)`,
      }}
    >
      <span
        className="sigil absolute bg-white/[0.07]"
        style={
          compact
            ? { width: "58%", height: "58%", right: "-14%", bottom: "-18%" }
            : { width: "52%", height: "76%", right: "-10%", bottom: "-26%" }
        }
      />
      <span
        className="sigil absolute bg-white/[0.05]"
        style={
          compact
            ? { width: "30%", height: "30%", left: "10%", top: "14%" }
            : { width: "26%", height: "38%", left: "9%", top: "13%" }
        }
      />
      {/* A floor-ward wash so the metadata that sits under the cover reads as
          attached to it rather than as a separate strip. */}
      <span className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-black/45 to-transparent" />
    </div>
  );
}

export function SourceIcon({ type }: { type: SourceType }) {
  if (type === "pdf") {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M6 3h7l5 5v13H6z" />
        <path d="M13 3v5h5" />
        <path d="M9 13h6M9 16.5h4" />
      </svg>
    );
  }
  if (type === "url") {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M10.5 13.5a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-1.5 1.5" />
        <path d="M13.5 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.5 3.5 0 0 0 5 5L12 17" />
      </svg>
    );
  }
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M5 5h14M5 9.5h14M5 14h10M5 18.5h7" />
    </svg>
  );
}

/** The notebook glyph that badges every cover. */
export function NotebookGlyph({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v14H6.5A2.5 2.5 0 0 0 4 19.5v-14Z" />
      <path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H19" />
      <path d="M8.5 7.5h6" />
    </svg>
  );
}
