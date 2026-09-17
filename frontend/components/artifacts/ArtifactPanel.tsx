"use client";

import { motion } from "framer-motion";
import { Check, Clock, Code2, Eye, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { artifactVersions, saveArtifact, type ArtifactRow } from "@/lib/artifacts";
import { cn } from "@/lib/cn";
import { useFocusTrap } from "@/lib/useFocusTrap";
import type { ArtifactPayload } from "@/lib/events";
import { SPRING_SOFT, useMotionOK } from "../Anim";
import { CodeEditor } from "../CodeEditor";
import { Markdown } from "../Markdown";

/**
 * An artifact, open.
 *
 * Four kinds, four renderings, and the rule that decides between them is
 * whether the thing is meant to be *read* or *run*. Markdown reads. Code is
 * read and edited, so it gets the editor. HTML and SVG are meant to be looked
 * at, so they render — with a toggle to the source, because the moment anyone
 * wants to change them they want the text.
 *
 * **Saving adds a version; it does not replace one.** The editor's status line
 * says "Autosaves to a new version" rather than its usual "to the sandbox",
 * because both halves of that default would be wrong here: an artifact never
 * touches the sandbox, and "save" normally means overwrite when here the
 * model's version survives underneath. Getting that wording wrong would make
 * people hesitate to edit at all.
 *
 * **HTML runs in a sandboxed frame with no same-origin access.** The content is
 * model-written and may contain scripts. `allow-scripts` without
 * `allow-same-origin` is the pairing that lets a page be interactive while
 * keeping it unable to read this origin's storage, cookies or DOM — the two
 * together would be equivalent to not sandboxing at all.
 */
export function ArtifactPanel({
  artifact,
  sessionId,
  token,
  onClose,
}: {
  artifact: ArtifactPayload;
  sessionId: string;
  token?: string | null;
  onClose: () => void;
}) {
  const motionOK = useMotionOK();
  const dialog = useRef<HTMLElement>(null);
  // Mounted only while open, so the trap is simply always on.
  useFocusTrap(dialog, true);
  const [showSource, setShowSource] = useState(false);
  const [history, setHistory] = useState<ArtifactRow[] | null>(null);
  const [viewing, setViewing] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // A new revision arriving on the socket makes any older version being
  // inspected stale, and makes the fetched history incomplete.
  useEffect(() => {
    setViewing(null);
    setHistory(null);
  }, [artifact.version]);

  const shown = useMemo(() => {
    if (viewing === null) return artifact;
    const row = history?.find((r) => r.version === viewing);
    return row
      ? { ...artifact, content: row.content, version: row.version, created_by: row.created_by }
      : artifact;
  }, [artifact, history, viewing]);

  const isCurrent = shown.version === artifact.version;

  const save = useCallback(
    async (content: string) => {
      await saveArtifact(sessionId, artifact.key, content, token);
      // The server broadcasts `artifact_updated`, so the panel's own copy
      // arrives through the same path an agent write takes. Nothing is set
      // here — one source of truth for what the current version is.
    },
    [sessionId, artifact.key, token],
  );

  const loadHistory = useCallback(() => {
    if (history) return;
    artifactVersions(sessionId, artifact.key, token)
      .then(setHistory)
      .catch(() => setError("Could not load the version history."));
  }, [history, sessionId, artifact.key, token]);

  const renderable = shown.kind === "html" || shown.kind === "svg";

  return (
    <motion.div
      className="fixed inset-0 z-[60] grid place-items-center overflow-y-auto p-5"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: motionOK ? 0.16 : 0 }}
    >
      <button
        type="button"
        aria-label="Close artifact"
        onClick={onClose}
        className="absolute inset-0 bg-black/62 backdrop-blur-[2px]"
      />

      <motion.section
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-label={artifact.title}
        initial={motionOK ? { opacity: 0, y: 12, scale: 0.985 } : false}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={motionOK ? { opacity: 0, y: 8, scale: 0.99 } : { opacity: 0 }}
        transition={motionOK ? SPRING_SOFT : { duration: 0 }}
        className="glass relative flex h-[84vh] w-full max-w-4xl flex-col
                   overflow-hidden rounded-card"
      >
        {/* ------------------------------------------------------------ head */}
        <header className="flex h-bar shrink-0 items-center gap-3 border-b border-line px-5">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-medium text-ink">{artifact.title}</h2>
            <p className="mt-0.5 flex items-center gap-2 text-2xs text-ink-faint">
              <span className="font-mono">{artifact.key}</span>
              <span aria-hidden>·</span>
              <span>{shown.kind}</span>
              {shown.language && (
                <>
                  <span aria-hidden>·</span>
                  <span>{shown.language}</span>
                </>
              )}
            </p>
          </div>

          {renderable && (
            <button
              type="button"
              onClick={() => setShowSource((v) => !v)}
              className="flex items-center gap-1.5 rounded-ctl border border-line px-2.5 py-1.5
                         text-2xs text-ink-muted transition-colors duration-200
                         hover:border-accent-line hover:text-ink"
            >
              {showSource ? <Eye size={12} strokeWidth={1.9} /> : <Code2 size={12} strokeWidth={1.9} />}
              {showSource ? "Preview" : "Source"}
            </button>
          )}

          <VersionMenu
            artifact={artifact}
            history={history}
            viewing={viewing}
            onOpen={loadHistory}
            onPick={setViewing}
          />

          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-ctl p-1.5 text-ink-faint transition-colors
                       hover:bg-raised hover:text-ink"
          >
            <X size={15} strokeWidth={1.75} />
          </button>
        </header>

        {!isCurrent && (
          <p className="shrink-0 border-b border-line bg-warn/10 px-5 py-2 text-2xs text-warn">
            Showing version {shown.version} of {artifact.version}. This is
            history — editing is disabled until you return to the current
            version.
          </p>
        )}

        {/* ------------------------------------------------------------ body */}
        <div className="min-h-0 flex-1 overflow-hidden">
          {shown.kind === "code" || (renderable && showSource) ? (
            <CodeEditor
              // The path is only a highlighting hint. Giving it the artifact's
              // key plus an extension is what makes CodeMirror pick a mode
              // without inventing a file that does not exist.
              key={`${artifact.key}-${shown.version}`}
              path={`${artifact.key}.${extensionFor(shown)}`}
              value={shown.content}
              onSave={save}
              // Naming the destination matters: this editor's default copy is
              // "Autosaves to the sandbox", which is simply false here — an
              // artifact saves as a new version and never touches the sandbox.
              destination="a new version"
              // History is readable, selectable and copyable, but not
              // editable. Letting someone type into a version that cannot be
              // saved and only telling them on save is a worse answer than not
              // accepting the keystrokes.
              readOnly={!isCurrent}
              className="h-full"
            />
          ) : shown.kind === "html" ? (
            <iframe
              title={artifact.title}
              srcDoc={shown.content}
              // Scripts, but no same-origin. Together they would be equivalent
              // to no sandbox at all: the frame could reach into this origin's
              // storage and DOM. Apart, the page can be interactive and still
              // cannot touch anything of ours.
              sandbox="allow-scripts allow-forms allow-popups"
              className="h-full w-full border-0 bg-white"
            />
          ) : shown.kind === "svg" ? (
            <div className="scroll-thin grid h-full place-items-center overflow-auto p-6">
              {/* Rendered as an image, not injected as markup. An <svg> written
                  into the DOM can carry scripts and event handlers; a data URL
                  in an <img> cannot execute anything. */}
              <img
                alt={artifact.title}
                src={`data:image/svg+xml;utf8,${encodeURIComponent(shown.content)}`}
                className="max-h-full max-w-full"
              />
            </div>
          ) : (
            <div className="scroll-thin h-full overflow-y-auto px-6 py-5">
              <Markdown source={shown.content} />
            </div>
          )}
        </div>

        {/* ------------------------------------------------------------ foot */}
        <footer className="flex shrink-0 items-center gap-3 border-t border-line px-5 py-2.5">
          <span className="text-2xs text-ink-faint">
            Version {shown.version}
            {shown.created_by === "user" ? " · your edit" : " · written by Loom"}
          </span>
          {shown.kind === "code" && isCurrent && (
            <span className="text-2xs text-ink-faint">
              {/* The wording matters: "save" normally means overwrite, and here
                  it does not — the model's version survives underneath. */}
              ⌘S saves as a new version
            </span>
          )}
          {error && (
            <span className="ml-auto text-2xs text-warn" role="alert">
              {error}
            </span>
          )}
        </footer>
      </motion.section>
    </motion.div>
  );
}

function extensionFor(a: Pick<ArtifactPayload, "kind" | "language">): string {
  if (a.kind === "html") return "html";
  if (a.kind === "svg") return "svg";
  const byLanguage: Record<string, string> = {
    python: "py",
    typescript: "ts",
    tsx: "tsx",
    javascript: "js",
    jsx: "jsx",
    json: "json",
    css: "css",
    html: "html",
    markdown: "md",
    sql: "sql",
  };
  return byLanguage[(a.language || "").toLowerCase()] ?? "txt";
}

/**
 * The version switcher.
 *
 * Fetched on open rather than held in socket state: a long session accumulates
 * versions without bound, and almost nobody looks at them. One request when
 * somebody actually asks is the right trade.
 */
function VersionMenu({
  artifact,
  history,
  viewing,
  onOpen,
  onPick,
}: {
  artifact: ArtifactPayload;
  history: ArtifactRow[] | null;
  viewing: number | null;
  onOpen: () => void;
  onPick: (version: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  if (artifact.version < 2) return null;

  return (
    <div ref={root} className="relative shrink-0">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          onOpen();
          setOpen((o) => !o);
        }}
        className="flex items-center gap-1.5 rounded-ctl border border-line px-2.5 py-1.5
                   text-2xs text-ink-muted transition-colors duration-200
                   hover:border-accent-line hover:text-ink"
      >
        <Clock size={12} strokeWidth={1.9} />
        v{viewing ?? artifact.version}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-9 z-50 max-h-64 w-56 overflow-y-auto rounded-ctl
                     border border-line-strong bg-overlay p-1 shadow-lift"
        >
          {history === null && (
            <p className="px-2 py-1.5 text-2xs text-ink-faint">Loading…</p>
          )}
          {history?.slice().reverse().map((row) => {
            const current = row.version === (viewing ?? artifact.version);
            return (
              <button
                key={row.version}
                type="button"
                role="menuitem"
                onClick={() => {
                  onPick(row.version === artifact.version ? null : row.version);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-ctl px-2.5 py-1.5 text-left text-2xs",
                  "transition-colors duration-150",
                  current ? "text-ink" : "text-ink-muted hover:bg-raised hover:text-ink",
                )}
              >
                <span className="w-8 shrink-0 font-mono">v{row.version}</span>
                <span className="min-w-0 flex-1 truncate">
                  {row.created_by === "user" ? "your edit" : "Loom"}
                </span>
                {current && <Check size={12} strokeWidth={2.2} className="shrink-0 text-accent" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
