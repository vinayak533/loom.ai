"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import type { Citation, NotebookSource } from "@/lib/learn";
import { SPRING, useMotionOK } from "../Anim";
import { Markdown } from "../Markdown";

export type NotebookTurn = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  error?: boolean;
};

const STARTERS = [
  "Summarise these sources in five bullets",
  "What are the key terms I should know?",
  "What do the sources disagree about?",
];

/**
 * The middle panel: questions answered from this notebook's sources.
 *
 * Deliberately not the agent's `ChatPanel`. That component renders a trace —
 * thinking blocks, tool cards, a spine threading them together — because the
 * agent's work is a sequence of steps worth watching. This is a single
 * retrieval and a single answer, and the interesting metadata is *which
 * passages it used*, so the citation strip is the thing that gets the room the
 * trace spine gets over there.
 *
 * Which is also the answer to why the message actions Chat, Code and Agents
 * share are absent here, rather than an oversight to be tidied up later. Each
 * of them assumes a thread you can rewind: edit re-runs the conversation from
 * a point, regenerate discards everything after a reply, the branch switcher
 * keeps the version you replaced. A notebook question forks nothing and leaves
 * nothing behind it to discard — and this chat is not persisted at all, so a
 * verdict recorded against turn three would be a verdict against something
 * that stops existing when the panel closes.
 */
export function NotebookChat({
  turns,
  sources,
  busy,
  modelName,
  onAsk,
  onSaveNote,
}: {
  turns: NotebookTurn[];
  sources: NotebookSource[];
  busy: boolean;
  modelName: string | null;
  onAsk: (question: string) => void;
  onSaveNote: (content: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const motionOK = useMotionOK();

  const ready = sources.some((s) => s.status === "ready");

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, busy]);

  useEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 22), 160)}px`;
  }, [draft]);

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    onAsk(text);
    setDraft("");
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={scroller} className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[42rem] px-5 py-6">
          {turns.length === 0 && (
            <div className="pb-2">
              <h2 className="text-[1.375rem] font-semibold leading-tight tracking-tight text-ink">
                Ask this notebook
              </h2>
              <p className="mt-2 max-w-[46ch] text-[0.875rem] leading-relaxed text-ink-muted">
                {ready
                  ? "Answers come from your sources only. If the sources don't cover it, you'll be told that instead of guessed at."
                  : "Add a source on the left first — there is nothing to answer from yet."}
              </p>
              {ready && (
                <div className="mt-4 flex flex-wrap gap-1.5">
                  {STARTERS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setDraft(s)}
                      className="rounded-full border border-line bg-elevated px-3 py-1.5 text-2xs
                                 text-ink-muted transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200 hover:border-accent-line
                                 hover:bg-raised hover:text-ink active:scale-[0.98]"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="space-y-5">
            <AnimatePresence initial={false}>
              {turns.map((turn) => (
                <Turn key={turn.id} turn={turn} onSaveNote={onSaveNote} />
              ))}
            </AnimatePresence>

            {busy && (
              <motion.div
                initial={motionOK ? { opacity: 0 } : false}
                animate={{ opacity: 1 }}
                className="flex items-center gap-2.5"
              >
                <motion.span
                  className="sigil h-1 w-1 bg-accent"
                  animate={motionOK ? { opacity: [0.3, 0.9, 0.3] } : {}}
                  transition={{ duration: 2.1, repeat: Infinity, ease: "easeInOut" }}
                  aria-hidden
                />
                <span className="voice-label text-ink-faint">
                  Reading your sources
                </span>
              </motion.div>
            )}
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------- composer */}
      <div className="shrink-0 px-5 pb-4">
        <div
          className={cn(
            "glass rounded-card transition-colors duration-200",
            "focus-within:border-line-focus focus-within:shadow-[0_0_0_3px_rgba(255,255,255,0.055)]",
          )}
        >
          <textarea
            ref={textarea}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={1}
            disabled={!ready}
            placeholder={
              ready ? "Ask anything about these sources…" : "Add a source to start asking"
            }
            className="scroll-thin block w-full resize-none bg-transparent px-3.5 pt-3 font-sans
                       text-[0.875rem] leading-relaxed text-ink placeholder:text-ink-faint
                       focus:outline-none focus-visible:shadow-none disabled:opacity-50"
          />
          <div className="flex items-center gap-2 px-3 pb-2.5 pt-1.5">
            <span className="voice-machine truncate text-ink-faint">
              {modelName ? `Grounded · ${modelName}` : "Grounded in your sources"}
            </span>
            <button
              type="button"
              onClick={submit}
              disabled={!draft.trim() || busy || !ready}
              aria-label="Ask"
              className={cn(
                "ml-auto grid h-8 w-8 place-items-center rounded-ctl transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200",
                draft.trim() && !busy && ready
                  ? "bg-gradient-to-br from-accent to-accent-alt text-accent-ink hover:brightness-110 active:scale-[0.92]"
                  : "cursor-not-allowed bg-raised text-ink-subtle opacity-55",
              )}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M12 20V5m0 0-6 6m6-6 6 6" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Turn({
  turn,
  onSaveNote,
}: {
  turn: NotebookTurn;
  onSaveNote: (content: string) => void;
}) {
  const [saved, setSaved] = useState(false);
  const motionOK = useMotionOK();

  if (turn.role === "user") {
    return (
      <motion.div
        layout={motionOK}
        initial={motionOK ? { opacity: 0, y: 6 } : false}
        animate={{ opacity: 1, y: 0 }}
        transition={motionOK ? SPRING : { duration: 0 }}
        className="flex justify-end"
      >
        <p className="max-w-[85%] rounded-bubble rounded-br-[6px] border border-line bg-raised
                      px-3.5 py-2 text-[0.875rem] leading-relaxed text-ink">
          {turn.content}
        </p>
      </motion.div>
    );
  }

  return (
    <motion.div
      layout={motionOK}
      initial={motionOK ? { opacity: 0, y: 6 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={motionOK ? SPRING : { duration: 0 }}
      className="group"
    >
      {turn.error ? (
        <p className="rounded-ctl border border-del/35 bg-del-bg px-3.5 py-2.5 text-[0.8125rem] text-del">
          {turn.content}
        </p>
      ) : (
        <>
          <div className="text-[0.9375rem]">
            <Markdown source={turn.content} />
          </div>

          {turn.citations && turn.citations.length > 0 && (
            <Citations citations={turn.citations} />
          )}

          <button
            type="button"
            onClick={() => {
              onSaveNote(turn.content);
              setSaved(true);
            }}
            disabled={saved}
            className={cn(
              "mt-2.5 flex h-7 items-center gap-1.5 rounded-ctl border border-line px-2.5",
              "text-2xs transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200",
              saved
                ? "border-accent-line text-accent"
                : "text-ink-faint opacity-0 hover:bg-raised hover:text-ink focus-visible:opacity-100 group-hover:opacity-100",
            )}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              {saved ? <path d="m5 12.5 4.5 4.5L19 7" /> : <path d="M6 4h9l3 3v13H6z M6 4v16" />}
            </svg>
            {saved ? "Saved to notes" : "Save as note"}
          </button>
        </>
      )}
    </motion.div>
  );
}

/**
 * What the answer was built from.
 *
 * Every marker the model actually wrote is resolved back to its passage, so
 * "[2]" in the prose is checkable rather than decorative. Clicking one opens
 * the snippet — the point is to be able to disbelieve the answer cheaply.
 */
function Citations({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const motionOK = useMotionOK();

  return (
    <div className="mt-3 border-l-2 border-accent-line pl-3">
      <div className="flex flex-wrap gap-1.5">
        {citations.map((c) => (
          <button
            key={c.marker}
            type="button"
            onClick={() => setOpen(open === c.marker ? null : c.marker)}
            aria-expanded={open === c.marker}
            className={cn(
              "flex max-w-[220px] items-center gap-1.5 rounded-ctl border px-2 py-1 text-2xs",
              "transition-colors duration-200",
              open === c.marker
                ? "border-accent-line bg-accent-soft text-ink"
                : "border-line bg-elevated text-ink-muted hover:border-accent-line hover:text-ink",
            )}
          >
            <span className="voice-machine text-accent">[{c.marker}]</span>
            <span className="truncate">{c.source_title}</span>
          </button>
        ))}
      </div>
      <AnimatePresence initial={false}>
        {open !== null && (
          <motion.blockquote
            initial={motionOK ? { height: 0, opacity: 0 } : false}
            animate={{ height: "auto", opacity: 1 }}
            exit={motionOK ? { height: 0, opacity: 0 } : { opacity: 0 }}
            transition={motionOK ? SPRING : { duration: 0 }}
            className="overflow-hidden"
          >
            <p className="voice-reason mt-2 whitespace-pre-wrap rounded-ctl bg-inset px-2.5 py-2">
              {citations.find((c) => c.marker === open)?.snippet}…
            </p>
          </motion.blockquote>
        )}
      </AnimatePresence>
    </div>
  );
}
