"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import type { Lesson, Progress, QuizQuestion } from "@/lib/learn";
import { SPRING, SPRING_SNAP, useMotionOK } from "../Anim";
import { Markdown } from "../Markdown";

/**
 * Structured mode.
 *
 * The same notebook, the same sources, arranged as a curriculum instead of a
 * conversation: a numbered spine of sections on the left, the section's
 * generated explanation and check-in on the right. It is a *view* of the
 * notebook rather than a separate object — which is why the toggle back to
 * free-form Q&A sits in the workspace header and nothing here owns any state
 * the chat side does not also see.
 */
export function CourseView({
  lessons,
  progress,
  busy,
  canGenerate,
  onGenerate,
  onComplete,
}: {
  lessons: Lesson[];
  progress: Progress[];
  busy: boolean;
  canGenerate: boolean;
  onGenerate: () => void;
  onComplete: (sectionId: string, completed: boolean) => void;
}) {
  const [activeId, setActiveId] = useState<string | null>(lessons[0]?.id ?? null);
  const motionOK = useMotionOK();

  const done = useMemo(
    () => new Set(progress.filter((p) => p.completed).map((p) => p.section_id)),
    [progress],
  );

  const active =
    lessons.find((l) => l.id === activeId) ?? lessons[0] ?? null;
  const percent = lessons.length
    ? Math.round((lessons.filter((l) => done.has(l.id)).length / lessons.length) * 100)
    : 0;

  if (lessons.length === 0) {
    return (
      <div className="grid h-full place-items-center p-8">
        <div className="max-w-[46ch] text-center">
          <span className="mx-auto mb-4 grid h-11 w-11 place-items-center rounded-card bg-accent-soft text-accent">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M4 6h7v13H4zM13 6h7v13h-7z" />
              <path d="M6.5 9.5h2M15.5 9.5h2" />
            </svg>
          </span>
          <h2 className="text-[1.125rem] font-semibold text-ink">Turn this into a course</h2>
          <p className="mt-2 text-[0.875rem] leading-relaxed text-ink-muted">
            {canGenerate
              ? "A sequential set of sections built from your sources, each with a short check-in to test what stuck."
              : "Add at least one readable source first — the curriculum is generated from what is in this notebook."}
          </p>
          <button
            type="button"
            onClick={onGenerate}
            disabled={busy || !canGenerate}
            className="mt-5 inline-flex h-9 items-center gap-2 rounded-ctl bg-gradient-to-br
                       from-accent to-accent-alt px-4 text-[0.8125rem] font-semibold text-accent-ink
                       transition-all duration-200 hover:brightness-110 active:scale-[0.97]
                       disabled:opacity-40"
          >
            {busy ? (
              <>
                <motion.span
                  className="sigil h-2 w-2 bg-accent-ink"
                  animate={motionOK ? { opacity: [1, 0.3, 1] } : {}}
                  transition={{ duration: 1.1, repeat: Infinity }}
                  aria-hidden
                />
                Reading your sources…
              </>
            ) : (
              "Generate course"
            )}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0">
      {/* ------------------------------------------------------ curriculum */}
      <aside className="hidden w-[268px] shrink-0 flex-col border-r border-line md:flex">
        <div className="shrink-0 border-b border-line px-4 py-3.5">
          <div className="flex items-baseline justify-between">
            <h2 className="voice-label text-ink-muted">Curriculum</h2>
            <span className="voice-machine text-ink">{percent}%</span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-inset">
            <motion.div
              className="h-full rounded-full bg-gradient-to-r from-accent to-accent-alt"
              initial={false}
              animate={{ width: `${percent}%` }}
              transition={motionOK ? SPRING : { duration: 0 }}
            />
          </div>
        </div>

        <ol className="scroll-thin min-h-0 flex-1 overflow-y-auto p-2">
          {lessons.map((lesson, i) => {
            const on = lesson.id === active?.id;
            const complete = done.has(lesson.id);
            return (
              <li key={lesson.id}>
                <button
                  type="button"
                  onClick={() => setActiveId(lesson.id)}
                  className={cn(
                    "flex w-full items-start gap-2.5 rounded-ctl px-2.5 py-2 text-left",
                    "transition-colors duration-200",
                    on ? "bg-accent-dim text-ink" : "text-ink-muted hover:bg-elevated hover:text-ink",
                  )}
                >
                  <span
                    className={cn(
                      "mt-px grid h-5 w-5 shrink-0 place-items-center rounded-full border text-[0.625rem]",
                      "font-mono transition-colors duration-200",
                      complete
                        ? "border-accent bg-accent text-accent-ink"
                        : on
                          ? "border-accent-line text-accent"
                          : "border-line text-ink-faint",
                    )}
                  >
                    {complete ? "✓" : i + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[0.8125rem] font-medium leading-snug">
                      {lesson.section_title}
                    </span>
                    {lesson.summary && (
                      <span className="mt-0.5 block truncate text-2xs text-ink-faint">
                        {lesson.summary}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>

        <button
          type="button"
          onClick={onGenerate}
          disabled={busy}
          className="shrink-0 border-t border-line px-4 py-2.5 text-left text-2xs text-ink-faint
                     transition-colors hover:text-ink-muted disabled:opacity-50"
        >
          {busy ? "Regenerating…" : "↻ Regenerate course"}
        </button>
      </aside>

      {/* --------------------------------------------------------- section */}
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        {active && (
          <article className="mx-auto w-full max-w-[44rem] px-6 py-7">
            {/* The mobile picker: the spine is hidden below md, but you still
                have to be able to move between sections. */}
            <select
              value={active.id}
              onChange={(e) => setActiveId(e.target.value)}
              aria-label="Section"
              className="mb-5 h-9 touch:h-11 w-full rounded-ctl border border-line bg-elevated px-2.5
                         text-[0.8125rem] text-ink focus:border-line-focus focus:outline-none md:hidden"
            >
              {lessons.map((l, i) => (
                <option key={l.id} value={l.id}>
                  {i + 1}. {l.section_title}
                </option>
              ))}
            </select>

            <p className="voice-label mb-2 text-accent">
              Section {active.section_order + 1} of {lessons.length}
            </p>
            <h1 className="text-[1.5rem] font-semibold leading-tight tracking-tight text-ink">
              {active.section_title}
            </h1>
            {active.summary && (
              <p className="mt-2 text-[0.9375rem] leading-relaxed text-ink-muted">
                {active.summary}
              </p>
            )}

            <div className="mt-5">
              <Markdown source={active.content || "_No content was generated for this section._"} />
            </div>

            {active.quiz_data && active.quiz_data.length > 0 && (
              <Quiz key={active.id} questions={active.quiz_data} />
            )}

            <div className="mt-7 flex items-center gap-2 border-t border-line pt-4">
              <button
                type="button"
                onClick={() => onComplete(active.id, !done.has(active.id))}
                className={cn(
                  "flex h-9 items-center gap-2 rounded-ctl px-3.5 text-[0.8125rem] font-medium",
                  "transition-all duration-200 active:scale-[0.98]",
                  done.has(active.id)
                    ? "border border-accent-line bg-accent-soft text-accent"
                    : "border border-line bg-elevated text-ink hover:border-accent-line hover:bg-raised",
                )}
              >
                <span className="text-[0.75rem]">{done.has(active.id) ? "✓" : "○"}</span>
                {done.has(active.id) ? "Completed" : "Mark complete"}
              </button>

              {lessons[active.section_order + 1] && (
                <button
                  type="button"
                  onClick={() => setActiveId(lessons[active.section_order + 1].id)}
                  className="ml-auto flex h-9 items-center gap-1.5 rounded-ctl bg-accent px-3.5
                             text-[0.8125rem] font-semibold text-accent-ink transition-all
                             duration-200 hover:brightness-110 active:scale-[0.98]"
                >
                  Next section
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path d="m9 6 6 6-6 6" />
                  </svg>
                </button>
              )}
            </div>
          </article>
        )}
      </div>
    </div>
  );
}

/**
 * The check-in.
 *
 * Answers are revealed one question at a time and never retracted — the point
 * is a moment of retrieval practice, not a score. The explanation appears
 * whether you were right or wrong, because "why" is the part that teaches.
 */
function Quiz({ questions }: { questions: QuizQuestion[] }) {
  const [picked, setPicked] = useState<Record<number, number>>({});
  const motionOK = useMotionOK();

  return (
    <section className="mt-8 rounded-card border border-line bg-elevated p-4">
      <div className="mb-3.5 flex items-center gap-2">
        <span className="sigil h-2 w-2 bg-accent-alt" aria-hidden />
        <h2 className="voice-label text-ink-muted">Check yourself</h2>
        <span className="voice-machine ml-auto text-ink-faint">
          {Object.keys(picked).length}/{questions.length}
        </span>
      </div>

      <ol className="space-y-5">
        {questions.map((q, qi) => {
          const answered = picked[qi] !== undefined;
          return (
            <li key={qi}>
              <p className="mb-2 text-[0.875rem] font-medium leading-snug text-ink">
                {qi + 1}. {q.question}
              </p>
              <div className="space-y-1">
                {q.options.map((option, oi) => {
                  const chosen = picked[qi] === oi;
                  const correct = oi === q.answer;
                  return (
                    <button
                      key={oi}
                      type="button"
                      disabled={answered}
                      onClick={() => setPicked((p) => ({ ...p, [qi]: oi }))}
                      className={cn(
                        "flex w-full items-center gap-2.5 rounded-ctl border px-3 py-2 text-left",
                        "text-[0.8125rem] transition-all duration-200",
                        !answered &&
                          "border-line bg-inset text-ink-muted hover:border-accent-line hover:text-ink",
                        answered && correct && "border-add/40 bg-add-bg text-ink",
                        answered && chosen && !correct && "border-del/40 bg-del-bg text-ink",
                        answered && !chosen && !correct && "border-line text-ink-faint opacity-60",
                      )}
                    >
                      <span
                        className={cn(
                          "grid h-5 w-5 shrink-0 place-items-center rounded-full border font-mono text-[0.625rem]",
                          answered && correct
                            ? "border-add text-add"
                            : answered && chosen
                              ? "border-del text-del"
                              : "border-line",
                        )}
                      >
                        {answered && correct ? "✓" : answered && chosen ? "✕" : String.fromCharCode(65 + oi)}
                      </span>
                      <span className="min-w-0">{option}</span>
                    </button>
                  );
                })}
              </div>

              <AnimatePresence initial={false}>
                {answered && q.explanation && (
                  <motion.p
                    initial={motionOK ? { height: 0, opacity: 0 } : false}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={motionOK ? SPRING_SNAP : { duration: 0 }}
                    className="overflow-hidden"
                  >
                    <span className="mt-2 block border-l-2 border-accent-line pl-3 text-2xs
                                     leading-relaxed text-ink-muted">
                      {q.explanation}
                    </span>
                  </motion.p>
                )}
              </AnimatePresence>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
