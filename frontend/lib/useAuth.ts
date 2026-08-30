"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { signOutEverywhere, supabase } from "./supabase";

/**
 * The one place that knows whether anybody is signed in.
 *
 * It used to live inside `AuthPanel`, which was fine while the only thing that
 * cared was the panel's own popover. It is not fine now: signing out has to
 * return the whole app to an auth screen, and Settings has to show the account
 * it is about to sign out of. Two components reading `getSession()`
 * independently would drift — and would each subscribe to
 * `onAuthStateChange`, so a single sign-out would race two state machines.
 *
 * `signedOut` is deliberately *not* `!signedIn`. They answer different
 * questions:
 *
 *   · `signedIn` — is there a session right now?
 *   · `signedOut` — did the user, in this browsing session, explicitly ask to
 *     leave?
 *
 * Only the second one should put a full-screen auth wall in front of the app.
 * Never having signed in is the ordinary anonymous path this project supports
 * by design (`REQUIRE_AUTH=0`), and walling that off would break local use for
 * a behaviour nobody asked for.
 */
export type Auth = {
  /** Supabase access token, or null when anonymous. */
  token: string | null;
  email: string | null;
  /** Supabase user id, or null when anonymous. Identity, not display. */
  userId: string | null;
  signedIn: boolean;
  /** True after an explicit sign-out, until the user signs in again. */
  signedOut: boolean;
  /** False until the first `getSession()` answers — avoids an auth-screen flash. */
  ready: boolean;
  /** Resolves to an error message, or null on success. */
  signOut: () => Promise<string | null>;
  /** Dismiss the post-sign-out wall and carry on anonymously. */
  continueAnonymously: () => void;
};

export function useAuth(): Auth {
  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  /**
   * Sign-out sets `signedOut` optimistically; the `onAuthStateChange` that
   * follows must not immediately clear it. Only a *new* session does that,
   * which is what this distinguishes.
   */
  const leaving = useRef(false);

  useEffect(() => {
    const sb = supabase();
    if (!sb) {
      setReady(true);
      return;
    }

    sb.auth.getSession().then(({ data }) => {
      setToken(data.session?.access_token ?? null);
      setEmail(data.session?.user.email ?? null);
      setUserId(data.session?.user.id ?? null);
      setReady(true);
    });

    const { data: sub } = sb.auth.onAuthStateChange((_e, session) => {
      setToken(session?.access_token ?? null);
      setEmail(session?.user.email ?? null);
      setUserId(session?.user.id ?? null);
      setReady(true);
      if (session) {
        leaving.current = false;
        setSignedOut(false);
      }
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  const signOut = useCallback(async () => {
    const message = await signOutEverywhere();
    if (message) return message;
    leaving.current = true;
    setToken(null);
    setEmail(null);
    setUserId(null);
    setSignedOut(true);
    return null;
  }, []);

  const continueAnonymously = useCallback(() => {
    leaving.current = false;
    setSignedOut(false);
  }, []);

  return useMemo(
    () => ({
      token,
      email,
      userId,
      signedIn: Boolean(token),
      signedOut,
      ready,
      signOut,
      continueAnonymously,
    }),
    [token, email, userId, signedOut, ready, signOut, continueAnonymously],
  );
}
