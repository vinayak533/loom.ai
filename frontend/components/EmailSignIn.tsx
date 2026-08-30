"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";
import { SPRING_SNAP, useMotionOK } from "./Anim";
import { cn } from "@/lib/cn";
import { sendEmailCode, verifyEmailCode } from "@/lib/supabase";

/**
 * How long the emailed code is.
 *
 * Not a fixed six. The length is a property of the Supabase project's mailer
 * (`GOTRUE_MAILER_OTP_LENGTH`), and this one issues eight digits — a form that
 * insisted on six would have silently refused every real code with the Sign in
 * button greyed out and nothing to explain why. So the button arms at the
 * shortest length any project uses and the field accepts the longest.
 */
const CODE_MIN = 6;
const CODE_MAX = 10;

/**
 * Signing in with an email address, as two steps rather than one dead end.
 *
 * The previous form stopped at "check your email for a link", which is fine
 * right up until the link opens in a different browser from the one waiting
 * for it — a phone, a webmail client's in-app browser — and the session lands
 * somewhere the user is not. Supabase issues one credential redeemable either
 * way, so the second step here accepts the code from the same message and
 * finishes the sign-in in the window the user is actually looking at.
 *
 * Both halves are offered because a project's email template decides which one
 * arrives, and the component cannot know which. Saying so in one line is
 * better than silently presenting a code box for an email that has no code in
 * it.
 */
export function EmailSignIn({
  onSignedIn,
  autoFocus = false,
  size = "compact",
}: {
  onSignedIn?: () => void;
  autoFocus?: boolean;
  /** `roomy` on the full auth screen, `compact` inside the rail popover. */
  size?: "compact" | "roomy";
}) {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resentAt, setResentAt] = useState(0);
  const codeInput = useRef<HTMLInputElement>(null);
  const motionOK = useMotionOK();

  const roomy = size === "roomy";

  useEffect(() => {
    if (step === "code") codeInput.current?.focus();
  }, [step]);

  const send = useCallback(async () => {
    const address = email.trim();
    if (!address || busy) return;
    setBusy(true);
    setError(null);
    const message = await sendEmailCode(address);
    setBusy(false);
    if (message) {
      setError(message);
      return;
    }
    setStep("code");
    setResentAt(Date.now());
  }, [email, busy]);

  const verify = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    const message = await verifyEmailCode(email, code);
    setBusy(false);
    if (message) {
      setError(message);
      return;
    }
    setCode("");
    setStep("email");
    onSignedIn?.();
  }, [busy, email, code, onSignedIn]);

  const field = cn(
    "w-full rounded-ctl border border-line bg-inset text-ink placeholder:text-ink-faint",
    // Deliberately not the accent: see the composer's note in ChatPanel. A
    // focused field is confirmation, not an alarm, so it brightens its own
    // hairline instead of lighting up in the section colour.
    "transition-colors duration-200 focus:border-line-strong focus:outline-none",
    roomy ? "px-3 py-2.5 text-sm" : "px-2.5 py-2 text-xs",
  );

  const primary = cn(
    "flex w-full items-center justify-center gap-2 rounded-ctl bg-brand font-semibold",
    "text-[#0A0E1C] transition-all duration-200 hover:brightness-110",
    "active:scale-[0.985] disabled:cursor-not-allowed disabled:opacity-55",
    roomy ? "h-11 text-sm" : "h-[38px] text-sm",
  );

  return (
    <div className={roomy ? "space-y-3" : "space-y-2"}>
      <AnimatePresence mode="wait" initial={false}>
        {step === "email" ? (
          <motion.div
            key="email"
            initial={motionOK ? { opacity: 0, y: 4 } : false}
            animate={{ opacity: 1, y: 0 }}
            exit={motionOK ? { opacity: 0, y: -4 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            className={roomy ? "space-y-3" : "space-y-2"}
          >
            <label className="block">
              <span className="sr-only">Email address</span>
              <input
                type="email"
                autoComplete="email"
                autoFocus={autoFocus}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void send()}
                placeholder="you@example.com"
                className={field}
              />
            </label>
            <button
              type="button"
              onClick={() => void send()}
              disabled={busy || !email.trim()}
              className={primary}
            >
              {busy ? "Sending…" : "Continue with email"}
            </button>
          </motion.div>
        ) : (
          <motion.div
            key="code"
            initial={motionOK ? { opacity: 0, y: 4 } : false}
            animate={{ opacity: 1, y: 0 }}
            exit={motionOK ? { opacity: 0, y: -4 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            className={roomy ? "space-y-3" : "space-y-2"}
          >
            <p
              className={cn(
                "leading-relaxed text-ink-muted",
                roomy ? "text-xs" : "text-2xs",
              )}
            >
              Sent to <span className="text-ink">{email}</span>. Enter the code
              from that email, or just click the link in it.
            </p>
            <label className="block">
              <span className="sr-only">Sign-in code</span>
              <input
                ref={codeInput}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={CODE_MAX}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                onKeyDown={(e) => e.key === "Enter" && void verify()}
                placeholder="••••••"
                className={cn(
                  field,
                  "text-center font-mono tracking-[0.45em]",
                  roomy ? "text-base" : "text-sm",
                )}
              />
            </label>
            <button
              type="button"
              onClick={() => void verify()}
              disabled={busy || code.length < CODE_MIN}
              className={primary}
            >
              {busy ? "Verifying…" : "Sign in"}
            </button>
            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => {
                  setStep("email");
                  setCode("");
                  setError(null);
                }}
                className="text-2xs text-ink-faint transition-colors duration-200 hover:text-ink-muted"
              >
                Use a different address
              </button>
              <button
                type="button"
                disabled={busy || Date.now() - resentAt < 30_000}
                onClick={() => void send()}
                className="text-2xs text-ink-faint transition-colors duration-200
                           hover:text-ink-muted disabled:cursor-not-allowed disabled:opacity-45"
              >
                Resend
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {error && (
        <p
          className={cn(
            "leading-relaxed text-del",
            roomy ? "text-xs" : "text-2xs",
          )}
          role="alert"
        >
          {error}
        </p>
      )}
    </div>
  );
}
