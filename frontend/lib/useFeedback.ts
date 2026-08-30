"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchFeedback, sendFeedback } from "./api";

/**
 * Thumbs on assistant replies, for one session and one person.
 *
 * Keyed by *assistant-turn ordinal* rather than by a message id, for the same
 * reason the branch switcher is: the transcript is replayed from the server's
 * checkpoint and carries no per-message row identity, so position is the only
 * coordinate that survives a reload. The caller counts assistant rows; the
 * server stores the number it is given.
 *
 * The write is optimistic and the read is once-per-session. Both follow from
 * what this data is for: nothing in the product reads it back, so a verdict
 * that takes a moment to reach the database costs nothing, while a thumb that
 * does not light up the instant you press it reads as broken.
 */
export function useFeedback(sessionId: string | null, token?: string | null) {
  const [ratings, setRatings] = useState<Record<number, "up" | "down">>({});
  const loadedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    // Clear first. Without this, switching sessions shows the previous
    // conversation's thumbs against this one's replies until the fetch lands —
    // briefly attributing an opinion to someone who never expressed it.
    setRatings({});
    loadedFor.current = sessionId;
    let live = true;
    fetchFeedback(sessionId, token)
      .then((rows) => {
        if (!live || loadedFor.current !== sessionId) return;
        const next: Record<number, "up" | "down"> = {};
        for (const row of rows) next[row.turn_index] = row.rating;
        setRatings(next);
      })
      .catch(() => {
        // Feedback is not load-bearing. A transcript without its thumbs is a
        // perfectly good transcript.
      });
    return () => {
      live = false;
    };
  }, [sessionId, token]);

  const rate = useCallback(
    (
      turnIndex: number,
      rating: "up" | "down" | null,
      meta: { modelId?: string | null; section?: string } = {},
    ) => {
      if (!sessionId) return;
      setRatings((prev) => {
        const next = { ...prev };
        if (rating === null) delete next[turnIndex];
        else next[turnIndex] = rating;
        return next;
      });
      void sendFeedback(sessionId, turnIndex, rating, token, meta).catch(() => {
        // Deliberately not reverted. The alternative is a thumb that
        // un-presses itself a second after you pressed it, which reads as the
        // interface arguing with you — over a value nothing consumes. The next
        // load reads the server's answer either way.
      });
    },
    [sessionId, token],
  );

  return { ratings, rate };
}
