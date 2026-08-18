"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { relativeDate, type Notebook } from "@/lib/learn";
import { SPRING, SPRING_SNAP, useMotionOK } from "../Anim";
import { SessionHistoryMenu, type HistoryAction } from "../SessionHistoryMenu";
import { NotebookCover, NotebookGlyph } from "./NotebookCover";

export type LibraryTab = "all" | "mine" | "featured" | "collections";
export type SortKey = "recent" | "created" | "title" | "sources";

const TABS: Array<{ key: LibraryTab; label: string }> = [
  { key: "all", label: "All" },
  { key: "mine", label: "My notebooks" },
  { key: "featured", label: "Featured" },
  { key: "collections", label: "Collections" },
];

const SORTS: Array<{ key: SortKey; label: string }> = [
  { key: "recent", label: "Most recent" },
  { key: "created", label: "Recently created" },
  { key: "title", label: "Title" },
  { key: "sources", label: "Most sources" },
];

/**
 * The library.
 *
 * The reference is NotebookLM's notebook list: filter tabs on the left of the
 * toolbar, search / view-toggle / sort / create on the right, and a card grid
 * under it carrying cover, badge, title, date and source count. The structure
 * and the density are the parts worth borrowing; the surface is this project's
 * own — hueless glass on a near-black floor, the section's amber accent, the
 * rhombus, and covers drawn from a seed rather than illustrated.
 */
export function NotebookLibrary({
  notebooks,
  loading,
  archivedView,
  onArchivedView,
  onOpen,
  onCreate,
  onAction,
}: {
  notebooks: Notebook[];
  loading: boolean;
  archivedView: boolean;
  onArchivedView: (archived: boolean) => void;
  onOpen: (id: string) => void;
  onCreate: () => void;
  onAction: (id: string, action: HistoryAction) => void;
}) {
  const [tab, setTab] = useState<LibraryTab>("all");
  const [sort, setSort] = useState<SortKey>("recent");
  const [grid, setGrid] = useState(true);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const searchInput = useRef<HTMLInputElement>(null);
  const motionOK = useMotionOK();

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = notebooks.filter(
      (n) => !needle || n.title.toLowerCase().includes(needle),
    );
    const sorted = [...filtered];
    sorted.sort((a, b) => {
      switch (sort) {
        case "title":
          return a.title.localeCompare(b.title);
        case "created":
          return b.created_at.localeCompare(a.created_at);
        case "sources":
          return (b.source_count ?? 0) - (a.source_count ?? 0);
        default:
          return b.updated_at.localeCompare(a.updated_at);
      }
    });
    return sorted;
  }, [notebooks, query, sort]);

  // Featured and Collections are shelves this deployment has nothing to put on
  // yet. They render an honest empty state rather than being hidden — the tab
  // row is part of the layout being matched, and a tab that quietly disappears
  // is more confusing than one that says it is empty.
  const placeholder = tab === "featured" || tab === "collections";

  return (
    <div className="scroll-thin h-full min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-[1180px] px-6 pb-16 pt-6 sm:px-9">
        <header className="mb-7">
          <h1 className="text-display font-semibold tracking-tight text-ink">
            {archivedView ? (
              "Archived"
            ) : (
              <>
                Your{" "}
                <span className="bg-gradient-to-br from-accent to-accent-alt bg-clip-text text-transparent">
                  notebooks
                </span>
              </>
            )}
          </h1>
          <p className="mt-2.5 max-w-[52ch] text-[0.9375rem] leading-relaxed text-ink-muted">
            {archivedView
              ? "Notebooks you have put away. Nothing here has been deleted."
              : "Upload sources, ask questions grounded in them, and turn what you have into a course."}
          </p>
        </header>

        {/* ------------------------------------------------------- toolbar */}
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <div role="tablist" aria-label="Library filter" className="flex flex-wrap gap-1">
            {TABS.map((t) => {
              const on = t.key === tab && !archivedView;
              return (
                <button
                  key={t.key}
                  role="tab"
                  type="button"
                  aria-selected={on}
                  onClick={() => {
                    setTab(t.key);
                    onArchivedView(false);
                  }}
                  className={cn(
                    "relative h-8 rounded-full px-3.5 text-[0.8125rem] font-medium",
                    "transition-colors duration-200",
                    on ? "text-accent-ink" : "text-ink-muted hover:bg-elevated hover:text-ink",
                  )}
                >
                  {on && (
                    <motion.span
                      layoutId="library-tab"
                      transition={motionOK ? SPRING_SNAP : { duration: 0 }}
                      className="absolute inset-0 rounded-full bg-accent"
                      aria-hidden
                    />
                  )}
                  <span className="relative">{t.label}</span>
                </button>
              );
            })}
            <button
              type="button"
              aria-selected={archivedView}
              role="tab"
              onClick={() => onArchivedView(!archivedView)}
              className={cn(
                "h-8 rounded-full px-3.5 text-[0.8125rem] font-medium transition-colors duration-200",
                archivedView
                  ? "bg-raised text-ink"
                  : "text-ink-faint hover:bg-elevated hover:text-ink-muted",
              )}
            >
              Archived
            </button>
          </div>

          <div className="ml-auto flex items-center gap-1.5">
            {/* Search opens in place rather than sitting there as a wide empty
                field — the grid is the thing this screen is for. */}
            <div className="flex items-center">
              <AnimatePresence initial={false}>
                {searching && (
                  <motion.input
                    ref={searchInput}
                    key="search"
                    autoFocus
                    initial={motionOK ? { width: 0, opacity: 0 } : { opacity: 0 }}
                    animate={{ width: 176, opacity: 1 }}
                    exit={motionOK ? { width: 0, opacity: 0 } : { opacity: 0 }}
                    transition={motionOK ? SPRING_SNAP : { duration: 0 }}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") {
                        setQuery("");
                        setSearching(false);
                      }
                    }}
                    placeholder="Search notebooks…"
                    aria-label="Search notebooks"
                    className="h-8 min-w-0 rounded-ctl border border-line bg-elevated px-2.5 text-[0.8125rem]
                               text-ink placeholder:text-ink-faint focus:border-accent-line focus:outline-none"
                  />
                )}
              </AnimatePresence>
              <IconButton
                label={searching ? "Close search" : "Search notebooks"}
                active={searching}
                onClick={() => {
                  if (searching) setQuery("");
                  setSearching((s) => !s);
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.85" strokeLinecap="round">
                  <circle cx="11" cy="11" r="6.5" />
                  <path d="m16 16 4 4" />
                </svg>
              </IconButton>
            </div>

            <div className="flex rounded-ctl border border-line bg-elevated p-0.5">
              <ViewToggle on={grid} onClick={() => setGrid(true)} label="Grid view">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <rect x="3.5" y="3.5" width="7" height="7" rx="1.6" />
                  <rect x="13.5" y="3.5" width="7" height="7" rx="1.6" />
                  <rect x="3.5" y="13.5" width="7" height="7" rx="1.6" />
                  <rect x="13.5" y="13.5" width="7" height="7" rx="1.6" />
                </svg>
              </ViewToggle>
              <ViewToggle on={!grid} onClick={() => setGrid(false)} label="List view">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                  <path d="M4 6.5h16M4 12h16M4 17.5h16" />
                </svg>
              </ViewToggle>
            </div>

            <SortMenu value={sort} onChange={setSort} />

            <button
              type="button"
              onClick={onCreate}
              className="ml-1 flex h-9 items-center gap-1.5 rounded-ctl bg-gradient-to-br from-accent to-accent-alt
                         px-3.5 text-[0.8125rem] font-semibold text-accent-ink transition-all duration-200
                         hover:brightness-110 active:scale-[0.97]"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
              Create new
            </button>
          </div>
        </div>

        {/* ---------------------------------------------------------- grid */}
        {placeholder ? (
          <EmptyState
            title={tab === "featured" ? "No featured notebooks" : "No collections"}
            body={
              tab === "featured"
                ? "Curated notebooks would appear here. Nothing has been published to this deployment."
                : "Group related notebooks into a collection. Not wired up yet — the shelf is here, the grouping is not."
            }
          />
        ) : loading ? (
          <div className={cn(grid ? "grid gap-4 sm:grid-cols-2 lg:grid-cols-3" : "space-y-2")}>
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div
                key={i}
                className={cn(
                  "animate-pulse rounded-card border border-line bg-elevated",
                  grid ? "h-[196px]" : "h-[68px]",
                )}
              />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <EmptyState
            title={query ? "Nothing matches that" : archivedView ? "Nothing archived" : "No notebooks yet"}
            body={
              query
                ? "Try a different search."
                : archivedView
                  ? "Archiving a notebook takes it out of the library without deleting anything in it."
                  : "Create one, drop in a PDF, a link or some text, and ask it questions."
            }
            action={!query && !archivedView ? { label: "Create your first notebook", onClick: onCreate } : undefined}
          />
        ) : (
          <ul
            className={cn(
              grid
                ? "grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
                : "flex flex-col gap-1.5",
            )}
          >
            {/* Not `mode="popLayout"`: it renders each child through
                framer's PopChild, which needs a forwarded ref that a plain
                function component cannot give it. The per-card `layout` prop
                already re-flows the grid when one is removed. */}
            <AnimatePresence initial={false}>
              {visible.map((notebook) => (
                <NotebookCard
                  key={notebook.id}
                  notebook={notebook}
                  grid={grid}
                  onOpen={onOpen}
                  onAction={onAction}
                />
              ))}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </div>
  );
}

function NotebookCard({
  notebook,
  grid,
  onOpen,
  onAction,
}: {
  notebook: Notebook;
  grid: boolean;
  onOpen: (id: string) => void;
  onAction: (id: string, action: HistoryAction) => void;
}) {
  const motionOK = useMotionOK();
  const count = notebook.source_count ?? 0;
  const meta = `${count} source${count === 1 ? "" : "s"} · ${relativeDate(notebook.updated_at)}`;

  return (
    <motion.li
      layout={motionOK}
      initial={motionOK ? { opacity: 0, y: 10 } : false}
      animate={{ opacity: 1, y: 0 }}
      exit={motionOK ? { opacity: 0, scale: 0.97 } : { opacity: 0 }}
      transition={motionOK ? SPRING : { duration: 0 }}
      className="group relative"
    >
      <button
        type="button"
        onClick={() => onOpen(notebook.id)}
        className={cn(
          "block w-full overflow-hidden rounded-card border border-line bg-elevated text-left",
          "transition-all duration-250 ease-out hover:border-accent-line hover:bg-raised",
          "hover:shadow-lift active:scale-[0.995]",
          grid ? "" : "flex items-center gap-3.5 p-2.5",
        )}
      >
        {grid ? (
          <>
            <NotebookCover seed={notebook.cover_image} id={notebook.id} className="h-[104px] w-full" />
            <div className="p-3.5">
              <div className="mb-2 flex items-center gap-2">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-[7px] bg-accent-soft text-accent">
                  <NotebookGlyph size={14} />
                </span>
                <h3 className="truncate text-[0.9375rem] font-medium leading-snug text-ink">
                  {notebook.title}
                </h3>
              </div>
              <p className="voice-machine truncate text-ink-faint">{meta}</p>
            </div>
          </>
        ) : (
          <>
            <NotebookCover
              seed={notebook.cover_image}
              id={notebook.id}
              compact
              className="h-11 w-11 shrink-0 rounded-ctl"
            />
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-[0.875rem] font-medium text-ink">{notebook.title}</h3>
              <p className="voice-machine truncate text-ink-faint">{meta}</p>
            </div>
          </>
        )}
      </button>

      <div className={cn("absolute", grid ? "right-2 top-2" : "right-2 top-1/2 -translate-y-1/2")}>
        <SessionHistoryMenu
          pinned={false}
          archived={Boolean(notebook.is_archived)}
          label="notebook"
          actions={["archive", "delete"]}
          onAction={(action) => onAction(notebook.id, action)}
        />
      </div>
    </motion.li>
  );
}

function SortMenu({
  value,
  onChange,
}: {
  value: SortKey;
  onChange: (key: SortKey) => void;
}) {
  const [open, setOpen] = useState(false);
  const motionOK = useMotionOK();
  const current = SORTS.find((s) => s.key === value) ?? SORTS[0];

  return (
    <div className="relative" onBlur={(e) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false);
    }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex h-8 items-center gap-1.5 rounded-ctl border border-line bg-elevated px-2.5
                   text-[0.8125rem] text-ink-muted transition-colors duration-200
                   hover:border-line-strong hover:text-ink"
      >
        {current.label}
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      <AnimatePresence>
        {open && (
          <motion.ul
            role="listbox"
            initial={motionOK ? { opacity: 0, y: -4, scale: 0.96 } : { opacity: 0 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={motionOK ? { opacity: 0, scale: 0.97 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            className="absolute right-0 top-9 z-40 w-[184px] overflow-hidden rounded-ctl
                       border border-line-strong bg-overlay p-1 shadow-lift"
          >
            {SORTS.map((s) => (
              <li key={s.key}>
                <button
                  type="button"
                  role="option"
                  aria-selected={s.key === value}
                  onClick={() => {
                    onChange(s.key);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex h-8 w-full items-center justify-between rounded-ctl px-2.5 text-left text-[0.8125rem]",
                    "transition-colors duration-150",
                    s.key === value
                      ? "bg-accent-soft text-ink"
                      : "text-ink-muted hover:bg-raised hover:text-ink",
                  )}
                >
                  {s.label}
                  {s.key === value && <span className="sigil h-1.5 w-1.5 bg-accent" aria-hidden />}
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  );
}

function IconButton({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={cn(
        "grid h-8 w-8 shrink-0 place-items-center rounded-ctl transition-colors duration-200",
        active ? "bg-raised text-ink" : "text-ink-faint hover:bg-elevated hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function ViewToggle({
  on,
  onClick,
  label,
  children,
}: {
  on: boolean;
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={on}
      title={label}
      className={cn(
        "grid h-7 w-7 place-items-center rounded-[calc(var(--r-ctl)-2px)] transition-colors duration-200",
        on ? "bg-raised text-ink" : "text-ink-faint hover:text-ink-muted",
      )}
    >
      {children}
    </button>
  );
}

function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="grid place-items-center rounded-card border border-dashed border-line py-16 text-center">
      <span className="mb-3 grid h-10 w-10 place-items-center rounded-card bg-accent-soft text-accent">
        <NotebookGlyph size={19} />
      </span>
      <h3 className="text-[0.9375rem] font-medium text-ink">{title}</h3>
      <p className="mt-1.5 max-w-[42ch] text-[0.8125rem] leading-relaxed text-ink-faint">{body}</p>
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="mt-4 rounded-ctl border border-line bg-elevated px-3.5 py-2 text-[0.8125rem]
                     font-medium text-ink transition-all duration-200 hover:border-accent-line
                     hover:bg-raised active:scale-[0.98]"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
