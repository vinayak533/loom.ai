"use client";

import { useState } from "react";
import { cn } from "@/lib/cn";
import { Meter } from "../Meter";
import type { ExamResult, Recommendation, TopicScore } from "@/lib/courses";
import { ResourceLink } from "./CoursePage";

type Tab = "summary" | "weak" | "review";

/**
 * The results screen.
 *
 * The brief's rule, and the right one: **do not simply show the score.** So the
 * score is one number at the top and everything under it is diagnostic — which
 * topics held up, which did not, which chapter each weak topic points back to,
 * and what to do next. The three tabs are the three questions someone actually
 * has after an exam: how did I do, what should I fix, and what did I get wrong.
 */
export function ExamResults({
  result,
  attemptNumber,
  onRetake,
  onReviewChapter,
  onContinue,
  onBackToCourse,
}: {
  result: ExamResult;
  attemptNumber: number;
  onRetake: () => void;
  onReviewChapter: (chapterId: string) => void;
  onContinue: () => void;
  onBackToCourse: () => void;
}) {
  const [tab, setTab] = useState<Tab>(
    result.recommendations.length > 0 ? "weak" : "summary",
  );

  const tone =
    result.score >= 85 ? "add" : result.score >= result.pass_score ? "accent" : "warn";

  return (
    <div className="scroll-thin h-full min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-[46rem] px-6 pb-20 pt-6 sm:px-8">
        {/* --------------------------------------------------------- score */}
        <section className="rounded-card border border-line bg-elevated p-6 text-center">
          <p className="voice-label text-ink-muted">Assessment complete</p>

          <p
            className={cn(
              "mt-3 font-mono text-[3.5rem] font-medium leading-none tabular-nums",
              tone === "add" && "text-add",
              tone === "accent" && "text-accent",
              tone === "warn" && "text-warn",
            )}
          >
            {result.score}%
          </p>
          <p className="mt-2 text-[0.9375rem] text-ink">
            {result.correct_count} / {result.total_count} correct
          </p>

          <Meter
            value={result.score}
            tone={tone === "add" ? "good" : tone === "warn" ? "warn" : "accent"}
            label="Score"
            className="mx-auto mt-4 max-w-[280px]"
          />

          <p className="voice-machine mt-3 text-ink-faint">
            {result.passed
              ? `Passed — the mark is ${result.pass_score}%`
              : `${result.pass_score}% needed to pass`}
            {attemptNumber > 1 && ` · attempt ${attemptNumber}`}
          </p>

          <div className="mt-5 flex flex-wrap justify-center gap-2">
            <button
              type="button"
              onClick={onRetake}
              className="h-9 rounded-ctl border border-line bg-inset px-4 text-[0.8125rem]
                         font-semibold text-ink transition-colors duration-200
                         hover:border-accent-line hover:bg-raised"
            >
              Retake assessment
            </button>
            <button
              type="button"
              onClick={onContinue}
              className="h-9 rounded-ctl bg-gradient-to-br from-accent to-accent-alt px-4
                         text-[0.8125rem] font-semibold text-accent-ink transition-[color,background-color,border-color,box-shadow,opacity,transform,filter]
                         duration-200 hover:brightness-110 active:scale-[0.98]"
            >
              Continue course
            </button>
          </div>
        </section>

        {/* ---------------------------------------------------------- tabs */}
        <div role="tablist" aria-label="Result detail" className="mt-5 flex gap-1">
          {(
            [
              ["weak", `Weak areas${result.weak_topics.length ? ` (${result.weak_topics.length})` : ""}`],
              ["summary", "By topic"],
              ["review", "Review answers"],
            ] as Array<[Tab, string]>
          ).map(([key, label]) => (
            <button
              key={key}
              role="tab"
              type="button"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={cn(
                "h-8 touch:h-11 rounded-full px-3.5 text-[0.8125rem] font-medium transition-colors duration-200",
                tab === key
                  ? "bg-accent text-accent-ink"
                  : "text-ink-muted hover:bg-elevated hover:text-ink",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="mt-4">
          {tab === "weak" && (
            <WeakAreas
              recommendations={result.recommendations}
              strong={result.strong_topics}
              onReviewChapter={onReviewChapter}
              onRetake={onRetake}
            />
          )}
          {tab === "summary" && <TopicBreakdown topics={result.topic_scores} />}
          {tab === "review" && <AnswerReview result={result} />}
        </div>

        <button
          type="button"
          onClick={onBackToCourse}
          className="mt-6 w-full rounded-ctl border border-line bg-elevated py-2.5 text-[0.8125rem]
                     font-medium text-ink-muted transition-colors duration-200
                     hover:bg-raised hover:text-ink"
        >
          Back to course
        </button>
      </div>
    </div>
  );
}

/**
 * The point of the whole screen.
 *
 * Every question carries the topic and chapter it came from, so a wrong answer
 * resolves to a specific chapter and a specific set of resources rather than to
 * a number. Each weak area is therefore a small, closed loop: what you missed,
 * where it was taught, what to read, what to practise, and the button back into
 * the exam.
 */
function WeakAreas({
  recommendations,
  strong,
  onReviewChapter,
  onRetake,
}: {
  recommendations: Recommendation[];
  strong: string[];
  onReviewChapter: (chapterId: string) => void;
  onRetake: () => void;
}) {
  if (recommendations.length === 0) {
    return (
      <div className="rounded-card border border-add/30 bg-add-bg p-5 text-center">
        <p className="text-[0.9375rem] font-medium text-ink">
          No weak areas — every topic scored 70% or better.
        </p>
        {strong.length > 0 && (
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-ink-muted">
            Strong across {strong.join(", ")}.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {strong.length > 0 && (
        <div className="rounded-card border border-add/25 bg-add-bg p-3.5">
          <p className="voice-label mb-1.5 text-add">Holding up well</p>
          <p className="text-[0.8125rem] leading-relaxed text-ink-muted">
            {strong.join(" · ")}
          </p>
        </div>
      )}

      {recommendations.map((rec, index) => (
        <section
          key={rec.topic}
          className="rounded-card border border-warn/30 bg-[rgba(240,181,74,0.05)] p-4"
        >
          <div className="flex items-baseline gap-2.5">
            <span className="font-mono text-[0.8125rem] text-ink-faint">{index + 1}.</span>
            <h3 className="text-[1rem] font-semibold text-ink">{rec.topic}</h3>
            <span className="voice-machine ml-auto text-warn">
              {rec.score}% ({rec.correct}/{rec.total})
            </span>
          </div>

          <Meter value={rec.score} tone="warn" size="sm" label="Score" className="mt-2.5" />

          <p className="mt-3 text-[0.875rem] leading-relaxed text-ink">{rec.advice}</p>

          {rec.chapters.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {rec.chapters.map((chapter) => (
                <button
                  key={chapter.id}
                  type="button"
                  onClick={() => onReviewChapter(chapter.id)}
                  className="flex h-8 items-center gap-1.5 rounded-ctl border border-accent-line
                             bg-accent-soft px-3 text-[0.8125rem] font-medium text-accent
                             transition-colors duration-200 hover:bg-raised"
                >
                  Review Chapter {chapter.order + 1} — {chapter.title}
                </button>
              ))}
            </div>
          )}

          {rec.resources.length > 0 && (
            <div className="mt-3">
              <p className="voice-label mb-1.5 text-ink-faint">Read</p>
              <ul className="space-y-0.5">
                {rec.resources.map((resource) => (
                  <li key={resource.url}>
                    <ResourceLink resource={resource} />
                  </li>
                ))}
              </ul>
            </div>
          )}

          {rec.practice.length > 0 && (
            <div className="mt-3">
              <p className="voice-label mb-1.5 text-ink-faint">Practise</p>
              <ul className="space-y-1">
                {rec.practice.map((prompt) => (
                  <li key={prompt} className="flex gap-2">
                    <span className="mt-[0.5em] h-1 w-1 shrink-0 rounded-full bg-ink-faint" aria-hidden />
                    <span className="text-[0.8125rem] leading-relaxed text-ink-muted">
                      {prompt}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      ))}

      <button
        type="button"
        onClick={onRetake}
        className="w-full rounded-ctl bg-gradient-to-br from-accent to-accent-alt py-2.5
                   text-[0.8125rem] font-semibold text-accent-ink transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200
                   hover:brightness-110 active:scale-[0.99]"
      >
        Reviewed it — retake the assessment
      </button>
    </div>
  );
}

function TopicBreakdown({ topics }: { topics: TopicScore[] }) {
  return (
    <ul className="overflow-hidden rounded-card border border-line bg-elevated">
      {topics.map((topic) => {
        const strong = topic.score >= 70;
        return (
          <li
            key={topic.topic}
            className="flex items-center gap-3 border-b border-line px-4 py-3 last:border-b-0"
          >
            <span className={cn("shrink-0 text-[0.875rem]", strong ? "text-add" : "text-warn")}>
              {strong ? "✓" : "⚠"}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[0.875rem] font-medium text-ink">{topic.topic}</span>
              <Meter
                value={topic.score}
                tone={strong ? "good" : "warn"}
                size="sm"
                label={`${topic.topic} score`}
                className="mt-1"
              />
            </span>
            <span className="voice-machine shrink-0 text-ink-muted">
              {topic.correct}/{topic.total} · {topic.score}%
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The answer key, released only after submission.
 *
 * Explanations are shown for right answers as well as wrong ones — "why" is the
 * part that teaches, and a correct guess is worth converting into knowledge.
 */
function AnswerReview({ result }: { result: ExamResult }) {
  return (
    <ol className="space-y-3">
      {result.questions.map((question, index) => (
        <li
          key={question.id}
          className={cn(
            "rounded-card border p-4",
            question.correct ? "border-line bg-elevated" : "border-del/30 bg-del-bg",
          )}
        >
          <div className="mb-2 flex items-baseline gap-2">
            <span className="font-mono text-[0.75rem] text-ink-faint">{index + 1}.</span>
            <span className={cn("text-[0.75rem]", question.correct ? "text-add" : "text-del")}>
              {question.correct ? "✓ Correct" : "✕ Incorrect"}
            </span>
            <span className="chip ml-auto border-line-strong">{question.topic}</span>
          </div>

          <p className="text-[0.875rem] font-medium leading-snug text-ink">{question.prompt}</p>

          <div className="mt-2.5 space-y-1">
            {question.options.map((option, optionIndex) => {
              const isAnswer = optionIndex === question.answer;
              const isChosen = optionIndex === question.chosen;
              if (!isAnswer && !isChosen) return null;
              return (
                <p
                  key={optionIndex}
                  className={cn(
                    "flex gap-2 rounded-ctl px-2.5 py-1.5 text-[0.8125rem] leading-relaxed",
                    isAnswer ? "bg-add-bg text-ink" : "bg-inset text-ink-muted",
                  )}
                >
                  <span className="shrink-0 font-mono text-2xs">
                    {isAnswer ? "✓" : "✕"}
                  </span>
                  <span>
                    {option}
                    <span className="ml-1.5 text-2xs text-ink-faint">
                      {isAnswer ? "(correct answer)" : "(you chose this)"}
                    </span>
                  </span>
                </p>
              );
            })}
            {question.chosen === null && (
              <p className="rounded-ctl bg-inset px-2.5 py-1.5 text-[0.8125rem] text-ink-faint">
                Not answered
              </p>
            )}
          </div>

          {question.explanation && (
            <p className="mt-2.5 border-l-2 border-accent-line pl-3 text-[0.8125rem] leading-relaxed text-ink-muted">
              {question.explanation}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}
