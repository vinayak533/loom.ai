"use client";

import { motion } from "framer-motion";
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import type { GitChange, GitCommit } from "@/lib/events";
import type { GitBranch } from "@/lib/api";
import type { GitState } from "@/lib/useAgentSocket";
import { cn, shortPath } from "@/lib/cn";
import { SPRING_SNAP, useMotionOK } from "./Anim";

const NO_CHANGES: GitChange[] = [];
const NO_COMMITS: GitCommit[] = [];

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
 * The commit box **is** a staging UI, which it deliberately was not before.
 * The reasoning that kept it out — partial staging needs a second selection
 * model over the file list, and the agent's `git` tool already takes a path
 * list — held right up until people started reviewing what the agent had
 * written before recording it. That is not a power-user case; it is the normal
 * one for anybody who does not trust a machine's whole diff at once.
 *
 * Nothing is selected by default, and that empty selection means "commit
 * everything" rather than "commit nothing". A commit box that refuses until
 * you tick boxes would have made the common case worse to buy the rare one.
 *
 * The branch bar and the suggested message are the other two things a person
 * reaches for here. The message is *suggested* into the box and never applied:
 * the user still presses commit, because that press is the only moment anyone
 * reads what is about to be recorded permanently.
 */

const CHANGE_TONE: Record<string, string> = {
  modified: "text-warn",
  added: "text-add",
  untracked: "text-add",
  deleted: "text-del",
  renamed: "text-accent",
  conflicted: "text-del",
};

/**
 * Branch list plus a "new branch" field, anchored under the branch name.
 *
 * Switching is a plain click; merging is the secondary action on a row that is
 * not the current branch, because merging *into* the branch you are looking at
 * is what the word means here and putting it anywhere else invites the
 * opposite reading.
 */
function BranchMenu({
  branches,
  current,
  busy,
  onPick,
  onClose,
}: {
  branches: GitBranch[];
  current: string | null;
  busy: boolean;
  onPick: (name: string, action: "create" | "checkout" | "merge") => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="menu"
      className="absolute left-4 top-8 z-40 w-60 overflow-hidden rounded-ctl
                 border border-line-strong bg-overlay p-1 shadow-lift"
    >
      <ul className="max-h-48 overflow-y-auto">
        {branches.length === 0 && (
          <li className="px-2 py-1.5 text-[11px] text-ink-faint">
            No branches yet — commit something first.
          </li>
        )}
        {branches.map((b) => (
          <li key={b.name} className="group flex items-center gap-1">
            <button
              type="button"
              disabled={busy || b.current}
              onClick={() => onPick(b.name, "checkout")}
              className={cn(
                "flex min-w-0 flex-1 items-baseline gap-2 rounded-ctl px-2 py-1.5",
                "text-left font-mono text-[11px] transition-colors duration-150",
                b.current
                  ? "text-ink"
                  : "text-ink-muted hover:bg-raised hover:text-ink",
              )}
            >
              <span className="w-2 shrink-0 text-accent">{b.current ? "•" : ""}</span>
              <span className="truncate">{b.name}</span>
            </button>
            {!b.current && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onPick(b.name, "merge")}
                title={`Merge ${b.name} into ${current ?? "this branch"}`}
                className="mr-1 shrink-0 rounded px-1.5 py-1 text-[10px] text-ink-faint
                           opacity-0 transition-opacity hover:text-accent
                           focus-visible:opacity-100 group-hover:opacity-100"
              >
                merge
              </button>
            )}
          </li>
        ))}
      </ul>

      <div className="mt-1 border-t border-line pt-1">
        <input
          value={name}
          placeholder="New branch…"
          aria-label="New branch name"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim()) onPick(name.trim(), "create");
          }}
          className="h-7 w-full rounded-ctl bg-transparent px-2 font-mono text-[11px]
                     text-ink outline-none placeholder:text-ink-faint"
        />
      </div>
    </div>
  );
}

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
  branches = [],
  onStage,
  onBranch,
  onSuggestMessage,
}: {
  git: GitState;
  root: string;
  /** True while a commit or init is in flight. */
  busy: boolean;
  error: string | null;
  /** `paths` empty means "everything"; otherwise commit exactly those. */
  onCommit: (message: string, paths: string[]) => void;
  onInit: () => void;
  onSelectFile?: (path: string) => void;
  branches?: GitBranch[];
  onStage?: (paths: string[], mode: "stage" | "unstage" | "discard") => void;
  onBranch?: (name: string, action: "create" | "checkout" | "merge") => void;
  /** Resolves to a suggested message, or "" if none could be written. */
  onSuggestMessage?: () => Promise<string>;
}) {
  const motionOK = useMotionOK();
  const [message, setMessage] = useState("");
  /**
   * Which files this commit will include. Empty means all of them — see the
   * component docstring; requiring a tick before anything can be committed
   * makes the common case worse to buy the rare one.
   */
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [suggesting, setSuggesting] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [branchOpen, setBranchOpen] = useState(false);

  // Shared empty arrays rather than fresh literals: `[]` is a new identity
  // every render, so the memos below that sort and group these re-ran on every
  // keystroke in the commit box while the panel had no repository attached.
  const changes = git?.status ?? NO_CHANGES;
  const commits = git?.log ?? NO_COMMITS;
  const canCommit = changes.length > 0 && message.trim().length > 0 && !busy;

  const submit = useCallback(() => {
    if (!canCommit) return;
    onCommit(message.trim(), [...selected]);
    setMessage("");
    setSelected(new Set());
  }, [canCommit, message, onCommit, selected]);

  const toggle = useCallback((path: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  // A selection pointing at files that are no longer changed would silently
  // commit nothing, so it is pruned whenever the status changes underneath it.
  useEffect(() => {
    setSelected((current) => {
      if (current.size === 0) return current;
      const live = new Set((git?.status ?? []).map((c) => c.path));
      const next = new Set([...current].filter((p) => live.has(p)));
      return next.size === current.size ? current : next;
    });
  }, [git?.status]);

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
        {error && <p className="text-xs text-del">{error}</p>}
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
      <div className="relative flex shrink-0 items-center gap-2 px-4 pb-2 pt-1">
        <BranchMark small />
        {onBranch ? (
          <button
            type="button"
            onClick={() => setBranchOpen((o) => !o)}
            aria-expanded={branchOpen}
            aria-haspopup="menu"
            className="flex items-center gap-1 rounded-ctl px-1.5 py-0.5 font-mono text-xs
                       text-ink transition-colors duration-150 hover:bg-elevated"
          >
            {git.branch}
            <span aria-hidden className="text-[9px] text-ink-faint">▾</span>
          </button>
        ) : (
          <span className="font-mono text-xs text-ink">{git.branch}</span>
        )}
        <span className="ml-auto text-[11px] text-ink-faint">
          {commits.length === 0
            ? "no commits"
            : `${commits.length} commit${commits.length === 1 ? "" : "s"}`}
        </span>

        {branchOpen && onBranch && (
          <BranchMenu
            branches={branches}
            current={git.branch}
            busy={busy}
            onPick={(name, action) => {
              setBranchOpen(false);
              onBranch(name, action);
            }}
            onClose={() => setBranchOpen(false)}
          />
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {changes.length > 0 && (
          <motion.section
            initial={motionOK ? { opacity: 0, y: -4 } : false}
            animate={{ opacity: 1, y: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            className="mb-4 rounded-ctl border border-line bg-elevated/40 p-3"
          >
            <div className="mb-2 flex items-baseline justify-between gap-3">
              <h3 className="text-[11px] uppercase tracking-wide text-ink-faint">
                {changes.length} uncommitted change
                {changes.length === 1 ? "" : "s"}
              </h3>
              {selected.size > 0 && (
                <button
                  type="button"
                  onClick={() => setSelected(new Set())}
                  className="text-[10px] text-ink-faint transition-colors hover:text-ink"
                >
                  Clear {selected.size}
                </button>
              )}
            </div>
            <ul className="mb-3 max-h-44 space-y-0.5 overflow-y-auto">
              {grouped.map((entry) => {
                const ticked = selected.has(entry.path);
                return (
                  <li
                    key={entry.path}
                    className="group flex items-baseline gap-2 rounded px-1 py-0.5
                               transition-colors duration-150 hover:bg-elevated"
                  >
                    {/* A real checkbox, not a styled div: this is the control
                        that decides what gets recorded, and it has to be
                        reachable by keyboard and announced as checkable. */}
                    <input
                      type="checkbox"
                      checked={ticked}
                      onChange={() => toggle(entry.path)}
                      aria-label={`Include ${shortPath(entry.path, root)} in the commit`}
                      className="mt-0.5 h-3 w-3 shrink-0 cursor-pointer accent-accent"
                    />
                    <button
                      type="button"
                      onClick={() => onSelectFile?.(entry.path)}
                      disabled={!onSelectFile}
                      className="flex min-w-0 flex-1 items-baseline gap-2 text-left
                                 disabled:cursor-default"
                    >
                      <span
                        className={cn(
                          "w-[4.5rem] shrink-0 text-[10px] uppercase tracking-wide",
                          CHANGE_TONE[changeLabel(entry)] ?? "text-ink-faint",
                        )}
                      >
                        {changeLabel(entry)}
                      </span>
                      <span
                        className={cn(
                          "truncate font-mono text-xs group-hover:text-ink",
                          ticked ? "text-ink" : "text-ink-dim",
                        )}
                      >
                        {shortPath(entry.path, root)}
                      </span>
                    </button>
                  </li>
                );
              })}
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
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={submit}
                disabled={!canCommit}
                className="rounded-ctl bg-accent px-3 py-1.5 text-xs font-medium text-base
                           transition-opacity duration-200 hover:opacity-90
                           disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy
                  ? "Committing…"
                  : selected.size > 0
                    ? `Commit ${selected.size} file${selected.size === 1 ? "" : "s"}`
                    : "Commit changes"}
              </button>
              <span className="text-[10px] text-ink-faint">⌘↵</span>

              {onSuggestMessage && (
                <button
                  type="button"
                  disabled={suggesting || busy}
                  onClick={async () => {
                    setSuggesting(true);
                    try {
                      const written = await onSuggestMessage();
                      // Only fills an empty box. Overwriting a message someone
                      // has already typed to replace it with a guess is the
                      // one thing this button must never do.
                      if (written) setMessage((m) => (m.trim() ? m : written));
                    } finally {
                      setSuggesting(false);
                    }
                  }}
                  className="rounded-ctl border border-line px-2.5 py-1.5 text-[11px]
                             text-ink-muted transition-colors duration-200
                             hover:border-accent/40 hover:text-ink disabled:opacity-40"
                >
                  {suggesting ? "Writing…" : "Write one"}
                </button>
              )}

              {onStage && selected.size > 0 && (
                confirmDiscard ? (
                  <span className="flex items-center gap-1.5">
                    <span className="text-[10px] text-ink-muted">
                      Discard {selected.size}? This cannot be undone.
                    </span>
                    <button
                      type="button"
                      onClick={() => {
                        onStage([...selected], "discard");
                        setSelected(new Set());
                        setConfirmDiscard(false);
                      }}
                      className="rounded-ctl bg-del/15 px-2 py-1 text-[10px] text-del
                                 transition-colors hover:bg-del/25"
                    >
                      Discard
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDiscard(false)}
                      className="rounded-ctl px-2 py-1 text-[10px] text-ink-muted
                                 transition-colors hover:text-ink"
                    >
                      Keep
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmDiscard(true)}
                    className="text-[11px] text-ink-faint transition-colors hover:text-del"
                  >
                    Discard
                  </button>
                )
              )}

              {error && <span className="ml-auto text-[11px] text-del">{error}</span>}
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
