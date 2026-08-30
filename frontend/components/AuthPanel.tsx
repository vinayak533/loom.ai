"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  authEnabled,
  fetchAuthProviders,
  signInWithGoogle,
  type AuthProviders,
} from "@/lib/supabase";
import type { Auth } from "@/lib/useAuth";
import { EmailSignIn } from "./EmailSignIn";
import { LoomMark } from "./LoomMark";
import { cn } from "@/lib/cn";

/** Google's mark, drawn rather than fetched — no external asset at sign-in. */
function GoogleMark({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden>
      <path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-3.2-.4-4.7H24v8.9h11.8a10 10 0 0 1-4.4 6.6v5.5h7.1c4.1-3.8 6.6-9.5 6.6-16.3z" />
      <path fill="#34A853" d="M24 46c5.9 0 10.9-2 14.5-5.3l-7.1-5.5c-2 1.3-4.5 2.1-7.4 2.1-5.7 0-10.5-3.8-12.2-9H4.5v5.7A22 22 0 0 0 24 46z" />
      <path fill="#FBBC05" d="M11.8 28.3a13.2 13.2 0 0 1 0-8.6v-5.7H4.5a22 22 0 0 0 0 20l7.3-5.7z" />
      <path fill="#EA4335" d="M24 10.8c3.2 0 6.1 1.1 8.4 3.3l6.3-6.3A22 22 0 0 0 4.5 14l7.3 5.7c1.7-5.2 6.5-9 12.2-9z" />
    </svg>
  );
}

/**
 * Account control, pinned to the bottom of the sidebar.
 *
 * Compact avatar + name + status dot; clicking opens an elevated popover rather
 * than navigating. Neutral by design — grayscale plus exactly one brand fill on
 * the CTA — so it never competes with whichever section accent is active.
 *
 * Supabase Auth is magic-link only. With no Supabase env vars the app runs
 * anonymously (the default local-dev path) and the popover says so instead of
 * showing a broken form.
 */
export function AuthPanel({
  auth,
  onOpenSettings,
  rail,
}: {
  /** The one auth state, owned by the page. See `lib/useAuth`. */
  auth: Auth;
  onOpenSettings: () => void;
  rail?: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"google" | "signout" | null>(null);
  const [providers, setProviders] = useState<AuthProviders | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const userEmail = auth.email;

  // Which providers the project has switched on. Asked for once the popover is
  // first opened rather than on mount, so the probe costs nothing to anyone who
  // never touches the account control.
  useEffect(() => {
    if (!open || providers) return;
    void fetchAuthProviders().then(setProviders);
  }, [open, providers]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const signedIn = Boolean(userEmail);
  const display = userEmail ?? "Local session";
  const initials = (userEmail?.[0] ?? "V").toUpperCase() + (userEmail ? "" : "K");

  const googleSignIn = async () => {
    setError(null);
    setBusy("google");
    // On success this navigates away, so `busy` is only ever cleared on the
    // failure path — which is the path that has something to say.
    const message = await signInWithGoogle();
    if (message) {
      setError(message);
      setBusy(null);
    }
  };

  const signOut = async () => {
    setBusy("signout");
    const message = await auth.signOut();
    setBusy(null);
    if (message) {
      setError(message);
      return;
    }
    // The popover would otherwise stay open over a form that has just become
    // the wrong one — and the auth screen is about to take the viewport.
    setError(null);
    setOpen(false);
  };

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-ctl p-2 transition-colors duration-200",
          "hover:bg-elevated",
          open && "bg-raised",
          rail && "justify-center",
        )}
      >
        <span className="relative grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full bg-raised-solid text-2xs font-semibold text-ink">
          {initials}
          <span
            className={cn(
              "absolute -bottom-px -right-px h-[9px] w-[9px] rounded-full ring-[2.5px] ring-surface",
              signedIn ? "bg-add" : "bg-ink-faint",
            )}
          />
        </span>
        {!rail && (
          <>
            <span className="flex min-w-0 flex-col items-start leading-tight md:hidden lg:flex">
              <span className="truncate text-sm font-medium text-ink">
                {signedIn ? display.split("@")[0] : "Vinayak K."}
              </span>
              <span className="truncate text-2xs text-ink-faint">
                {signedIn ? "Signed in" : "Anonymous · Local"}
              </span>
            </span>
            <motion.span
              animate={{ rotate: open ? 180 : 0 }}
              transition={{ duration: 0.2 }}
              className="ml-auto shrink-0 text-ink-faint md:hidden lg:block"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                <path d="m6 15 6-6 6 6" />
              </svg>
            </motion.span>
          </>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            initial={{ opacity: 0, y: 6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.98 }}
            transition={{ duration: 0.2, ease: [0.2, 0, 0, 1] }}
            style={{ transformOrigin: "bottom left" }}
            className={cn(
              "absolute z-50 w-[15.75rem] rounded-[14px] border border-line bg-overlay p-2 shadow-lift",
              rail ? "bottom-0 left-[calc(100%+10px)]" : "bottom-[calc(100%+8px)] left-0",
            )}
          >
            <div className="flex items-center gap-2.5 px-2 pb-3 pt-2">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-raised-solid text-sm font-semibold text-ink">
                {initials}
              </span>
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="truncate text-sm font-medium text-ink">
                  {signedIn ? display.split("@")[0] : "Vinayak K."}
                </span>
                <span className="truncate text-2xs text-ink-faint">{display}</span>
              </span>
            </div>

            <div className="my-1 h-px bg-line" />

            {!authEnabled && (
              <p className="px-2 py-2 text-2xs leading-relaxed text-ink-faint">
                Running anonymously. Set{" "}
                <code className="font-mono">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
                <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to
                enable accounts.
              </p>
            )}

            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onOpenSettings();
              }}
              className="flex h-9 w-full items-center gap-2.5 rounded-ctl px-2 text-sm text-ink-muted
                         transition-colors duration-200 hover:bg-white/[0.055] hover:text-ink"
            >
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3.1" />
                <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.9 19.3a1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.7 15a1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.7 8.9a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.7a1.7 1.7 0 0 0 1.03-1.56V3a2 2 0 1 1 4 0v.09A1.7 1.7 0 0 0 15 4.7a1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.3 9v.09a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.09A1.7 1.7 0 0 0 19.4 15z" />
              </svg>
              Settings
            </button>

            {authEnabled && signedIn && (
              <>
                <button
                  type="button"
                  role="menuitem"
                  disabled={busy === "signout"}
                  onClick={() => void signOut()}
                  className="flex h-9 w-full items-center gap-2.5 rounded-ctl px-2 text-sm text-ink-muted
                             transition-colors duration-200 hover:bg-white/[0.055] hover:text-ink
                             disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
                    <path d="M15 17v1.5A2.5 2.5 0 0 1 12.5 21h-6A2.5 2.5 0 0 1 4 18.5v-13A2.5 2.5 0 0 1 6.5 3h6A2.5 2.5 0 0 1 15 5.5V7" />
                    <path d="M10 12h11m0 0-3-3m3 3-3 3" />
                  </svg>
                  {busy === "signout" ? "Signing out…" : "Sign out"}
                </button>
                {error && <p className="px-2 pt-1 text-2xs text-del">{error}</p>}
              </>
            )}

            {authEnabled && !signedIn && (
              <div className="space-y-2 px-1 pb-1">
                {/* The one account surface that is a *sign-in* rather than a
                    session read-out, so it is the one that gets to sign its
                    name. Grayscale everywhere else in this panel still holds:
                    the mark is the artwork's own gold, not an accent fill, so
                    it does not start competing with the active section. */}
                <div className="flex flex-col items-center gap-2.5 px-1 pb-3 pt-1">
                  <LoomMark size={40} />
                  <p className="text-center text-2xs leading-relaxed text-ink-faint">
                    Sign in to carry your sessions between devices.
                  </p>
                </div>

                {/* Google first when the project actually has it switched
                    on. It used to render unconditionally with a paragraph
                    underneath explaining that it would not work — a button
                    that says "do not press me" is worse than no button, and
                    the full sign-in screen already made the other choice. */}
                {providers?.reachable && providers.google && (
                  <>
                    <button
                      type="button"
                      disabled={busy === "google"}
                      onClick={() => void googleSignIn()}
                      className="flex h-[38px] w-full items-center justify-center gap-2.5 rounded-ctl
                                 border border-line bg-elevated text-sm font-medium text-ink
                                 transition-colors duration-200 hover:bg-white/[0.055]
                                 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <GoogleMark />
                      {busy === "google" ? "Opening Google…" : "Continue with Google"}
                    </button>

                    <div className="flex items-center gap-2 px-1 py-0.5">
                      <span className="h-px flex-1 bg-line" />
                      <span className="text-2xs text-ink-faint">or</span>
                      <span className="h-px flex-1 bg-line" />
                    </div>
                  </>
                )}

                <EmailSignIn onSignedIn={() => setOpen(false)} />

                {error && <p className="px-1 text-2xs text-del">{error}</p>}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
