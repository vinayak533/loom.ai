"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { cn } from "@/lib/cn";
import type { Lesson, Note, NotebookSource, Progress } from "@/lib/learn";
import { SPRING_SNAP, SPRING_SOFT, useMotionOK } from "../Anim";
import { CourseView } from "./CourseView";
import { NotebookChat, type NotebookTurn } from "./NotebookChat";
import { NotesPanel } from "./NotesPanel";
import { SourcesPanel, type AddPayload } from "./SourcesPanel";

export type WorkspaceMode = "ask" | "course";

/**
 * A single notebook.
 *
 * Three columns — sources, chat, notes — because the middle one is only
 * meaningful in the company of the other two: you need to see what it is
 * reading from and where the answer went. The outer two are fixed and narrow;
 * the conversation takes what is left, the same asymmetry the Chat section
 * uses. Below `lg` they collapse into sheets over the conversation rather than
 * stacking, so the thing you came here to do stays on screen.
 *
 * Structured mode replaces the three columns entirely rather than adding a
 * fourth: a curriculum is a different way of reading the same notebook, not
 * another panel of it.
 */
export function NotebookWorkspace({
  title,
  mode,
  onMode,
  sources,
  notes,
  lessons,
  progress,
  turns,
  asking,
  addingSource,
  addingLabel,
  generating,
  modelName,
  onBack,
  onAsk,
  onAddSource,
  onRemoveSource,
  onCreateNote,
  onSaveAnswer,
  onUpdateNote,
  onDeleteNote,
  onGenerateCourse,
  onCompleteSection,
  onRename,
}: {
  title: string;
  mode: WorkspaceMode;
  onMode: (mode: WorkspaceMode) => void;
  sources: NotebookSource[];
  notes: Note[];
  lessons: Lesson[];
  progress: Progress[];
  turns: NotebookTurn[];
  asking: boolean;
  addingSource: boolean;
  /** The file currently being read, when one is. */
  addingLabel?: string | null;
  generating: boolean;
  modelName: string | null;
  onBack: () => void;
  onAsk: (question: string) => void;
  onAddSource: (payload: AddPayload) => void;
  onRemoveSource: (id: string) => void;
  /** A note the user typed. */
  onCreateNote: (content: string) => void;
  /** An answer the user kept. Stored as `ai_generated`, and marked as such. */
  onSaveAnswer: (content: string) => void;
  onUpdateNote: (id: string, content: string) => void;
  onDeleteNote: (id: string) => void;
  onGenerateCourse: () => void;
  onCompleteSection: (sectionId: string, completed: boolean) => void;
  onRename: (title: string) => void;
}) {
  /** Below `lg` the side panels float; this is which one is showing. */
  const [sheet, setSheet] = useState<"sources" | "notes" | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const motionOK = useMotionOK();

  const canGenerate = sources.some((s) => s.status === "ready");

  const commitRename = () => {
    const next = draftTitle.trim();
    if (next && next !== title) onRename(next);
    setRenaming(false);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ------------------------------------------------------ breadcrumb */}
      <header className="flex h-bar-sub shrink-0 items-center gap-2 border-b border-line px-3.5">
        <button
          type="button"
          onClick={onBack}
          className="flex h-8 items-center gap-1.5 rounded-ctl px-2 text-2xs text-ink-muted
                     transition-colors duration-200 hover:bg-elevated hover:text-ink"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="m14 6-6 6 6 6" />
          </svg>
          Library
        </button>

        <span className="text-ink-decor" aria-hidden>
          /
        </span>

        {renaming ? (
          <input
            autoFocus
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitRename();
              if (e.key === "Escape") {
                setDraftTitle(title);
                setRenaming(false);
              }
            }}
            aria-label="Notebook title"
            className="h-8 min-w-0 max-w-[280px] flex-1 rounded-ctl border border-accent-line bg-inset
                       px-2 text-[0.875rem] font-medium text-ink focus:outline-none"
          />
        ) : (
          <button
            type="button"
            onClick={() => {
              setDraftTitle(title);
              setRenaming(true);
            }}
            data-tip="Rename notebook"
            className="truncate rounded-ctl px-1.5 py-1 text-[0.875rem] font-medium text-ink
                       transition-colors duration-200 hover:bg-elevated"
          >
            {title}
          </button>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          <ModeToggle mode={mode} onMode={onMode} />

          {/* The panel triggers only exist where the panels do not. */}
          <div className="flex gap-0.5 lg:hidden">
            <PanelButton
              label="Sources"
              active={sheet === "sources"}
              onClick={() => setSheet(sheet === "sources" ? null : "sources")}
              count={sources.length}
            />
            <PanelButton
              label="Notes"
              active={sheet === "notes"}
              onClick={() => setSheet(sheet === "notes" ? null : "notes")}
              count={notes.length}
            />
          </div>
        </div>
      </header>

      {/* ---------------------------------------------------------- panels */}
      <div className="relative flex min-h-0 flex-1">
        {mode === "course" ? (
          <div className="min-w-0 flex-1">
            <CourseView
              lessons={lessons}
              progress={progress}
              busy={generating}
              canGenerate={canGenerate}
              onGenerate={onGenerateCourse}
              onComplete={onCompleteSection}
            />
          </div>
        ) : (
          <>
            <aside className="hidden w-[248px] shrink-0 border-r border-line lg:block">
              <SourcesPanel
                sources={sources}
                busy={addingSource}
                busyLabel={addingLabel}
                onAdd={onAddSource}
                onRemove={onRemoveSource}
              />
            </aside>

            <div className="min-w-0 flex-1">
              <NotebookChat
                turns={turns}
                sources={sources}
                busy={asking}
                modelName={modelName}
                onAsk={onAsk}
                onSaveNote={onSaveAnswer}
              />
            </div>

            <aside className="hidden w-[288px] shrink-0 border-l border-line xl:block">
              <NotesPanel
                notes={notes}
                onCreate={onCreateNote}
                onUpdate={onUpdateNote}
                onDelete={onDeleteNote}
              />
            </aside>

            {/* Notes has one more breakpoint than sources: between lg and xl
                the sources panel is docked and notes is still a sheet. */}
            <AnimatePresence>
              {sheet && (
                <>
                  <motion.div
                    key="scrim"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={motionOK ? { duration: 0.2 } : { duration: 0 }}
                    onClick={() => setSheet(null)}
                    className={cn(
                      "absolute inset-0 z-30 bg-black/45 backdrop-blur-[2px]",
                      sheet === "sources" ? "lg:hidden" : "xl:hidden",
                    )}
                  />
                  <motion.aside
                    key={sheet}
                    initial={motionOK ? { opacity: 0, x: sheet === "sources" ? -24 : 24 } : { opacity: 0 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={motionOK ? { opacity: 0, x: sheet === "sources" ? -24 : 24 } : { opacity: 0 }}
                    transition={motionOK ? SPRING_SOFT : { duration: 0 }}
                    className={cn(
                      "glass absolute inset-y-2 z-40 w-[min(300px,calc(100%-2rem))] overflow-hidden rounded-panel",
                      sheet === "sources" ? "left-2 lg:hidden" : "right-2 xl:hidden",
                    )}
                  >
                    {sheet === "sources" ? (
                      <SourcesPanel
                        sources={sources}
                        busy={addingSource}
                        busyLabel={addingLabel}
                        onAdd={onAddSource}
                        onRemove={onRemoveSource}
                      />
                    ) : (
                      <NotesPanel
                        notes={notes}
                        onCreate={onCreateNote}
                        onUpdate={onUpdateNote}
                        onDelete={onDeleteNote}
                      />
                    )}
                  </motion.aside>
                </>
              )}
            </AnimatePresence>
          </>
        )}
      </div>
    </div>
  );
}

function ModeToggle({
  mode,
  onMode,
}: {
  mode: WorkspaceMode;
  onMode: (mode: WorkspaceMode) => void;
}) {
  const motionOK = useMotionOK();
  return (
    <div
      role="tablist"
      aria-label="Notebook mode"
      className="flex gap-0.5 rounded-ctl bg-inset p-0.5"
    >
      {(
        [
          ["ask", "Q&A"],
          ["course", "Course"],
        ] as const
      ).map(([key, label]) => {
        const on = key === mode;
        return (
          <button
            key={key}
            role="tab"
            type="button"
            aria-selected={on}
            onClick={() => onMode(key)}
            className={cn(
              "relative h-7 rounded-[calc(var(--r-ctl)-2px)] px-3 text-2xs font-medium",
              "transition-colors duration-200",
              on ? "text-ink" : "text-ink-faint hover:text-ink-muted",
            )}
          >
            {on && (
              <motion.span
                layoutId="workspace-mode"
                transition={motionOK ? SPRING_SNAP : { duration: 0 }}
                className="absolute inset-0 rounded-[calc(var(--r-ctl)-2px)] bg-raised"
                aria-hidden
              />
            )}
            <span className="relative">{label}</span>
          </button>
        );
      })}
    </div>
  );
}

function PanelButton({
  label,
  active,
  count,
  onClick,
}: {
  label: string;
  active: boolean;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex h-7 items-center gap-1.5 rounded-ctl px-2 text-2xs transition-colors duration-200",
        active ? "bg-raised text-ink" : "text-ink-faint hover:bg-elevated hover:text-ink",
      )}
    >
      {label}
      <span className="voice-machine text-ink-faint">{count}</span>
    </button>
  );
}
