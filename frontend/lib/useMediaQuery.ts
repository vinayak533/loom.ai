"use client";

import { useEffect, useState } from "react";

/**
 * Reads a media query in React, SSR-safely.
 *
 * The Ledger layout genuinely behaves differently either side of `xl`: above
 * it the context column is docked and a file landing only needs to swap the
 * column's face; below it the column is an overlay, and a file landing has to
 * open it. That is a behavioural fork, not a styling one, so it cannot live in
 * a Tailwind breakpoint.
 *
 * Starts `false` on the server and on the first client frame, then settles —
 * so the small-screen behaviour is the one that renders before hydration,
 * which is the safer of the two to be briefly wrong about.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia(query);
    setMatches(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** `xl` — the width at which the context column docks instead of floating. */
export const DOCKED_QUERY = "(min-width: 1280px)";

/**
 * No hover. Row affordances that are revealed on hover — the session overflow
 * menu, chiefly — have to be permanently visible here, because there is no
 * gesture that would reveal them otherwise.
 */
export const TOUCH_QUERY = "(hover: none)";
