/**
 * The browser talks to exactly one backend: our FastAPI service. It never
 * holds an xAI / OpenRouter / E2B / Exa key — those live server-side.
 */

import type { FileNode, GitChange, GitCommit } from "./events";

const WS_BASE =
  process.env.NEXT_PUBLIC_BACKEND_WS_URL ?? "ws://localhost:8000";

/** Derive the HTTP origin from the websocket URL so there's one thing to configure. */
export const HTTP_BASE = WS_BASE.replace(/^ws/, "http").replace(/\/$/, "");

/**
 * `section` tags the session row the backend creates on first connect. The
 * browser mints the session id locally, so this socket is the only place the
 * originating surface is still known — without it Chat and Code sessions are
 * indistinguishable and each section's history returns the other's.
 */
export function socketUrl(
  sessionId: string,
  token?: string | null,
  section: "chat" | "code" = "chat",
): string {
  const base = WS_BASE.replace(/\/$/, "");
  const params = new URLSearchParams({ section });
  if (token) params.set("token", token);
  return `${base}/ws/${encodeURIComponent(sessionId)}?${params.toString()}`;
}

function authHeaders(token?: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type SessionRow = {
  id: string;
  title: string;
  /**
   * One line about what the project *is*, as opposed to what the opening
   * message asked for. Written by hand or generated from the transcript, so it
   * is absent until someone asks for it — which is what the history list keys
   * off to decide whether to render a second line.
   */
  description?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  sandbox_id?: string | null;
  model_id?: string | null;
  /** Sorts above everything else, in its own group. */
  is_pinned?: boolean;
  /** Hidden from the default list, kept in the archive. Never deleted. */
  is_archived?: boolean;
  pinned_at?: string | null;
};

/**
 * Sent as a `model_id` to mean "route automatically". It is a mode, not a
 * model: the backend classifies each turn and picks for itself, so it never
 * appears in `BackendConfig.models`.
 */
export const AUTO_MODEL_ID = "auto";

/**
 * What every section starts on before the user picks anything.
 *
 * A concrete model rather than Auto: Auto is still one click away and its
 * routing is untouched by this, but the model a new conversation opens on
 * should be the strongest general one rather than a dispatcher. The backend's
 * `default_model_id` is authoritative when config has loaded — this is the
 * value used until it does, so the selector never renders empty. Keep the two
 * in step: this is `DEFAULT_MODEL_ID` in backend/app/config.py.
 */
export const DEFAULT_MODEL_ID = "grok-4-5";

export type ModelOption = {
  id: string;
  name: string;
  provider: string;
  /** Provider label the dropdown groups under, e.g. "Groq". */
  group: string;
  /** Optional secondary line, e.g. "17B-16E-Instruct". */
  detail: string | null;
  supports_tools: boolean;
  /** Whether attached images reach this model, or become a text note. */
  supports_vision: boolean;
  /** Set on models Auto can route to; null on manual-only models. */
  routing_hint: string | null;
  /** What the model is good at. Shown as the Auto pool's tooltip copy. */
  description: string | null;
};

export type BackendConfig = {
  openrouter: boolean;
  groq: boolean;
  xai: boolean;
  opencode: boolean;
  e2b: boolean;
  exa: boolean;
  supabase: boolean;
  model: string;
  default_model_id: string;
  /** section → model_id. Auto's fallback table when task routing is off. */
  auto_routes: Record<string, string>;
  /** True when Auto routes by task rather than by section. */
  auto_task_routing: boolean;
  max_iterations: number;
  models: ModelOption[];
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${detail ? ` — ${detail}` : ""}`);
  }
  return (await res.json()) as T;
}

export async function fetchConfig(): Promise<BackendConfig> {
  return json(await fetch(`${HTTP_BASE}/api/config`, { cache: "no-store" }));
}

/**
 * The session shelf, already ordered by the backend: pinned first, then by
 * activity. `archived` swaps to the archive, which is disjoint from it — the
 * ordering rule lives server-side so the Chat and Code lists cannot drift.
 */
export async function listSessions(
  token?: string | null,
  archived = false,
  section: "chat" | "code" = "chat",
): Promise<SessionRow[]> {
  // `section` is required in practice even though it has a default: Chat and
  // Code share this table, and an unscoped list returns both — which is
  // exactly how a Chat session used to turn up in Code's history.
  const search = new URLSearchParams({ section });
  if (archived) search.set("archived", "true");
  return json(
    await fetch(`${HTTP_BASE}/api/sessions?${search}`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

/** Pin, archive, rename, describe. Every field is independent and optional. */
export async function updateSession(
  id: string,
  patch: {
    is_pinned?: boolean;
    is_archived?: boolean;
    title?: string;
    description?: string;
  },
  token?: string | null,
): Promise<SessionRow | null> {
  const res = await fetch(`${HTTP_BASE}/api/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as SessionRow;
}

export async function createSession(
  token?: string | null,
  modelId?: string | null,
  section: "chat" | "code" = "chat",
): Promise<SessionRow> {
  const search = new URLSearchParams({ section });
  if (modelId) search.set("model_id", modelId);
  return json(
    await fetch(`${HTTP_BASE}/api/sessions?${search}`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

export async function deleteSession(id: string, token?: string | null): Promise<void> {
  await fetch(`${HTTP_BASE}/api/sessions/${id}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

/**
 * Ask the backend to name and describe the session from its transcript.
 *
 * On demand rather than automatic: `title` is already generated from the first
 * message for free, and this one reads the whole conversation, so it costs a
 * real model call and is the user's to spend.
 */
export async function autoNameSession(
  id: string,
  token?: string | null,
): Promise<SessionRow> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${id}/name`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

// --- workspace: inline editing, export, preview ----------------------------

export type WorkspaceFile = { path: string; content: string };

/** Read any file in the sandbox — including ones the agent never touched. */
export async function readWorkspaceFile(
  sessionId: string,
  path: string,
  token?: string | null,
): Promise<WorkspaceFile> {
  return json(
    await fetch(
      `${HTTP_BASE}/api/sessions/${sessionId}/file?path=${encodeURIComponent(path)}`,
      { headers: authHeaders(token), cache: "no-store" },
    ),
  );
}

export type SavedFile = {
  path: string;
  content: string;
  diff: string;
  change: "created" | "modified";
  ts: number;
};

/**
 * Save a manual edit into the sandbox.
 *
 * The response is shaped like a `file_changed` event so the caller can fold it
 * into the same state the agent's own edits land in. It comes back over HTTP
 * rather than the socket on purpose: the browser that saved already has the
 * content, and echoing it would land in the editor the user is still typing in.
 */
export async function writeWorkspaceFile(
  sessionId: string,
  path: string,
  content: string,
  token?: string | null,
): Promise<SavedFile> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/file`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ path, content }),
    }),
  );
}

export type WorkspaceTree = { path: string; nodes: FileNode[] };

/**
 * Walk the sandbox now.
 *
 * `file_tree` normally arrives over the socket as a side effect of the agent
 * working. After the *user* changes the filesystem — opening a folder, renaming
 * a file — there is no agent turn to wait for, so the tree is fetched.
 */
export async function fetchWorkspaceTree(
  sessionId: string,
  token?: string | null,
): Promise<WorkspaceTree> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/tree`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export type ImportResult = {
  written: number;
  bytes: number;
  skipped: Array<{ path: string; reason: string }>;
  root?: string;
};

/**
 * Upload one batch of a folder into the sandbox, preserving its structure.
 *
 * The relative paths travel as a JSON array beside the files rather than as
 * their filenames: a multipart part carries only a basename, and the structure
 * is the entire point of the feature.
 */
export async function importWorkspaceFiles(
  sessionId: string,
  files: Array<{ path: string; file: File }>,
  token?: string | null,
  dest = "",
): Promise<ImportResult> {
  const body = new FormData();
  body.append("paths", JSON.stringify(files.map((f) => f.path)));
  if (dest) body.append("dest", dest);
  for (const entry of files) body.append("files", entry.file, entry.file.name);

  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/files`, {
      method: "POST",
      headers: authHeaders(token),
      body,
    }),
  );
}

export type FsOp = "rename" | "delete" | "new_file" | "new_dir";

/** Rename, delete, or create an entry in the sandbox tree. */
export async function workspaceFs(
  sessionId: string,
  op: FsOp,
  path: string,
  name?: string,
  token?: string | null,
): Promise<{ op: FsOp; path: string; previous_path?: string }> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/fs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ op, path, name }),
    }),
  );
}

export type PreviewStatusResponse = {
  running: boolean;
  url: string | null;
  port: number | null;
  command: string | null;
  erroring?: boolean;
  uptime_seconds?: number;
};

export async function fetchPreviewStatus(
  sessionId: string,
  token?: string | null,
): Promise<PreviewStatusResponse> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/preview`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

/**
 * Git state for a session, as the History panel renders it.
 *
 * `repo: false` is the normal "no repository here yet" answer, not an error —
 * the panel turns it into an offer to start one. A session whose sandbox has
 * not been woken reports the same thing rather than paying to wake it.
 */
export type GitSnapshot = {
  repo: boolean;
  path: string;
  branch: string | null;
  status: GitChange[];
  log: GitCommit[];
};

export async function fetchGitState(
  sessionId: string,
  token?: string | null,
  limit = 30,
): Promise<GitSnapshot> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/git?limit=${limit}`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export type GitCommitResult = {
  committed: boolean;
  reason?: string;
  branch: string;
  commit?: GitCommit | null;
  snapshot: GitSnapshot;
};

/**
 * Commit the working tree without spending an agent turn.
 *
 * The backend broadcasts `git_state` on the session socket afterwards, so the
 * panel updates from the same path an agent-driven commit takes and the two
 * can never disagree about what history looks like.
 */
export async function commitWorkspace(
  sessionId: string,
  message: string,
  token?: string | null,
  paths?: string[],
): Promise<GitCommitResult> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/git/commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify(paths?.length ? { message, paths } : { message }),
    }),
  );
}

export async function initRepo(
  sessionId: string,
  token?: string | null,
): Promise<{ repo: string; created: boolean; branch: string; snapshot: GitSnapshot }> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/git/init`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

export async function stopPreview(
  sessionId: string,
  token?: string | null,
): Promise<void> {
  await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/preview`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export type ExportResult = { filename: string; files: number; bytes: number };

/**
 * Download the sandbox project as a zip.
 *
 * The blob is turned into a download here rather than by navigating to the URL,
 * because the endpoint needs the bearer token — and a link cannot carry one.
 */
export async function exportProject(
  sessionId: string,
  token?: string | null,
): Promise<ExportResult> {
  const res = await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/export`, {
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename =
    disposition.match(/filename="([^"]+)"/)?.[1] ?? `project-${sessionId.slice(0, 8)}.zip`;

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoked on the next frame: Safari cancels an in-flight download if the
  // object URL is released synchronously after the click.
  setTimeout(() => URL.revokeObjectURL(url), 2000);

  return {
    filename,
    files: Number(res.headers.get("X-Export-Files") ?? 0),
    bytes: blob.size,
  };
}

export type SessionHistory = {
  checkpoint: {
    messages: Array<{ role: string; content: unknown }>;
    iterations: number;
    usage: { input_tokens?: number; output_tokens?: number; cost_estimate?: number };
  };
  log: Array<{
    id: string;
    role: string;
    content: string | null;
    tool_calls: Record<string, unknown> | null;
    created_at: string;
  }>;
  sandbox_id: string | null;
};

export async function fetchHistory(
  sessionId: string,
  token?: string | null,
): Promise<SessionHistory> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/messages`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export type UploadedFile = {
  id: string;
  filename: string;
  file_type: string;
  size: number;
  persisted: boolean;
};

/** A source attached in the Learning section: an uploaded PDF or a YouTube link. */
export type LearningSource = {
  kind: "youtube" | "pdf";
  id: string;
  title: string;
  /** Image URL for YouTube; absent for PDFs, which render a glyph. */
  thumbnail?: string;
  url?: string;
  /** Extracted transcript text (YouTube). PDFs travel as `fileId` instead. */
  text?: string | null;
  chars?: number;
  /** Upload id for PDFs — the backend turns it into a native document block. */
  fileId?: string;
  /** Set when the source resolved but its content could not be read. */
  error?: string | null;
};

export async function addYouTubeSource(
  url: string,
  sessionId: string,
  token?: string | null,
): Promise<LearningSource> {
  return json(
    await fetch(`${HTTP_BASE}/api/sources/youtube`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ url, session_id: sessionId }),
    }),
  );
}

export async function uploadFile(
  sessionId: string,
  file: File,
  token?: string | null,
): Promise<UploadedFile> {
  const body = new FormData();
  body.append("session_id", sessionId);
  body.append("file", file);
  return json(
    await fetch(`${HTTP_BASE}/api/upload`, {
      method: "POST",
      headers: authHeaders(token),
      body,
    }),
  );
}
