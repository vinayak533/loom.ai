"use client";

import { memo, useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import {
  courseHues,
  duration,
  DIFFICULTY_LABEL,
  type CatalogStats,
  type CourseCard,
  type Difficulty,
} from "@/lib/courses";

/**
 * The catalogue.
 *
 * A course grid, in the shape every learning platform has converged on because
 * it answers the four questions a learner arrives with in one glance: what is
 * this, how hard, how far am I, and what do I press. Everything on a card is
 * one of those four — there is no metadata here for its own sake.
 *
 * Deliberately light on motion. Cards use CSS transitions rather than
 * framer-motion: a grid of ten animated subtrees re-measures on every parent
 * render, and the brief asks for fast over decorated.
 */

type Filter = "all" | "in-progress" | "not-started" | "completed";

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: "all", label: "All courses" },
  { key: "in-progress", label: "In progress" },
  { key: "not-started", label: "Not started" },
  { key: "completed", label: "Completed" },
];

export function CourseCatalog({
  courses,
  stats,
  loading,
  error,
  onOpen,
  onContinue,
  onRetry,
}: {
  courses: CourseCard[];
  stats: CatalogStats | null;
  loading: boolean;
  error: string | null;
  onOpen: (courseId: string) => void;
  onContinue: (courseId: string, chapterId: string) => void;
  onRetry: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return courses.filter((course) => {
      if (needle) {
        const haystack = `${course.title} ${course.subtitle} ${course.tags.join(" ")}`;
        if (!haystack.toLowerCase().includes(needle)) return false;
      }
      const { started, completed } = course.progress;
      if (filter === "in-progress") return started && !completed;
      if (filter === "not-started") return !started;
      if (filter === "completed") return completed;
      return true;
    });
  }, [courses, query, filter]);

  const resume = useMemo(() => {
    const active = courses
      .filter((c) => c.progress.started && !c.progress.completed && c.progress.last_accessed)
      .sort((a, b) =>
        (b.progress.last_accessed ?? "").localeCompare(a.progress.last_accessed ?? ""),
      );
    return active[0] ?? null;
  }, [courses]);

  return (
    <div className="scroll-thin h-full min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-[1240px] px-6 pb-16 pt-6 sm:px-9">
        <header className="mb-6">
          <h1 className="text-display font-semibold tracking-tight text-ink">
            Your{" "}
            <span className="bg-gradient-to-br from-accent to-accent-alt bg-clip-text text-transparent">
              courses
            </span>
          </h1>
          <p className="mt-2.5 max-w-[56ch] text-[0.9375rem] leading-relaxed text-ink-muted">
            Structured tracks with chapters, worked examples and assessments.
            Finish a group of chapters and the assessment for it unlocks.
          </p>
        </header>

        {stats && stats.chapters_completed + stats.exams_taken > 0 && (
          <div className="mb-6 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            <Stat label="In progress" value={String(stats.courses_started)} />
            <Stat label="Chapters done" value={String(stats.chapters_completed)} />
            <Stat label="Assessments" value={String(stats.exams_taken)} />
            <Stat
              label="Avg score"
              value={stats.average_score === null ? "—" : `${stats.average_score}%`}
            />
          </div>
        )}

        {resume && (
          <ResumeBanner
            course={resume}
            onContinue={() => onContinue(resume.id, resume.progress.next_chapter_id)}
          />
        )}

        {/* ------------------------------------------------------- toolbar */}
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <div role="tablist" aria-label="Filter courses" className="flex flex-wrap gap-1">
            {FILTERS.map((f) => {
              const on = f.key === filter;
              return (
                <button
                  key={f.key}
                  role="tab"
                  type="button"
                  aria-selected={on}
                  onClick={() => setFilter(f.key)}
                  className={cn(
                    "h-8 rounded-full px-3.5 text-[0.8125rem] font-medium transition-colors duration-200",
                    on
                      ? "bg-accent text-accent-ink"
                      : "text-ink-muted hover:bg-elevated hover:text-ink",
                  )}
                >
                  {f.label}
                </button>
              );
            })}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search courses…"
              aria-label="Search courses"
              className="h-8 w-[180px] rounded-ctl border border-line bg-elevated px-2.5 text-[0.8125rem]
                         text-ink placeholder:text-ink-faint focus:border-accent-line focus:outline-none"
            />
          </div>
        </div>

        {/* ---------------------------------------------------------- grid */}
        {error ? (
          <div className="grid place-items-center rounded-card border border-del/25 bg-del-bg py-14 text-center">
            <p className="text-[0.875rem] text-ink">{error}</p>
            <button
              type="button"
              onClick={onRetry}
              className="mt-3.5 rounded-ctl border border-line bg-elevated px-3.5 py-2 text-[0.8125rem]
                         font-medium text-ink transition-colors hover:border-accent-line hover:bg-raised"
            >
              Try again
            </button>
          </div>
        ) : loading && courses.length === 0 ? (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <li
                key={i}
                className="h-[268px] animate-pulse rounded-card border border-line bg-elevated"
              />
            ))}
          </ul>
        ) : visible.length === 0 ? (
          <div className="grid place-items-center rounded-card border border-dashed border-line py-16 text-center">
            <h3 className="text-[0.9375rem] font-medium text-ink">Nothing matches that</h3>
            <p className="mt-1.5 max-w-[42ch] text-[0.8125rem] leading-relaxed text-ink-faint">
              Try a different search, or switch back to all courses.
            </p>
          </div>
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {visible.map((course) => (
              <CourseTile
                key={course.id}
                course={course}
                onOpen={onOpen}
                onContinue={onContinue}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/**
 * One course card.
 *
 * `memo`d with primitive-only callbacks above it: the catalogue re-renders on
 * every progress write, and ten cards each re-rendering a gradient and a
 * progress bar for a change to one of them is exactly the lag the brief warns
 * about.
 */
const CourseTile = memo(function CourseTile({
  course,
  onOpen,
  onContinue,
}: {
  course: CourseCard;
  onOpen: (courseId: string) => void;
  onContinue: (courseId: string, chapterId: string) => void;
}) {
  const [h1, h2] = courseHues(course.id);
  const { percent, completed_count, chapter_count, started, completed } = course.progress;

  return (
    <li>
      <article
        className="group flex h-full flex-col overflow-hidden rounded-card border border-line
                   bg-elevated transition-all duration-200 ease-out hover:border-accent-line
                   hover:bg-raised hover:shadow-lift"
      >
        {/* The face. Generated from the id, so a new course needs no asset. */}
        <button
          type="button"
          onClick={() => onOpen(course.id)}
          className="relative block h-[92px] w-full overflow-hidden text-left"
          aria-label={`Open ${course.title}`}
        >
          <span
            aria-hidden
            className="absolute inset-0"
            style={{
              background: `linear-gradient(135deg, hsl(${h1} 62% 22%) 0%, hsl(${h2} 58% 13%) 100%)`,
            }}
          />
          <span
            aria-hidden
            className="absolute inset-0 opacity-[0.13]"
            style={{
              backgroundImage:
                "radial-gradient(circle at 1px 1px, rgba(255,255,255,0.9) 1px, transparent 0)",
              backgroundSize: "14px 14px",
            }}
          />
          <span className="relative flex h-full items-center px-4">
            <span
              className="font-mono text-[1.375rem] font-medium tracking-tight text-white/90"
              style={{ textShadow: "0 1px 12px rgba(0,0,0,0.45)" }}
            >
              {course.short_title}
            </span>
            {completed && (
              <span className="ml-auto rounded-full bg-black/35 px-2 py-0.5 text-2xs font-medium text-white/90">
                ✓ Complete
              </span>
            )}
          </span>
        </button>

        <div className="flex min-h-0 flex-1 flex-col p-4">
          <button
            type="button"
            onClick={() => onOpen(course.id)}
            className="text-left"
          >
            <h3 className="text-[0.9375rem] font-semibold leading-snug text-ink">
              {course.title}
            </h3>
            <p className="mt-1 line-clamp-2 text-[0.8125rem] leading-relaxed text-ink-muted">
              {course.subtitle}
            </p>
          </button>

          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <DifficultyChip difficulty={course.difficulty} />
            <span className="chip">{course.chapter_count} chapters</span>
            <span className="chip">{duration(course.minutes)}</span>
            {course.best_score !== null && (
              <span
                className={cn(
                  "chip",
                  course.best_score >= 70
                    ? "border-add/35 text-add"
                    : "border-warn/35 text-warn",
                )}
              >
                {course.best_score}% best
              </span>
            )}
          </div>

          <div className="mt-4">
            <div className="mb-1.5 flex items-baseline justify-between">
              <span className="voice-machine text-ink-faint">
                {completed_count}/{chapter_count} complete
              </span>
              <span className="voice-machine text-ink">{percent}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-inset">
              <div
                className="h-full rounded-full bg-gradient-to-r from-accent to-accent-alt
                           transition-[width] duration-300 ease-out"
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>

          <button
            type="button"
            onClick={() => onContinue(course.id, course.progress.next_chapter_id)}
            className={cn(
              "mt-4 flex h-9 w-full items-center justify-center gap-1.5 rounded-ctl text-[0.8125rem]",
              "font-semibold transition-all duration-200 active:scale-[0.985]",
              started
                ? "bg-gradient-to-br from-accent to-accent-alt text-accent-ink hover:brightness-110"
                : "border border-line bg-inset text-ink hover:border-accent-line hover:bg-raised",
            )}
          >
            {completed ? "Review course" : started ? "Continue learning" : "Start course"}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="m9 6 6 6-6 6" />
            </svg>
          </button>
        </div>
      </article>
    </li>
  );
});

function ResumeBanner({
  course,
  onContinue,
}: {
  course: CourseCard;
  onContinue: () => void;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-center gap-4 rounded-card border border-accent-line
                    bg-accent-soft px-4 py-3.5">
      <div className="min-w-0 flex-1">
        <p className="voice-label text-accent">Pick up where you left off</p>
        <p className="mt-1 truncate text-[0.9375rem] font-medium text-ink">{course.title}</p>
        <p className="voice-machine mt-0.5 text-ink-faint">
          {course.progress.completed_count}/{course.progress.chapter_count} chapters ·{" "}
          {course.progress.percent}% · {duration(course.progress.minutes_remaining)} left
        </p>
      </div>
      <button
        type="button"
        onClick={onContinue}
        className="flex h-9 shrink-0 items-center gap-1.5 rounded-ctl bg-gradient-to-br from-accent
                   to-accent-alt px-4 text-[0.8125rem] font-semibold text-accent-ink
                   transition-all duration-200 hover:brightness-110 active:scale-[0.98]"
      >
        Continue
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="m9 6 6 6-6 6" />
        </svg>
      </button>
    </div>
  );
}

export function DifficultyChip({ difficulty }: { difficulty: Difficulty }) {
  return (
    <span
      className={cn(
        "chip",
        difficulty === "beginner" && "border-add/30 text-add",
        difficulty === "intermediate" && "border-warn/30 text-warn",
        difficulty === "advanced" && "border-del/30 text-del",
      )}
    >
      {DIFFICULTY_LABEL[difficulty]}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-card border border-line bg-elevated px-3.5 py-2.5">
      <p className="voice-label text-ink-faint">{label}</p>
      <p className="mt-1 font-mono text-[1.125rem] font-medium tabular-nums text-ink">{value}</p>
    </div>
  );
}
