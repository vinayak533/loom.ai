"use client";

import { motion } from "framer-motion";
import { memo, useCallback, useMemo, useState } from "react";
import type { GitChange, GitCommit } from "@/lib/events";
import type { GitState } from "@/lib/useAgentSocket";
import { cn, shortPath } from "@/lib/cn";
import { SPRING_SNAP, useMotionOK } from "./Anim";

/**
 * The Code section's version-control face.
 *
 * A sandbox is ephemeral by design, which is exactly why history inside it is
 * worth surfacing: it is the one artefact of a session that survives being
 * exported, and until now the only way to see it was to ask the agent to run
 * `git log` and read the answer out of a tool card.
 *
 * Three states, and no empty one:
 *
 *  · **No repository.** An offer to start one, not a blank list. The offer is
 *    the feature — most sessions never think to ask for git, and a one-click
 *    start is what makes it something people actually use.
 *  · **Repository, uncommitted work.** The changes come first, with the commit
 *    box directly under them, because the reason you opened this panel while
 *    changes are pending is to commit them.
 *  · **Repository, clean.** History alone.
 *
 * The commit box does not try to be a staging UI. Partial staging is a power
 * feature that would need a second selection model on top of the file list,
 * and the agent's `git` tool already accepts a path list for the rare case.
 * What this does is the common case, well: type why, commit everything.
 */

const CHANGE_TONE: Record<string, string> = {
  modified: "text-warn",
  added: "text-ok",
  untracked: "text-ok",
  deleted: "text-danger",
  renamed: "text-accent",
  conflicted: "text-danger",
};

function changeLabel(entry: GitChange): string {
  return entry.index || entry.worktree || "changed";
}

/** Relative where it reads better than a date, absolute once it stops. */
function when(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 604800) return `${Math.floor(secs / 86400)}d ago`;
  return new Date(then).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export const GitPanel = memo(function GitPanel({
  git,
  root,
  busy,
  error,
  onCommit,
  onInit,
  onSelectFile,
}: {
  git: GitState;
  root: string;
  /** True while a commit or init is in flight. */
  busy: boolean;
  error: string | null;
  onCommit: (message: string) => void;
  onInit: () => void;
  onSelectFile?: (path: string) => void;
}) {
  const motionOK = useMotionOK();
  const [message, setMessage] = useState("");

  const changes = git?.status ?? [];
  const commits = git?.log ?? [];
  const canCommit = changes.length > 0 && message.trim().length > 0 && !busy;

  const submit = useCallback(() => {
    if (!canCommit) return;
    onCommit(message.trim());
    setMessage("");
  }, [canCommit, message, onCommit]);

  // Cmd/Ctrl+Enter commits — the same chord every commit box in every editor
  // uses, so nobody has to be told.
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        submit();
      }
    },
    [submit],
  );

  const grouped = useMemo(
    () =>
      [...changes].sort((a, b) => a.path.localeCompare(b.path)),
    [changes],
  );

  // --- no repository yet ----------------------------------------------------

  if (git && !git.repo) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 px-8 text-center">
        <BranchMark />
        <div className="space-y-1.5">
          <p className="text-sm text-ink">No version control yet</p>
          <p className="max-w-[34ch] text-xs leading-relaxed text-ink-faint">
            Start a repository and this session&rsquo;s work becomes real
            history — every checkpoint commitable, reviewable, and exportable.
          </p>
        </div>
        <button
          type="button"
          onClick={onInit}
          disabled={busy}
          className="rounded-ctl border border-line bg-elevated px-3.5 py-2 text-xs
                     text-ink transition-colors duration-200 hover:border-accent/40
                     hover:text-accent disabled:opacity-50"
        >
          {busy ? "Starting…" : "Start a repository"}
        </button>
        {error && <p className="text-xs text-danger">{error}</p>}
      </div>
    );
  }

  // --- not told yet ---------------------------------------------------------

  if (!git) {
    return (
      <div className="grid h-full place-items-center px-8 text-center">
        <p className="text-xs text-ink-faint">
          Version control appears once this session has a sandbox.
        </p>
      </div>
    );
  }

  // --- repository -----------------------------------------------------------

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 px-4 pb-2 pt-1">
        <BranchMark small />
        <span className="font-mono text-xs text-ink">{git.branch}</span>
        <span className="ml-auto text-[11px] text-ink-faint">
          {commits.length === 0
            ? "no commits"
            : `${commits.length} commit${commits.length === 1 ? "" : "s"}`}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {changes.length > 0 && (
          <motion.section
            initial={motionOK ? { opacity: 0, y: -4 } : false}
            animate={{ opacity: 1, y: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            className="mb-4 rounded-ctl border border-line bg-elevated/40 p-3"
          >
            <h3 className="mb-2 text-[11px] uppercase tracking-wide text-ink-faint">
              {changes.length} uncommitted change
              {changes.length === 1 ? "" : "s"}
            </h3>
            <ul className="mb-3 max-h-44 space-y-0.5 overflow-y-auto">
              {grouped.map((entry) => (
                <li key={entry.path}>
                  <button
                    type="button"
                    onClick={() => onSelectFile?.(entry.path)}
                    disabled={!onSelectFile}
                    className="group flex w-full items-baseline gap-2 rounded px-1 py-0.5
                               text-left transition-colors duration-150
                               hover:bg-elevated disabled:cursor-default"
                  >
                    <span
                      className={cn(
                        "w-[4.5rem] shrink-0 text-[10px] uppercase tracking-wide",
                        CHANGE_TONE[changeLabel(entry)] ?? "text-ink-faint",
                      )}
                    >
                      {changeLabel(entry)}
                    </span>
                    <span className="truncate font-mono text-xs text-ink-dim group-hover:text-ink">
                      {shortPath(entry.path, root)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>

            <textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              placeholder="What changed, and why?"
              aria-label="Commit message"
              className="w-full resize-none rounded-ctl border border-line bg-base px-2.5 py-2
                         text-xs text-ink placeholder:text-ink-faint
                         focus:border-accent/40 focus:outline-none"
            />
            <div className="mt-2 flex items-center gap-2">
              <button
                type="button"
                onClick={submit}
                disabled={!canCommit}
                className="rounded-ctl bg-accent px-3 py-1.5 text-xs font-medium text-base
                           transition-opacity duration-200 hover:opacity-90
                           disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy ? "Committing…" : "Commit changes"}
              </button>
              <span className="text-[10px] text-ink-faint">⌘↵</span>
              {error && <span className="ml-auto text-[11px] text-danger">{error}</span>}
            </div>
          </motion.section>
        )}

        {changes.length === 0 && (
          <p className="mb-4 rounded-ctl border border-line/60 px-3 py-2 text-xs text-ink-faint">
            Working tree clean — everything is committed.
          </p>
        )}

        <h3 className="mb-2 text-[11px] uppercase tracking-wide text-ink-faint">
          History
        </h3>
        {commits.length === 0 ? (
          <p className="text-xs text-ink-faint">
            No commits yet. The first one starts the history.
          </p>
        ) : (
          <ol className="relative space-y-0">
            {commits.map((commit, index) => (
              <CommitRow
                key={commit.sha}
                commit={commit}
                last={index === commits.length - 1}
              />
            ))}
          </ol>
        )}
      </div>
    </div>
  );
});

/** One commit, on a spine — the same vertical-thread idiom the trace uses. */
function CommitRow({ commit, last }: { commit: GitCommit; last: boolean }) {
  return (
    <li className="relative flex gap-3 pb-3">
      <div className="relative flex w-3 shrink-0 justify-center">
        <span className="z-10 mt-1.5 h-1.5 w-1.5 rounded-full bg-accent" />
        {!last && (
          <span
            aria-hidden
            className="absolute top-1.5 h-full w-px bg-line"
          />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs text-ink">{commit.subject}</p>
        <p className="mt-0.5 flex items-center gap-2 text-[10px] text-ink-faint">
          <span className="font-mono">{commit.short}</span>
          <span>·</span>
          <span>{when(commit.date)}</span>
        </p>
      </div>
    </li>
  );
}

function BranchMark({ small = false }: { small?: boolean }) {
  const size = small ? 14 : 28;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={small ? "text-ink-faint" : "text-ink-faint/60"}
      aria-hidden
    >
      <circle cx="6" cy="5" r="2.2" />
      <circle cx="6" cy="19" r="2.2" />
      <circle cx="18" cy="9" r="2.2" />
      <path d="M6 7.2v9.6" />
      <path d="M18 11.2c0 4-4 3.8-6 5.4" />
    </svg>
  );
}
