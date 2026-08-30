/**
 * The browser talks to exactly one backend: our FastAPI service. It never
 * holds an OpenCode / OpenRouter / Groq / E2B / Exa key — those live server-side.
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
  /** Which project this conversation is filed under, or null for unfiled. */
  project_id?: string | null;
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
export const DEFAULT_MODEL_ID = "qwen3_7_plus";

/**
 * What each section opens on, before `/api/config` has answered.
 *
 * The sections do different work and open on different models: Code on MiMo
 * V2.5 (long-horizon coding, and it reads images), Chat on DeepSeek V4 Flash
 * (fastest and cheapest — what a conversational surface should start on),
 * Learn on MiMo V2.5 to match the tutor's own preference order. Agents keeps
 * its per-specialist selection and only needs a placeholder here.
 *
 * Same contract as `DEFAULT_MODEL_ID` above: this is the pre-config value, and
 * `config.default_model_ids` — already resolved server-side to models this
 * deployment can actually reach — takes over the moment it lands. Keep in step
 * with `DEFAULT_MODEL_*` in backend/app/config.py.
 */
export const SECTION_DEFAULT_MODEL_ID: Record<string, string> = {
  chat: "deepseek_v4_flash",
  code: "mimo_v2_5",
  learning: "mimo_v2_5",
  agents: DEFAULT_MODEL_ID,
};

/** The section's opening model, preferring what the backend reports. */
export function defaultModelFor(
  section: string,
  config?: { default_model_ids?: Record<string, string>; default_model_id?: string } | null,
): string {
  return (
    config?.default_model_ids?.[section] ||
    SECTION_DEFAULT_MODEL_ID[section] ||
    config?.default_model_id ||
    DEFAULT_MODEL_ID
  );
}

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
  opencode: boolean;
  e2b: boolean;
  exa: boolean;
  supabase: boolean;
  model: string;
  default_model_id: string;
  /**
   * section → the model a new session in that section opens on, already
   * resolved server-side to something this deployment has a key for.
   */
  default_model_ids: Record<string, string>;
  /** section → model_id. Auto's fallback table when task routing is off. */
  auto_routes: Record<string, string>;
  /** True when Auto routes by task rather than by section. */
  auto_task_routing: boolean;
  /**
   * Whether the backend rejects an anonymous caller. Decides whether the
   * post-sign-out screen offers a way back in without an account.
   */
  require_auth: boolean;
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

/**
 * Upload one file, reporting progress and cancellable.
 *
 * `fetch` cannot do either half of that: it has no upload-progress event, and
 * aborting it mid-body is unreliable across browsers. So this is XHR — the
 * only API in the platform that will tell you how many bytes have actually
 * gone out. Without it a progress bar can only be a lie or a spinner, and a
 * user who picked the wrong 40 MB PDF has no way to take it back.
 *
 * `onProgress` receives 0–1, and is only called while the length is
 * computable; a chunked request reports nothing rather than inventing a
 * number, which the chip renders as an indeterminate state.
 */
export function uploadFileTracked(
  sessionId: string,
  file: File,
  token?: string | null,
  opts: { onProgress?: (fraction: number) => void; signal?: AbortSignal } = {},
): Promise<UploadedFile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const body = new FormData();
    body.append("session_id", sessionId);
    body.append("file", file);

    xhr.open("POST", `${HTTP_BASE}/api/upload`);
    for (const [k, v] of Object.entries(authHeaders(token))) {
      xhr.setRequestHeader(k, v as string);
    }

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && opts.onProgress) {
        opts.onProgress(e.loaded / e.total);
      }
    };
    xhr.onload = () => {
      let parsed: unknown = null;
      try {
        parsed = JSON.parse(xhr.responseText);
      } catch {
        /* handled below */
      }
      if (xhr.status >= 200 && xhr.status < 300 && parsed) {
        opts.onProgress?.(1);
        resolve(parsed as UploadedFile);
        return;
      }
      const detail =
        (parsed as { detail?: string } | null)?.detail ??
        `Upload failed (${xhr.status})`;
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error("Upload failed — the request did not complete."));
    xhr.ontimeout = () => reject(new Error("Upload timed out."));
    // A cancel is a user decision, not a failure. The caller tells the two
    // apart by the error's name, exactly as it would with `fetch`.
    xhr.onabort = () => {
      const err = new Error("Upload cancelled.");
      err.name = "AbortError";
      reject(err);
    };

    if (opts.signal) {
      if (opts.signal.aborted) {
        xhr.abort();
        return;
      }
      opts.signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }

    xhr.send(body);
  });
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

/**
 * A session that matched a search, and how.
 *
 * `match` distinguishes a title hit from a hit somewhere in the transcript,
 * which is what lets the list explain itself — a row whose title has nothing
 * to do with the query is baffling until you can see the line that matched.
 */
export type SessionSearchHit = SessionRow & {
  match: "title" | "message";
  /** The matched phrase with a little context. Null for a title match. */
  snippet: string | null;
};

/**
 * Search a section's history by title and by message content.
 *
 * A database query, not a generative one — two indexed `ilike` scans on the
 * server. Callers debounce; this does not, because a hook that owns the timer
 * can also cancel it, and one buried in a fetch helper cannot.
 *
 * An empty query returns `[]` without a request. The server would answer the
 * same way, but not making the call at all is what keeps a cleared input from
 * putting a round trip on the wire for a question with no answer.
 */
export async function searchSessions(
  query: string,
  token?: string | null,
  section: "chat" | "code" = "chat",
  options: { agentId?: string | null; signal?: AbortSignal } = {},
): Promise<SessionSearchHit[]> {
  const q = query.trim();
  if (!q) return [];
  const search = new URLSearchParams({ q });
  if (options.agentId) search.set("agent_id", options.agentId);
  else search.set("section", section);
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/search?${search}`, {
      headers: authHeaders(token),
      cache: "no-store",
      signal: options.signal,
    }),
  );
}

/** One person's thumbs on one session, keyed by assistant-turn ordinal. */
export type FeedbackRow = {
  turn_index: number;
  rating: "up" | "down";
  reason?: string | null;
};

export async function fetchFeedback(
  sessionId: string,
  token?: string | null,
): Promise<FeedbackRow[]> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/feedback`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

/** Record a verdict, or pass `rating: null` to withdraw one. */
export async function sendFeedback(
  sessionId: string,
  turnIndex: number,
  rating: "up" | "down" | null,
  token?: string | null,
  extra: { reason?: string; modelId?: string | null; section?: string } = {},
): Promise<void> {
  await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({
      turn_index: turnIndex,
      rating,
      reason: extra.reason,
      model_id: extra.modelId,
      section: extra.section,
    }),
  });
}

/**
 * Preferences that follow the account rather than the browser.
 *
 * `default_model_id` is null when the user has never chosen one, which is not
 * the same as choosing whatever the current default happens to be: someone who
 * has expressed no preference should follow the build's default when it moves,
 * and someone who has should not.
 */
export type Preferences = {
  user_id: string | null;
  default_model_id: string | null;
  theme: string;
  /**
   * The two halves of "custom instructions", asked as two questions because
   * they are answered differently: one is who you are, the other is how you
   * want to be written to. Always strings — the server coalesces null to ""
   * so a textarea can bind to them directly.
   */
  about_you: string;
  response_style: string;
  /**
   * Whether new facts are learned from conversations. It does NOT gate the two
   * fields above: those were typed into this panel deliberately, and turning
   * memory off means "stop learning things about me", not "discard what I told
   * you on purpose".
   */
  memory_enabled: boolean;
};

export async function fetchPreferences(
  token?: string | null,
): Promise<Preferences> {
  return json(
    await fetch(`${HTTP_BASE}/api/preferences`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

/** Save preferences. Requires a signed-in account; the server rejects otherwise. */
export async function savePreferences(
  patch: {
    default_model_id?: string | null;
    theme?: string;
    about_you?: string;
    response_style?: string;
    memory_enabled?: boolean;
  },
  token?: string | null,
): Promise<Preferences> {
  return json(
    await fetch(`${HTTP_BASE}/api/preferences`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify(patch),
    }),
  );
}
