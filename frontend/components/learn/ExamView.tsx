"use client";

import { useCallback, useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import type { ExamPaper } from "@/lib/courses";

const TYPE_LABEL: Record<string, string> = {
  mcq: "Multiple choice",
  truefalse: "True / False",
  code: "Code",
  scenario: "Scenario",
};

/**
 * The assessment.
 *
 * One question at a time, on purpose: a scrollable wall of ten questions turns
 * an assessment into a form, and people answer forms by pattern-matching. A
 * single question with its own screen is read.
 *
 * Answers live in this component's state and are submitted in one request at
 * the end. Nothing is graded in the browser — the paper arrives without its
 * answer key, which is what stops the exam from being solvable with devtools.
 */
export function ExamView({
  paper,
  submitting,
  onExit,
  onSubmit,
}: {
  paper: ExamPaper;
  submitting: boolean;
  onExit: () => void;
  onSubmit: (answers: Record<string, number>) => void;
}) {
  const { exam, course } = paper;
  const questions = exam.questions;

  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [confirming, setConfirming] = useState(false);

  const question = questions[index];
  const answered = Object.keys(answers).length;
  const unanswered = useMemo(
    () => questions.filter((q) => answers[q.id] === undefined).length,
    [questions, answers],
  );

  const choose = useCallback(
    (optionIndex: number) => {
      setAnswers((prev) => ({ ...prev, [question.id]: optionIndex }));
    },
    [question?.id],
  );

  const go = useCallback(
    (delta: number) => {
      setIndex((i) => Math.min(questions.length - 1, Math.max(0, i + delta)));
    },
    [questions.length],
  );

  if (!question) {
    return (
      <div className="grid h-full place-items-center p-8 text-center">
        <p className="text-[0.875rem] text-ink-muted">This assessment has no questions.</p>
      </div>
    );
  }

  return (
    <div className="scroll-thin h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[44rem] flex-col px-6 pb-16 pt-4 sm:px-8">
        {/* ---------------------------------------------------------- head */}
        <div className="mb-5 flex items-center gap-2">
          <button
            type="button"
            onClick={onExit}
            className="flex h-8 items-center gap-1.5 rounded-ctl px-2 text-2xs text-ink-muted
                       transition-colors duration-200 hover:bg-elevated hover:text-ink"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="m14 6-6 6 6 6" />
            </svg>
            Leave assessment
          </button>
          <span className="voice-machine ml-auto text-ink-faint">
            {answered}/{questions.length} answered
          </span>
        </div>

        <header className="mb-5">
          <p className="voice-label text-accent-alt">{course.short_title} assessment</p>
          <h1 className="mt-1.5 text-[1.375rem] font-semibold leading-tight tracking-tight text-ink">
            {exam.title}
          </h1>
          <p className="voice-machine mt-1.5 text-ink-faint">
            {questions.length} questions · pass mark {exam.pass_score}%
            {exam.attempt_count > 0 && ` · attempt ${exam.attempt_count + 1}`}
          </p>
        </header>

        {/* The map: position, what is answered, and a way to jump. */}
        <div className="mb-6 flex flex-wrap gap-1.5">
          {questions.map((q, i) => {
            const isAnswered = answers[q.id] !== undefined;
            return (
              <button
                key={q.id}
                type="button"
                onClick={() => setIndex(i)}
                aria-label={`Question ${i + 1}${isAnswered ? ", answered" : ""}`}
                aria-current={i === index}
                className={cn(
                  "h-7 w-7 touch:h-11 touch:w-11 rounded-ctl border font-mono text-[0.6875rem] transition-colors duration-150",
                  i === index
                    ? "border-accent bg-accent text-accent-ink"
                    : isAnswered
                      ? "border-accent-line bg-accent-soft text-accent"
                      : "border-line text-ink-faint hover:border-line-strong hover:text-ink",
                )}
              >
                {i + 1}
              </button>
            );
          })}
        </div>

        {/* ------------------------------------------------------- question */}
        <section className="rounded-card border border-line bg-elevated p-5">
          <div className="mb-3 flex items-center gap-2">
            <span className="voice-machine text-ink-faint">
              Question {index + 1} / {questions.length}
            </span>
            <span className="chip ml-auto border-line-strong">
              {TYPE_LABEL[question.type] ?? question.type}
            </span>
            <span className="chip border-line-strong">{question.topic}</span>
          </div>

          <p className="text-[1.0625rem] font-medium leading-snug text-ink">
            {question.prompt}
          </p>

          {question.code && (
            <pre
              className="scroll-thin mt-3.5 overflow-x-auto rounded-card border border-line
                         bg-inset p-3.5 font-mono text-[0.8125rem] leading-[1.65]"
            >
              <div className="voice-label mb-2 select-none">{question.language}</div>
              <code className="text-ink/85">{question.code}</code>
            </pre>
          )}

          <div className="mt-4 space-y-2">
            {question.options.map((option, optionIndex) => {
              const chosen = answers[question.id] === optionIndex;
              return (
                <button
                  key={optionIndex}
                  type="button"
                  onClick={() => choose(optionIndex)}
                  aria-pressed={chosen}
                  className={cn(
                    "flex w-full items-start gap-3 rounded-ctl border px-3.5 py-2.5 text-left",
                    "text-[0.875rem] leading-relaxed transition-colors duration-150",
                    chosen
                      ? "border-accent bg-accent-soft text-ink"
                      : "border-line bg-inset text-ink-muted hover:border-accent-line hover:text-ink",
                  )}
                >
                  <span
                    className={cn(
                      "mt-px grid h-5 w-5 shrink-0 place-items-center rounded-full border font-mono text-2xs",
                      chosen ? "border-accent bg-accent text-accent-ink" : "border-line",
                    )}
                    aria-hidden
                  >
                    {String.fromCharCode(65 + optionIndex)}
                  </span>
                  <span className="min-w-0">{option}</span>
                </button>
              );
            })}
          </div>
        </section>

        {/* --------------------------------------------------------- nav */}
        <div className="mt-4 flex items-center gap-2">
          <button
            type="button"
            disabled={index === 0}
            onClick={() => go(-1)}
            className="flex h-9 items-center gap-1.5 rounded-ctl border border-line bg-elevated
                       px-3.5 text-[0.8125rem] font-medium text-ink transition-colors duration-200
                       hover:bg-raised disabled:opacity-35 disabled:hover:bg-elevated"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="m14 6-6 6 6 6" />
            </svg>
            Previous
          </button>

          {index < questions.length - 1 ? (
            <button
              type="button"
              onClick={() => go(1)}
              className="ml-auto flex h-9 items-center gap-1.5 rounded-ctl bg-raised px-3.5
                         text-[0.8125rem] font-medium text-ink transition-colors duration-200
                         hover:bg-elevated"
            >
              Next
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="m9 6 6 6-6 6" />
              </svg>
            </button>
          ) : (
            <button
              type="button"
              disabled={submitting}
              onClick={() => (unanswered > 0 ? setConfirming(true) : onSubmit(answers))}
              className="ml-auto flex h-9 items-center gap-1.5 rounded-ctl bg-gradient-to-br
                         from-accent to-accent-alt px-4 text-[0.8125rem] font-semibold
                         text-accent-ink transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200 hover:brightness-110
                         active:scale-[0.98] disabled:opacity-60"
            >
              {submitting ? "Scoring…" : "Submit assessment"}
            </button>
          )}
        </div>

        {index === questions.length - 1 && unanswered > 0 && !confirming && (
          <p className="mt-3 text-center text-2xs text-warn">
            {unanswered} question{unanswered === 1 ? "" : "s"} still unanswered.
          </p>
        )}

        {/* Unanswered questions are scored as wrong, so this asks rather than
            silently submitting a blank. */}
        {confirming && (
          <div className="mt-4 rounded-card border border-warn/30 bg-[rgba(240,181,74,0.07)] p-4">
            <p className="text-[0.875rem] text-ink">
              {unanswered} question{unanswered === 1 ? " is" : "s are"} unanswered and will be
              marked incorrect. Submit anyway?
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                disabled={submitting}
                onClick={() => onSubmit(answers)}
                className="h-9 rounded-ctl bg-accent px-3.5 text-[0.8125rem] font-semibold
                           text-accent-ink transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] hover:brightness-110 disabled:opacity-60"
              >
                {submitting ? "Scoring…" : "Submit anyway"}
              </button>
              <button
                type="button"
                onClick={() => {
                  const first = questions.findIndex((q) => answers[q.id] === undefined);
                  if (first >= 0) setIndex(first);
                  setConfirming(false);
                }}
                className="h-9 rounded-ctl border border-line bg-elevated px-3.5 text-[0.8125rem]
                           font-medium text-ink transition-colors hover:bg-raised"
              >
                Go to first unanswered
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
