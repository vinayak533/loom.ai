"use client";

import { AnimatePresence, motion } from "framer-motion";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { SessionRow, SessionSearchHit } from "@/lib/api";
import { searchSessions } from "@/lib/api";
import { cn } from "@/lib/cn";
import { TOUCH_QUERY, useMediaQuery } from "@/lib/useMediaQuery";
import { SPRING_SNAP, useMotionOK } from "./Anim";
import { ProjectStrip } from "./projects/ProjectStrip";
import { SessionListItem } from "./SessionListItem";
import type { HistoryAction } from "./SessionHistoryMenu";
import type { ProjectsState } from "@/lib/useProjects";

export type ShelfView = "active" | "archived";

/**
 * The sessions flyout.
 *
 * In the Ledger layout the rail never expands in place — it stays 72px and
 * this panel *floats over* the conversation instead, which is why it is glass
 * with its own radius rather than another column butted onto the edge. It
 * closes on selection: it is a jump list, not a place to live.
 *
 * The list has two shelves and two groups. Active / Archived are separate
 * fetches from the backend and are disjoint; inside the active shelf, pinned
 * sessions form their own group above the rest. Both the ordering and the
 * split are decided server-side (`repository._shelf_order`) so that Chat and
 * Code cannot end up disagreeing about what "most recent" means.
 *
 * Memoized: none of this depends on the token currently being streamed into
 * the chat, but it sits under a component that re-renders for each one. The
 * handlers it takes are `useCallback`ed in `page.tsx` so the guard holds.
 */
export const SessionSidebar = memo(function SessionSidebar({
  sessions,
  activeId,
  view,
  onView,
  onSelectSession,
  onNewSession,
  onSessionAction,
  onClose,
  token,
  section = "chat",
  projects,
  signedIn = false,
  onOpenProject,
  onMoveToProject,
}: {
  sessions: SessionRow[];
  activeId: string | null;
  view: ShelfView;
  onView: (view: ShelfView) => void;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onSessionAction: (id: string, action: HistoryAction) => void;
  onClose?: () => void;
  /** Needed to search: the endpoint scopes results to the caller. */
  token?: string | null;
  /** Which section's history to search. Chat and Code are separate lists. */
  section?: "chat" | "code";
  /**
   * Projects, when the surface has them. Optional so the Agents shelf — which
   * mounts this same list and has no project concept — is not forced to
   * invent one.
   */
  projects?: ProjectsState;
  signedIn?: boolean;
  onOpenProject?: (id: string) => void;
  onMoveToProject?: (sessionId: string, projectId: string | null) => void;
}) {
  const motionOK = useMotionOK();
  const touch = useMediaQuery(TOUCH_QUERY);
  const search = useSessionSearch(token, section);

  const { pinned, rest } = useMemo(
    () => ({
      pinned: sessions.filter((s) => s.is_pinned),
      rest: sessions.filter((s) => !s.is_pinned),
    }),
    [sessions],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-12 shrink-0 items-center gap-2 px-4">
        <span className="sigil h-2 w-2 bg-accent" aria-hidden />
        <h2 className="voice-label text-ink-muted">Sessions</h2>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close sessions"
            className="ml-auto grid h-7 w-7 place-items-center rounded-ctl text-ink-faint
                       transition-colors duration-200 hover:bg-elevated hover:text-ink"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
        )}
      </header>

      {projects && onOpenProject && (
        <ProjectStrip
          projects={projects}
          signedIn={signedIn}
          onOpenProject={onOpenProject}
        />
      )}

      <div className="shrink-0 px-3 pb-2.5 pt-2.5">
        <button
          type="button"
          onClick={onNewSession}
          className="flex h-9 w-full items-center justify-center gap-2 rounded-ctl border border-line
                     bg-elevated text-[0.8125rem] font-medium text-ink transition-all duration-200
                     hover:border-accent-line hover:bg-raised active:scale-[0.985]"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-accent">
            <path d="M12 5v14M5 12h14" />
          </svg>
          {projects?.filter
            ? `New in ${projects.byId(projects.filter)?.name ?? "project"}`
            : "New session"}
        </button>
      </div>

      <div className="shrink-0 px-3 pb-2.5">
        <SearchBox
          value={search.query}
          onChange={search.setQuery}
          busy={search.busy}
        />
      </div>

      {/* The shelf tabs are about *this* list. While a search is running the
          list is not that list, so offering "active / archived" over it would
          be describing something the user is not looking at. */}
      {!search.active && (
        <div className="shrink-0 px-3 pb-2.5">
          <ShelfTabs view={view} onView={onView} />
        </div>
      )}

      {search.active ? (
        <SearchResults
          hits={search.hits}
          busy={search.busy}
          query={search.query}
          error={search.error}
          activeId={activeId}
          onSelect={(id) => {
            search.clear();
            onSelectSession(id);
          }}
        />
      ) : (
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        {sessions.length === 0 ? (
          <p className="px-3 py-2 text-xs leading-relaxed text-ink-faint">
            {/* A project filter has its own empty state. Falling through to
                the Supabase notice below tells the user their database is
                unconfigured when in fact it answered fine and the filter
                simply matched nothing — a wrong diagnosis is worse than none. */}
            {projects?.filter
              ? `Nothing filed in ${projects.byId(projects.filter)?.name ?? "this project"} yet. Starting a session while it is selected files it here.`
              : view === "archived"
                ? "Nothing archived. Archiving a session takes it out of this list without deleting it."
                : "Sessions are listed here once Supabase is connected. Until then the current session lives in memory."}
          </p>
        ) : (
          <>
            {pinned.length > 0 && (
              <>
                <GroupLabel>Pinned</GroupLabel>
                <ul className="space-y-px">
                  <AnimatePresence initial={false}>
                    {pinned.map((s) => (
                      <SessionListItem
                        key={s.id}
                        session={s}
                        active={s.id === activeId}
                        touch={touch}
                        onSelect={onSelectSession}
                        onAction={onSessionAction}
                        projectOptions={projects?.projects}
                        onMoveToProject={onMoveToProject}
                      />
                    ))}
                  </AnimatePresence>
                </ul>
              </>
            )}

            {rest.length > 0 && (
              <>
                {pinned.length > 0 && <GroupLabel>Recent</GroupLabel>}
                <ul className="space-y-px">
                  <AnimatePresence initial={false}>
                    {rest.map((s) => (
                      <SessionListItem
                        key={s.id}
                        session={s}
                        active={s.id === activeId}
                        touch={touch}
                        onSelect={onSelectSession}
                        onAction={onSessionAction}
                        projectOptions={projects?.projects}
                        onMoveToProject={onMoveToProject}
                      />
                    ))}
                  </AnimatePresence>
                </ul>
              </>
            )}
          </>
        )}
      </div>
      )}

      {/* A quiet reminder of where the rest of the history went. Only shown
          when there is somewhere to go. */}
      <AnimatePresence>
        {view === "archived" && !search.active && (
          <motion.button
            type="button"
            initial={motionOK ? { opacity: 0 } : false}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            onClick={() => onView("active")}
            className="shrink-0 border-t border-line px-4 py-2.5 text-left text-2xs
                       text-ink-faint transition-colors hover:text-ink-muted"
          >
            ← Back to active sessions
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  );
});

function GroupLabel({ children }: { children: React.ReactNode }) {
  return <p className="voice-label px-3 pb-1.5 pt-2.5">{children}</p>;
}

function ShelfTabs({
  view,
  onView,
}: {
  view: ShelfView;
  onView: (view: ShelfView) => void;
}) {
  const motionOK = useMotionOK();
  return (
    <div
      role="tablist"
      aria-label="Session shelf"
      className="relative flex gap-0.5 rounded-ctl bg-inset p-0.5"
    >
      {(["active", "archived"] as const).map((tab) => {
        const on = tab === view;
        return (
          <button
            key={tab}
            role="tab"
            type="button"
            aria-selected={on}
            onClick={() => onView(tab)}
            className={cn(
              "relative flex-1 rounded-[calc(var(--r-ctl)-2px)] py-1.5 text-2xs font-medium capitalize",
              "transition-colors duration-200",
              on ? "text-ink" : "text-ink-faint hover:text-ink-muted",
            )}
          >
            {on && (
              <motion.span
                layoutId="shelf-tab"
                transition={motionOK ? SPRING_SNAP : { duration: 0 }}
                className="absolute inset-0 rounded-[calc(var(--r-ctl)-2px)] bg-raised"
                aria-hidden
              />
            )}
            <span className="relative">{tab}</span>
          </button>
        );
      })}
    </div>
  );
}


/**
 * The search box's state machine.
 *
 * Three things it has to get right, none of which are about searching:
 *
 * **Debounce.** A keystroke is not a query. 200ms is long enough that typing
 * "authentication" is one request rather than fourteen, and short enough that
 * it does not read as lag.
 *
 * **Cancellation.** Every superseded request is aborted. Without it, results
 * for "auth" can land after results for "authentication" and overwrite them —
 * the classic out-of-order-response bug, which looks like the search being
 * wrong rather than late.
 *
 * **No spinner on the first keystroke.** `busy` is only true while a request
 * is genuinely in flight, so a fast local backend never flashes one.
 */
function useSessionSearch(token?: string | null, section: "chat" | "code" = "chat") {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SessionSearchHit[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef<AbortController | null>(null);

  const term = query.trim();

  useEffect(() => {
    inflight.current?.abort();
    if (!term) {
      setHits([]);
      setBusy(false);
      setError(null);
      return;
    }

    const controller = new AbortController();
    inflight.current = controller;
    setBusy(true);
    const timer = setTimeout(() => {
      searchSessions(term, token, section, { signal: controller.signal })
        .then((rows) => {
          if (controller.signal.aborted) return;
          setHits(rows);
          setError(null);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          // An aborted fetch is the normal case, not a failure — it means the
          // user kept typing. Anything else is worth saying out loud rather
          // than showing an empty list that looks like "no matches".
          if (err instanceof DOMException && err.name === "AbortError") return;
          setError("Search is unavailable right now.");
          setHits([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setBusy(false);
        });
    }, 200);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [term, token, section]);

  return {
    query,
    setQuery,
    hits,
    busy: busy && Boolean(term),
    error,
    active: Boolean(term),
    clear: () => setQuery(""),
  };
}

function SearchBox({
  value,
  onChange,
  busy,
}: {
  value: string;
  onChange: (value: string) => void;
  busy: boolean;
}) {
  return (
    <div className="relative">
      <span
        className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
        aria-hidden
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.6-3.6" />
        </svg>
      </span>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          // Escape clears the search rather than closing the flyout. The
          // page-level handler treats Escape as "leave the deepest surface",
          // and a search you have typed into is deeper than the panel holding
          // it — so this stops one press from doing both.
          if (e.key === "Escape" && value) {
            e.preventDefault();
            e.stopPropagation();
            onChange("");
          }
        }}
        placeholder="Search this section's history…"
        aria-label="Search sessions"
        className="h-9 w-full rounded-ctl border border-line bg-inset pl-8 pr-8
                   font-sans text-[0.8125rem] text-ink placeholder:text-ink-faint
                   transition-colors duration-200
                   focus:border-line-focus focus:outline-none focus-visible:shadow-none
                   [&::-webkit-search-cancel-button]:appearance-none
                   [&::-webkit-search-decoration]:appearance-none"
      />
      {(busy || value) && (
        <span className="absolute right-2.5 top-1/2 -translate-y-1/2">
          {busy ? (
            <span className="sigil block h-2 w-2 animate-pulse bg-accent" aria-hidden />
          ) : (
            <button
              type="button"
              onClick={() => onChange("")}
              aria-label="Clear search"
              className="grid h-5 w-5 place-items-center rounded text-ink-faint
                         transition-colors hover:text-ink"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                <path d="m6 6 12 12M18 6 6 18" />
              </svg>
            </button>
          )}
        </span>
      )}
    </div>
  );
}

function SearchResults({
  hits,
  busy,
  query,
  error,
  activeId,
  onSelect,
}: {
  hits: SessionSearchHit[];
  busy: boolean;
  query: string;
  error: string | null;
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  if (error) {
    return (
      <p className="px-4 py-3 text-xs leading-relaxed text-warn" role="alert">
        {error}
      </p>
    );
  }
  if (!hits.length) {
    return (
      <p className="px-4 py-3 text-xs leading-relaxed text-ink-faint">
        {busy ? "Searching…" : `No sessions match “${query.trim()}”.`}
      </p>
    );
  }

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
      <p className="voice-label px-3 pb-1.5 pt-1">
        {hits.length} result{hits.length === 1 ? "" : "s"}
      </p>
      <ul className="space-y-px">
        {hits.map((hit) => (
          <li key={hit.id}>
            <button
              type="button"
              onClick={() => onSelect(hit.id)}
              className={cn(
                "flex w-full flex-col gap-0.5 rounded-ctl px-3 py-2 text-left",
                "transition-colors duration-150 hover:bg-elevated",
                hit.id === activeId && "bg-raised",
              )}
            >
              <span className="truncate text-[0.8125rem] text-ink">
                {hit.title || "Untitled session"}
              </span>
              {/* Only a body match gets a second line. A title match already
                  shows what matched on the first one, and repeating it would
                  make every row two lines tall to say one thing. */}
              {hit.match === "message" && hit.snippet && (
                <span className="line-clamp-2 text-[11px] leading-snug text-ink-faint">
                  {hit.snippet}
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
