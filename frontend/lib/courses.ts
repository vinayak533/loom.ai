/**
 * The course platform's API client.
 *
 * Split from `lib/learn.ts` for the same reason that file is split from
 * `lib/api.ts`: notebooks are a workspace the user fills, courses are authored
 * content they move through, and the two have no types in common.
 *
 * The caching here is the reason the section feels instant, so it is worth
 * stating the rule it follows: **content is cached forever, progress is
 * cached briefly and dropped on every write.** A chapter body is identical for
 * every learner and never changes between deploys, so re-opening one is a map
 * lookup. The catalogue and the course page carry per-user progress, so they
 * get a short TTL and are invalidated the moment anything is marked complete.
 */

import { HTTP_BASE } from "./api";

const BASE = `${HTTP_BASE}/api/learn`;

function authHeaders(token?: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    let message = `${res.status} ${res.statusText}`;
    try {
      const parsed = JSON.parse(detail);
      if (parsed?.detail) message = String(parsed.detail);
    } catch {
      if (detail) message = detail;
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

// --- types -----------------------------------------------------------------

export type Difficulty = "beginner" | "intermediate" | "advanced";

export type CourseProgress = {
  course_id: string;
  chapter_count: number;
  completed_count: number;
  percent: number;
  next_chapter_id: string;
  started: boolean;
  completed: boolean;
  last_accessed: string | null;
  minutes_remaining: number;
};

export type CourseCard = {
  id: string;
  title: string;
  short_title: string;
  subtitle: string;
  description: string;
  difficulty: Difficulty;
  tags: string[];
  chapter_count: number;
  exam_count: number;
  minutes: number;
  progress: CourseProgress;
  exam_attempts: number;
  best_score: number | null;
};

export type CatalogStats = {
  courses_started: number;
  courses_completed: number;
  chapters_completed: number;
  exams_taken: number;
  average_score: number | null;
};

export type Catalog = { courses: CourseCard[]; stats: CatalogStats };

export type Resource = {
  kind: string;
  title: string;
  url: string;
  note: string | null;
};

export type VideoRef =
  | { type: "id"; id: string; title: string; channel: string | null }
  | { type: "search"; query: string; title: string; channel: string | null };

export type ChapterRow = {
  id: string;
  order: number;
  title: string;
  summary: string;
  topic: string;
  minutes: number;
  has_video: boolean;
  resource_count: number;
  completed: boolean;
  completed_at: string | null;
  last_accessed: string | null;
};

export type ExamState = {
  id: string;
  order: number;
  title: string;
  description: string;
  chapter_ids: string[];
  question_count: number;
  pass_score: number;
  unlocked: boolean;
  remaining_chapter_ids: string[];
  attempt_count: number;
  best_score: number | null;
  last_score: number | null;
  passed: boolean;
  last_weak_topics: string[];
  last_attempt_at: string | null;
};

export type CourseDetail = {
  course: Omit<CourseCard, "progress" | "exam_attempts" | "best_score"> & {
    objectives: string[];
    resources: Resource[];
  };
  chapters: ChapterRow[];
  exams: ExamState[];
  progress: CourseProgress;
};

export type Chapter = {
  id: string;
  course_id: string;
  order: number;
  title: string;
  summary: string;
  topic: string;
  minutes: number;
  body: string;
  concepts: Array<{ term: string; definition: string }>;
  takeaways: string[];
  resources: Resource[];
  video: VideoRef | null;
  notes: string | null;
};

export type ChapterPayload = {
  course: { id: string; title: string; short_title: string };
  chapter: Chapter;
  chapter_count: number;
  previous_id: string | null;
  next_id: string | null;
  exam_id: string | null;
};

export type ExamQuestion = {
  id: string;
  type: "mcq" | "truefalse" | "code" | "scenario";
  topic: string;
  prompt: string;
  code: string | null;
  language: string;
  options: string[];
};

export type ExamPaper = {
  exam: ExamState & { questions: ExamQuestion[] };
  course: { id: string; title: string; short_title: string };
};

export type GradedQuestion = {
  id: string;
  topic: string;
  chapter_id: string | null;
  prompt: string;
  options: string[];
  chosen: number | null;
  answer: number;
  correct: boolean;
  explanation: string;
};

export type TopicScore = {
  topic: string;
  correct: number;
  total: number;
  score: number;
  chapter_ids: string[];
};

export type Recommendation = {
  topic: string;
  score: number;
  correct: number;
  total: number;
  advice: string;
  chapters: Array<{ id: string; order: number; title: string }>;
  resources: Resource[];
  practice: string[];
  retake_exam_id: string;
};

export type ExamResult = {
  exam_id: string;
  course_id: string;
  score: number;
  correct_count: number;
  total_count: number;
  passed: boolean;
  pass_score: number;
  questions: GradedQuestion[];
  topic_scores: TopicScore[];
  strong_topics: string[];
  weak_topics: string[];
  recommendations: Recommendation[];
};

export type SubmitResponse = {
  result: ExamResult;
  attempt_id: string | null;
  attempt_number: number;
  exams: ExamState[];
  progress: CourseProgress;
};

export type ProgressResponse = { progress: CourseProgress; exams: ExamState[] };

// --- caching ---------------------------------------------------------------

/** Chapter bodies and exam papers: identical for everyone, kept for the session. */
const contentCache = new Map<string, unknown>();
/** Catalogue and course pages: carry progress, so they expire and are dropped on writes. */
const stateCache = new Map<string, { at: number; value: unknown }>();
/** In-flight requests, so two components mounting at once make one request. */
const inflight = new Map<string, Promise<unknown>>();

const STATE_TTL_MS = 30_000;

function dedupe<T>(key: string, run: () => Promise<T>): Promise<T> {
  const existing = inflight.get(key) as Promise<T> | undefined;
  if (existing) return existing;
  const promise = run().finally(() => inflight.delete(key));
  inflight.set(key, promise);
  return promise;
}

async function cachedContent<T>(key: string, run: () => Promise<T>): Promise<T> {
  const hit = contentCache.get(key) as T | undefined;
  if (hit) return hit;
  return dedupe(key, async () => {
    const value = await run();
    contentCache.set(key, value);
    return value;
  });
}

async function cachedState<T>(key: string, run: () => Promise<T>): Promise<T> {
  const hit = stateCache.get(key);
  if (hit && Date.now() - hit.at < STATE_TTL_MS) return hit.value as T;
  return dedupe(key, async () => {
    const value = await run();
    stateCache.set(key, { at: Date.now(), value });
    return value;
  });
}

/**
 * Drop every progress-bearing cache entry.
 *
 * Called after any write. Content stays — marking a chapter complete does not
 * change a single word of it, and re-downloading 80KB of prose to update a
 * percentage would be the exact opposite of the point.
 */
export function invalidateProgress(): void {
  stateCache.clear();
}

/** Read the cached catalogue without a request, for an instant first paint. */
export function peekCatalog(): Catalog | null {
  const hit = stateCache.get("catalog");
  return hit ? (hit.value as Catalog) : null;
}

export function peekCourse(courseId: string): CourseDetail | null {
  const hit = stateCache.get(`course:${courseId}`);
  return hit ? (hit.value as CourseDetail) : null;
}

export function peekChapter(courseId: string, chapterId: string): ChapterPayload | null {
  return (contentCache.get(`chapter:${courseId}:${chapterId}`) as ChapterPayload) ?? null;
}

// --- calls -----------------------------------------------------------------

export async function fetchCatalog(token?: string | null): Promise<Catalog> {
  return cachedState("catalog", async () =>
    json<Catalog>(
      await fetch(`${BASE}/courses`, { headers: authHeaders(token), cache: "no-store" }),
    ),
  );
}

export async function fetchCourse(
  courseId: string,
  token?: string | null,
): Promise<CourseDetail> {
  return cachedState(`course:${courseId}`, async () =>
    json<CourseDetail>(
      await fetch(`${BASE}/courses/${courseId}`, {
        headers: authHeaders(token),
        cache: "no-store",
      }),
    ),
  );
}

export async function fetchChapter(
  courseId: string,
  chapterId: string,
): Promise<ChapterPayload> {
  return cachedContent(`chapter:${courseId}:${chapterId}`, async () =>
    json<ChapterPayload>(
      await fetch(`${BASE}/courses/${courseId}/chapters/${chapterId}`),
    ),
  );
}

/**
 * Prefetch the next chapter's body.
 *
 * Fired when a chapter is opened, so pressing "Next" resolves from the cache.
 * Failures are swallowed on purpose — this is an optimisation, and a warmed
 * cache that missed is just a normal fetch a moment later.
 */
export function prefetchChapter(courseId: string, chapterId: string | null): void {
  if (!chapterId || contentCache.has(`chapter:${courseId}:${chapterId}`)) return;
  void fetchChapter(courseId, chapterId).catch(() => undefined);
}

export async function fetchExam(
  courseId: string,
  examId: string,
  token?: string | null,
): Promise<ExamPaper> {
  return json<ExamPaper>(
    await fetch(`${BASE}/courses/${courseId}/exams/${examId}`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function submitExam(
  courseId: string,
  examId: string,
  answers: Record<string, number>,
  token?: string | null,
): Promise<SubmitResponse> {
  const result = await json<SubmitResponse>(
    await fetch(`${BASE}/courses/${courseId}/exams/${examId}/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ answers }),
    }),
  );
  invalidateProgress();
  return result;
}

export async function setChapterProgress(
  courseId: string,
  chapterId: string,
  completed: boolean,
  token?: string | null,
): Promise<ProgressResponse> {
  const result = await json<ProgressResponse>(
    await fetch(`${BASE}/courses/${courseId}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ chapter_id: chapterId, completed }),
    }),
  );
  invalidateProgress();
  return result;
}

/**
 * Record that a chapter was opened, without blocking on it.
 *
 * Fire-and-forget: nothing on screen depends on the response, and the only
 * thing it affects is where "Continue learning" resumes next time.
 */
export function trackChapterView(
  courseId: string,
  chapterId: string,
  token?: string | null,
): void {
  void fetch(`${BASE}/courses/${courseId}/progress`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ chapter_id: chapterId }),
    keepalive: true,
  }).catch(() => undefined);
}

// --- presentation helpers --------------------------------------------------

export const DIFFICULTY_LABEL: Record<Difficulty, string> = {
  beginner: "Beginner",
  intermediate: "Intermediate",
  advanced: "Advanced",
};

/** "3h 20m" — course length, in the one format used everywhere. */
export function duration(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

/**
 * A stable hue for a course, derived from its id.
 *
 * Same idea as the notebook covers: a course needs a face, and generating one
 * from the id means adding a course never involves adding an asset.
 */
export function courseHues(id: string): [number, number] {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  const base = hash % 360;
  return [base, (base + 38 + ((hash >> 9) % 54)) % 360];
}

export const RESOURCE_LABEL: Record<string, string> = {
  doc: "Docs",
  paper: "Paper",
  article: "Article",
  video: "Video",
  book: "Book",
};

/** A YouTube URL for a video reference — a watch link, or a search. */
export function videoUrl(video: VideoRef): string {
  return video.type === "id"
    ? `https://www.youtube.com/watch?v=${video.id}`
    : `https://www.youtube.com/results?search_query=${encodeURIComponent(video.query)}`;
}
