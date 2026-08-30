"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Project } from "./projects";
import {
  createProject,
  deleteProject,
  listProjects,
  setSessionProject,
  updateProject,
} from "./projects";

/**
 * The fields a project edit can touch.
 *
 * Spelled out rather than `Partial<Project>` because the two are genuinely
 * different: the row has nullable columns, but "set this to null" is not an
 * edit this UI ever makes — clearing a box sends "", which the server maps to
 * null itself. Accepting null here would type-check a call the API rejects.
 */
export type ProjectPatch = {
  name?: string;
  description?: string;
  instructions?: string;
  color?: string;
  is_archived?: boolean;
};

/**
 * Project state, kept out of `page.tsx`.
 *
 * That file is already the largest in the frontend and carries most of the
 * shell's state; adding a project list, a selection, a loading flag and five
 * mutations directly to it makes the next feature worse rather than the same.
 * Everything a caller needs is returned as one object with stable callbacks,
 * so the components below it can stay memoized.
 *
 * There is no cache and no optimistic list. A project list is a few dozen rows
 * at most and every mutation is followed by a re-fetch, which is one round trip
 * against something the user just waited for anyway. The exception is `filter`,
 * which is pure client state and never round-trips.
 */
export type ProjectsState = {
  projects: Project[];
  loading: boolean;
  error: string | null;
  /**
   * Which project the session list is narrowed to, or null for "everything".
   *
   * Deliberately distinct from "sessions in no project": null here means the
   * filter is off. Narrowing to the unfiled sessions is not currently offered,
   * because the unfiltered list already shows them alongside everything else
   * and a third state on this control would have to earn its place.
   */
  filter: string | null;
  setFilter: (id: string | null) => void;
  reload: () => void;
  create: (name: string) => Promise<Project | null>;
  update: (id: string, patch: ProjectPatch) => Promise<void>;
  remove: (id: string) => Promise<void>;
  assign: (sessionId: string, projectId: string | null) => Promise<void>;
  byId: (id: string | null | undefined) => Project | null;
};

export function useProjects(
  token: string | null | undefined,
  signedIn: boolean,
): ProjectsState {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    listProjects(token)
      .then((rows) => {
        setProjects(rows);
        setError(null);
      })
      .catch(() => setError("Could not load your projects."))
      .finally(() => setLoading(false));
  }, [token]);

  // Re-runs on sign-in and sign-out, which is the point: projects are scoped to
  // the account, and the anonymous shelf is a different set of rows rather than
  // an empty one.
  useEffect(() => {
    reload();
  }, [reload, signedIn]);

  // A filter pointing at a project that no longer exists would silently show an
  // empty session list with no way back, so it is cleared when its target goes.
  useEffect(() => {
    if (filter && !projects.some((p) => p.id === filter)) setFilter(null);
  }, [projects, filter]);

  const create = useCallback(
    async (name: string) => {
      try {
        const row = await createProject({ name }, token);
        setProjects((list) => [row, ...list]);
        return row;
      } catch {
        setError("Could not create that project.");
        return null;
      }
    },
    [token],
  );

  const update = useCallback(
    async (id: string, patch: ProjectPatch) => {
      // Optimistic: the user is looking at the field they just changed, and
      // reverting it under them on a slow round trip reads as data loss.
      setProjects((list) =>
        list.map((p) => (p.id === id ? { ...p, ...patch } : p)),
      );
      try {
        const saved = await updateProject(id, patch, token);
        setProjects((list) => list.map((p) => (p.id === id ? saved : p)));
      } catch {
        setError("Could not save that change.");
        reload();
      }
    },
    [token, reload],
  );

  const remove = useCallback(
    async (id: string) => {
      setProjects((list) => list.filter((p) => p.id !== id));
      try {
        await deleteProject(id, token);
      } catch {
        setError("Could not delete that project.");
        reload();
      }
    },
    [token, reload],
  );

  const assign = useCallback(
    async (sessionId: string, projectId: string | null) => {
      try {
        await setSessionProject(sessionId, projectId, token);
      } catch {
        setError("Could not move that conversation.");
      }
    },
    [token],
  );

  const byId = useCallback(
    (id: string | null | undefined) =>
      id ? projects.find((p) => p.id === id) ?? null : null,
    [projects],
  );

  /**
   * Memoized, and it has to be.
   *
   * This object is passed straight to `SessionSidebar`, which is `memo`'d
   * specifically so that streaming a reply does not re-render the session
   * list. A fresh object literal on every render defeats that guard
   * completely — and it also re-creates every `useCallback` in `page.tsx`
   * that lists this hook in its dependencies, which defeats the guard a
   * second way. The members below are each already stable.
   */
  return useMemo(
    () => ({
      projects,
      loading,
      error,
      filter,
      setFilter,
      reload,
      create,
      update,
      remove,
      assign,
      byId,
    }),
    [projects, loading, error, filter, reload, create, update, remove, assign, byId],
  );
}
