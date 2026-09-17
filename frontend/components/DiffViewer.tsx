"use client";

import { AnimatePresence, motion } from "framer-motion";
import { memo, useCallback, useMemo, useRef, useState } from "react";
import type { ChangedFile } from "@/lib/useAgentSocket";
import { cn, shortPath } from "@/lib/cn";
import { CodeEditor } from "./CodeEditor";

/**
 * Unified diff view for the file the agent most recently touched.
 *
 * Lines are parsed out of the server-produced unified diff so line numbers on
 * both sides stay honest. Rows stagger in on mount — fast enough to read as one
 * motion, slow enough to show which lines changed.
 */

type Row = {
  kind: "add" | "del" | "ctx" | "hunk";
  text: string;
  oldNo?: number;
  newNo?: number;
};

function parseDiff(diff: string): Row[] {
  const rows: Row[] = [];
  let oldNo = 0;
  let newNo = 0;

  for (const line of diff.split("\n")) {
    if (line.startsWith("---") || line.startsWith("+++")) continue;
    const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      rows.push({ kind: "hunk", text: line });
      continue;
    }
    if (line.startsWith("+")) {
      rows.push({ kind: "add", text: line.slice(1), newNo: newNo++ });
    } else if (line.startsWith("-")) {
      rows.push({ kind: "del", text: line.slice(1), oldNo: oldNo++ });
    } else if (line.startsWith(" ") || line === "") {
      rows.push({ kind: "ctx", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  return rows;
}

type Mode = "diff" | "file" | "edit";

/** Memoized — a diff can be hundreds of rows and none of them change while the
 *  agent is streaming its next sentence. */
export const DiffViewer = memo(function DiffViewer({
  file,
  root,
  onClose,
  onSave,
  uncommitted,
  onCommitFile,
}: {
  file: ChangedFile | null;
  root: string;
  /** Omitted when the host already owns dismissal — see `ContextColumn`. */
  onClose?: () => void;
  /**
   * Save a manual edit back to the sandbox. Absent means read-only, and the
   * Edit tab is not offered — an editor whose Save cannot land is worse than
   * no editor.
   */
  onSave?: (path: string, content: string) => Promise<void>;
  /**
   * Whether this file has changes git does not have yet. Drives the commit
   * affordance below — absent or false and no button is offered, because a
   * "Commit" that would do nothing is worse than no button.
   */
  uncommitted?: boolean;
  /** Open the History panel with this file in view, ready to commit. */
  onCommitFile?: (path: string) => void;
}) {
  /**
   * Which view opens first follows what there is to show. A file the agent
   * edited opens on its diff — that is the news. A file opened for reading has
   * no diff at all, and showing an empty one would be a panel reporting that
   * nothing happened to a file the user just asked to see.
   *
   * Keyed on the path so switching files re-derives it, while a mode the user
   * picked survives the agent re-writing the file they are already looking at.
   */
  const hasDiff = Boolean(file?.diff.trim());
  const [mode, setMode] = useState<Mode>(hasDiff ? "diff" : "file");
  const settledFor = useRef(file?.path);
  if (settledFor.current !== file?.path) {
    settledFor.current = file?.path;
    // Safe during render: this is the useState-derived-from-props pattern, and
    // it only fires on the render where the file identity actually changed.
    setMode(hasDiff ? "diff" : "file");
  }

  // A blank diff is checked before parsing: `"".split("\n")` yields one empty
  // string, which the parser reads as a single blank context line — so an
  // empty diff would otherwise render one ghost row with empty line numbers
  // instead of the "nothing changed" message.
  const rows = useMemo(() => (hasDiff ? parseDiff(file!.diff) : []), [hasDiff, file]);
  const stats = useMemo(() => {
    const added = rows.filter((r) => r.kind === "add").length;
    const removed = rows.filter((r) => r.kind === "del").length;
    return { added, removed };
  }, [rows]);

  const modes: Mode[] = onSave ? ["diff", "file", "edit"] : ["diff", "file"];
  const path = file?.path ?? "";
  const save = useCallback(
    (content: string) => onSave!(path, content),
    [onSave, path],
  );

  if (!file) return null;

  const contentLines = file.content.split("\n");

  return (
    <div className="flex h-full flex-col">
      {/* A stat strip, not a title bar. The host — the context column's face
          tabs — already names the file and owns dismissal, so repeating either
          here would be chrome for its own sake. */}
      <header className="flex h-bar-sub shrink-0 items-center gap-3 border-b border-line px-3.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span
            className={cn(
              "voice-label shrink-0",
              file.change === "created" ? "text-add" : "text-warn",
            )}
          >
            {file.change}
          </span>
          {stats.added > 0 && (
            <span className="voice-machine text-add">+{stats.added}</span>
          )}
          {stats.removed > 0 && (
            <span className="voice-machine text-del">−{stats.removed}</span>
          )}
          <span className="voice-machine truncate text-ink-faint" data-tip={file.path}>
            {shortPath(file.path, root)}
          </span>
        </div>

        <div className="flex shrink-0 items-center gap-1 rounded-ctl border border-line bg-elevated p-0.5">
          {modes.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              aria-pressed={mode === m}
              className={cn(
                "relative rounded-[5px] px-2 py-1 text-2xs capitalize transition-colors",
                mode === m ? "text-ink" : "text-ink-faint hover:text-ink-muted",
              )}
            >
              {mode === m && (
                <motion.span
                  layoutId="diff-mode"
                  className="absolute inset-0 rounded-[5px] bg-raised"
                  transition={{ type: "spring", stiffness: 500, damping: 40 }}
                />
              )}
              <span className="relative">{m}</span>
            </button>
          ))}
        </div>

        {/* The bridge to version control. It sits here rather than in the
            History panel alone because this is where you are when you decide a
            change is worth keeping — reviewing the diff *is* the decision. It
            hands off to History rather than committing inline: a commit needs a
            message, and a message box does not belong in a diff header. */}
        {uncommitted && onCommitFile && (
          <button
            type="button"
            onClick={() => onCommitFile(file.path)}
            data-tip="Commit this change"
            className="shrink-0 rounded-ctl border border-line px-2 py-1 text-2xs
                       text-ink-faint transition-colors duration-200
                       hover:border-accent/40 hover:text-accent"
          >
            Commit…
          </button>
        )}

        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close file panel"
            className="btn-ghost shrink-0 px-2 py-1 text-sm"
          >
            ✕
          </button>
        )}
      </header>

      {/* Edit lives outside the scroller: CodeMirror owns its own scrolling and
          its save bar has to stay pinned to the foot of the panel. */}
      {mode === "edit" && onSave ? (
        <motion.div
          key={`edit-${file.path}`}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.16 }}
          className="min-h-0 flex-1"
        >
          <CodeEditor path={file.path} value={file.content} onSave={save} />
        </motion.div>
      ) : (
        <div className="scroll-thin min-h-0 flex-1 overflow-auto">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={`${file.path}-${file.at}-${mode}`}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.16 }}
              className="min-w-max font-mono text-xs leading-[1.65]"
            >
              {mode === "diff" ? (
                rows.length === 0 ? (
                  <p className="p-4 font-sans text-xs text-ink-faint">
                    No textual diff — the file was written with identical content.
                  </p>
                ) : (
                  rows.map((r, i) => <DiffRow key={i} row={r} index={i} />)
                )
              ) : (
                contentLines.map((line, i) => (
                  <div key={i} className="flex hover:bg-elevated">
                    <span className="w-12 shrink-0 select-none px-2 text-right text-ink-faint">
                      {i + 1}
                    </span>
                    <span className="whitespace-pre px-2 text-ink/80">{line || " "}</span>
                  </div>
                ))
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      )}
    </div>
  );
});

function DiffRow({ row, index }: { row: Row; index: number }) {
  if (row.kind === "hunk") {
    return (
      <div className="my-1 bg-elevated px-3 py-1 text-2xs text-ink-faint">{row.text}</div>
    );
  }

  const tone =
    row.kind === "add"
      ? "bg-add-bg text-add"
      : row.kind === "del"
        ? "bg-del-bg text-del"
        : "text-ink/70";

  return (
    <motion.div
      initial={{ opacity: 0, x: -4 }}
      animate={{ opacity: 1, x: 0 }}
      // Stagger only the first ~40 rows; beyond that the delay would be felt
      // as lag rather than read as motion.
      transition={{ duration: 0.18, delay: Math.min(index, 40) * 0.008 }}
      className={cn("flex", tone)}
    >
      <span className="w-10 shrink-0 select-none px-1.5 text-right text-ink-faint">
        {row.oldNo ?? ""}
      </span>
      <span className="w-10 shrink-0 select-none px-1.5 text-right text-ink-faint">
        {row.newNo ?? ""}
      </span>
      <span className="w-4 shrink-0 select-none text-center">
        {row.kind === "add" ? "+" : row.kind === "del" ? "−" : ""}
      </span>
      <span className="whitespace-pre pr-4">{row.text || " "}</span>
    </motion.div>
  );
}
