"use client";

import { memo } from "react";
import { cn } from "@/lib/cn";
import { Meter } from "../Meter";
import {
  courseHues,
  duration,
  RESOURCE_LABEL,
  type ChapterRow,
  type CourseDetail,
  type ExamState,
  type Resource,
} from "@/lib/courses";
import { DifficultyChip } from "./CourseCatalog";

/**
 * One course.
 *
 * The spine of the whole section: a learner should be able to answer "where am
 * I and what is next" without reading anything. So the chapter list carries
 * three states and only three — done (✓), next (→), not yet (○) — and the one
 * chapter marked `next` is the same chapter the Continue button opens. Two
 * different answers to "what now" would be worse than none.
 */
export function CoursePage({
  detail,
  onBack,
  onOpenChapter,
  onOpenExam,
}: {
  detail: CourseDetail;
  onBack: () => void;
  onOpenChapter: (chapterId: string) => void;
  onOpenExam: (examId: string) => void;
}) {
  const { course, chapters, exams, progress } = detail;
  const [h1, h2] = courseHues(course.id);
  const nextId = progress.next_chapter_id;

  // Exams are shown inline, after the last chapter they cover — a milestone in
  // the curriculum rather than a separate tab you have to know about.
  const examAfter = new Map<string, ExamState>();
  for (const exam of exams) {
    const last = exam.chapter_ids[exam.chapter_ids.length - 1];
    if (last) examAfter.set(last, exam);
  }

  return (
    <div className="scroll-thin h-full min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-[1040px] px-6 pb-16 pt-4 sm:px-9">
        <button
          type="button"
          onClick={onBack}
          className="mb-4 flex h-8 items-center gap-1.5 rounded-ctl px-2 text-2xs text-ink-muted
                     transition-colors duration-200 hover:bg-elevated hover:text-ink"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="m14 6-6 6 6 6" />
          </svg>
          All courses
        </button>

        {/* --------------------------------------------------------- header */}
        <header className="overflow-hidden rounded-card border border-line bg-elevated">
          <div
            className="relative h-[108px]"
            style={{
              background: `linear-gradient(135deg, hsl(${h1} 62% 22%) 0%, hsl(${h2} 58% 13%) 100%)`,
            }}
          >
            <span
              aria-hidden
              className="absolute inset-0 opacity-[0.13]"
              style={{
                backgroundImage:
                  "radial-gradient(circle at 1px 1px, rgba(255,255,255,0.9) 1px, transparent 0)",
                backgroundSize: "14px 14px",
              }}
            />
            <span className="relative flex h-full items-end px-5 pb-3.5">
              <span
                className="font-mono text-[1.75rem] font-medium tracking-tight text-white/90"
                style={{ textShadow: "0 1px 14px rgba(0,0,0,0.45)" }}
              >
                {course.short_title}
              </span>
            </span>
          </div>

          <div className="p-5">
            <h1 className="text-[1.5rem] font-semibold leading-tight tracking-tight text-ink">
              {course.title}
            </h1>
            <p className="mt-1.5 text-[0.9375rem] leading-relaxed text-ink-muted">
              {course.subtitle}
            </p>

            <div className="mt-3.5 flex flex-wrap items-center gap-1.5">
              <DifficultyChip difficulty={course.difficulty} />
              <span className="chip">{course.chapter_count} chapters</span>
              <span className="chip">{course.exam_count} assessments</span>
              <span className="chip">{duration(course.minutes)}</span>
              {course.tags.map((tag) => (
                <span key={tag} className="chip border-line-strong">
                  {tag}
                </span>
              ))}
            </div>

            <p className="mt-4 max-w-[68ch] text-[0.875rem] leading-relaxed text-ink-muted">
              {course.description}
            </p>

            {/* ---------------------------------------------------- progress */}
            <div className="mt-5 rounded-ctl border border-line bg-inset p-3.5">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="voice-label text-ink-muted">Your progress</span>
                <span className="voice-machine text-ink">
                  {progress.completed_count}/{progress.chapter_count} · {progress.percent}%
                </span>
              </div>
              <Meter value={progress.percent} label="Your progress" />
              <button
                type="button"
                onClick={() => onOpenChapter(nextId)}
                className="mt-3.5 flex h-9 items-center gap-1.5 rounded-ctl bg-gradient-to-br
                           from-accent to-accent-alt px-4 text-[0.8125rem] font-semibold
                           text-accent-ink transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200 hover:brightness-110
                           active:scale-[0.98]"
              >
                {progress.completed
                  ? "Review course"
                  : progress.started
                    ? "Continue learning"
                    : "Start course"}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="m9 6 6 6-6 6" />
                </svg>
              </button>
            </div>
          </div>
        </header>

        {/* ----------------------------------------------------- objectives */}
        {course.objectives.length > 0 && (
          <section className="mt-5 rounded-card border border-line bg-elevated p-5">
            <h2 className="voice-label mb-3 text-ink-muted">What you will be able to do</h2>
            <ul className="grid gap-2 sm:grid-cols-2">
              {course.objectives.map((objective) => (
                <li key={objective} className="flex gap-2.5">
                  <span className="mt-[0.45em] h-1.5 w-1.5 shrink-0 rotate-45 bg-accent" aria-hidden />
                  <span className="text-[0.875rem] leading-relaxed text-ink-muted">
                    {objective}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* -------------------------------------------------------- chapters */}
        <section className="mt-5">
          <h2 className="voice-label mb-3 px-1 text-ink-muted">Curriculum</h2>
          <ol className="overflow-hidden rounded-card border border-line bg-elevated">
            {chapters.map((chapter, index) => (
              <li key={chapter.id}>
                <ChapterRowItem
                  chapter={chapter}
                  index={index}
                  isNext={chapter.id === nextId && !chapter.completed}
                  onOpen={onOpenChapter}
                />
                {examAfter.has(chapter.id) && (
                  <ExamRow exam={examAfter.get(chapter.id)!} onOpen={onOpenExam} />
                )}
              </li>
            ))}
          </ol>
        </section>

        {/* ------------------------------------------------------- resources */}
        {course.resources.length > 0 && (
          <section className="mt-5 rounded-card border border-line bg-elevated p-5">
            <h2 className="voice-label mb-3 text-ink-muted">Course resources</h2>
            <ul className="space-y-1.5">
              {course.resources.map((resource) => (
                <li key={resource.url}>
                  <ResourceLink resource={resource} />
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

const ChapterRowItem = memo(function ChapterRowItem({
  chapter,
  index,
  isNext,
  onOpen,
}: {
  chapter: ChapterRow;
  index: number;
  isNext: boolean;
  onOpen: (chapterId: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(chapter.id)}
      className={cn(
        "flex w-full items-start gap-3 border-b border-line px-4 py-3 text-left last:border-b-0",
        "transition-colors duration-150 hover:bg-raised",
        isNext && "bg-accent-dim",
      )}
    >
      <span
        className={cn(
          "mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border font-mono text-[0.6875rem]",
          chapter.completed
            ? "border-accent bg-accent text-accent-ink"
            : isNext
              ? "border-accent text-accent"
              : "border-line text-ink-faint",
        )}
        aria-hidden
      >
        {chapter.completed ? "✓" : isNext ? "→" : index + 1}
      </span>

      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span
            className={cn(
              "text-[0.875rem] font-medium leading-snug",
              chapter.completed ? "text-ink-muted" : "text-ink",
            )}
          >
            Chapter {index + 1} — {chapter.title}
          </span>
          {isNext && (
            <span className="voice-label text-accent">Up next</span>
          )}
        </span>
        <span className="mt-0.5 block text-[0.8125rem] leading-relaxed text-ink-faint">
          {chapter.summary}
        </span>
        <span className="voice-machine mt-1 flex items-center gap-2 text-ink-subtle">
          <span>{chapter.minutes} min</span>
          {chapter.has_video && <span>· video</span>}
          {chapter.resource_count > 0 && <span>· {chapter.resource_count} resources</span>}
        </span>
      </span>
    </button>
  );
});

function ExamRow({
  exam,
  onOpen,
}: {
  exam: ExamState;
  onOpen: (examId: string) => void;
}) {
  const locked = !exam.unlocked;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 last:border-b-0",
        locked ? "bg-inset/50" : "bg-[rgba(255,255,255,0.02)]",
      )}
    >
      <span
        className={cn(
          "grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[0.6875rem]",
          locked ? "border-line text-ink-subtle" : "border-accent-alt text-accent-alt",
        )}
        aria-hidden
      >
        {locked ? "🔒" : "★"}
      </span>

      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "text-[0.875rem] font-medium",
            locked ? "text-ink-faint" : "text-ink",
          )}
        >
          {exam.title}
        </p>
        <p className="voice-machine mt-0.5 text-ink-subtle">
          {locked
            ? `Complete ${exam.remaining_chapter_ids.length} more chapter${
                exam.remaining_chapter_ids.length === 1 ? "" : "s"
              } to unlock`
            : `${exam.question_count} questions · pass ${exam.pass_score}%${
                exam.attempt_count
                  ? ` · ${exam.attempt_count} attempt${exam.attempt_count === 1 ? "" : "s"}`
                  : ""
              }`}
        </p>
      </div>

      {exam.best_score !== null && (
        <span
          className={cn(
            "chip shrink-0",
            exam.passed ? "border-add/35 text-add" : "border-warn/35 text-warn",
          )}
        >
          {exam.best_score}%
        </span>
      )}

      <button
        type="button"
        disabled={locked}
        onClick={() => onOpen(exam.id)}
        className={cn(
          "h-8 touch:h-11 shrink-0 rounded-ctl px-3.5 text-[0.8125rem] font-semibold transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200",
          locked
            ? "cursor-not-allowed border border-line text-ink-subtle"
            : "bg-accent-alt text-accent-ink hover:brightness-110 active:scale-[0.98]",
        )}
      >
        {exam.attempt_count ? "Retake" : "Take assessment"}
      </button>
    </div>
  );
}

/**
 * An external link.
 *
 * `noopener noreferrer` is not boilerplate here: a `target="_blank"` link
 * without it hands the opened page a reference back to this window, and the
 * brief's requirement that external resources open without breaking the app is
 * exactly that.
 */
export function ResourceLink({ resource }: { resource: Resource }) {
  return (
    <a
      href={resource.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex items-start gap-2.5 rounded-ctl px-2 py-1.5 transition-colors
                 duration-150 hover:bg-raised"
    >
      <span className="chip mt-px shrink-0 border-line-strong text-ink-faint">
        {RESOURCE_LABEL[resource.kind] ?? resource.kind}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[0.8125rem] leading-snug text-ink group-hover:text-accent">
          {resource.title}
          <svg
            className="ml-1 inline-block opacity-50"
            width="11" height="11" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"
            aria-hidden
          >
            <path d="M7 17 17 7M8 7h9v9" />
          </svg>
        </span>
        {resource.note && (
          <span className="mt-0.5 block text-2xs leading-relaxed text-ink-faint">
            {resource.note}
          </span>
        )}
      </span>
    </a>
  );
}
