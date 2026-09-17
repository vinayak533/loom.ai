"use client";

import { useEffect, useRef, useState } from "react";
import type { SessionSearchHit } from "@/lib/api";
import { searchSessions } from "@/lib/api";
import { cn } from "@/lib/cn";

/**
 * Searching one section's history — the box, the state machine behind it, and
 * the result list.
 *
 * Lifted out of `SessionSidebar` when the Agents section needed the same
 * thing. Which scope is being searched is a parameter and nothing else
 * differs: Chat and Code search by section, an agent searches by its own id,
 * and the endpoint treats those as the same question asked two ways. Copying
 * the debounce-and-abort dance into a second file would have meant two places
 * to get out-of-order responses wrong.
 */

/**
 * What the caller is searching: a section's shelf, one agent's, or every
 * surface at once.
 *
 * The third is not "the other two left blank". On the server an absent
 * `agent_id` filters to `agent_id is null`, which is exactly how Chat and Code
 * keep the ten specialists out of their own history; searching everywhere has
 * to ask for it by name. See `scope=all` in `searchSessions`.
 */
export type SearchScope =
  | { section: "chat" | "code"; agentId?: undefined; all?: undefined }
  | { agentId: string; section?: undefined; all?: undefined }
  | { all: true; section?: undefined; agentId?: undefined };

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
export function useSessionSearch(
  token?: string | null,
  scope: SearchScope = { section: "chat" },
) {
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
      searchSessions(term, token, scope.section ?? "chat", {
        agentId: scope.agentId ?? null,
        all: scope.all ?? false,
        signal: controller.signal,
      })
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
    // Depending on the two fields rather than on `scope` itself: the caller
    // writes the scope inline, so a new object identity arrives every render
    // and the effect would re-fire — and re-request — on every keystroke's
    // re-render rather than only when what is being searched changes.
  }, [term, token, scope.section, scope.agentId, scope.all]);

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

export function SearchBox({
  value,
  onChange,
  busy,
  placeholder = "Search this section's history…",
}: {
  value: string;
  onChange: (value: string) => void;
  busy: boolean;
  /** Overridden where "this section" is not what is being searched. */
  placeholder?: string;
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
        placeholder={placeholder}
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

export function SearchResults({
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
