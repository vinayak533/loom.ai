"use client";

/**
 * Artifacts: the browser half.
 *
 * Reads and one write. The model's writes arrive over the socket rather than
 * through here — the panel is watching a live conversation, and a fetch after
 * every revision would be a round trip to display something the event already
 * carried.
 */

import { HTTP_BASE } from "./api";
import type { ArtifactPayload } from "./events";

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

/** A stored row. The socket payload plus the columns only the API returns. */
export type ArtifactRow = ArtifactPayload & {
  id: string;
  session_id: string;
  artifact_key: string;
  created_at: string;
};

/** Turn a stored row into the shape the socket sends, so the UI has one type. */
export function toPayload(row: ArtifactRow): ArtifactPayload {
  return {
    artifact_id: row.id,
    key: row.artifact_key,
    version: row.version,
    kind: row.kind,
    title: row.title,
    language: row.language ?? "",
    content: row.content,
    created_by: row.created_by,
  };
}

/**
 * Every artifact in a session, current versions only.
 *
 * Called once when a session is opened: the socket carries everything written
 * *during* a conversation, but a session resumed after a reload has artifacts
 * nobody is going to re-emit.
 */
export async function listArtifacts(
  sessionId: string,
  token?: string | null,
): Promise<ArtifactRow[]> {
  return json(
    await fetch(`${HTTP_BASE}/api/sessions/${sessionId}/artifacts`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

/** Every version of one artifact, oldest first. */
export async function artifactVersions(
  sessionId: string,
  key: string,
  token?: string | null,
): Promise<ArtifactRow[]> {
  return json(
    await fetch(
      `${HTTP_BASE}/api/sessions/${sessionId}/artifacts/${encodeURIComponent(key)}/versions`,
      { headers: authHeaders(token), cache: "no-store" },
    ),
  );
}

/**
 * Save a person's edit.
 *
 * This *adds* a version attributed to the user; it does not overwrite the
 * model's. Any UI wording around it should say so, because "save" normally
 * means replace and here it does not.
 */
export async function saveArtifact(
  sessionId: string,
  key: string,
  content: string,
  token?: string | null,
): Promise<ArtifactRow> {
  return json(
    await fetch(
      `${HTTP_BASE}/api/sessions/${sessionId}/artifacts/${encodeURIComponent(key)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...authHeaders(token) },
        body: JSON.stringify({ content }),
      },
    ),
  );
}
