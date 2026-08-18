"use client";

import { motion } from "framer-motion";
import { memo } from "react";
import type { FileNode } from "@/lib/events";
import type {
  AgentStatus,
  ChangedFile,
  GitState,
  PreviewState,
  TerminalLine,
} from "@/lib/useAgentSocket";
import { cn, shortPath } from "@/lib/cn";
import { SPRING_SNAP, useMotionOK } from "./Anim";
import { DiffViewer } from "./DiffViewer";
import type { TreeActions } from "./FileTree";
import { GitPanel } from "./GitPanel";
import { PreviewPanel } from "./PreviewPanel";
import { ProjectPulse } from "./ProjectPulse";
import { TerminalPanel } from "./TerminalPanel";

/**
 * The context column — the right-hand third of the Ledger layout.
 *
 * Three faces and no empty one. With no file in play it shows the Project
 * Pulse; the moment the agent touches a file the code takes over, and the
 * Pulse is one click away rather than gone. Preview joins them as a peer rather
 * than as a new panel elsewhere: what the agent built and what it looks like
 * running are two views of the same thing, and they belong to the same column.
 * The sandbox terminal stays docked to the bottom, because every surface in
 * here is the sandbox — which is what leaves the conversation column free to be
 * nothing but conversation.
 *
 * The column reads as *recessed*: a quieter tint than the conversation, a
 * hairline rather than a border, and its own blur. The conversation floats
 * above it. That difference in depth is doing the work the old fixed
 * three-panel split asked plain borders to do.
 */

export type ContextFace = "pulse" | "code" | "preview" | "history";

export const ContextColumn = memo(function ContextColumn({
  face,
  onFace,
  status,
  connected,
  iterations,
  usage,
  changed,
  activeFile,
  activeFileData,
  tree,
  treeRoot,
  flash,
  modelName,
  routingMode,
  routingHint,
  terminal,
  terminalBusy,
  terminalOpen,
  preview,
  git,
  gitBusy,
  gitError,
  onCommit,
  onInitRepo,
  exporting,
  onToggleTerminal,
  onClearTerminal,
  onSelectFile,
  onExport,
  onSaveFile,
  onReloadPreview,
  onDismissPreviewError,
  onStopPreview,
  treeActions,
  onDismiss,
}: {
  /** Which face is showing. `code` falls back to `pulse` with no active file. */
  face: ContextFace;
  onFace: (f: ContextFace) => void;
  status: AgentStatus;
  connected: boolean;
  iterations: number;
  usage: { input: number; output: number; cost: number };
  changed: Record<string, ChangedFile>;
  activeFile: string | null;
  activeFileData: ChangedFile | null;
  tree: FileNode[];
  treeRoot: string;
  flash: Record<string, number>;
  modelName: string;
  routingMode: "manual" | "auto";
  routingHint: string;
  terminal: TerminalLine[];
  terminalBusy: boolean;
  terminalOpen: boolean;
  preview: PreviewState;
  /** Repository state, or null before the server has reported any. */
  git: GitState;
  gitBusy: boolean;
  gitError: string | null;
  onCommit: (message: string) => void;
  onInitRepo: () => void;
  /** True while the project zip is being packaged. */
  exporting: boolean;
  onToggleTerminal: () => void;
  onClearTerminal: () => void;
  onSelectFile: (path: string) => void;
  onExport: () => void;
  /** Absent while there is no session to save into — the editor goes read-only. */
  onSaveFile?: (path: string, content: string) => Promise<void>;
  onReloadPreview: () => void;
  onDismissPreviewError: () => void;
  onStopPreview: () => void;
  /** Rename / delete / create from the sandbox tree. Absent = read-only. */
  treeActions?: TreeActions;
  /** Present only when the column is floating as an overlay. */
  onDismiss?: () => void;
}) {
  const motionOK = useMotionOK();
  const showing: ContextFace =
    face === "code" && !activeFileData ? "pulse" : face;

  // The Preview tab wears the dev server's state, so switching to it is never
  // the only way to learn something went wrong there.
  const previewTone =
    preview.status === "live"
      ? preview.error
        ? "warn"
        : "live"
      : preview.status === "starting"
        ? "starting"
        : preview.status === "stopped"
          ? "stopped"
          : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-14 shrink-0 items-center gap-1 px-3">
        <Face
          active={showing === "pulse"}
          onClick={() => onFace("pulse")}
          label="Pulse"
        />
        <Face
          active={showing === "code"}
          disabled={!activeFileData}
          onClick={() => onFace("code")}
          label={
            activeFileData
              ? shortPath(activeFileData.path, treeRoot).split("/").pop()!
              : "Code"
          }
          mono={Boolean(activeFileData)}
        />
        <Face
          active={showing === "preview"}
          onClick={() => onFace("preview")}
          label="Preview"
          tone={previewTone}
        />
        <Face
          active={showing === "history"}
          onClick={() => onFace("history")}
          label="History"
          // The dot is the uncommitted-work count: the panel is worth opening
          // precisely when there is something in it to commit.
          tone={git?.repo && git.status.length > 0 ? "warn" : null}
        />

        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Close context panel"
            className="ml-auto grid h-8 w-8 place-items-center rounded-ctl text-ink-faint
                       transition-colors duration-200 hover:bg-elevated hover:text-ink"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
        )}
      </header>

      <div className="relative min-h-0 flex-1">
        {/*
          One face at a time, keyed, entering under its own animation — and
          deliberately *not* wrapped in `AnimatePresence`.

          It used to be `AnimatePresence mode="wait"`, which holds the outgoing
          face mounted until its exit animation reports completion. Once the
          inline editor had been opened, that report never came: CodeMirror
          inside the outgoing subtree leaves framer-motion's projection mid-flight
          (the exit starts, the transform resets, and the child is never
          released), so the whole column froze on the file the user had been
          editing and no tab could move it. Dropping the exit is the trade —
          the incoming face still animates in, and the panel cannot deadlock,
          which is worth more than a slide-out nobody asked for.
        */}
        <motion.div
          key={
            showing === "code"
              ? `code:${activeFileData?.path}`
              : showing === "preview"
                ? "preview"
                : showing === "history"
                  ? "history"
                  : "pulse"
          }
          initial={motionOK ? { opacity: 0, x: faceOffset(showing) } : false}
          animate={{ opacity: 1, x: 0 }}
          transition={motionOK ? SPRING_SNAP : { duration: 0 }}
          className="absolute inset-0"
        >
          {showing === "preview" ? (
            <PreviewPanel
              preview={preview}
              onReload={onReloadPreview}
              onDismissError={onDismissPreviewError}
              onStop={onStopPreview}
            />
          ) : showing === "history" ? (
            <GitPanel
              git={git}
              root={treeRoot}
              busy={gitBusy}
              error={gitError}
              onCommit={onCommit}
              onInit={onInitRepo}
              onSelectFile={onSelectFile}
            />
          ) : showing === "code" && activeFileData ? (
            <DiffViewer
              file={activeFileData}
              root={treeRoot}
              onSave={onSaveFile}
              // Path comparison is against git's own status list, so the
              // button appears for exactly the files git would commit.
              uncommitted={Boolean(
                git?.repo &&
                  git.status.some(
                    (change) =>
                      activeFileData.path === change.path ||
                      activeFileData.path.endsWith(`/${change.path}`),
                  ),
              )}
              onCommitFile={() => onFace("history")}
            />
          ) : (
            <ProjectPulse
              status={status}
              connected={connected}
              iterations={iterations}
              usage={usage}
              changed={changed}
              activeFile={activeFile}
              tree={tree}
              treeRoot={treeRoot}
              flash={flash}
              modelName={modelName}
              routingMode={routingMode}
              routingHint={routingHint}
              exporting={exporting}
              onSelectFile={onSelectFile}
              onExport={onExport}
              treeActions={treeActions}
            />
          )}
        </motion.div>
      </div>

      <TerminalPanel
        lines={terminal}
        busy={terminalBusy}
        open={terminalOpen}
        onToggle={onToggleTerminal}
        onClear={onClearTerminal}
      />
    </div>
  );
});

/**
 * Which way a face enters from, so the motion agrees with the tab order the
 * user just clicked along. Pulse is leftmost, Preview rightmost.
 */
function faceOffset(face: ContextFace): number {
  return face === "pulse" ? -12 : face === "code" ? 8 : 12;
}

function Face({
  active,
  disabled,
  label,
  mono,
  tone,
  onClick,
}: {
  active: boolean;
  disabled?: boolean;
  label: string;
  mono?: boolean;
  /** A dot beside the label, carrying the dev server's state onto the tab. */
  tone?: "live" | "starting" | "warn" | "stopped" | null;
  onClick: () => void;
}) {
  const motionOK = useMotionOK();
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={cn(
        "relative flex max-w-[11rem] items-center gap-1.5 truncate rounded-ctl px-2.5 py-1.5",
        "transition-colors duration-200",
        mono ? "voice-machine" : "font-sans text-xs font-medium",
        disabled
          ? "cursor-not-allowed text-ink-dim"
          : active
            ? "text-ink"
            : "text-ink-faint hover:text-ink-muted",
      )}
    >
      {active && (
        <motion.span
          layoutId={motionOK ? "context-face" : undefined}
          className="absolute inset-0 rounded-ctl bg-raised"
          transition={motionOK ? SPRING_SNAP : { duration: 0 }}
        />
      )}
      <span className="relative truncate">{label}</span>
      {tone && (
        <motion.span
          aria-hidden
          className={cn(
            "sigil relative h-[6px] w-[6px] shrink-0",
            tone === "live"
              ? "bg-add"
              : tone === "starting"
                ? "bg-warn"
                : tone === "warn"
                  ? "bg-del"
                  : "bg-ink-dim",
          )}
          animate={
            motionOK && tone === "starting"
              ? { opacity: [0.35, 1, 0.35] }
              : { opacity: 1 }
          }
          transition={
            motionOK && tone === "starting"
              ? { duration: 2, repeat: Infinity, ease: "easeInOut" }
              : { duration: 0 }
          }
        />
      )}
    </button>
  );
}
