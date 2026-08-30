"use client";

/**
 * Projects and memory: the browser half.
 *
 * Two different things behind one file because they are the two halves of the
 * same idea and are edited from the same places. A **project** is a container
 * the user makes on purpose — sessions, standing instructions, knowledge
 * files — and its instructions apply only inside it. **Memory** is per-account
 * and applies everywhere, which is why it has its own switch.
 *
 * Everything here is a thin fetch wrapper over `/api`, matching `lib/api.ts`:
 * no caching, no client-side store, no optimistic writes. The server owns the
 * data and a re-fetch after a mutation is one round trip against a list that
 * is at most a few dozen rows.
 */

import { HTTP_BASE, type SessionRow } from "./api";

function authHeaders(token?: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${detail ? ` — ${detail}` : ""}`);
  }
  return (await res.json()) as T;
}

export type Project = {
  id: string;
  user_id: string | null;
  name: string;
  description: string | null;
  /** Standing instructions prepended to every turn inside this project. */
  instructions: string | null;
  color: string;
  icon: string | null;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
};

/**
 * A file attached to a project as background knowledge.
 *
 * `status` is `failed` for anything no text could be read from — a scanned PDF
 * has no text layer and there is no OCR step — and `error` says why. A failed
 * file is kept and shown rather than rejected at upload, so the user can see
 * what happened instead of watching a file silently not work.
 */
export type ProjectFile = {
  id: string;
  project_id: string;
  name: string;
  char_count: number;
  mime: string | null;
  bytes: number;
  status: "ready" | "failed";
  error: string | null;
  added_at: string;
};

/** A project plus what is inside it. Returned by the detail route only. */
export type ProjectDetail = Project & {
  files: ProjectFile[];
  sessions: SessionRow[];
};

export async function listProjects(
  token?: string | null,
  archived = false,
): Promise<Project[]> {
  const search = new URLSearchParams();
  if (archived) search.set("archived", "true");
  return json(
    await fetch(`${HTTP_BASE}/api/projects?${search}`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function fetchProject(
  id: string,
  token?: string | null,
): Promise<ProjectDetail> {
  return json(
    await fetch(`${HTTP_BASE}/api/projects/${id}`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function createProject(
  patch: { name?: string; description?: string; instructions?: string; color?: string },
  token?: string | null,
): Promise<Project> {
  return json(
    await fetch(`${HTTP_BASE}/api/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify(patch),
    }),
  );
}

/** Rename, re-describe, re-instruct or archive. Every field is independent. */
export async function updateProject(
  id: string,
  patch: {
    name?: string;
    description?: string;
    instructions?: string;
    color?: string;
    is_archived?: boolean;
  },
  token?: string | null,
): Promise<Project> {
  return json(
    await fetch(`${HTTP_BASE}/api/projects/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify(patch),
    }),
  );
}

/**
 * Delete a project. The sessions inside it survive and become unfiled — the
 * foreign key is `set null`, deliberately. Any UI that calls this must say so,
 * because "delete project" reads like it takes the conversations with it.
 */
export async function deleteProject(id: string, token?: string | null): Promise<void> {
  const res = await fetch(`${HTTP_BASE}/api/projects/${id}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

/** Move a session into a project, or out of one with `null`. */
export async function setSessionProject(
  sessionId: string,
  projectId: string | null,
  token?: string | null,
): Promise<void> {
  const res = await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/project`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
}

export async function listProjectFiles(
  projectId: string,
  token?: string | null,
): Promise<ProjectFile[]> {
  return json(
    await fetch(`${HTTP_BASE}/api/projects/${projectId}/files`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function uploadProjectFile(
  projectId: string,
  file: File,
  token?: string | null,
): Promise<ProjectFile> {
  const body = new FormData();
  body.append("file", file);
  return json(
    await fetch(`${HTTP_BASE}/api/projects/${projectId}/files`, {
      method: "POST",
      headers: authHeaders(token),
      body,
    }),
  );
}

export async function deleteProjectFile(
  projectId: string,
  fileId: string,
  token?: string | null,
): Promise<void> {
  const res = await fetch(`${HTTP_BASE}/api/projects/${projectId}/files/${fileId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

// --- memory ----------------------------------------------------------------

export type Memory = {
  id: string;
  content: string;
  source_session_id: string | null;
  created_at: string;
};

export type MemoryState = {
  /** Whether new facts are being learned. False for an anonymous caller. */
  enabled: boolean;
  memories: Memory[];
};

export async function fetchMemories(token?: string | null): Promise<MemoryState> {
  return json(
    await fetch(`${HTTP_BASE}/api/memories`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function addMemory(
  content: string,
  token?: string | null,
): Promise<Memory> {
  return json(
    await fetch(`${HTTP_BASE}/api/memories`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ content }),
    }),
  );
}

export async function deleteMemory(id: string, token?: string | null): Promise<void> {
  const res = await fetch(`${HTTP_BASE}/api/memories/${id}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
}

export async function clearMemories(token?: string | null): Promise<void> {
  const res = await fetch(`${HTTP_BASE}/api/memories`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
}
