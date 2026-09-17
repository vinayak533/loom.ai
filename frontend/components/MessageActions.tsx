"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Pencil,
  RefreshCw,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import type { BranchGroup } from "@/lib/events";
import { SPRING_SNAP, useMotionOK } from "./Anim";

/**
 * The controls that live under a message: copy, edit, regenerate, and the
 * branch switcher.
 *
 * Two rules shape the whole file.
 *
 * **They appear on hover and are otherwise invisible.** A transcript is
 * something you read; a row of buttons under every paragraph turns it into a
 * form. They are always in the DOM and always focusable, though — hiding with
 * `opacity` rather than unmounting is what keeps them reachable by keyboard and
 * to a screen reader, which a hover-only affordance otherwise quietly excludes.
 *
 * **They never occupy layout.** The row is absolutely positioned into the gap
 * the trace already leaves below a bubble, so the conversation does not shift
 * when the pointer moves across it — a thread that twitches as you read it is
 * worse than one with no controls at all.
 */

const ROW = cn(
  "flex items-center gap-0.5",
  // Present but invisible: focus inside the row reveals it, which is what
  // makes these reachable without a pointer.
  "opacity-0 transition-opacity duration-150",
  "group-hover/msg:opacity-100 focus-within:opacity-100",
  // Touch has no hover, so on those devices the controls simply stay visible.
  "touch:opacity-100",
);

function ActionButton({
  label,
  onClick,
  disabled,
  children,
  active,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      data-tip={label}
      className={cn(
        "grid h-7 w-7 place-items-center rounded-md transition-colors duration-150",
        "touch:h-9 touch:w-9",
        active ? "text-accent" : "text-ink-faint hover:bg-raised hover:text-ink",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent",
      )}
    >
      {children}
    </button>
  );
}

/**
 * Copy to clipboard, with the confirmation the action needs to be believable.
 *
 * `navigator.clipboard` is unavailable on an insecure origin and can be denied
 * outright, and a copy button that silently does nothing is a bug report. The
 * textarea path is the fallback that works everywhere, and the tick is the
 * evidence that either of them worked.
 */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      // Secure-context or permissions failure. The old execCommand path is
      // deprecated but still the only thing that works here.
      try {
        const scratch = document.createElement("textarea");
        scratch.value = text;
        scratch.setAttribute("readonly", "");
        scratch.style.position = "fixed";
        scratch.style.opacity = "0";
        document.body.appendChild(scratch);
        scratch.select();
        ok = document.execCommand("copy");
        document.body.removeChild(scratch);
      } catch {
        ok = false;
      }
    }
    if (!ok) return;
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1600);
  };

  return (
    <ActionButton
      label={copied ? "Copied" : label}
      onClick={() => void copy()}
      active={copied}
    >
      {copied ? (
        <Check size={14} strokeWidth={2.2} aria-hidden />
      ) : (
        <Copy size={14} strokeWidth={1.9} aria-hidden />
      )}
    </ActionButton>
  );
}

/**
 * `‹ 1/2 ›` — which version of an edited message you are looking at.
 *
 * Shown only on turns that actually have alternates. A switcher reading "1/1"
 * on every message would be noise, and would also imply an edit history that
 * does not exist.
 */
export function BranchSwitcher({
  group,
  onSwitch,
  disabled,
}: {
  group: BranchGroup;
  onSwitch: (version: number) => void;
  disabled?: boolean;
}) {
  const versions = group.versions.map((v) => v.version);
  const at = Math.max(0, versions.indexOf(group.active));
  const total = versions.length;
  if (total < 2) return null;

  return (
    <span className="flex items-center gap-0.5 text-ink-faint">
      <button
        type="button"
        onClick={() => onSwitch(versions[at - 1])}
        disabled={disabled || at === 0}
        aria-label="Previous version of this message"
        className="grid h-6 w-5 place-items-center rounded transition-colors
                   hover:text-ink disabled:opacity-30 disabled:hover:text-ink-faint
                   touch:h-8 touch:w-7"
      >
        <ChevronLeft size={13} strokeWidth={2.2} aria-hidden />
      </button>
      <span
        className="select-none font-sans text-[11px] tabular-nums"
        aria-live="polite"
      >
        {at + 1}/{total}
      </span>
      <button
        type="button"
        onClick={() => onSwitch(versions[at + 1])}
        disabled={disabled || at === total - 1}
        aria-label="Next version of this message"
        className="grid h-6 w-5 place-items-center rounded transition-colors
                   hover:text-ink disabled:opacity-30 disabled:hover:text-ink-faint
                   touch:h-8 touch:w-7"
      >
        <ChevronRight size={13} strokeWidth={2.2} aria-hidden />
      </button>
    </span>
  );
}

/** The row under a user message: branch switcher, copy, edit. */
export function UserActions({
  text,
  branch,
  busy,
  onEdit,
  onSwitch,
}: {
  text: string;
  branch?: BranchGroup;
  busy: boolean;
  onEdit: () => void;
  onSwitch: (version: number) => void;
}) {
  return (
    <div className={cn(ROW, "justify-end")}>
      {branch && (
        <BranchSwitcher group={branch} onSwitch={onSwitch} disabled={busy} />
      )}
      <CopyButton text={text} label="Copy message" />
      <ActionButton
        label={busy ? "Wait for the current reply to finish" : "Edit message"}
        onClick={onEdit}
        disabled={busy}
      >
        <Pencil size={14} strokeWidth={1.9} aria-hidden />
      </ActionButton>
    </div>
  );
}

/**
 * The row under an assistant message: copy, and — on the most recent reply
 * only — regenerate.
 *
 * Regenerate is deliberately last-turn-only. Re-running an *earlier* answer is
 * the same operation as editing the message above it and not changing the
 * text, so offering it separately on every reply would be a second door onto
 * one room, with the added cost that it silently discards everything that came
 * after.
 */
export function AssistantActions({
  text,
  canRegenerate,
  busy,
  onRegenerate,
  extra,
}: {
  text: string;
  canRegenerate: boolean;
  busy: boolean;
  onRegenerate: () => void;
  /** Feedback controls, added in a later pass. Rendered inside the same row. */
  extra?: React.ReactNode;
}) {
  return (
    <div className={cn(ROW, "-ml-1.5")}>
      <CopyButton text={text} label="Copy reply" />
      {canRegenerate && (
        <ActionButton
          label={busy ? "Wait for the current reply to finish" : "Regenerate"}
          onClick={onRegenerate}
          disabled={busy}
        >
          <RefreshCw size={14} strokeWidth={1.9} aria-hidden />
        </ActionButton>
      )}
      {extra}
    </div>
  );
}

/**
 * A user message, opened for editing in place.
 *
 * In place rather than in a dialog because the edit is a re-reading of what is
 * already on screen: the surrounding conversation is the context for the change
 * and taking it away makes the decision harder. Enter saves, Escape cancels,
 * which is the pairing every inline editor uses and the one people try first.
 */
export function MessageEditor({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (text: string) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const area = useRef<HTMLTextAreaElement>(null);
  const motionOK = useMotionOK();

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.focus();
    // Caret at the end rather than selecting everything: the usual edit is a
    // change to part of a sentence, and a full selection means the first
    // keystroke destroys the message you were trying to adjust.
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 48), 320)}px`;
  }, [draft]);

  const save = () => {
    const text = draft.trim();
    if (!text) return;
    onSave(text);
  };

  return (
    <motion.div
      initial={motionOK ? { opacity: 0, y: -3 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={motionOK ? SPRING_SNAP : { duration: 0 }}
      className="w-full min-w-[min(34rem,80vw)] rounded-bubble border border-line-focus
                 bg-raised px-3.5 py-3 shadow-e1"
    >
      <textarea
        ref={area}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            save();
          } else if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
        rows={1}
        aria-label="Edit your message"
        className="scroll-thin block w-full resize-none bg-transparent font-sans
                   text-[0.9375rem] leading-relaxed text-ink
                   focus:outline-none focus-visible:shadow-none"
      />
      <div className="mt-2.5 flex items-center justify-end gap-2">
        <span className="mr-auto hidden select-none font-sans text-2xs text-ink-faint sm:inline">
          Sending this re-runs the conversation from here. The original is kept.
        </span>
        <button
          type="button"
          onClick={onCancel}
          className="h-8 rounded-ctl px-3 text-xs text-ink-muted transition-colors
                     duration-200 hover:bg-elevated hover:text-ink"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={save}
          disabled={!draft.trim()}
          className={cn(
            "h-8 rounded-ctl px-3 text-xs font-medium transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200",
            draft.trim()
              ? "bg-gradient-to-br from-accent to-accent-alt text-accent-ink hover:brightness-110"
              : "cursor-not-allowed bg-raised text-ink-subtle opacity-55",
          )}
        >
          Save & submit
        </button>
      </div>
    </motion.div>
  );
}

/**
 * Thumbs up / down on a reply.
 *
 * Deliberately inert as a product feature: nothing reads this back, no model
 * is retrained, no answer changes. It is a record that somebody said a reply
 * was good or bad, kept so the question "which model and which route produce
 * answers people actually like" can one day be asked against real data instead
 * of reconstructed from nothing.
 *
 * Which makes the interface obligation the interesting part. A control that
 * does nothing visible must at least be *honest* and must not cost the reader
 * anything: it lives in the same hover-revealed row as Copy, it takes no
 * layout, and pressing the thumb you already chose withdraws the verdict
 * rather than casting a second one - because "I changed my mind" is a thing
 * people do, and a one-way switch quietly makes the data worse.
 */
export function FeedbackButtons({
  rating,
  busy,
  onRate,
}: {
  rating: "up" | "down" | null;
  busy?: boolean;
  onRate: (rating: "up" | "down" | null) => void;
}) {
  const press = (next: "up" | "down") => onRate(rating === next ? null : next);

  return (
    <>
      <ActionButton
        label={rating === "up" ? "Remove your rating" : "Good response"}
        onClick={() => press("up")}
        disabled={busy}
        active={rating === "up"}
      >
        <ThumbsUp
          size={14}
          strokeWidth={1.9}
          fill={rating === "up" ? "currentColor" : "none"}
          aria-hidden
        />
      </ActionButton>
      <ActionButton
        label={rating === "down" ? "Remove your rating" : "Bad response"}
        onClick={() => press("down")}
        disabled={busy}
        active={rating === "down"}
      >
        <ThumbsDown
          size={14}
          strokeWidth={1.9}
          fill={rating === "down" ? "currentColor" : "none"}
          aria-hidden
        />
      </ActionButton>
    </>
  );
}

/** Re-exported so callers do not have to reach into framer-motion. */
export { AnimatePresence };
