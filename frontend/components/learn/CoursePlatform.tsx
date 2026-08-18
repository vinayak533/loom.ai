"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchCatalog,
  fetchChapter,
  fetchCourse,
  fetchExam,
  peekCatalog,
  peekChapter,
  peekCourse,
  prefetchChapter,
  setChapterProgress,
  submitExam,
  trackChapterView,
  type Catalog,
  type ChapterPayload,
  type CourseDetail,
  type ExamPaper,
  type ExamResult,
} from "@/lib/courses";
import { CourseCatalog } from "./CourseCatalog";
import { CoursePage } from "./CoursePage";
import { ChapterView } from "./ChapterView";
import { ExamView } from "./ExamView";
import { ExamResults } from "./ExamResults";

const ROUTE_KEY = "atlas:learn:course-route";

/**
 * Where the learner is.
 *
 * A tagged union rather than a set of booleans, so "on a chapter but no chapter
 * id" cannot be represented. Results deliberately hold their own payload: a
 * result is a *moment*, not a resource, and re-fetching it would either mean
 * storing attempts by id in the URL or showing a different attempt than the one
 * just submitted.
 */
type Route =
  | { view: "catalog" }
  | { view: "course"; courseId: string }
  | { view: "chapter"; courseId: string; chapterId: string }
  | { view: "exam"; courseId: string; examId: string }
  | { view: "results"; courseId: string; examId: string };

/**
 * The course platform.
 *
 * Owns navigation and all data fetching for the course side of Learn. Three
 * decisions shape it:
 *
 *  · **The client cache is in `lib/courses.ts`, not in this component.** So
 *    going back to a course you already opened is a synchronous read, and this
 *    file never has to grow a store.
 *  · **Progress writes are optimistic.** Marking a chapter complete repaints
 *    instantly and reconciles with the server's own numbers when the response
 *    lands; the percentage is computed in one place — the backend — so nothing
 *    on screen can disagree about it.
 *  · **The route is persisted.** Closing the app mid-chapter and coming back
 *    puts you on that chapter, which is the whole promise of "continue".
 */
export function CoursePlatform({ token }: { token: string | null }) {
  const [route, setRoute] = useState<Route>({ view: "catalog" });
  const [restored, setRestored] = useState(false);

  const [catalog, setCatalog] = useState<Catalog | null>(() => peekCatalog());
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [detail, setDetail] = useState<CourseDetail | null>(null);
  const [chapter, setChapter] = useState<ChapterPayload | null>(null);
  const [paper, setPaper] = useState<ExamPaper | null>(null);
  const [result, setResult] = useState<{ result: ExamResult; attempt: number } | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Every async load checks its ticket before writing state, so a fast
  // back-navigation can never be overwritten by a slower earlier request.
  //
  // One counter *per loader*, not one shared between them: the course detail
  // and the chapter body are fetched by separate effects that fire together on
  // mount, and a shared counter meant whichever started first had its result
  // thrown away by the other — leaving the course page stuck on its skeleton.
  const detailLoad = useRef(0);
  const chapterLoad = useRef(0);
  const examLoad = useRef(0);

  const fail = useCallback((err: unknown) => {
    const message = err instanceof Error ? err.message : "Something went wrong.";
    setError(
      /failed to fetch|networkerror/i.test(message)
        ? "Could not reach the backend. Check that the FastAPI server is running."
        : message,
    );
  }, []);

  // --- catalogue -----------------------------------------------------------

  const loadCatalog = useCallback(() => {
    setCatalogLoading(true);
    setCatalogError(null);
    fetchCatalog(token)
      .then(setCatalog)
      .catch((err) =>
        setCatalogError(
          err instanceof Error && /failed to fetch|networkerror/i.test(err.message)
            ? "Could not reach the backend. Check that the FastAPI server is running."
            : err instanceof Error
              ? err.message
              : "Could not load courses.",
        ),
      )
      .finally(() => setCatalogLoading(false));
  }, [token]);

  useEffect(loadCatalog, [loadCatalog]);

  // --- route restore / persist ---------------------------------------------

  useEffect(() => {
    try {
      const raw = localStorage.getItem(ROUTE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Route;
        // A stored `results` route has no payload to show, so it degrades to
        // the course page rather than to an empty screen.
        if (parsed?.view === "results") setRoute({ view: "course", courseId: parsed.courseId });
        else if (parsed?.view) setRoute(parsed);
      }
    } catch {
      /* a corrupt value is not worth failing on */
    }
    setRestored(true);
  }, []);

  useEffect(() => {
    if (!restored) return;
    if (route.view === "catalog") localStorage.removeItem(ROUTE_KEY);
    else localStorage.setItem(ROUTE_KEY, JSON.stringify(route));
  }, [route, restored]);

  // --- loading for the current route ---------------------------------------

  const courseId = "courseId" in route ? route.courseId : null;

  // The course detail backs the chapter view too (completion state, exam
  // unlocking), so it is loaded for every route that names a course.
  useEffect(() => {
    if (!courseId) {
      setDetail(null);
      return;
    }
    const cached = peekCourse(courseId);
    if (cached) setDetail(cached);
    const id = ++detailLoad.current;
    fetchCourse(courseId, token)
      .then((data) => {
        if (detailLoad.current === id) setDetail(data);
      })
      .catch((err) => {
        if (detailLoad.current === id) fail(err);
      });
  }, [courseId, token, fail]);

  useEffect(() => {
    if (route.view !== "chapter") return;
    const { courseId: cid, chapterId } = route;

    const cached = peekChapter(cid, chapterId);
    if (cached) setChapter(cached);
    else setChapter(null);

    const id = ++chapterLoad.current;
    fetchChapter(cid, chapterId)
      .then((data) => {
        if (chapterLoad.current !== id) return;
        setChapter(data);
        // Warm the next chapter so "Next" is instant. Cheap, and it is the
        // one navigation almost every reader makes.
        prefetchChapter(cid, data.next_id);
      })
      .catch(fail);

    // Opening is progress: this is what "continue" resumes from. Fired and
    // forgotten — nothing on screen waits for it.
    trackChapterView(cid, chapterId, token);
  }, [route, token, fail]);

  useEffect(() => {
    if (route.view !== "exam") return;
    const { courseId: cid, examId } = route;
    const id = ++examLoad.current;
    setPaper(null);
    fetchExam(cid, examId, token)
      .then((data) => {
        if (examLoad.current === id) setPaper(data);
      })
      .catch(fail);
  }, [route, token, fail]);

  // --- navigation ----------------------------------------------------------

  const openCatalog = useCallback(() => {
    setRoute({ view: "catalog" });
    // Progress may have changed while inside a course; the cache was already
    // invalidated by the write, so this repaints the grid with fresh numbers.
    loadCatalog();
  }, [loadCatalog]);

  const openCourse = useCallback((id: string) => setRoute({ view: "course", courseId: id }), []);

  const openChapter = useCallback(
    (cid: string, chapterId: string) => setRoute({ view: "chapter", courseId: cid, chapterId }),
    [],
  );

  const openExam = useCallback(
    (cid: string, examId: string) => setRoute({ view: "exam", courseId: cid, examId }),
    [],
  );

  // --- mutations -----------------------------------------------------------

  const completeChapter = useCallback(
    async (completed: boolean) => {
      if (route.view !== "chapter") return;
      const { courseId: cid, chapterId } = route;

      // Optimistic: repaint the row, the count and the bar from what we know,
      // then let the server's own numbers replace them.
      setDetail((current) => {
        if (!current || current.course.id !== cid) return current;
        const chapters = current.chapters.map((c) =>
          c.id === chapterId
            ? { ...c, completed, completed_at: completed ? new Date().toISOString() : null }
            : c,
        );
        const done = chapters.filter((c) => c.completed).length;
        return {
          ...current,
          chapters,
          progress: {
            ...current.progress,
            completed_count: done,
            percent: chapters.length ? Math.round((100 * done) / chapters.length) : 0,
            completed: done === chapters.length,
            started: true,
          },
        };
      });

      setBusy(true);
      try {
        const response = await setChapterProgress(cid, chapterId, completed, token);
        setDetail((current) =>
          current && current.course.id === cid
            ? { ...current, progress: response.progress, exams: response.exams }
            : current,
        );
      } catch (err) {
        fail(err);
        // Put the row back the way it was; the server is the authority.
        fetchCourse(cid, token).then(setDetail).catch(() => undefined);
      } finally {
        setBusy(false);
      }
    },
    [route, token, fail],
  );

  const submit = useCallback(
    async (answers: Record<string, number>) => {
      if (route.view !== "exam") return;
      const { courseId: cid, examId } = route;
      setBusy(true);
      try {
        const response = await submitExam(cid, examId, answers, token);
        setResult({ result: response.result, attempt: response.attempt_number });
        setDetail((current) =>
          current && current.course.id === cid
            ? { ...current, progress: response.progress, exams: response.exams }
            : current,
        );
        setRoute({ view: "results", courseId: cid, examId });
      } catch (err) {
        fail(err);
      } finally {
        setBusy(false);
      }
    },
    [route, token, fail],
  );

  // --- derived -------------------------------------------------------------

  const chapterCompleted = useMemo(() => {
    if (route.view !== "chapter" || !detail) return false;
    return detail.chapters.some((c) => c.id === route.chapterId && c.completed);
  }, [route, detail]);

  const chapterExamUnlocked = useMemo(() => {
    if (!chapter?.exam_id || !detail) return false;
    return detail.exams.some((e) => e.id === chapter.exam_id && e.unlocked);
  }, [chapter, detail]);

  // --- render --------------------------------------------------------------

  return (
    <div className="relative h-full min-h-0">
      {route.view === "catalog" && (
        <CourseCatalog
          courses={catalog?.courses ?? []}
          stats={catalog?.stats ?? null}
          loading={catalogLoading}
          error={catalogError}
          onOpen={openCourse}
          onContinue={openChapter}
          onRetry={loadCatalog}
        />
      )}

      {route.view === "course" &&
        (detail && detail.course.id === route.courseId ? (
          <CoursePage
            detail={detail}
            onBack={openCatalog}
            onOpenChapter={(chapterId) => openChapter(route.courseId, chapterId)}
            onOpenExam={(examId) => openExam(route.courseId, examId)}
          />
        ) : (
          <Loading label="Loading course…" />
        ))}

      {route.view === "chapter" &&
        (chapter && chapter.chapter.id === route.chapterId ? (
          <ChapterView
            payload={chapter}
            completed={chapterCompleted}
            saving={busy}
            examUnlocked={chapterExamUnlocked}
            onBack={() => openCourse(route.courseId)}
            onComplete={completeChapter}
            onNavigate={(chapterId) => openChapter(route.courseId, chapterId)}
            onOpenExam={(examId) => openExam(route.courseId, examId)}
          />
        ) : (
          <Loading label="Loading chapter…" />
        ))}

      {route.view === "exam" &&
        (paper && paper.exam.id === route.examId ? (
          <ExamView
            paper={paper}
            submitting={busy}
            onExit={() => openCourse(route.courseId)}
            onSubmit={submit}
          />
        ) : (
          <Loading label="Preparing assessment…" />
        ))}

      {route.view === "results" &&
        (result ? (
          <ExamResults
            result={result.result}
            attemptNumber={result.attempt}
            onRetake={() => openExam(route.courseId, route.examId)}
            onReviewChapter={(chapterId) => openChapter(route.courseId, chapterId)}
            onContinue={() =>
              openChapter(
                route.courseId,
                detail?.progress.next_chapter_id ?? detail?.chapters[0]?.id ?? "",
              )
            }
            onBackToCourse={() => openCourse(route.courseId)}
          />
        ) : (
          <Loading label="Loading result…" />
        ))}

      {error && (
        <div
          className="glass absolute bottom-4 left-1/2 z-50 flex max-w-[min(420px,90%)]
                     -translate-x-1/2 items-start gap-2.5 rounded-ctl border-del/30 px-3.5 py-2.5"
          role="alert"
        >
          <span className="sigil mt-1 h-1.5 w-1.5 shrink-0 bg-del" aria-hidden />
          <p className="text-2xs leading-relaxed text-ink">{error}</p>
          <button
            type="button"
            onClick={() => setError(null)}
            aria-label="Dismiss"
            className="ml-1 shrink-0 text-ink-faint transition-colors hover:text-ink"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}

function Loading({ label }: { label: string }) {
  return (
    <div className="scroll-thin h-full min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-[46rem] px-6 pt-10 sm:px-8">
        <p className="voice-machine mb-5 text-ink-faint">{label}</p>
        <div className="h-6 w-2/3 animate-pulse rounded-ctl bg-elevated" />
        <div className="mt-3 h-4 w-1/2 animate-pulse rounded-ctl bg-elevated" />
        <div className="mt-7 space-y-2.5">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="h-4 animate-pulse rounded-ctl bg-elevated" />
          ))}
        </div>
      </div>
    </div>
  );
}
