"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FileNode } from "@/lib/events";
import { cn, shortPath } from "@/lib/cn";
import { SPRING_SNAP, useMotionOK } from "./Anim";

/**
 * The command palette — ⌘K / Ctrl+K.
 *
 * One list, not a mode switcher: commands and files are matched by the same
 * query and ranked against each other, so "prev" finds Toggle preview and
 * "app.tsx" finds the file, without the user first having to declare which
 * kind of thing they are after.
 *
 * It is a floating surface like every other in the app — the same hairline,
 * blur and radius — hung from the top third rather than centred, because a
 * list that grows downward from a fixed input is easier to read than one that
 * grows in both directions around it.
 */

export type Command = {
  id: string;
  label: string;
  /** Groups the list. Shown as a heading above the first item in each group. */
  group: string;
  /** Right-aligned secondary text — the shortcut, or the model it switches to. */
  hint?: string;
  /** Extra words that should match, e.g. "sandbox" for the terminal toggle. */
  keywords?: string;
  /** Disabled commands stay listed: absence is harder to explain than greying. */
  disabled?: boolean;
  run: () => void;
};

export function CommandPalette({
  open,
  onClose,
  commands,
  files,
  treeRoot,
  onSelectFile,
}: {
  open: boolean;
  onClose: () => void;
  commands: Command[];
  /** The sandbox tree, flattened and searched alongside the commands. */
  files: FileNode[];
  treeRoot: string;
  onSelectFile: (path: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const motionOK = useMotionOK();

  const filePaths = useMemo(() => flatten(files), [files]);

  const results = useMemo(
    () => rank(query, commands, filePaths, treeRoot, onSelectFile),
    [query, commands, filePaths, treeRoot, onSelectFile],
  );

  // A fresh open is a fresh search. Resetting on close instead would show the
  // last query for one frame as the palette animates away.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setCursor(0);
    // The palette mounts inside an AnimatePresence subtree, so the input does
    // not exist until after this effect's first tick.
    const timer = setTimeout(() => input.current?.focus(), 20);
    return () => clearTimeout(timer);
  }, [open]);

  useEffect(() => setCursor(0), [query]);

  // Keep the highlighted row in view while arrowing through a long list.
  useEffect(() => {
    if (!open) return;
    listRef.current
      ?.querySelector('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor, open]);

  const choose = useCallback(
    (index: number) => {
      const item = results[index];
      if (!item || item.disabled) return;
      onClose();
      // After the close, so a command that opens another surface is not
      // immediately dismissed by this one's own teardown.
      requestAnimationFrame(item.run);
    },
    [results, onClose],
  );

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || (e.key === "n" && e.ctrlKey)) {
      e.preventDefault();
      setCursor((c) => (results.length ? (c + 1) % results.length : 0));
    } else if (e.key === "ArrowUp" || (e.key === "p" && e.ctrlKey)) {
      e.preventDefault();
      setCursor((c) => (results.length ? (c - 1 + results.length) % results.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(cursor);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="palette-scrim"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={motionOK ? { duration: 0.16 } : { duration: 0 }}
            onClick={onClose}
            className="fixed inset-0 z-[60] bg-black/55 backdrop-blur-[3px]"
          />
          <motion.div
            key="palette"
            role="dialog"
            aria-modal="true"
            aria-label="Command palette"
            // The horizontal centring rides in the motion props rather than as
            // a `-translate-x-1/2` class: framer-motion writes the whole
            // `transform` inline, so a Tailwind translate on the same element
            // is silently overwritten the moment the animation starts.
            initial={
              motionOK
                ? { opacity: 0, x: "-50%", y: -10, scale: 0.985 }
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
            onKeyDown={onKeyDown}
            className="glass fixed left-1/2 top-[14vh] z-[61] w-[min(38rem,calc(100vw-2rem))]
                       overflow-hidden rounded-panel"
          >
            <div className="flex items-center gap-2.5 border-b border-line px-4">
              <span className="sigil h-2 w-2 shrink-0 bg-accent" aria-hidden />
              <input
                ref={input}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Run a command, or open a file…"
                spellCheck={false}
                aria-label="Command or file"
                className="h-12 min-w-0 flex-1 bg-transparent font-sans text-[0.9375rem]
                           text-ink placeholder:text-ink-faint focus:outline-none"
              />
              <kbd className="chip shrink-0">esc</kbd>
            </div>

            <div ref={listRef} className="scroll-thin max-h-[min(24rem,50vh)] overflow-y-auto p-1.5">
              {results.length === 0 ? (
                <p className="px-3 py-6 text-center font-sans text-xs text-ink-faint">
                  Nothing matches “{query}”.
                </p>
              ) : (
                results.map((item, i) => (
                  <Row
                    key={item.id}
                    item={item}
                    active={i === cursor}
                    firstOfGroup={i === 0 || results[i - 1].group !== item.group}
                    onHover={() => setCursor(i)}
                    onClick={() => choose(i)}
                  />
                ))
              )}
            </div>

            <div className="flex items-center gap-3 border-t border-line px-4 py-2">
              <Legend keys="↑↓" label="navigate" />
              <Legend keys="⏎" label="run" />
              <Legend keys="esc" label="close" />
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

function Row({
  item,
  active,
  firstOfGroup,
  onHover,
  onClick,
}: {
  item: Command;
  active: boolean;
  firstOfGroup: boolean;
  onHover: () => void;
  onClick: () => void;
}) {
  return (
    <>
      {firstOfGroup && <p className="voice-label px-2.5 pb-1 pt-2.5">{item.group}</p>}
      <button
        type="button"
        data-active={active}
        onMouseMove={onHover}
        onClick={onClick}
        disabled={item.disabled}
        className={cn(
          "flex w-full items-center gap-3 rounded-ctl px-2.5 py-2 text-left transition-colors duration-150",
          item.disabled
            ? "cursor-not-allowed text-ink-dim"
            : active
              ? "bg-raised text-ink"
              : "text-ink-muted",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "sigil h-1.5 w-1.5 shrink-0 transition-colors",
            active && !item.disabled ? "bg-accent" : "bg-ink-dim",
          )}
        />
        <span
          className={cn(
            "min-w-0 flex-1 truncate",
            item.group === "Files"
              ? "voice-machine"
              : "font-sans text-[0.8125rem]",
          )}
        >
          {item.label}
        </span>
        {item.hint && (
          <span className="voice-machine shrink-0 text-2xs text-ink-faint">
            {item.hint}
          </span>
        )}
      </button>
    </>
  );
}

function Legend({ keys, label }: { keys: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <kbd className="chip px-1.5 py-0">{keys}</kbd>
      <span className="font-sans text-2xs text-ink-faint">{label}</span>
    </span>
  );
}

// --- matching --------------------------------------------------------------

function flatten(nodes: FileNode[], out: string[] = []): string[] {
  for (const node of nodes) {
    if (node.type === "file") out.push(node.path);
    else if (node.children) flatten(node.children, out);
  }
  return out;
}

/**
 * Subsequence match with a positional score.
 *
 * Full fuzzy matching ("apt" → `AppPreviewToggle`) is the wrong trade here: the
 * lists are short and a loose matcher mostly produces surprising hits. This
 * scores contiguity and word-boundary starts, which is what makes "prev" rank
 * *Toggle preview* above *Preview stopped* without any tuning.
 */
function score(query: string, text: string): number {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  if (!q) return 1;

  const direct = t.indexOf(q);
  if (direct === 0) return 1000;
  if (direct > 0) {
    // A match at a word boundary reads as intentional; mid-word is incidental.
    const boundary = direct === 0 || /[\s/._-]/.test(t[direct - 1]);
    return (boundary ? 700 : 400) - direct;
  }

  // Fall back to subsequence, penalising the gaps between matched characters.
  let ti = 0;
  let gaps = 0;
  for (const char of q) {
    const next = t.indexOf(char, ti);
    if (next === -1) return 0;
    gaps += next - ti;
    ti = next + 1;
  }
  return Math.max(1, 200 - gaps);
}

function rank(
  query: string,
  commands: Command[],
  filePaths: string[],
  treeRoot: string,
  onSelectFile: (path: string) => void,
): Command[] {
  const q = query.trim();

  const scored = commands
    .map((c) => ({
      item: c,
      score: Math.max(
        score(q, c.label),
        c.keywords ? score(q, c.keywords) * 0.9 : 0,
      ),
    }))
    .filter((r) => r.score > 0);

  // Files only join the list once there is something to match them against —
  // an unfiltered palette should show what you can *do*, not the file tree.
  const fileHits = q
    ? filePaths
        .map((path) => {
          const rel = shortPath(path, treeRoot);
          return { path, rel, score: score(q, rel) };
        })
        .filter((r) => r.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, 8)
        .map((r) => ({
          item: {
            id: `file:${r.path}`,
            label: r.rel,
            group: "Files",
            run: () => onSelectFile(r.path),
          } as Command,
          // Nudged below commands at equal strength: with an empty-ish query a
          // command is nearly always what was meant.
          score: r.score * 0.85,
        }))
    : [];

  // Groups stay contiguous, ordered by their strongest member, and items are
  // ordered by score inside each. Sorting the flat list by score alone would
  // interleave groups — and since a heading is drawn whenever the group
  // changes, "Preview / Project / Session / Preview / Project" is what the
  // user would actually read.
  const all = [...scored, ...fileHits];
  const best = new Map<string, number>();
  for (const r of all) {
    best.set(r.item.group, Math.max(best.get(r.item.group) ?? 0, r.score));
  }

  return all
    .sort(
      (a, b) =>
        (best.get(b.item.group) ?? 0) - (best.get(a.item.group) ?? 0) ||
        a.item.group.localeCompare(b.item.group) ||
        b.score - a.score,
    )
    .map((r) => r.item);
}
