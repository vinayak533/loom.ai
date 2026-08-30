"use client";

import { motion } from "framer-motion";
import { FileText, Plus, Trash2, TriangleAlert, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import type { ProjectDetail, ProjectFile } from "@/lib/projects";
import {
  deleteProjectFile,
  fetchProject,
  uploadProjectFile,
} from "@/lib/projects";
import type { ProjectsState } from "@/lib/useProjects";
import { SPRING_SOFT, useMotionOK } from "../Anim";

/**
 * A project, opened.
 *
 * Three things live here and the order they appear in is the argument: what
 * this project *is*, what the agent is told about it, and what is in it. The
 * instructions box sits above the file list because it is the part that
 * changes behaviour on the very next message, and the part people come back to
 * edit.
 *
 * Deleting says what it does. "Delete project" reads like it takes the
 * conversations with it, and it does not — the foreign key is `set null` and
 * the sessions become unfiled. A destructive action whose blast radius the
 * user has guessed wrong is the one case where the confirmation has to spell
 * the consequence out rather than just asking twice.
 */
export function ProjectPanel({
  projectId,
  projects,
  token,
  onClose,
  onOpenSession,
}: {
  projectId: string;
  projects: ProjectsState;
  token?: string | null;
  onClose: () => void;
  onOpenSession: (sessionId: string) => void;
}) {
  const motionOK = useMotionOK();
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    fetchProject(projectId, token)
      .then((d) => {
        setDetail(d);
        setError(null);
      })
      .catch(() => setError("Could not open that project."));
  }, [projectId, token]);

  useEffect(load, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const project = projects.byId(projectId) ?? detail;

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setError(null);
    for (const file of Array.from(files)) {
      try {
        await uploadProjectFile(projectId, file, token);
      } catch (e) {
        setError(
          e instanceof Error && e.message.includes("413")
            ? `${file.name} is larger than the 20 MB limit.`
            : `Could not add ${file.name}.`,
        );
      }
    }
    setUploading(false);
    load();
  };

  return (
    <motion.div
      className="fixed inset-0 z-[65] grid place-items-center overflow-y-auto p-5"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: motionOK ? 0.16 : 0 }}
    >
      <button
        type="button"
        aria-label="Close project"
        onClick={onClose}
        className="absolute inset-0 bg-black/62 backdrop-blur-[2px]"
      />

      <motion.section
        role="dialog"
        aria-modal="true"
        aria-label={project?.name ?? "Project"}
        initial={motionOK ? { opacity: 0, y: 12, scale: 0.985 } : false}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={motionOK ? { opacity: 0, y: 8, scale: 0.99 } : { opacity: 0 }}
        transition={motionOK ? SPRING_SOFT : { duration: 0 }}
        className="glass relative flex max-h-[85vh] w-full max-w-[34rem] flex-col
                   overflow-hidden rounded-card"
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <input
              value={project?.name ?? ""}
              aria-label="Project name"
              onChange={(e) =>
                setDetail((d) => (d ? { ...d, name: e.target.value } : d))
              }
              onBlur={(e) => {
                const name = e.target.value.trim();
                if (name && name !== projects.byId(projectId)?.name) {
                  void projects.update(projectId, { name });
                }
              }}
              className="w-full truncate border-none bg-transparent p-0 font-sans text-base
                         text-ink outline-none placeholder:text-ink-faint"
            />
            <p className="mt-0.5 text-2xs text-ink-faint">
              {detail
                ? `${detail.sessions.length} conversation${detail.sessions.length === 1 ? "" : "s"} · ${detail.files.length} file${detail.files.length === 1 ? "" : "s"}`
                : "Loading…"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-ctl p-1.5 text-ink-faint transition-colors
                       hover:bg-raised hover:text-ink"
          >
            <X size={15} strokeWidth={1.75} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {/* -------------------------------------------------- instructions */}
          <h3 className="voice-label pb-2">Instructions</h3>
          <p className="pb-2 text-2xs leading-relaxed text-ink-faint">
            Prepended to every conversation in this project, and they win over
            your account-wide preferences where the two disagree.
          </p>
          <InstructionsBox
            value={project?.instructions ?? ""}
            onSave={(instructions) =>
              projects.update(projectId, { instructions })
            }
          />

          {/* ------------------------------------------------------- knowledge */}
          <div className="mt-6 flex items-center justify-between gap-4 pb-2">
            <h3 className="voice-label">Knowledge</h3>
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              disabled={uploading}
              className="flex items-center gap-1.5 rounded-ctl border border-line bg-elevated
                         px-2.5 py-1.5 text-2xs text-ink-muted transition-colors
                         hover:border-accent-line hover:text-ink disabled:opacity-55"
            >
              <Plus size={12} strokeWidth={2} />
              {uploading ? "Adding…" : "Add file"}
            </button>
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                void upload(e.target.files);
                e.target.value = "";
              }}
            />
          </div>

          {detail && detail.files.length === 0 && (
            <p className="text-2xs leading-relaxed text-ink-faint">
              PDFs and text files the agent should treat as background. They are
              read as text — a scanned PDF has no text layer and will be kept
              but marked unusable.
            </p>
          )}

          <ul className="flex flex-col gap-1.5">
            {(detail?.files ?? []).map((f) => (
              <KnowledgeRow
                key={f.id}
                file={f}
                onDelete={async () => {
                  setDetail((d) =>
                    d ? { ...d, files: d.files.filter((x) => x.id !== f.id) } : d,
                  );
                  try {
                    await deleteProjectFile(projectId, f.id, token);
                  } catch {
                    setError("Could not remove that file.");
                    load();
                  }
                }}
              />
            ))}
          </ul>

          {/* -------------------------------------------------- conversations */}
          <h3 className="voice-label mt-6 pb-2">Conversations</h3>
          {detail && detail.sessions.length === 0 && (
            <p className="text-2xs leading-relaxed text-ink-faint">
              Nothing filed here yet. Move a conversation in from its menu in
              the history list.
            </p>
          )}
          <ul className="flex flex-col gap-1">
            {(detail?.sessions ?? []).map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => {
                    onOpenSession(s.id);
                    onClose();
                  }}
                  className="w-full truncate rounded-ctl px-3 py-2 text-left text-xs
                             text-ink-muted transition-colors hover:bg-elevated hover:text-ink"
                >
                  {s.title}
                </button>
              </li>
            ))}
          </ul>

          {error && (
            <p className="pt-3 text-2xs text-warn" role="alert">
              {error}
            </p>
          )}
        </div>

        {/* ------------------------------------------------------------ delete */}
        <footer className="shrink-0 border-t border-line px-5 py-3">
          {!confirmDelete ? (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="text-2xs text-ink-faint transition-colors hover:text-del"
            >
              Delete project
            </button>
          ) : (
            <div className="flex items-center justify-between gap-4">
              <p className="text-2xs leading-relaxed text-ink-muted">
                Delete this project? Its {detail?.sessions.length ?? 0}{" "}
                conversation
                {detail?.sessions.length === 1 ? "" : "s"} will be kept and
                become unfiled.
              </p>
              <div className="flex shrink-0 gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="rounded-ctl px-2.5 py-1.5 text-2xs text-ink-muted
                             transition-colors hover:bg-elevated hover:text-ink"
                >
                  Keep
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    await projects.remove(projectId);
                    onClose();
                  }}
                  className="rounded-ctl border border-del/40 px-2.5 py-1.5 text-2xs
                             text-del transition-colors hover:bg-del/10"
                >
                  Delete
                </button>
              </div>
            </div>
          )}
        </footer>
      </motion.section>
    </motion.div>
  );
}

/** Saved on blur, with the status under it. Same contract as Settings. */
function InstructionsBox({
  value,
  onSave,
}: {
  value: string;
  onSave: (value: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState(value);
  const [state, setState] = useState<"clean" | "dirty" | "saving" | "saved">("clean");
  const focused = useRef(false);

  useEffect(() => {
    if (!focused.current) setDraft(value);
  }, [value]);

  return (
    <>
      <textarea
        rows={4}
        value={draft}
        aria-label="Project instructions"
        placeholder="Always use Python 3.12. The database is Postgres 16. Never suggest Rails."
        onFocus={() => {
          focused.current = true;
        }}
        onChange={(e) => {
          setDraft(e.target.value);
          setState("dirty");
        }}
        onBlur={async () => {
          focused.current = false;
          if (draft === value) return setState("clean");
          setState("saving");
          await onSave(draft);
          setState("saved");
        }}
        className="w-full resize-y rounded-ctl border border-line bg-inset px-3 py-2
                   font-sans text-xs leading-relaxed text-ink transition-colors duration-200
                   placeholder:text-ink-faint focus:border-line-focus focus:outline-none"
      />
      <p className="pt-1.5 text-2xs text-ink-faint">
        {state === "saving" && "Saving…"}
        {state === "saved" && "Saved."}
        {state === "dirty" && "Click outside the box to save."}
      </p>
    </>
  );
}

function KnowledgeRow({
  file,
  onDelete,
}: {
  file: ProjectFile;
  onDelete: () => void;
}) {
  const failed = file.status === "failed";
  return (
    <li
      className={cn(
        "group flex items-start justify-between gap-3 rounded-ctl border bg-inset px-3 py-2",
        failed ? "border-warn/30" : "border-line",
      )}
    >
      <span className="flex min-w-0 items-start gap-2">
        {failed ? (
          <TriangleAlert
            size={13}
            strokeWidth={1.75}
            className="mt-0.5 shrink-0 text-warn"
          />
        ) : (
          <FileText
            size={13}
            strokeWidth={1.75}
            className="mt-0.5 shrink-0 text-ink-faint"
          />
        )}
        <span className="min-w-0">
          <span className="block truncate text-2xs text-ink">{file.name}</span>
          <span className="mt-0.5 block text-2xs leading-relaxed text-ink-faint">
            {failed
              ? file.error ?? "No text could be read from this file."
              : `${file.char_count.toLocaleString()} characters`}
          </span>
        </span>
      </span>
      <button
        type="button"
        aria-label={`Remove ${file.name}`}
        onClick={onDelete}
        className="shrink-0 rounded-[5px] p-1 text-ink-faint opacity-0 transition-opacity
                   duration-150 hover:text-del focus-visible:opacity-100 group-hover:opacity-100"
      >
        <Trash2 size={13} strokeWidth={1.75} />
      </button>
    </li>
  );
}
