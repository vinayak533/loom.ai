"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useState } from "react";
import {
  type Note,
  addNote as apiAddNote,
  addSource as apiAddSource,
  ask as apiAsk,
  createNotebook as apiCreateNotebook,
  deleteNote as apiDeleteNote,
  deleteNotebook as apiDeleteNotebook,
  deleteSource as apiDeleteSource,
  fetchLearnConfig,
  fetchWorkspace,
  generateLessons as apiGenerateLessons,
  listNotebooks,
  setProgress as apiSetProgress,
  updateNote as apiUpdateNote,
  updateNotebook as apiUpdateNotebook,
  uploadSource,
  type LearnConfig,
  type Notebook,
  type Workspace,
} from "@/lib/learn";
import type { HistoryAction } from "../SessionHistoryMenu";
import { SPRING_SOFT, useMotionOK } from "../Anim";
import { cn } from "@/lib/cn";
import { useToast } from "../Toast";
import { CoursePlatform } from "./CoursePlatform";
import { NotebookLibrary } from "./NotebookLibrary";
import { NotebookWorkspace, type WorkspaceMode } from "./NotebookWorkspace";
import type { NotebookTurn } from "./NotebookChat";
import type { AddPayload } from "./SourcesPanel";

const OPEN_KEY = "atlas:learn:notebook";
const SURFACE_KEY = "atlas:learn:surface";

/** The two things Learn is: authored courses, and your own notebooks. */
type Surface = "courses" | "notebooks";

/**
 * The Learn section.
 *
 * Two surfaces that share a section but nothing else. **Courses** are authored
 * content with a fixed curriculum, chapters, assessments and progress —
 * structured learning, and the default, because it is what someone opening
 * "Learn" with no material of their own can actually start doing. **Notebooks**
 * are the workspace: your PDFs and links, grounded Q&A over them, notes.
 *
 * They are switched rather than merged because the two answer different
 * questions ("teach me this" vs "help me with my sources"), and only one of
 * them is mounted at a time — so opening Learn on Courses never fires a single
 * notebook request.
 */
export function LearnSection({ token }: { token: string | null }) {
  const [surface, setSurface] = useState<Surface>("courses");

  useEffect(() => {
    const stored = localStorage.getItem(SURFACE_KEY);
    if (stored === "courses" || stored === "notebooks") setSurface(stored);
  }, []);

  const select = useCallback((next: Surface) => {
    setSurface(next);
    localStorage.setItem(SURFACE_KEY, next);
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Sits directly under the page header, so it takes the page header's
          own height and gutter: the tab labels line up with the section name
          above them instead of landing 24px off it. */}
      <div className="flex h-bar shrink-0 items-center gap-1 border-b border-line px-gutter">
        <div role="tablist" aria-label="Learn surface" className="flex gap-0.5 rounded-ctl bg-inset p-0.5">
          {(
            [
              ["courses", "Courses"],
              ["notebooks", "Notebooks"],
            ] as Array<[Surface, string]>
          ).map(([key, label]) => (
            <button
              key={key}
              role="tab"
              type="button"
              aria-selected={surface === key}
              onClick={() => select(key)}
              className={cn(
                "h-7 touch:h-11 rounded-[calc(var(--r-ctl)-2px)] px-3 text-2xs font-medium",
                "transition-colors duration-200",
                surface === key
                  ? "bg-raised text-ink"
                  : "text-ink-faint hover:text-ink-muted",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1">
        {surface === "courses" ? (
          <CoursePlatform token={token} />
        ) : (
          <NotebookSection
            token={token}
            onBrowseCourses={() => select("courses")}
          />
        )}
      </div>
    </div>
  );
}

/**
 * The notebook surface.
 *
 * Owns the two views (library, workspace), all of the notebook state, and the
 * one decision that shapes both: a notebook is a *document* workspace, not an
 * agent session, so nothing here touches the websocket. Every call is REST,
 * every answer is one request, and the section can be open while an agent run
 * is streaming in Code without either interfering with the other.
 *
 * Chat history is deliberately *not* persisted. The notes panel is where a
 * notebook keeps things on purpose; a transcript that survived reloads without
 * anyone asking it to would quietly become a second, worse notes panel.
 */
function NotebookSection({
  token,
  onBrowseCourses,
}: {
  token: string | null;
  /** Hands control back to the Courses tab, for the first-run empty state. */
  onBrowseCourses: () => void;
}) {
  const [config, setConfig] = useState<LearnConfig | null>(null);
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [loading, setLoading] = useState(true);
  const [archivedView, setArchivedView] = useState(false);

  const [openId, setOpenId] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [mode, setMode] = useState<WorkspaceMode>("ask");
  const [turns, setTurns] = useState<NotebookTurn[]>([]);

  const [asking, setAsking] = useState(false);
  const [addingSource, setAddingSource] = useState(false);
  /** Which file the ingest is on, so the panel can name it. */
  const [addingLabel, setAddingLabel] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const motionOK = useMotionOK();

  // --- bootstrap -----------------------------------------------------------

  useEffect(() => {
    fetchLearnConfig().then(setConfig).catch(() => setConfig(null));
    setOpenId(localStorage.getItem(OPEN_KEY));
  }, []);

  useEffect(() => {
    if (openId) localStorage.setItem(OPEN_KEY, openId);
    else localStorage.removeItem(OPEN_KEY);
  }, [openId]);

  const refresh = useCallback(() => {
    setLoading(true);
    listNotebooks(token, archivedView)
      .then(setNotebooks)
      .catch(() => setNotebooks([]))
      .finally(() => setLoading(false));
  }, [token, archivedView]);

  useEffect(refresh, [refresh]);

  // Loading a workspace is also how a stale stored id is discovered: a
  // notebook deleted in another tab (or lost to a backend restart in the
  // in-memory mode) drops us back to the library rather than to a dead view.
  useEffect(() => {
    if (!openId) {
      setWorkspace(null);
      return;
    }
    let cancelled = false;
    fetchWorkspace(openId, token)
      .then((data) => {
        if (!cancelled) setWorkspace(data);
      })
      .catch(() => {
        if (!cancelled) {
          setOpenId(null);
          setWorkspace(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [openId, token]);

  // A notebook's transcript belongs to that notebook.
  useEffect(() => {
    setTurns([]);
    setMode("ask");
  }, [openId]);

  const reloadWorkspace = useCallback(async () => {
    if (!openId) return;
    const data = await fetchWorkspace(openId, token).catch(() => null);
    if (data) setWorkspace(data);
  }, [openId, token]);

  const fail = useCallback((err: unknown) => {
    const message = err instanceof Error ? err.message : "Something went wrong.";
    // `fetch` rejects with a bare "Failed to fetch" for every network-level
    // failure, including the backend not being up — which is the likeliest
    // one here and the only one the user can act on.
    setError(
      /failed to fetch|networkerror/i.test(message)
        ? "Could not reach the backend. Check that the FastAPI server is running."
        : message,
    );
  }, []);

  // --- library actions -----------------------------------------------------

  const create = useCallback(async () => {
    try {
      const notebook = await apiCreateNotebook("Untitled notebook", token);
      setNotebooks((n) => [notebook, ...n]);
      setArchivedView(false);
      setOpenId(notebook.id);
    } catch (err) {
      fail(err);
    }
  }, [token, fail]);

  const toast = useToast();

  const notebookAction = useCallback(
    async (id: string, action: HistoryAction) => {
      // Pin is not offered on notebooks — the library grid has no top to pin
      // to — so the menu never raises it here.
      if (action === "pin") return;

      const row = notebooks.find((n) => n.id === id);
      setNotebooks((current) => current.filter((n) => n.id !== id));
      if (id === openId) setOpenId(null);

      if (action === "delete") {
        // Reversible: the card leaves now, the server is asked in six
        // seconds unless Undo puts it back. See `removeSession` in page.tsx.
        toast.undoable(`Deleted “${row?.title?.trim() || "notebook"}”`, {
          revert: () => {
            if (row) setNotebooks((cur) => (cur.some((n) => n.id === id) ? cur : [row, ...cur]));
          },
          commit: async () => {
            try {
              await apiDeleteNotebook(id, token);
            } catch (err) {
              fail(err);
            }
            refresh();
          },
        });
        return;
      }

      try {
        await apiUpdateNotebook(id, { is_archived: !row?.is_archived }, token);
      } catch (err) {
        fail(err);
      }
      refresh();
    },
    [notebooks, openId, token, refresh, fail, toast],
  );

  const rename = useCallback(
    async (title: string) => {
      if (!openId) return;
      setWorkspace((w) => (w ? { ...w, notebook: { ...w.notebook, title } } : w));
      setNotebooks((n) => n.map((x) => (x.id === openId ? { ...x, title } : x)));
      await apiUpdateNotebook(openId, { title }, token).catch(fail);
    },
    [openId, token, fail],
  );

  // --- workspace actions ---------------------------------------------------

  const addSource = useCallback(
    async (payload: AddPayload) => {
      if (!openId) return;
      setAddingSource(true);
      try {
        if (payload.kind === "pdf") {
          // Sequential rather than parallel: each upload runs extraction and
          // an embedding pass on the backend, and firing five at once at a
          // single-instance service is how you turn "add sources" into a
          // timeout.
          const files = Array.from(payload.files);
          for (const [i, file] of files.entries()) {
            setAddingLabel(
              files.length > 1
                ? `${file.name} (${i + 1} of ${files.length})`
                : file.name,
            );
            await uploadSource(openId, file, token);
          }
        } else if (payload.kind === "url") {
          await apiAddSource(openId, { source_type: "url", url: payload.url }, token);
        } else {
          await apiAddSource(openId, { source_type: "text", text: payload.text }, token);
        }
        await reloadWorkspace();
      } catch (err) {
        fail(err);
      } finally {
        setAddingSource(false);
        setAddingLabel(null);
      }
    },
    [openId, token, reloadWorkspace, fail],
  );

  const removeSource = useCallback(
    async (sourceId: string) => {
      if (!openId) return;
      setWorkspace((w) =>
        w ? { ...w, sources: w.sources.filter((s) => s.id !== sourceId) } : w,
      );
      await apiDeleteSource(openId, sourceId, token).catch(fail);
      await reloadWorkspace();
    },
    [openId, token, reloadWorkspace, fail],
  );

  const ask = useCallback(
    async (question: string) => {
      if (!openId) return;
      const id = crypto.randomUUID();
      const history = turns.map((t) => ({ role: t.role, content: t.content }));
      setTurns((t) => [...t, { id, role: "user", content: question }]);
      setAsking(true);
      try {
        const result = await apiAsk(openId, question, history, null, token);
        setTurns((t) => [
          ...t,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: result.answer,
            citations: result.citations,
          },
        ]);
      } catch (err) {
        setTurns((t) => [
          ...t,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: err instanceof Error ? err.message : "The answer failed.",
            error: true,
          },
        ]);
      } finally {
        setAsking(false);
      }
    },
    [openId, token, turns],
  );

  const createNote = useCallback(
    async (content: string, source: "ai_generated" | "user_written" = "user_written") => {
      if (!openId) return;
      try {
        const note = await apiAddNote(openId, content, source, token);
        setWorkspace((w) => (w ? { ...w, notes: [note, ...w.notes] } : w));
      } catch (err) {
        fail(err);
      }
    },
    [openId, token, fail],
  );

  const updateNote = useCallback(
    async (noteId: string, content: string) => {
      if (!openId) return;
      setWorkspace((w) =>
        w
          ? {
              ...w,
              notes: w.notes.map((n) => (n.id === noteId ? { ...n, content } : n)),
            }
          : w,
      );
      await apiUpdateNote(openId, noteId, content, token).catch(fail);
    },
    [openId, token, fail],
  );

  const deleteNote = useCallback(
    async (noteId: string) => {
      if (!openId) return;
      const notebookId = openId;
      let removed: Note | undefined;
      setWorkspace((w) => {
        if (!w) return w;
        removed = w.notes.find((n) => n.id === noteId);
        return { ...w, notes: w.notes.filter((n) => n.id !== noteId) };
      });
      toast.undoable("Note deleted", {
        revert: () =>
          setWorkspace((w) =>
            w && removed && !w.notes.some((n) => n.id === noteId)
              ? { ...w, notes: [removed, ...w.notes] }
              : w,
          ),
        commit: () => apiDeleteNote(notebookId, noteId, token).catch(fail),
      });
    },
    [openId, token, fail, toast],
  );

  const generateCourse = useCallback(async () => {
    if (!openId) return;
    setGenerating(true);
    try {
      const { lessons, progress } = await apiGenerateLessons(openId, null, token);
      setWorkspace((w) => (w ? { ...w, lessons, progress } : w));
    } catch (err) {
      fail(err);
    } finally {
      setGenerating(false);
    }
  }, [openId, token, fail]);

  const completeSection = useCallback(
    async (sectionId: string, completed: boolean) => {
      if (!openId) return;
      setWorkspace((w) => {
        if (!w) return w;
        const others = w.progress.filter((p) => p.section_id !== sectionId);
        return {
          ...w,
          progress: [
            ...others,
            {
              id: sectionId,
              section_id: sectionId,
              completed,
              completed_at: completed ? new Date().toISOString() : null,
            },
          ],
        };
      });
      await apiSetProgress(openId, sectionId, completed, token).catch(fail);
    },
    [openId, token, fail],
  );

  // --- render --------------------------------------------------------------

  return (
    <div className="relative h-full min-h-0">
      {workspace ? (
        <NotebookWorkspace
          title={workspace.notebook.title}
          mode={mode}
          onMode={setMode}
          sources={workspace.sources}
          notes={workspace.notes}
          lessons={workspace.lessons}
          progress={workspace.progress}
          turns={turns}
          asking={asking}
          addingSource={addingSource}
          addingLabel={addingLabel}
          generating={generating}
          modelName={config?.model_name ?? null}
          onBack={() => setOpenId(null)}
          onAsk={ask}
          onAddSource={addSource}
          onRemoveSource={removeSource}
          // Two callbacks rather than one with a guess: which surface saved
          // the note is the only reliable signal of who wrote it, and the
          // panel shows that distinction.
          onCreateNote={(content) => createNote(content, "user_written")}
          onSaveAnswer={(content) => createNote(content, "ai_generated")}
          onUpdateNote={updateNote}
          onDeleteNote={deleteNote}
          onGenerateCourse={generateCourse}
          onCompleteSection={completeSection}
          onRename={rename}
        />
      ) : (
        <>
          <NotebookLibrary
            notebooks={notebooks}
            loading={loading}
            archivedView={archivedView}
            onArchivedView={setArchivedView}
            onOpen={setOpenId}
            onCreate={create}
            onAction={notebookAction}
            onBrowseCourses={onBrowseCourses}
          />
          {config && !config.persisted && (
            <p className="pointer-events-none absolute inset-x-0 bottom-3 mx-auto w-fit rounded-ctl
                          border border-warn/25 bg-[rgba(240,181,74,0.09)] px-3 py-1.5 text-2xs text-warn">
              Supabase is not configured — notebooks live in the backend’s memory
              and are lost on restart.
            </p>
          )}
        </>
      )}

      <AnimatePresence>
        {error && (
          <motion.div
            initial={motionOK ? { opacity: 0, y: 12 } : { opacity: 0 }}
            animate={{ opacity: 1, y: 0 }}
            exit={motionOK ? { opacity: 0, y: 12 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SOFT : { duration: 0 }}
            className="glass absolute bottom-4 left-1/2 z-50 flex max-w-[min(420px,90%)] -translate-x-1/2
                       items-start gap-2.5 rounded-ctl border-del/30 px-3.5 py-2.5"
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
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
