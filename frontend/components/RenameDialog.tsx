"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { SPRING_SNAP, useMotionOK } from "./Anim";

/**
 * Name and describe a session as a *project*.
 *
 * A modal here rather than an inline edit on the row: this asks for two fields,
 * and one of them is a sentence — a row in a 272px flyout is not somewhere to
 * write a sentence.
 *
 * The generate button is the LLM half of the feature and it fills the form
 * rather than committing: a generated name is a suggestion, and the user still
 * presses Save. That also means a name they dislike costs one keystroke to fix
 * instead of a second round trip to undo.
 */
export function RenameDialog({
  open,
  title,
  description,
  busy,
  generating,
  error,
  onGenerate,
  onSave,
  onClose,
}: {
  open: boolean;
  title: string;
  description: string;
  busy?: boolean;
  generating?: boolean;
  error?: string | null;
  /** Resolves with the generated pair, which is written into the fields. */
  onGenerate?: () => void;
  onSave: (title: string, description: string) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(title);
  const [about, setAbout] = useState(description);
  const input = useRef<HTMLInputElement>(null);
  const motionOK = useMotionOK();

  // Re-seed whenever the dialog opens, and whenever a generate lands while it
  // is open — the parent owns the canonical values, this only edits them.
  useEffect(() => {
    if (!open) return;
    setName(title);
    setAbout(description);
  }, [open, title, description]);

  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => input.current?.select(), 20);
    return () => clearTimeout(timer);
  }, [open]);

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    onSave(trimmed, about.trim());
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="rename-scrim"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={motionOK ? { duration: 0.16 } : { duration: 0 }}
            onClick={onClose}
            className="fixed inset-0 z-[60] bg-black/55 backdrop-blur-[3px]"
          />
          <motion.div
            key="rename"
            role="dialog"
            aria-modal="true"
            aria-label="Name this project"
            // `x: "-50%"` rather than a `-translate-x-1/2` class: framer-motion
            // owns `transform` on an animated element, and a Tailwind translate
            // on the same node is overwritten as soon as the animation runs.
            initial={
              motionOK
                ? { opacity: 0, x: "-50%", y: -8, scale: 0.985 }
                : { opacity: 0, x: "-50%" }
            }
            animate={{ opacity: 1, x: "-50%", y: 0, scale: 1 }}
            exit={
              motionOK
                ? {
                    opacity: 0,
                    x: "-50%",
                    y: -6,
                    scale: 0.99,
                    transition: { duration: 0.14 },
                  }
                : { opacity: 0, x: "-50%" }
            }
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                onClose();
              }
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                submit();
              }
            }}
            className="glass fixed left-1/2 top-[18vh] z-[61] w-[min(30rem,calc(100vw-2rem))]
                       overflow-hidden rounded-panel"
          >
            <header className="flex items-center gap-2.5 border-b border-line px-4 py-3">
              <span className="sigil h-2 w-2 bg-accent" aria-hidden />
              <h2 className="voice-label text-ink-muted">Name this project</h2>
              {onGenerate && (
                <button
                  type="button"
                  onClick={onGenerate}
                  disabled={generating}
                  // The section accent, not `accent-rare`. The rare register is
                  // reserved for the auto-router and the notices it emits;
                  // spending it on an unrelated control is what would make it
                  // stop reading as meaningful everywhere else.
                  className={cn(
                    "ml-auto flex items-center gap-1.5 rounded-ctl px-2 py-1 text-2xs",
                    "border border-accent-line bg-accent/[0.06] text-accent",
                    "transition-all duration-200 hover:bg-accent/[0.11]",
                    "disabled:pointer-events-none disabled:opacity-55",
                  )}
                >
                  <motion.span
                    aria-hidden
                    className="sigil h-1.5 w-1.5 bg-accent"
                    animate={
                      generating && motionOK
                        ? { opacity: [0.35, 1, 0.35] }
                        : { opacity: 1 }
                    }
                    transition={
                      generating && motionOK
                        ? { duration: 1.4, repeat: Infinity, ease: "easeInOut" }
                        : { duration: 0 }
                    }
                  />
                  {generating ? "Reading the session…" : "Generate"}
                </button>
              )}
            </header>

            <div className="space-y-3 px-4 py-4">
              <Field label="Name">
                <input
                  ref={input}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={120}
                  placeholder="Recipe Finder"
                  className="h-9 w-full rounded-ctl border border-line bg-inset px-3
                             font-sans text-[0.875rem] text-ink placeholder:text-ink-faint
                             transition-colors focus:border-line-focus focus:outline-none"
                />
              </Field>

              <Field label="Description" hint="optional">
                <textarea
                  value={about}
                  onChange={(e) => setAbout(e.target.value)}
                  maxLength={200}
                  rows={2}
                  placeholder="What it is, and what it is built with."
                  className="scroll-thin w-full resize-none rounded-ctl border border-line bg-inset
                             px-3 py-2 font-sans text-[0.8125rem] leading-relaxed text-ink
                             placeholder:text-ink-faint transition-colors
                             focus:border-line-focus focus:outline-none"
                />
              </Field>

              {error && <p className="text-2xs leading-relaxed text-del">{error}</p>}
            </div>

            <footer className="flex items-center gap-2 border-t border-line px-4 py-2.5">
              <span className="hidden font-sans text-2xs text-ink-faint sm:inline">
                ⌘⏎ save · esc cancel
              </span>
              <button
                type="button"
                onClick={onClose}
                className="btn-ghost ml-auto text-xs"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={busy || !name.trim()}
                className="btn-primary text-xs"
              >
                {busy ? "Saving…" : "Save"}
              </button>
            </footer>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="voice-label mb-1.5 flex items-baseline gap-2">
        {label}
        {hint && <span className="text-ink-dim">{hint}</span>}
      </span>
      {children}
    </label>
  );
}
