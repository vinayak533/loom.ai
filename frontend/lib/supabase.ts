"use client";

/**
 * Supabase Auth only. The browser uses the ANON key — never the service-role
 * key, which stays in backend/.env. All data reads/writes go through our
 * FastAPI backend, not directly from here.
 *
 * If the env vars are unset the app runs anonymously, which is the default
 * local-dev experience.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

let client: SupabaseClient | null = null;

export const authEnabled = Boolean(url && anonKey);

export function supabase(): SupabaseClient | null {
  if (!authEnabled) return null;
  if (!client) {
    client = createClient(url!, anonKey!, {
      auth: {
        // OAuth returns to the app with a `?code=` in the URL; this is what
        // exchanges it for a session. On by default, but stated explicitly
        // because the Google flow below is entirely dependent on it.
        detectSessionInUrl: true,
        persistSession: true,
        autoRefreshToken: true,
        flowType: "pkce",
      },
    });
  }
  return client;
}

export async function currentAccessToken(): Promise<string | null> {
  const sb = supabase();
  if (!sb) return null;
  const { data } = await sb.auth.getSession();
  return data.session?.access_token ?? null;
}

/* ------------------------------------------------------------------ providers
 * Which sign-in methods this Supabase *project* actually has switched on.
 *
 * This is asked for rather than assumed because the failure it prevents is
 * otherwise silent and very confusing: with the Google provider disabled in
 * the project, `signInWithOAuth` still returns a URL and still navigates —
 * straight to a Supabase error page reading `Unsupported provider`. The user
 * sees a redirect that "did nothing". Probing the public settings endpoint
 * lets the button say precisely what is wrong, before spending a navigation
 * on it.
 *
 * The endpoint is public (anon key) and returns booleans only, never secrets.
 */
export type AuthProviders = {
  google: boolean;
  email: boolean;
  /** Null while unknown — the probe has not answered yet, or it failed. */
  reachable: boolean;
};

let providerCache: AuthProviders | null = null;

export async function fetchAuthProviders(): Promise<AuthProviders | null> {
  if (!authEnabled) return null;
  if (providerCache) return providerCache;
  try {
    const res = await fetch(`${url}/auth/v1/settings`, {
      headers: { apikey: anonKey! },
      cache: "no-store",
    });
    if (!res.ok) return { google: false, email: false, reachable: false };
    const body = (await res.json()) as { external?: Record<string, boolean> };
    providerCache = {
      google: Boolean(body.external?.google),
      email: Boolean(body.external?.email),
      reachable: true,
    };
    return providerCache;
  } catch {
    return { google: false, email: false, reachable: false };
  }
}

/**
 * Start the Google OAuth redirect.
 *
 * Returns an error *string* rather than throwing, because every failure here
 * is something the panel has to render in place — there is no second screen to
 * fail onto once the redirect has been handed to the browser.
 */
export async function signInWithGoogle(): Promise<string | null> {
  const sb = supabase();
  if (!sb) {
    return "Accounts are not configured: NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are unset in frontend/.env.local.";
  }

  // Check before navigating, so a disabled provider reports itself instead of
  // bouncing the user to a Supabase error page.
  const providers = await fetchAuthProviders();
  if (providers && providers.reachable && !providers.google) {
    return (
      "Google sign-in is switched off for this Supabase project. " +
      "Enable it under Authentication → Providers → Google and paste in the " +
      "Client ID and Client Secret from your Google Cloud OAuth credentials. " +
      "The authorised redirect URI in Google Cloud must be " +
      `${url}/auth/v1/callback`
    );
  }

  const { error } = await sb.auth.signInWithOAuth({
    provider: "google",
    options: {
      redirectTo: window.location.origin,
      queryParams: { access_type: "offline", prompt: "select_account" },
    },
  });

  if (!error) return null;
  if (/provider is not enabled|unsupported provider/i.test(error.message)) {
    return (
      "Google sign-in is switched off for this Supabase project. Enable it " +
      "under Authentication → Providers → Google and supply the Google Cloud " +
      "OAuth Client ID and Secret."
    );
  }
  return error.message;
}

/**
 * Sign out for real.
 *
 * The plain `signOut()` is not enough on its own. Its default `global` scope
 * asks the server to revoke every session for the user, and when the stored
 * refresh token has already expired or been revoked that call fails — at which
 * point supabase-js can leave the local session in place. The visible result
 * is precisely the reported bug: the UI returns to a signed-out look while the
 * token in local storage is still there and still being sent.
 *
 * So: try global, fall back to local (which never touches the network), and
 * as a last resort clear the client's own storage keys by hand. Then confirm
 * the session is actually gone before reporting success.
 */
export async function signOutEverywhere(): Promise<string | null> {
  const sb = supabase();
  if (!sb) return null;

  try {
    const { error } = await sb.auth.signOut({ scope: "global" });
    if (error) {
      // Network/expired-token path: drop the local session regardless.
      await sb.auth.signOut({ scope: "local" }).catch(() => undefined);
    }
  } catch {
    await sb.auth.signOut({ scope: "local" }).catch(() => undefined);
  }

  // Belt and braces: if anything above silently no-opped, the session is still
  // readable here, and leaving it would mean a "signed out" UI over a live
  // token. Clearing the storage keys is the only thing left that always works.
  const { data } = await sb.auth.getSession();
  if (data.session) {
    try {
      for (const key of Object.keys(window.localStorage)) {
        if (key.startsWith("sb-") && key.includes("-auth-token")) {
          window.localStorage.removeItem(key);
        }
      }
    } catch {
      /* storage unavailable — nothing further we can do */
    }
    const after = await sb.auth.getSession();
    if (after.data.session) return "Could not clear the session. Try reloading.";
  }
  return null;
}
