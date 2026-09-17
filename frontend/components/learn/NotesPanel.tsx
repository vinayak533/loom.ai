"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { cn } from "@/lib/cn";
import { relativeDate, type Note } from "@/lib/learn";
import { SPRING, SPRING_SNAP, useMotionOK } from "../Anim";
import { Markdown } from "../Markdown";

/**
 * The right panel: what you are keeping.
 *
 * Two kinds of note live here and they are marked differently — one you wrote,
 * one the model wrote and you decided to keep. That distinction is the whole
 * value of the panel later: a revision pass needs to know which claims came
 * from you and which came from a model reading your sources.
 */
export function NotesPanel({
  notes,
  onCreate,
  onUpdate,
  onDelete,
}: {
  notes: Note[];
  onCreate: (content: string) => void;
  onUpdate: (id: string, content: string) => void;
  onDelete: (id: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const [composing, setComposing] = useState(false);
  const motionOK = useMotionOK();

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onCreate(text);
    setDraft("");
    setComposing(false);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-bar-sub shrink-0 items-center gap-2 border-b border-line px-3.5">
        <span className="sigil h-1.5 w-1.5 bg-accent-alt" aria-hidden />
        <h2 className="voice-label text-ink-muted">Notes</h2>
        <span className="voice-machine ml-auto text-ink-faint">{notes.length}</span>
      </header>

      <div className="shrink-0 border-b border-line p-2.5">
        <AnimatePresence mode="wait" initial={false}>
          {composing ? (
            <motion.div
              key="compose"
              initial={motionOK ? { opacity: 0, y: -4 } : false}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            >
              <textarea
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setDraft("");
                    setComposing(false);
                  }
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
                }}
                rows={4}
                placeholder="Write a note. Markdown works."
                className="scroll-thin w-full resize-none rounded-ctl border border-line bg-inset px-2.5 py-2
                           text-[0.8125rem] leading-relaxed text-ink placeholder:text-ink-faint
                           focus:border-line-focus focus:outline-none"
              />
              <div className="mt-1.5 flex gap-1.5">
                <button
                  type="button"
                  onClick={submit}
                  disabled={!draft.trim()}
                  className="h-8 flex-1 rounded-ctl bg-accent text-2xs font-semibold text-accent-ink
                             transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200 hover:brightness-110 active:scale-[0.98]
                             disabled:opacity-40"
                >
                  Save note
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setDraft("");
                    setComposing(false);
                  }}
                  className="h-8 rounded-ctl px-3 text-2xs text-ink-muted transition-colors
                             duration-200 hover:bg-raised hover:text-ink"
                >
                  Cancel
                </button>
              </div>
            </motion.div>
          ) : (
            <motion.button
              key="new"
              type="button"
              initial={motionOK ? { opacity: 0 } : false}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={motionOK ? { duration: 0.14 } : { duration: 0 }}
              onClick={() => setComposing(true)}
              className="flex h-9 w-full items-center justify-center gap-2 rounded-ctl border border-line
                         bg-elevated text-2xs font-medium text-ink transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200
                         hover:border-accent-line hover:bg-raised active:scale-[0.985]"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-accent" aria-hidden>
                <path d="M12 5v14M5 12h14" />
              </svg>
              New note
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto p-2">
        {notes.length === 0 ? (
          <p className="px-2 py-3 text-2xs leading-relaxed text-ink-faint">
            Nothing saved yet. Write your own, or keep an answer from the chat
            with “Save as note”.
          </p>
        ) : (
          <ul className="space-y-1.5">
            <AnimatePresence initial={false}>
              {notes.map((note) => (
                <NoteCard
                  key={note.id}
                  note={note}
                  onUpdate={onUpdate}
                  onDelete={onDelete}
                />
              ))}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </div>
  );
}

function NoteCard({
  note,
  onUpdate,
  onDelete,
}: {
  note: Note;
  onUpdate: (id: string, content: string) => void;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(note.content);
  const [expanded, setExpanded] = useState(false);
  const motionOK = useMotionOK();

  const ai = note.source === "ai_generated";
  const long = note.content.length > 260;

  return (
    <motion.li
      layout={motionOK}
      initial={motionOK ? { opacity: 0, y: -4 } : false}
      animate={{ opacity: 1, y: 0 }}
      exit={motionOK ? { opacity: 0, height: 0 } : { opacity: 0 }}
      transition={motionOK ? SPRING : { duration: 0 }}
      className="group rounded-ctl border border-line bg-elevated p-2.5 transition-colors
                 duration-200 hover:border-line-strong"
    >
      <div className="mb-1.5 flex items-center gap-1.5">
        <span
          className={cn(
            "rounded px-1.5 py-0.5 font-mono text-2xs uppercase tracking-wider",
            ai ? "bg-accent-soft text-accent" : "bg-raised text-ink-faint",
          )}
        >
          {ai ? "From chat" : "Yours"}
        </span>
        <span className="voice-machine ml-auto text-ink-faint">
          {relativeDate(note.updated_at)}
        </span>
      </div>

      {editing ? (
        <>
          <textarea
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setValue(note.content);
                setEditing(false);
              }
            }}
            rows={6}
            className="scroll-thin w-full resize-none rounded-ctl border border-line bg-inset px-2 py-1.5
                       text-[0.8125rem] leading-relaxed text-ink focus:border-line-focus focus:outline-none"
          />
          <div className="mt-1.5 flex gap-1.5">
            <button
              type="button"
              onClick={() => {
                onUpdate(note.id, value.trim() || note.content);
                setEditing(false);
              }}
              className="h-7 flex-1 rounded-ctl bg-accent text-2xs font-semibold text-accent-ink
                         transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200 hover:brightness-110"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => {
                setValue(note.content);
                setEditing(false);
              }}
              className="h-7 rounded-ctl px-2.5 text-2xs text-ink-muted transition-colors
                         duration-200 hover:bg-raised hover:text-ink"
            >
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <div
            className={cn(
              "text-[0.8125rem] leading-relaxed text-ink/90",
              !expanded && long && "line-clamp-5",
            )}
          >
            <Markdown source={note.content} />
          </div>

          <div className="mt-1.5 flex items-center gap-1">
            {long && (
              <button
                type="button"
                onClick={() => setExpanded((e) => !e)}
                className="text-2xs text-accent transition-opacity hover:opacity-80"
              >
                {expanded ? "Show less" : "Show more"}
              </button>
            )}
            <div className="ml-auto flex gap-0.5 opacity-0 transition-opacity duration-200 focus-within:opacity-100 group-hover:opacity-100">
              <NoteAction label="Edit note" onClick={() => setEditing(true)}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M16.5 4.5 19.5 7.5 8 19H5v-3z" />
                </svg>
              </NoteAction>
              <NoteAction label="Delete note" danger onClick={() => onDelete(note.id)}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden>
                  <path d="M4 7h16M9.5 7V5h5v2M6.5 7l.8 12h9.4l.8-12" />
                </svg>
              </NoteAction>
            </div>
          </div>
        </>
      )}
    </motion.li>
  );
}

function NoteAction({
  label,
  danger,
  onClick,
  children,
}: {
  label: string;
  danger?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      data-tip={label}
      onClick={onClick}
      className={cn(
        "grid h-6 w-6 place-items-center rounded transition-colors duration-150",
        danger
          ? "text-ink-faint hover:bg-del/12 hover:text-del"
          : "text-ink-faint hover:bg-raised hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}
