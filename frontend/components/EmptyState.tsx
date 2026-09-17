"use client";

import { motion } from "framer-motion";
import {
  ArrowUpRight,
  FileText,
  FolderOpen,
  Sparkles,
  TerminalSquare,
  Wand2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";
import type { Section } from "@/lib/sections";
import { SPRING_SOFT, useMotionOK } from "./Anim";

/**
 * What a section shows before anything has happened in it.
 *
 * The bar the Agents section set is worth naming, because it is the reason
 * this exists: each specialist opens with a sentence about what it is *for*
 * and three starters that are real work, not demonstrations. Chat and Code
 * opened with three generic chips — "Help me plan my week" — which is filler
 * dressed as guidance, and Learn's notebook tab opened on an empty grid that
 * never mentioned the ten seeded courses sitting one tab away.
 *
 * So the rule here: **every entry point must be something a new user would
 * actually want on their first visit**, and where a section has a real action
 * rather than a prompt (Code can open a folder; Learn has authored courses),
 * the action is offered as an action instead of being described in a chip that
 * only fills the composer.
 */

type Starter = {
  label: string;
  /** The one-line reason this is here, not a restatement of the label. */
  detail: string;
  /** Dropped into the composer. Omitted for entries that *do* something. */
  prompt?: string;
  /** Performed directly. Takes precedence over `prompt`. */
  action?: () => void;
  icon: typeof Sparkles;
};

export function SectionEmptyState({
  section,
  onUse,
  onOpenFolder,
  className,
}: {
  section: Section;
  /** Put this text in the composer, ready to send or edit. */
  onUse: (prompt: string) => void;
  /** Code only: import a real folder into the sandbox. */
  onOpenFolder?: () => void;
  className?: string;
}) {
  const motionOK = useMotionOK();
  const starters = startersFor(section, { onUse, onOpenFolder });
  // The command palette is the deepest keyboard surface in the app and the
  // only way to discover it was a small search-shaped button hidden below
  // `sm`. One line here, until the palette has been opened once.
  const [showPaletteHint, setShowPaletteHint] = useState(false);
  useEffect(() => {
    try {
      setShowPaletteHint(!localStorage.getItem(PALETTE_USED_KEY));
    } catch {
      /* storage unavailable: no hint, nothing lost */
    }
  }, []);
  if (!starters.length) return null;

  return (
    <div className={cn("grid gap-2 sm:grid-cols-2", className)}>
      {showPaletteHint && (
        <p className="font-sans text-2xs text-ink-faint sm:col-span-2">
          <kbd className="rounded-inner border border-line bg-raised px-1.5 py-0.5 font-mono text-2xs text-ink-muted">
            {isApple() ? "⌘K" : "Ctrl K"}
          </kbd>{" "}
          opens the command palette: switch models, open files, run anything here.
        </p>
      )}
      {starters.map((starter, i) => (
        <motion.button
          key={starter.label}
          type="button"
          initial={motionOK ? { opacity: 0, y: 6 } : false}
          animate={{ opacity: 1, y: 0 }}
          transition={
            motionOK ? { ...SPRING_SOFT, delay: 0.03 * i } : { duration: 0 }
          }
          onClick={() => {
            if (starter.action) starter.action();
            else if (starter.prompt) onUse(starter.prompt);
          }}
          className={cn(
            "group/starter flex items-start gap-3 rounded-card border border-line bg-elevated",
            "px-3.5 py-3 text-left transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200",
            "hover:border-accent-line hover:bg-raised active:scale-[0.99]",
          )}
        >
          <span
            className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-[8px]
                       bg-inset text-accent"
            aria-hidden
          >
            <starter.icon size={14} strokeWidth={1.8} />
          </span>
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="flex items-center gap-1 text-[0.8125rem] font-medium text-ink">
              {starter.label}
              {starter.action && (
                <ArrowUpRight
                  size={12}
                  strokeWidth={2}
                  className="shrink-0 text-ink-faint transition-colors
                             group-hover/starter:text-accent"
                  aria-hidden
                />
              )}
            </span>
            <span className="text-2xs leading-relaxed text-ink-faint">
              {starter.detail}
            </span>
          </span>
        </motion.button>
      ))}
    </div>
  );
}

/** Set by the palette the first time it opens; read here to retire the hint. */
export const PALETTE_USED_KEY = "loom.palette.used";

function isApple(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);
}

function startersFor(
  section: Section,
  handlers: { onUse: (p: string) => void; onOpenFolder?: () => void },
): Starter[] {
  if (section === "code") {
    return [
      // A real action first. Someone opening Code with a project already on
      // disk wants that project in the sandbox, and until now the only route
      // to it was a menu behind a "+" glyph.
      ...(handlers.onOpenFolder
        ? [
            {
              label: "Open a folder",
              detail:
                "Put an existing project into the sandbox and work on it in place.",
              action: handlers.onOpenFolder,
              icon: FolderOpen,
            },
          ]
        : []),
      {
        label: "Start a new project",
        detail: "Scaffolded, running, and previewable in one turn.",
        prompt:
          "Scaffold a small Next.js app with a home page and one API route, " +
          "install the dependencies, and start the dev server so I can see it.",
        icon: Sparkles,
      },
      {
        label: "Debug a stack trace",
        detail: "Paste the error; it reproduces it in the sandbox before fixing.",
        prompt:
          "Here is a stack trace I do not understand. Reproduce it in the " +
          "sandbox, explain the cause, then fix it:\n\n",
        icon: TerminalSquare,
      },
      {
        label: "Explain this codebase",
        detail: "Reads the tree first, then tells you where things actually live.",
        prompt:
          "Read the project in the sandbox and give me a tour: what it does, " +
          "how it is laid out, and where I would go to change the main behaviour.",
        icon: FileText,
      },
    ];
  }

  // Learn is deliberately absent. It never renders `ChatPanel` — it owns its
  // whole content area (see `ownsSurface` in app/page.tsx) — so a branch here
  // would be code that cannot run. Its first-run state lives where its first
  // run actually happens: `learn/NotebookLibrary`.

  return [
    {
      label: "Explain something properly",
      detail: "A real explanation, not a definition — with the part that trips people up.",
      prompt:
        "Explain how database indexes actually work, including when adding " +
        "one makes a query slower.",
      icon: Sparkles,
    },
    {
      label: "Work through a decision",
      detail: "Give it the constraints; it argues both sides and then picks one.",
      prompt:
        "I need to choose between Postgres and SQLite for a side project with " +
        "maybe 200 users. Walk me through the trade-offs and then tell me " +
        "which you would pick and why.",
      icon: Wand2,
    },
    {
      label: "Summarise a document",
      detail: "Attach a PDF with the + button, or paste the text straight in.",
      prompt:
        "Summarise the attached document: the argument it makes, the evidence " +
        "it gives, and anything it leaves out.",
      icon: FileText,
    },
    {
      label: "Rewrite something",
      detail: "Say who it is for and what it is doing, not just 'make it better'.",
      prompt:
        "Rewrite this so it reads like a person wrote it, keeping every fact " +
        "and cutting the throat-clearing:\n\n",
      icon: TerminalSquare,
    },
  ];
}
