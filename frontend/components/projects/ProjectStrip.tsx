"use client";

import { FolderPlus, Settings2 } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";
import type { ProjectsState } from "@/lib/useProjects";

/**
 * Projects, at the top of the sessions flyout.
 *
 * A row of chips rather than a second list. The flyout is a jump list you pass
 * through, not somewhere you live, and a nested tree of projects-containing-
 * sessions would turn it into a file manager — two levels of scrolling in a
 * 272px column, to reach something the chips reach in one click.
 *
 * Selecting a chip *filters* the list below. It does not navigate anywhere,
 * because a project is not a place: it is a lens on the same history. The gear
 * on the selected chip is what opens the project itself, which is the only
 * screen where instructions and knowledge files can be edited.
 *
 * Shown signed out as well as in. Projects follow exactly the rule sessions
 * already follow — an anonymous caller gets the anonymous shelf, a real set of
 * rows rather than an empty one — so hiding this when signed out would make
 * the feature invisible in the default `REQUIRE_AUTH=0` configuration while
 * the API behind it worked fine. Memory is the opposite case and *is* gated,
 * because the server genuinely refuses to store it without an account.
 */
export function ProjectStrip({
  projects,
  signedIn,
  onOpenProject,
}: {
  projects: ProjectsState;
  signedIn: boolean;
  onOpenProject: (id: string) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  const submit = async () => {
    const trimmed = name.trim();
    setCreating(false);
    setName("");
    if (!trimmed) return;
    const created = await projects.create(trimmed);
    // Open it straight away: a project with no instructions does nothing, and
    // the panel is where that gets fixed. Creating one and being returned to
    // the list leaves the user with an empty container and no prompt to fill it.
    if (created) onOpenProject(created.id);
  };

  return (
    <div className="border-b border-line px-3 pb-2.5 pt-3">
      <div className="flex items-center justify-between gap-2 pb-2">
        <span className="voice-label text-ink-faint">Projects</span>
        <button
          type="button"
          onClick={() => setCreating(true)}
          aria-label="New project"
          className="rounded-[5px] p-1 text-ink-faint transition-colors
                     hover:bg-raised hover:text-ink"
        >
          <FolderPlus size={13} strokeWidth={1.75} />
        </button>
      </div>

      {creating && (
        <input
          autoFocus
          value={name}
          placeholder="Project name"
          aria-label="New project name"
          onChange={(e) => setName(e.target.value)}
          onBlur={() => void submit()}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit();
            if (e.key === "Escape") {
              setCreating(false);
              setName("");
            }
          }}
          className="mb-2 h-8 w-full rounded-ctl border border-line bg-inset px-2.5
                     font-sans text-2xs text-ink outline-none
                     placeholder:text-ink-faint focus:border-line-focus"
        />
      )}

      {projects.projects.length === 0 && !creating && (
        <p className="text-2xs leading-relaxed text-ink-faint">
          Group conversations and give them shared instructions and files.
          {!signedIn && " Sign in to keep them across devices."}
        </p>
      )}

      {projects.projects.length > 0 && (
        <div className="flex flex-wrap gap-1">
          <Chip
            active={projects.filter === null}
            onClick={() => projects.setFilter(null)}
          >
            All
          </Chip>
          {projects.projects.map((p) => {
            const active = projects.filter === p.id;
            return (
              <Chip
                key={p.id}
                active={active}
                onClick={() => projects.setFilter(active ? null : p.id)}
              >
                <span className="truncate">{p.name}</span>
                {active && (
                  /* A span, not a nested button — a button inside a button is
                     invalid markup and browsers resolve it by dropping one of
                     them, usually the one you wanted. */
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label={`Open ${p.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpenProject(p.id);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        onOpenProject(p.id);
                      }
                    }}
                    className="ml-0.5 -mr-0.5 shrink-0 rounded-[4px] p-0.5
                               text-accent-ink/70 transition-colors hover:text-accent-ink"
                  >
                    <Settings2 size={11} strokeWidth={1.9} />
                  </span>
                )}
              </Chip>
            );
          })}
        </div>
      )}

      {projects.error && (
        <p className="pt-2 text-2xs text-warn" role="alert">
          {projects.error}
        </p>
      )}
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex max-w-full items-center gap-1 rounded-full border px-2.5 py-1",
        "text-2xs transition-colors duration-150",
        active
          ? "border-accent-line bg-accent/15 text-accent-ink"
          : "border-line bg-inset text-ink-muted hover:border-line-strong hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}
