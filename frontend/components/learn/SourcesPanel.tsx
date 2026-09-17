"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { SOURCE_LABEL, type NotebookSource, type SourceType } from "@/lib/learn";
import { SPRING, SPRING_SNAP, useMotionOK } from "../Anim";
import { SourceIcon } from "./NotebookCover";

export type AddPayload =
  | { kind: "url"; url: string }
  | { kind: "text"; text: string }
  | { kind: "pdf"; files: FileList };

/**
 * The left panel: what this notebook knows.
 *
 * Three ways in — a PDF, a link, a block of pasted text — and they are all one
 * control rather than three scattered buttons, because they are one decision:
 * "add something to read". The panel is also the only place a source can be
 * removed, which keeps the answer to "why did the chat stop citing that?"
 * in the same column as the thing that caused it.
 */
export function SourcesPanel({
  sources,
  busy,
  busyLabel,
  onAdd,
  onRemove,
}: {
  sources: NotebookSource[];
  busy: boolean;
  /** What is being read right now, when the caller knows. */
  busyLabel?: string | null;
  onAdd: (payload: AddPayload) => void;
  onRemove: (id: string) => void;
}) {
  const [mode, setMode] = useState<"idle" | "url" | "text">("idle");
  const [draft, setDraft] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const motionOK = useMotionOK();

  const ready = sources.filter((s) => s.status === "ready").length;

  const submit = () => {
    const value = draft.trim();
    if (!value) return;
    onAdd(mode === "url" ? { kind: "url", url: value } : { kind: "text", text: value });
    setDraft("");
    setMode("idle");
  };

  return (
    <div
      className="flex h-full min-h-0 flex-col"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files?.length) onAdd({ kind: "pdf", files: e.dataTransfer.files });
      }}
    >
      <header className="flex h-bar-sub shrink-0 items-center gap-2 border-b border-line px-3.5">
        <span className="sigil h-1.5 w-1.5 bg-accent" aria-hidden />
        <h2 className="voice-label text-ink-muted">Sources</h2>
        <span className="voice-machine ml-auto text-ink-faint">
          {ready}/{sources.length || 0}
        </span>
      </header>

      {/* ------------------------------------------------------------- add */}
      <div className="shrink-0 border-b border-line p-2.5">
        <AnimatePresence mode="wait" initial={false}>
          {mode === "idle" ? (
            <motion.div
              key="idle"
              initial={motionOK ? { opacity: 0 } : false}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={motionOK ? { duration: 0.14 } : { duration: 0 }}
              className="grid grid-cols-3 gap-1.5"
            >
              <AddButton
                label="PDF"
                disabled={busy}
                onClick={() => fileInput.current?.click()}
                icon={<SourceIcon type="pdf" />}
              />
              <AddButton
                label="Link"
                disabled={busy}
                onClick={() => setMode("url")}
                icon={<SourceIcon type="url" />}
              />
              <AddButton
                label="Text"
                disabled={busy}
                onClick={() => setMode("text")}
                icon={<SourceIcon type="text" />}
              />
            </motion.div>
          ) : (
            <motion.div
              key="input"
              initial={motionOK ? { opacity: 0, y: -4 } : false}
              animate={{ opacity: 1, y: 0 }}
              exit={motionOK ? { opacity: 0 } : { opacity: 0 }}
              transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            >
              {mode === "url" ? (
                <input
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submit();
                    if (e.key === "Escape") {
                      setDraft("");
                      setMode("idle");
                    }
                  }}
                  placeholder="https://… or a YouTube link"
                  aria-label="Source URL"
                  className="h-9 w-full rounded-ctl border border-line bg-inset px-2.5 font-mono text-2xs
                             text-ink placeholder:text-ink-faint focus:border-line-focus focus:outline-none"
                />
              ) : (
                <textarea
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      setDraft("");
                      setMode("idle");
                    }
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
                  }}
                  rows={5}
                  placeholder="Paste the text you want to study…"
                  aria-label="Source text"
                  className="scroll-thin w-full resize-none rounded-ctl border border-line bg-inset px-2.5 py-2
                             text-[0.8125rem] leading-relaxed text-ink placeholder:text-ink-faint
                             focus:border-line-focus focus:outline-none"
                />
              )}
              <div className="mt-1.5 flex gap-1.5">
                <button
                  type="button"
                  onClick={submit}
                  disabled={!draft.trim() || busy}
                  className="h-8 flex-1 rounded-ctl bg-accent text-2xs font-semibold text-accent-ink
                             transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200 hover:brightness-110 active:scale-[0.98]
                             disabled:opacity-40"
                >
                  Add source
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setDraft("");
                    setMode("idle");
                  }}
                  className="h-8 rounded-ctl px-3 text-2xs text-ink-muted transition-colors
                             duration-200 hover:bg-raised hover:text-ink"
                >
                  Cancel
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <input
          ref={fileInput}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) onAdd({ kind: "pdf", files: e.target.files });
            e.target.value = "";
          }}
        />
      </div>

      {/* ----------------------------------------------------------- list */}
      <div className="scroll-thin relative min-h-0 flex-1 overflow-y-auto p-2">
        {busy && (
          <div className="mb-2 flex items-center gap-2 rounded-ctl border border-accent-line bg-accent-soft px-2.5 py-2">
            <motion.span
              className="sigil h-1.5 w-1.5 shrink-0 bg-accent"
              animate={motionOK ? { opacity: [1, 0.25, 1] } : {}}
              transition={{ duration: 1.2, repeat: Infinity }}
              aria-hidden
            />
            <span className="min-w-0 truncate text-2xs text-ink-muted">
              {busyLabel ? `Reading ${busyLabel}…` : "Reading and indexing…"}
            </span>
          </div>
        )}

        {sources.length === 0 && !busy ? (
          <p className="px-2 py-3 text-2xs leading-relaxed text-ink-faint">
            Nothing here yet. Add a PDF, a link or some text — every answer in
            this notebook is drawn from these and nothing else.
          </p>
        ) : (
          <ul className="space-y-1">
            <AnimatePresence initial={false}>
              {sources.map((source) => (
                <SourceRow key={source.id} source={source} onRemove={onRemove} />
              ))}
            </AnimatePresence>
          </ul>
        )}

        <AnimatePresence>
          {dragging && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={motionOK ? { duration: 0.15 } : { duration: 0 }}
              className="pointer-events-none absolute inset-2 grid place-items-center rounded-card
                         border-2 border-dashed border-accent-line bg-accent-soft"
            >
              <p className="text-2xs font-medium text-accent">Drop a PDF to add it</p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function SourceRow({
  source,
  onRemove,
}: {
  source: NotebookSource;
  onRemove: (id: string) => void;
}) {
  const motionOK = useMotionOK();
  const failed = source.status === "failed";

  return (
    <motion.li
      layout={motionOK}
      initial={motionOK ? { opacity: 0, y: -4 } : false}
      animate={{ opacity: 1, y: 0 }}
      exit={motionOK ? { opacity: 0, height: 0 } : { opacity: 0 }}
      transition={motionOK ? SPRING : { duration: 0 }}
      className={cn(
        "group relative rounded-ctl border px-2.5 py-2 transition-colors duration-200",
        failed
          ? "border-del/25 bg-del-bg"
          : "border-line bg-elevated hover:border-line-strong",
      )}
    >
      <div className="flex items-start gap-2 pr-6">
        <span className={cn("mt-px shrink-0", failed ? "text-del" : "text-accent")}>
          <SourceIcon type={source.source_type as SourceType} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-2xs font-medium text-ink" title={source.title}>
            {source.title}
          </p>
          <p className="voice-machine mt-0.5 truncate text-ink-faint">
            {failed
              ? source.error || "Could not be read"
              : `${SOURCE_LABEL[source.source_type as SourceType]} · ${formatChars(source.char_count)}`}
          </p>
        </div>
      </div>
      {/* Revealed on hover on a pointer device, and simply *present* on a
          touch one: `group-hover` never fires without a mouse, so on a phone
          this control did not exist. `@media (hover: hover)` is the only thing
          that tells those two cases apart. */}
      <button
        type="button"
        aria-label={`Remove ${source.title}`}
        onClick={() => onRemove(source.id)}
        className="absolute right-1.5 top-1.5 grid h-7 w-7 place-items-center rounded text-ink-faint
                   transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200 hover:text-del focus-visible:opacity-100
                   [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
          <path d="m6 6 12 12M18 6 6 18" />
        </svg>
      </button>
    </motion.li>
  );
}

function AddButton({
  label,
  icon,
  disabled,
  onClick,
}: {
  label: string;
  icon: React.ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-[52px] flex-col items-center justify-center gap-1 rounded-ctl border border-line
                 bg-elevated text-ink-muted transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200 hover:border-accent-line
                 hover:bg-raised hover:text-ink active:scale-[0.97] disabled:opacity-40"
    >
      <span className="text-accent">{icon}</span>
      <span className="text-2xs font-medium uppercase tracking-wider">{label}</span>
    </button>
  );
}

function formatChars(count: number): string {
  if (count >= 1000) return `${Math.round(count / 1000)}k chars`;
  return `${count} chars`;
}
