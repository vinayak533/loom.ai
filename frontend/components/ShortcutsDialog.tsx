"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef } from "react";
import { cn } from "@/lib/cn";
import { SPRING_SOFT, useMotionOK } from "./Anim";
import { useFocusTrap } from "@/lib/useFocusTrap";
import type { Section } from "@/lib/sections";

/**
 * The keyboard reference.
 *
 * One rule governs the content: **every line here is a binding that actually
 * works.** A shortcuts sheet is a promise, and a sheet listing a key that does
 * nothing is worse than no sheet at all — it teaches something false and then
 * makes the user doubt the rest of it. So this list is derived from the two
 * places bindings are really registered (the global handler in `app/page.tsx`
 * and the composer's own `onKeyDown` in `ChatPanel`), and the `scope` field
 * says plainly where a binding does and does not apply, because several of
 * them are Chat/Code only.
 *
 * Deliberately static. There is no registry to enumerate and building one to
 * populate a help sheet would be a larger, more fragile thing than the sheet
 * itself; the cost of that decision is that this file has to be edited when a
 * binding is added, which is what the comment above the table says.
 */

type Shortcut = {
  keys: string[];
  label: string;
  /** Where it works. Omitted when it works everywhere. */
  scope?: string;
};

type Group = { title: string; items: Shortcut[] };

/** ⌘ on Apple hardware, Ctrl everywhere else. */
function isApple(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent);
}

/**
 * KEEP IN STEP WITH THE HANDLERS.
 *
 * Global bindings: the `useEffect` keydown handler in `app/page.tsx`.
 * Composer bindings: the `<textarea onKeyDown>` in `components/ChatPanel.tsx`.
 * Editor bindings: `MessageEditor` in `components/MessageActions.tsx`.
 */
function groups(mod: string, section: Section): Group[] {
  const conversational = section === "chat" || section === "code";
  return [
    {
      title: "Conversation",
      items: [
        { keys: ["⏎"], label: "Send message" },
        { keys: ["⇧", "⏎"], label: "New line" },
        {
          keys: ["Esc"],
          label: "Stop generating",
          scope: "while a reply is streaming",
        },
      ],
    },
    {
      title: "Messages",
      items: [
        {
          keys: ["⏎"],
          label: "Save and re-run an edited message",
          scope: "while editing",
        },
        { keys: ["Esc"], label: "Cancel an edit", scope: "while editing" },
      ],
    },
    {
      title: "Navigation",
      items: [
        { keys: [mod, "K"], label: "Command palette" },
        {
          keys: [mod, "B"],
          label: "Sessions and history search",
          scope: conversational ? undefined : "Chat and Code",
        },
        {
          keys: [mod, "⇧", "O"],
          label: "New session",
          scope: conversational ? undefined : "Chat and Code",
        },
        { keys: ["?"], label: "This panel" },
        { keys: ["Esc"], label: "Close the panel in front" },
      ],
    },
    {
      title: "Code",
      items: [
        { keys: [mod, "\\"], label: "Context column", scope: "Code" },
        { keys: [mod, "J"], label: "Terminal", scope: "Code" },
      ],
    },
  ];
}

export function ShortcutsDialog({
  open,
  onClose,
  section,
}: {
  open: boolean;
  onClose: () => void;
  section: Section;
}) {
  const motionOK = useMotionOK();
  const dialog = useRef<HTMLDivElement>(null);
  useFocusTrap(dialog, open);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      // Both close it, including the key that opened it — a reference sheet
      // you have to reach for the mouse to dismiss is a poor advertisement for
      // keyboard shortcuts.
      if (e.key === "Escape" || e.key === "?") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const mod = isApple() ? "⌘" : "Ctrl";

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="shortcuts"
          className="fixed inset-0 z-[70] grid place-items-center overflow-y-auto p-5"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: motionOK ? 0.16 : 0 }}
        >
          <button
            type="button"
            aria-label="Close keyboard shortcuts"
            onClick={onClose}
            className="absolute inset-0 bg-black/62 backdrop-blur-[2px]"
          />

          <motion.div
            ref={dialog}
            role="dialog"
            aria-modal="true"
            aria-label="Keyboard shortcuts"
            initial={motionOK ? { opacity: 0, y: 12, scale: 0.985 } : false}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={motionOK ? { opacity: 0, y: 8, scale: 0.99 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SOFT : { duration: 0 }}
            className="glass relative w-full max-w-[32rem] rounded-card"
          >
            <header className="flex h-bar items-center justify-between border-b border-line px-5">
              <h2 className="text-sm font-semibold tracking-[-0.005em] text-ink">
                Keyboard shortcuts
              </h2>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="grid h-8 w-8 place-items-center rounded-ctl text-ink-faint
                           transition-colors duration-200 hover:bg-raised hover:text-ink"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </header>

            <div className="max-h-[70vh] overflow-y-auto scroll-thin px-5 py-5">
              {groups(mod, section).map((group) => (
                <section key={group.title} className="mb-6 last:mb-0">
                  <h3 className="voice-label pb-2.5">{group.title}</h3>
                  <ul>
                    {group.items.map((item) => (
                      <li
                        key={`${group.title}-${item.label}`}
                        className="flex items-center justify-between gap-4 border-b
                                   border-line py-2.5 last:border-b-0 last:pb-0"
                      >
                        <span className="min-w-0 text-xs text-ink-muted">
                          {item.label}
                          {item.scope && (
                            <span className="ml-1.5 text-2xs text-ink-faint">
                              · {item.scope}
                            </span>
                          )}
                        </span>
                        <span className="flex shrink-0 items-center gap-1">
                          {item.keys.map((key, i) => (
                            <Key key={`${key}-${i}`}>{key}</Key>
                          ))}
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}

              <p className="pt-1 text-2xs leading-relaxed text-ink-faint">
                Bindings outside the composer only fire when no text field has
                focus — except Escape, which is how you leave one.
              </p>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Key({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      className={cn(
        "grid h-6 min-w-[1.5rem] place-items-center rounded-[5px] border border-line",
        "bg-raised px-1.5 font-sans text-[11px] font-medium text-ink",
      )}
    >
      {children}
    </kbd>
  );
}
