"use client";

import { motion } from "framer-motion";
import { useEffect, useState } from "react";
import { SPRING_SOFT, useMotionOK } from "./Anim";
import { EmailSignIn } from "./EmailSignIn";
import { LoomMark } from "./LoomMark";
import { cn } from "@/lib/cn";
import {
  fetchAuthProviders,
  signInWithGoogle,
  type AuthProviders,
} from "@/lib/supabase";

/** Google's mark, drawn rather than fetched — no external asset at sign-in. */
function GoogleMark({ size = 17 }: { size?: number }) {
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
 * Where signing out lands you.
 *
 * Not a modal: a sign-out that leaves the previous account's conversation
 * legible behind a scrim has not really signed anyone out, whatever the token
 * says. This covers the app completely.
 *
 * `onContinueAnonymous` is passed only when the backend does not require a
 * token. That is a real supported mode — `REQUIRE_AUTH=0` is the documented
 * local-dev path — and turning this screen into a wall in front of it would
 * break running the project without a Supabase project at all. When the
 * backend does require auth the escape is simply absent, and this is a wall.
 */
export function AuthScreen({
  onContinueAnonymous,
}: {
  onContinueAnonymous?: () => void;
}) {
  const [providers, setProviders] = useState<AuthProviders | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const motionOK = useMotionOK();

  useEffect(() => {
    void fetchAuthProviders().then(setProviders);
  }, []);

  const google = async () => {
    setError(null);
    setBusy(true);
    // On success this navigates away, so `busy` only clears on the failure
    // path — which is the path that has something to say.
    const message = await signInWithGoogle();
    if (message) {
      setError(message);
      setBusy(false);
    }
  };

  // Only advertise Google once the probe has confirmed the project has it
  // switched on. Offering a button that leads to a Supabase error page is
  // worse than not offering it.
  const showGoogle = providers?.reachable ? providers.google : false;

  return (
    <div className="fixed inset-0 z-[70] grid place-items-center overflow-y-auto bg-base px-6 py-10">
      <motion.div
        initial={motionOK ? { opacity: 0, y: 10, scale: 0.985 } : false}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={motionOK ? SPRING_SOFT : { duration: 0 }}
        className="glass w-full max-w-[23rem] rounded-card p-7"
      >
        <div className="flex flex-col items-center gap-3 pb-6">
          <LoomMark size={44} />
          <div className="text-center">
            <h1 className="text-lg font-semibold tracking-[-0.01em] text-ink">
              Sign in to Loom
            </h1>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
              Your sessions, projects and notebooks follow the account.
            </p>
          </div>
        </div>

        {showGoogle && (
          <>
            <button
              type="button"
              onClick={() => void google()}
              disabled={busy}
              className="flex h-11 w-full items-center justify-center gap-2.5 rounded-ctl
                         border border-line bg-elevated text-sm font-medium text-ink
                         transition-colors duration-200 hover:bg-raised
                         disabled:cursor-not-allowed disabled:opacity-55"
            >
              <GoogleMark />
              {busy ? "Opening Google…" : "Continue with Google"}
            </button>
            <div className="flex items-center gap-2.5 py-4">
              <span className="h-px flex-1 bg-line" />
              <span className="text-2xs text-ink-faint">or</span>
              <span className="h-px flex-1 bg-line" />
            </div>
          </>
        )}

        <EmailSignIn size="roomy" autoFocus />

        {error && (
          <p className="mt-3 text-xs leading-relaxed text-del" role="alert">
            {error}
          </p>
        )}

        {onContinueAnonymous && (
          <>
            <div className="my-5 h-px bg-line" />
            <button
              type="button"
              onClick={onContinueAnonymous}
              className={cn(
                "block w-full text-center text-2xs text-ink-faint",
                "transition-colors duration-200 hover:text-ink-muted",
              )}
            >
              Continue without an account
            </button>
          </>
        )}
      </motion.div>
    </div>
  );
}
