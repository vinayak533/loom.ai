/**
 * The Learn section's API client.
 *
 * Separate from `lib/api.ts` on purpose: that file is the agent's contract —
 * sessions, the websocket URL, uploads that become model content blocks.
 * Learn talks to a plain REST surface with no agent loop behind it, and
 * keeping the two apart means neither one grows fields that only the other
 * uses.
 */

import { HTTP_BASE } from "./api";

function authHeaders(token?: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    // FastAPI puts the human-readable reason in `detail`; surface that rather
    // than a status code the user cannot act on.
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

const BASE = `${HTTP_BASE}/api/learn`;

// --- types -----------------------------------------------------------------

export type Notebook = {
  id: string;
  title: string;
  /** A seed string; the cover is drawn from it, never fetched. */
  cover_image: string | null;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
  source_count?: number;
};

export type SourceType = "pdf" | "url" | "text";

export type NotebookSource = {
  id: string;
  notebook_id: string;
  source_type: SourceType;
  title: string;
  url: string | null;
  char_count: number;
  status: "ready" | "failed";
  error: string | null;
  added_at: string;
  /** Present only on the response that created it. */
  chunk_count?: number;
};

export type Note = {
  id: string;
  notebook_id: string;
  content: string;
  source: "ai_generated" | "user_written";
  created_at: string;
  updated_at: string;
};

export type QuizQuestion = {
  question: string;
  options: string[];
  answer: number;
  explanation: string;
};

export type Lesson = {
  id: string;
  notebook_id: string;
  section_title: string;
  section_order: number;
  summary: string | null;
  content: string | null;
  quiz_data: QuizQuestion[] | null;
};

export type Progress = {
  id: string;
  section_id: string;
  completed: boolean;
  completed_at: string | null;
};

export type Citation = {
  marker: number;
  source_id: string;
  source_title: string;
  snippet: string;
  similarity: number;
};

export type AskResult = {
  answer: string;
  citations: Citation[];
  model_id: string | null;
  model_name: string | null;
  grounded: boolean;
};

export type LearnConfig = {
  /** False when Supabase is not configured — notebooks live in memory. */
  persisted: boolean;
  embedding_mode: "provider" | "local";
  embedding_dim: number;
  model_id: string;
  model_name: string;
};

export type Workspace = {
  notebook: Notebook;
  sources: NotebookSource[];
  notes: Note[];
  lessons: Lesson[];
  progress: Progress[];
};

// --- calls -----------------------------------------------------------------

export async function fetchLearnConfig(): Promise<LearnConfig> {
  return json(await fetch(`${BASE}/config`, { cache: "no-store" }));
}

export async function listNotebooks(
  token?: string | null,
  archived = false,
): Promise<Notebook[]> {
  return json(
    await fetch(`${BASE}/notebooks${archived ? "?archived=true" : ""}`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function createNotebook(
  title: string,
  token?: string | null,
): Promise<Notebook> {
  return json(
    await fetch(`${BASE}/notebooks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ title }),
    }),
  );
}

export async function fetchWorkspace(
  id: string,
  token?: string | null,
): Promise<Workspace> {
  return json(
    await fetch(`${BASE}/notebooks/${id}`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function updateNotebook(
  id: string,
  patch: { title?: string; is_archived?: boolean },
  token?: string | null,
): Promise<Notebook> {
  return json(
    await fetch(`${BASE}/notebooks/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify(patch),
    }),
  );
}

export async function deleteNotebook(id: string, token?: string | null): Promise<void> {
  await fetch(`${BASE}/notebooks/${id}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export async function addSource(
  notebookId: string,
  payload:
    | { source_type: "url"; url: string; title?: string }
    | { source_type: "text"; text: string; title?: string },
  token?: string | null,
): Promise<NotebookSource> {
  return json(
    await fetch(`${BASE}/notebooks/${notebookId}/sources`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify(payload),
    }),
  );
}

export async function uploadSource(
  notebookId: string,
  file: File,
  token?: string | null,
): Promise<NotebookSource> {
  const body = new FormData();
  body.append("file", file);
  return json(
    await fetch(`${BASE}/notebooks/${notebookId}/sources/upload`, {
      method: "POST",
      headers: authHeaders(token),
      body,
    }),
  );
}

export async function deleteSource(
  notebookId: string,
  sourceId: string,
  token?: string | null,
): Promise<void> {
  await fetch(`${BASE}/notebooks/${notebookId}/sources/${sourceId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export async function ask(
  notebookId: string,
  question: string,
  history: Array<{ role: string; content: string }>,
  modelId: string | null,
  token?: string | null,
): Promise<AskResult> {
  return json(
    await fetch(`${BASE}/notebooks/${notebookId}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ question, history, model_id: modelId }),
    }),
  );
}

export async function addNote(
  notebookId: string,
  content: string,
  source: Note["source"],
  token?: string | null,
): Promise<Note> {
  return json(
    await fetch(`${BASE}/notebooks/${notebookId}/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ content, source }),
    }),
  );
}

export async function updateNote(
  notebookId: string,
  noteId: string,
  content: string,
  token?: string | null,
): Promise<Note> {
  return json(
    await fetch(`${BASE}/notebooks/${notebookId}/notes/${noteId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ content }),
    }),
  );
}

export async function deleteNote(
  notebookId: string,
  noteId: string,
  token?: string | null,
): Promise<void> {
  await fetch(`${BASE}/notebooks/${notebookId}/notes/${noteId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export async function generateLessons(
  notebookId: string,
  modelId: string | null,
  token?: string | null,
): Promise<{ lessons: Lesson[]; progress: Progress[] }> {
  return json(
    await fetch(`${BASE}/notebooks/${notebookId}/lessons/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ model_id: modelId }),
    }),
  );
}

export async function setProgress(
  notebookId: string,
  sectionId: string,
  completed: boolean,
  token?: string | null,
): Promise<Progress> {
  return json(
    await fetch(`${BASE}/notebooks/${notebookId}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ section_id: sectionId, completed }),
    }),
  );
}

// --- presentation helpers --------------------------------------------------

/** "2 hours ago" / "3 days ago" — the card subtitle in the library. */
export function relativeDate(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const units: Array<[number, string]> = [
    [60, "minute"],
    [3600, "hour"],
    [86400, "day"],
    [604800, "week"],
    [2629800, "month"],
    [31557600, "year"],
  ];
  let unit = "minute";
  let size = 60;
  for (const [seconds_, name] of units) {
    if (seconds >= seconds_) {
      size = seconds_;
      unit = name;
    }
  }
  const value = Math.floor(seconds / size);
  return `${value} ${unit}${value === 1 ? "" : "s"} ago`;
}

/**
 * A stable hue pair for a notebook, derived from its cover seed.
 *
 * Covers are generated rather than uploaded: a notebook needs an identity at
 * the moment it is created, and an image pipeline for that would be a lot of
 * machinery to make a grid look less empty. The two hues are drawn from the
 * seed, so a notebook keeps its face forever and no two adjacent cards read as
 * the same one.
 */
export function coverHues(seed: string | null, id: string): [number, number] {
  const source = seed || id || "atlas";
  let hash = 0;
  for (let i = 0; i < source.length; i++) {
    hash = (hash * 31 + source.charCodeAt(i)) >>> 0;
  }
  const base = hash % 360;
  // A second hue 40–100° away: related, never identical, never a clash.
  return [base, (base + 40 + ((hash >> 9) % 60)) % 360];
}

export const SOURCE_LABEL: Record<SourceType, string> = {
  pdf: "PDF",
  url: "Link",
  text: "Text",
};
