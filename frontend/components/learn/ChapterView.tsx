"use client";

import { memo, useState } from "react";
import { cn } from "@/lib/cn";
import { videoUrl, type ChapterPayload, type VideoRef } from "@/lib/courses";
import { Markdown } from "../Markdown";
import { ResourceLink } from "./CoursePage";

/**
 * A chapter.
 *
 * One column, one measure, one thing to read — the opposite of the notebook
 * workspace's three panels, because reading and researching are different
 * activities and a curriculum is meant to be *followed*. Everything that is
 * not the prose (concepts, takeaways, video, resources, notes) sits below it in
 * a fixed order, so the tenth chapter is navigated exactly like the first.
 */
export function ChapterView({
  payload,
  completed,
  saving,
  examUnlocked,
  onBack,
  onComplete,
  onNavigate,
  onOpenExam,
}: {
  payload: ChapterPayload;
  completed: boolean;
  saving: boolean;
  /** True when finishing this chapter has unlocked the assessment it belongs to. */
  examUnlocked: boolean;
  onBack: () => void;
  onComplete: (completed: boolean) => void;
  onNavigate: (chapterId: string) => void;
  onOpenExam: (examId: string) => void;
}) {
  const { chapter, course, chapter_count, previous_id, next_id, exam_id } = payload;

  return (
    <div className="scroll-thin h-full min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-[46rem] px-6 pb-20 pt-4 sm:px-8">
        {/* ----------------------------------------------------- breadcrumb */}
        <div className="mb-5 flex items-center gap-2">
          <button
            type="button"
            onClick={onBack}
            className="flex h-8 items-center gap-1.5 rounded-ctl px-2 text-2xs text-ink-muted
                       transition-colors duration-200 hover:bg-elevated hover:text-ink"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="m14 6-6 6 6 6" />
            </svg>
            {course.short_title}
          </button>
          <span className="voice-machine ml-auto text-ink-faint">
            Chapter {chapter.order + 1} of {chapter_count}
          </span>
        </div>

        {/* A step bar, not a percentage: in a chapter, "where am I in this
            course" is a position, and a position is easier to read as dots. */}
        <div className="mb-6 flex gap-1" aria-hidden>
          {Array.from({ length: chapter_count }, (_, i) => (
            <span
              key={i}
              className={cn(
                "h-1 flex-1 rounded-full",
                i < chapter.order ? "bg-accent/55" : i === chapter.order ? "bg-accent" : "bg-inset",
              )}
            />
          ))}
        </div>

        <header>
          <p className="voice-label mb-2 text-accent">{chapter.topic}</p>
          <h1 className="text-[1.625rem] font-semibold leading-tight tracking-tight text-ink">
            {chapter.title}
          </h1>
          <p className="mt-2 text-[0.9375rem] leading-relaxed text-ink-muted">
            {chapter.summary}
          </p>
          <p className="voice-machine mt-2.5 text-ink-faint">
            {chapter.minutes} min read
            {completed && <span className="text-accent"> · completed</span>}
          </p>
        </header>

        <article className="mt-7">
          <Markdown source={chapter.body} />
        </article>

        {chapter.concepts.length > 0 && (
          <section className="mt-8 rounded-card border border-line bg-elevated p-4">
            <h2 className="voice-label mb-3 text-ink-muted">Key concepts</h2>
            <dl className="space-y-2.5">
              {chapter.concepts.map((concept) => (
                <div key={concept.term} className="border-l-2 border-accent-line pl-3">
                  <dt className="text-[0.875rem] font-semibold text-ink">{concept.term}</dt>
                  <dd className="mt-0.5 text-[0.8125rem] leading-relaxed text-ink-muted">
                    {concept.definition}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        {chapter.video && <VideoCard video={chapter.video} />}

        {chapter.notes && (
          <section className="mt-6 rounded-card border border-warn/25 bg-[rgba(240,181,74,0.06)] p-4">
            <h2 className="voice-label mb-2 text-warn">Notes</h2>
            <div className="text-[0.875rem] leading-relaxed text-ink-muted">
              <Markdown source={chapter.notes} />
            </div>
          </section>
        )}

        {chapter.takeaways.length > 0 && (
          <section className="mt-6 rounded-card border border-accent-line bg-accent-soft p-4">
            <h2 className="voice-label mb-2.5 text-accent">Key takeaways</h2>
            <ul className="space-y-1.5">
              {chapter.takeaways.map((takeaway) => (
                <li key={takeaway} className="flex gap-2.5">
                  <span className="mt-[0.5em] h-1.5 w-1.5 shrink-0 rotate-45 bg-accent" aria-hidden />
                  <span className="text-[0.875rem] leading-relaxed text-ink">{takeaway}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {chapter.resources.length > 0 && (
          <section className="mt-6 rounded-card border border-line bg-elevated p-4">
            <h2 className="voice-label mb-2.5 text-ink-muted">Go deeper</h2>
            <ul className="space-y-1">
              {chapter.resources.map((resource) => (
                <li key={resource.url}>
                  <ResourceLink resource={resource} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* --------------------------------------------------------- footer */}
        <footer className="mt-8 border-t border-line pt-5">
          <button
            type="button"
            disabled={saving}
            onClick={() => onComplete(!completed)}
            className={cn(
              "flex h-10 w-full items-center justify-center gap-2 rounded-ctl text-[0.875rem]",
              "font-semibold transition-[color,background-color,border-color,box-shadow,opacity,transform,filter] duration-200 active:scale-[0.99] disabled:opacity-60",
              completed
                ? "border border-accent-line bg-accent-soft text-accent"
                : "bg-gradient-to-br from-accent to-accent-alt text-accent-ink hover:brightness-110",
            )}
          >
            <span>{completed ? "✓" : "○"}</span>
            {completed ? "Completed — mark as unread" : "Mark chapter complete"}
          </button>

          {completed && exam_id && examUnlocked && (
            <button
              type="button"
              onClick={() => onOpenExam(exam_id)}
              className="mt-2.5 flex h-10 w-full items-center justify-center gap-2 rounded-ctl
                         border border-accent-alt/45 bg-[rgba(255,255,255,0.03)] text-[0.875rem]
                         font-semibold text-accent-alt transition-colors duration-200
                         hover:bg-raised"
            >
              ★ Assessment unlocked — take it now
            </button>
          )}

          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              disabled={!previous_id}
              onClick={() => previous_id && onNavigate(previous_id)}
              className="flex h-9 items-center gap-1.5 rounded-ctl border border-line bg-elevated
                         px-3.5 text-[0.8125rem] font-medium text-ink transition-colors
                         duration-200 hover:bg-raised disabled:opacity-35
                         disabled:hover:bg-elevated"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="m14 6-6 6 6 6" />
              </svg>
              Previous
            </button>

            <button
              type="button"
              disabled={!next_id}
              onClick={() => next_id && onNavigate(next_id)}
              className="ml-auto flex h-9 items-center gap-1.5 rounded-ctl bg-raised px-3.5
                         text-[0.8125rem] font-medium text-ink transition-colors duration-200
                         hover:bg-elevated disabled:opacity-35"
            >
              Next chapter
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="m9 6 6 6-6 6" />
              </svg>
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

/**
 * The video.
 *
 * A facade, not an iframe. An embedded YouTube player pulls in roughly a
 * megabyte of script per chapter and runs it whether or not anyone presses
 * play; this renders a thumbnail and only mounts the real player on click.
 * That single decision is most of this section's page weight.
 *
 * Two shapes are supported because one of them cannot break: `id` embeds a
 * specific video, `search` opens a YouTube search for a phrase — which stays
 * correct even when a channel re-uploads or re-titles.
 */
const VideoCard = memo(function VideoCard({ video }: { video: VideoRef }) {
  const [playing, setPlaying] = useState(false);
  const [thumbFailed, setThumbFailed] = useState(false);

  if (video.type === "search") {
    return (
      <section className="mt-6">
        <h2 className="voice-label mb-2.5 text-ink-muted">Watch</h2>
        <a
          href={videoUrl(video)}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3.5 rounded-card border border-line bg-elevated p-3.5
                     transition-colors duration-200 hover:border-accent-line hover:bg-raised"
        >
          <span className="grid h-11 w-16 shrink-0 place-items-center rounded-ctl bg-del/15 text-del">
            <PlayGlyph />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[0.875rem] font-medium text-ink">
              {video.title}
            </span>
            <span className="voice-machine mt-0.5 block text-ink-faint">
              Opens a YouTube search in a new tab
            </span>
          </span>
        </a>
      </section>
    );
  }

  return (
    <section className="mt-6">
      <h2 className="voice-label mb-2.5 text-ink-muted">Watch</h2>
      <div className="overflow-hidden rounded-card border border-line bg-elevated">
        <div className="relative aspect-video w-full bg-black">
          {playing ? (
            <iframe
              // nocookie host, and only mounted after a click — so nothing is
              // requested from YouTube until the learner asks for it.
              src={`https://www.youtube-nocookie.com/embed/${video.id}?autoplay=1&rel=0`}
              title={video.title}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; picture-in-picture"
              allowFullScreen
              className="absolute inset-0 h-full w-full border-0"
            />
          ) : (
            <button
              type="button"
              onClick={() => setPlaying(true)}
              className="group absolute inset-0 grid place-items-center"
              aria-label={`Play ${video.title}`}
            >
              {!thumbFailed && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={`https://i.ytimg.com/vi/${video.id}/hqdefault.jpg`}
                  alt=""
                  loading="lazy"
                  onError={() => setThumbFailed(true)}
                  className="absolute inset-0 h-full w-full object-cover opacity-80
                             transition-opacity duration-200 group-hover:opacity-95"
                />
              )}
              <span className="relative grid h-14 w-14 place-items-center rounded-full bg-del/90
                               text-white shadow-lift transition-transform duration-200
                               group-hover:scale-105">
                <PlayGlyph />
              </span>
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 px-3.5 py-2.5">
          <p className="min-w-0 flex-1 truncate text-[0.8125rem] text-ink">{video.title}</p>
          {video.channel && (
            <span className="voice-machine shrink-0 text-ink-faint">{video.channel}</span>
          )}
          <a
            href={videoUrl(video)}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-2xs text-ink-faint transition-colors hover:text-accent"
          >
            YouTube ↗
          </a>
        </div>
      </div>
    </section>
  );
});

function PlayGlyph() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M8 5.5v13l11-6.5z" />
    </svg>
  );
}
