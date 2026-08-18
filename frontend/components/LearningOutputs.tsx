"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { Markdown } from "./Markdown";

/**
 * The two output modes a Learning turn offers once sources are attached.
 *
 * Both are prompts, not client-side transforms — clicking sends a follow-up
 * instruction to the same agent, so the content is really generated from the
 * ingested source rather than templated here.
 */

export const STUDY_NOTES_PROMPT =
  "Generate structured study notes from the attached source(s). Use markdown: " +
  "a title, then short uppercase section headers, bulleted points under each, " +
  "and wrap key terms in **bold**. Be specific to the source, not generic.";

export const SLIDES_PROMPT =
  "Generate a slide deck from the attached source(s). Output ONLY a markdown " +
  "list of slides in exactly this shape, 5 to 8 slides:\n\n" +
  "## Slide Title\n- bullet\n- bullet\n- bullet\n\n" +
  "Each slide gets a level-2 heading and 3 to 5 short bullets. No prose outside " +
  "the slides.";

export function OutputActions({
  onGenerate,
  disabled,
  used,
}: {
  onGenerate: (kind: "notes" | "slides") => void;
  disabled?: boolean;
  /** Which formats were already produced in this turn. */
  used: Set<"notes" | "slides">;
}) {
  const cards = [
    {
      kind: "notes" as const,
      title: "Generate Study Notes",
      sub: "Structured markdown · headers, bullets, key terms",
      icon: (
        <>
          <path d="M6 3h12v18H6z" />
          <path d="M9 8h6M9 12h6M9 16h3" />
        </>
      ),
    },
    {
      kind: "slides" as const,
      title: "Generate Slides",
      sub: "Deck view · title + 3–5 bullets per slide",
      icon: (
        <>
          <rect x="3" y="4" width="18" height="13" rx="2" />
          <path d="M8 21h8M12 17v4" />
        </>
      ),
    },
  ];

  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {cards.map((c) => (
        <button
          key={c.kind}
          type="button"
          onClick={() => onGenerate(c.kind)}
          disabled={disabled || used.has(c.kind)}
          className={cn(
            "flex items-center gap-3 rounded-card border border-line bg-elevated px-4 py-3 text-left",
            "transition-all duration-200 ease-out",
            "hover:border-accent-line hover:bg-raised active:scale-[0.99]",
            "disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-line disabled:hover:bg-elevated",
          )}
        >
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[9px] bg-accent-soft">
            <svg
              width="17"
              height="17"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="text-accent"
            >
              {c.icon}
            </svg>
          </span>
          <span className="flex flex-col leading-tight">
            <span className="text-sm font-medium text-ink">{c.title}</span>
            <span className="text-2xs text-ink-faint">{c.sub}</span>
          </span>
        </button>
      ))}
    </div>
  );
}

// --- slide deck ------------------------------------------------------------

type Slide = { title: string; bullets: string[] };

/** Parse the `## Title` + `- bullet` shape SLIDES_PROMPT asks for. */
export function parseSlides(markdown: string): Slide[] {
  const slides: Slide[] = [];
  let current: Slide | null = null;

  for (const rawLine of markdown.split("\n")) {
    const line = rawLine.trim();
    const heading = line.match(/^#{1,3}\s+(.*)$/);
    if (heading) {
      if (current) slides.push(current);
      current = { title: heading[1].replace(/[*_`]/g, "").trim(), bullets: [] };
      continue;
    }
    const bullet = line.match(/^[-*+]\s+(.*)$/);
    if (bullet && current) {
      current.bullets.push(bullet[1].replace(/^\*\*|\*\*$/g, "").trim());
    }
  }
  if (current) slides.push(current);
  return slides.filter((s) => s.title && s.bullets.length > 0);
}

export function SlideDeck({ markdown }: { markdown: string }) {
  const slides = useMemo(() => parseSlides(markdown), [markdown]);
  const [i, setI] = useState(0);

  // Streaming can grow the deck under us; keep the index in range.
  const index = Math.min(i, Math.max(0, slides.length - 1));

  if (slides.length === 0) {
    // Not parseable as a deck (yet) — show the raw markdown rather than nothing.
    return <Markdown source={markdown} />;
  }

  const slide = slides[index];

  return (
    <div className="mt-4 overflow-hidden rounded-card border border-line bg-elevated">
      <div className="relative aspect-[16/9] bg-inset">
        <AnimatePresence mode="wait" initial={false}>
          <motion.section
            key={index}
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -12 }}
            transition={{ duration: 0.25, ease: [0.2, 0, 0, 1] }}
            className="absolute inset-0 flex flex-col justify-center px-8 py-6"
          >
            <h3 className="mb-4 text-2xl font-semibold leading-tight tracking-tight text-ink">
              {slide.title}
              <span className="mt-3 block h-[3px] w-9 rounded-sm bg-accent" />
            </h3>
            <ul className="space-y-2.5 pl-5">
              {slide.bullets.map((b, n) => (
                <li
                  key={n}
                  className="list-disc text-base leading-relaxed text-ink-muted marker:text-accent"
                >
                  {b}
                </li>
              ))}
            </ul>
          </motion.section>
        </AnimatePresence>
      </div>

      <div className="flex items-center gap-2 border-t border-line px-3 py-2.5">
        <DeckNav
          dir="prev"
          disabled={index === 0}
          onClick={() => setI(Math.max(0, index - 1))}
        />
        <span className="text-2xs tabular-nums text-ink-faint">
          {index + 1} / {slides.length}
        </span>
        <span className="mx-auto flex gap-1.5">
          {slides.map((_, n) => (
            <button
              key={n}
              type="button"
              aria-label={`Slide ${n + 1}`}
              aria-current={n === index}
              onClick={() => setI(n)}
              className={cn(
                "h-1.5 w-1.5 rounded-full p-0 transition-all duration-200",
                n === index ? "scale-[1.35] bg-accent" : "bg-line-strong",
              )}
            />
          ))}
        </span>
        <DeckNav
          dir="next"
          disabled={index >= slides.length - 1}
          onClick={() => setI(Math.min(slides.length - 1, index + 1))}
        />
      </div>
    </div>
  );
}

function DeckNav({
  dir,
  disabled,
  onClick,
}: {
  dir: "prev" | "next";
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={dir === "prev" ? "Previous slide" : "Next slide"}
      className="grid h-7 w-7 place-items-center rounded-ctl text-ink-muted transition-colors
                 duration-200 hover:bg-raised hover:text-ink
                 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent"
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d={dir === "prev" ? "m15 6-6 6 6 6" : "m9 6 6 6-6 6"} />
      </svg>
    </button>
  );
}
