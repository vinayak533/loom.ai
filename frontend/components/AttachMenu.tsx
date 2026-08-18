"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { importWorkspaceFiles } from "@/lib/api";
import {
  batchFiles,
  describeSkips,
  directoryPickerSupported,
  pickDirectory,
  pickFromInput,
  type FolderPick,
} from "@/lib/folder";
import { cn } from "@/lib/cn";
import { SPRING_SNAP, useMotionOK } from "./Anim";

/**
 * The composer's "+" — one entry point for everything you can add to a session.
 *
 * Three things live behind it, in the order they are reached for: a folder from
 * the machine, loose files, and a PDF or image for the model to read. They were
 * previously nowhere, nowhere, and a separate button in one section — the point
 * of collecting them is that "add something to this session" is one gesture
 * regardless of what the something is.
 *
 * Folder and file imports go into the sandbox filesystem, so the agent sees
 * them exactly as it sees anything it wrote itself. A PDF or an image goes to
 * the upload pipeline instead and rides along with the next message as a
 * content block, because those are for the model to *look at*, not for the
 * project to contain.
 */

export type ImportSummary = {
  files: number;
  bytes: number;
  folder: string;
  skipped: string | null;
  truncated: boolean;
};

type Phase =
  | { kind: "idle" }
  | { kind: "reading"; found: number }
  | { kind: "uploading"; done: number; total: number; folder: string }
  | { kind: "done"; summary: ImportSummary }
  | { kind: "error"; message: string };

export function AttachMenu({
  sessionId,
  token,
  disabled,
  sandbox = true,
  onImported,
  onAttach,
}: {
  sessionId: string;
  token?: string | null;
  disabled?: boolean;
  /**
   * Whether this composer has a sandbox filesystem behind it. Code does; Chat
   * does not. The folder and file-import entries put files *into* the sandbox,
   * so in Chat they would be two menu items that cannot do anything — the
   * document attach is the whole menu there.
   */
  sandbox?: boolean;
  /** Fired once the sandbox has the files, so the tree can be refreshed. */
  onImported: (summary: ImportSummary) => void;
  /** PDFs and images — handled by the composer's existing upload path. */
  onAttach: (files: FileList | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const folderInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const docInput = useRef<HTMLInputElement>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const motionOK = useMotionOK();

  const busy = phase.kind === "reading" || phase.kind === "uploading";

  // Close on an outside click or Escape — a menu that outlives the pointer
  // leaving it is the sort of thing that gets clicked by accident.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapper.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  // The completion line withdraws itself; the failure line does not.
  useEffect(() => {
    if (phase.kind !== "done") return;
    const timer = setTimeout(() => setPhase({ kind: "idle" }), 4500);
    return () => clearTimeout(timer);
  }, [phase]);

  /** Upload a walked folder batch by batch, so progress is real work done. */
  const upload = async (pick: FolderPick) => {
    if (!pick.files.length) {
      setPhase({
        kind: "error",
        message: `Nothing to open in ${pick.name} — every file was excluded.`,
      });
      return;
    }

    const batches = batchFiles(pick.files);
    let done = 0;
    setPhase({
      kind: "uploading",
      done: 0,
      total: pick.files.length,
      folder: pick.name,
    });

    try {
      for (const batch of batches) {
        await importWorkspaceFiles(sessionId, batch, token);
        done += batch.length;
        setPhase({
          kind: "uploading",
          done,
          total: pick.files.length,
          folder: pick.name,
        });
      }
    } catch (err) {
      setPhase({
        kind: "error",
        message:
          err instanceof Error
            ? err.message.replace(/^\d+\s+[\w ]+\s+—\s+/, "")
            : "The upload failed.",
      });
      // Partial imports are still imports: whatever landed is in the sandbox,
      // so the tree is refreshed even on the failure path.
      if (done) {
        onImported({
          files: done,
          bytes: pick.bytes,
          folder: pick.name,
          skipped: describeSkips(pick),
          truncated: pick.truncated,
        });
      }
      return;
    }

    const summary: ImportSummary = {
      files: done,
      bytes: pick.bytes,
      folder: pick.name,
      skipped: describeSkips(pick),
      truncated: pick.truncated,
    };
    setPhase({ kind: "done", summary });
    onImported(summary);
  };

  const openFolder = async () => {
    setOpen(false);
    if (!directoryPickerSupported()) {
      folderInput.current?.click();
      return;
    }
    setPhase({ kind: "reading", found: 0 });
    try {
      const pick = await pickDirectory((found) =>
        setPhase({ kind: "reading", found }),
      );
      if (!pick) {
        setPhase({ kind: "idle" }); // dismissed: not an error
        return;
      }
      await upload(pick);
    } catch (err) {
      setPhase({
        kind: "error",
        message: err instanceof Error ? err.message : "Could not read that folder.",
      });
    }
  };

  const addLooseFiles = (list: FileList | null) => {
    if (!list?.length) return;
    const pick: FolderPick = {
      name: list.length === 1 ? list[0].name : `${list.length} files`,
      files: Array.from(list).map((file) => ({ path: file.name, file })),
      skippedDirs: {},
      skippedLarge: [],
      truncated: false,
      bytes: Array.from(list).reduce((n, f) => n + f.size, 0),
    };
    void upload(pick);
  };

  return (
    <div ref={wrapper} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={disabled || busy}
        aria-label="Add to this session"
        aria-expanded={open}
        title="Add folder, files, or a document"
        className={cn(
          "grid h-8 w-8 shrink-0 place-items-center rounded-ctl transition-all duration-200",
          "text-ink-faint hover:bg-raised hover:text-ink active:scale-[0.94]",
          "disabled:pointer-events-none disabled:opacity-45",
          open && "bg-raised text-ink",
        )}
      >
        {busy ? (
          <motion.span
            className="sigil h-2.5 w-2.5 bg-accent"
            animate={motionOK ? { opacity: [0.35, 1, 0.35] } : { opacity: 1 }}
            transition={
              motionOK
                ? { duration: 1.3, repeat: Infinity, ease: "easeInOut" }
                : { duration: 0 }
            }
            aria-hidden
          />
        ) : (
          <motion.svg
            width="17"
            height="17"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinecap="round"
            animate={{ rotate: open ? 45 : 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
          >
            <path d="M12 5v14M5 12h14" />
          </motion.svg>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            initial={motionOK ? { opacity: 0, y: 8, scale: 0.97 } : { opacity: 0 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={motionOK ? { opacity: 0, y: 6, scale: 0.98 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            className="glass absolute bottom-full left-0 z-50 mb-2 w-[16.5rem] overflow-hidden
                       rounded-panel p-1.5 shadow-lift"
          >
            {sandbox && (
            <MenuItem
              label="Open folder"
              hint={directoryPickerSupported() ? "into the sandbox" : "select a folder"}
              onClick={openFolder}
              icon={
                <>
                  <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h7A1.5 1.5 0 0 1 19 10v7a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 3 17z" />
                </>
              }
            />
            )}
            {sandbox && (
            <MenuItem
              label="Upload files"
              hint="into the sandbox"
              onClick={() => {
                setOpen(false);
                fileInput.current?.click();
              }}
              icon={
                <>
                  <path d="M6 3h7l5 5v13H6z" />
                  <path d="M13 3v5h5" />
                  <path d="M12 18v-6m0 0-2.2 2.2M12 12l2.2 2.2" />
                </>
              }
            />
            )}
            <MenuItem
              label="Attach PDF, CSV or image"
              hint="for the model to read"
              onClick={() => {
                setOpen(false);
                docInput.current?.click();
              }}
              icon={
                <>
                  <rect x="3" y="5" width="18" height="14" rx="2.5" />
                  <circle cx="8.5" cy="10" r="1.6" />
                  <path d="m4 17 5-4.5 4 3.5 3-2.5 4 3.5" />
                </>
              }
            />
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {phase.kind !== "idle" && !open && (
          <motion.div
            initial={motionOK ? { opacity: 0, y: 6 } : { opacity: 0 }}
            animate={{ opacity: 1, y: 0 }}
            exit={motionOK ? { opacity: 0, y: 4 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            className="glass absolute bottom-full left-0 z-40 mb-2 w-[19rem] rounded-panel px-3 py-2.5"
          >
            <ProgressBody phase={phase} onDismiss={() => setPhase({ kind: "idle" })} />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Fallback folder picker, for browsers without `showDirectoryPicker`.
          `webkitdirectory` is not in React's DOM typings, hence the cast. */}
      <input
        ref={folderInput}
        type="file"
        hidden
        multiple
        {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
        onChange={(e) => {
          const list = e.target.files;
          if (list?.length) void upload(pickFromInput(list));
          e.target.value = "";
        }}
      />
      <input
        ref={fileInput}
        type="file"
        hidden
        multiple
        onChange={(e) => {
          addLooseFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={docInput}
        type="file"
        hidden
        multiple
        accept="application/pdf,.pdf,.csv,text/csv,image/png,image/jpeg,image/gif,image/webp"
        onChange={(e) => {
          onAttach(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}

function MenuItem({
  label,
  hint,
  icon,
  onClick,
}: {
  label: string;
  hint: string;
  icon: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="flex w-full items-center gap-2.5 rounded-ctl px-2.5 py-2 text-left
                 transition-colors duration-150 hover:bg-elevated"
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="shrink-0 text-ink-faint"
      >
        {icon}
      </svg>
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="truncate font-sans text-[0.8125rem] text-ink">{label}</span>
        <span className="truncate text-[11px] text-ink-faint">{hint}</span>
      </span>
    </button>
  );
}

function ProgressBody({
  phase,
  onDismiss,
}: {
  phase: Phase;
  onDismiss: () => void;
}) {
  if (phase.kind === "reading") {
    return (
      <Line
        tone="busy"
        text={
          phase.found
            ? `Reading the folder — ${phase.found} file${phase.found === 1 ? "" : "s"} so far`
            : "Reading the folder…"
        }
      />
    );
  }

  if (phase.kind === "uploading") {
    const pct = Math.round((phase.done / Math.max(phase.total, 1)) * 100);
    return (
      <div>
        <Line
          tone="busy"
          text={`Uploading ${phase.folder} — ${phase.done} of ${phase.total}`}
        />
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-inset">
          <motion.div
            className="h-full rounded-full bg-gradient-to-r from-accent to-accent-alt"
            animate={{ width: `${pct}%` }}
            transition={{ duration: 0.25, ease: "easeOut" }}
          />
        </div>
      </div>
    );
  }

  if (phase.kind === "done") {
    const { summary } = phase;
    return (
      <div>
        <Line
          tone="ok"
          text={`Opened ${summary.folder} — ${summary.files} file${
            summary.files === 1 ? "" : "s"
          } · ${formatBytes(summary.bytes)}`}
        />
        {summary.skipped && (
          <p className="mt-1 text-[11px] leading-snug text-ink-faint">
            {summary.skipped}
          </p>
        )}
        {summary.truncated && (
          <p className="mt-1 text-[11px] leading-snug text-warn">
            Stopped at the 4,000-file limit — open a subfolder for the rest.
          </p>
        )}
      </div>
    );
  }

  if (phase.kind === "error") {
    return (
      <div className="flex items-start gap-2">
        <Line tone="error" text={phase.message} />
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="ml-auto shrink-0 text-ink-faint transition-colors hover:text-ink"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>
    );
  }

  return null;
}

function Line({
  tone,
  text,
}: {
  tone: "busy" | "ok" | "error";
  text: string;
}) {
  const motionOK = useMotionOK();
  return (
    <div className="flex items-start gap-2">
      <motion.span
        aria-hidden
        className={cn(
          "sigil mt-[3px] h-[7px] w-[7px] shrink-0",
          tone === "ok" ? "bg-add" : tone === "error" ? "bg-del" : "bg-accent",
        )}
        animate={
          motionOK && tone === "busy" ? { opacity: [0.35, 1, 0.35] } : { opacity: 1 }
        }
        transition={
          motionOK && tone === "busy"
            ? { duration: 1.3, repeat: Infinity, ease: "easeInOut" }
            : { duration: 0 }
        }
      />
      <span
        className={cn(
          "min-w-0 font-sans text-2xs leading-snug",
          tone === "error" ? "text-del" : "text-ink-muted",
        )}
      >
        {text}
      </span>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
