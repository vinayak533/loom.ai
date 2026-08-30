"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { SPRING_SOFT, useMotionOK } from "./Anim";
import { cn } from "@/lib/cn";
import type { BackendConfig, Preferences } from "@/lib/api";
import { AUTO_MODEL_ID, fetchPreferences, savePreferences } from "@/lib/api";
import type { Auth } from "@/lib/useAuth";
import { authEnabled } from "@/lib/supabase";
import { PersonalizationGroup } from "./PersonalizationGroup";

/**
 * Settings.
 *
 * It exists for one reason: there was nowhere to sign out from. The control
 * did exist, buried in the account popover at the foot of the rail, which is
 * the last place anybody looks for it — so the reasonable conclusion was that
 * the app had no logout at all.
 *
 * Deliberately small. This is not a preferences system: the things a user can
 * change here are the things they were already able to change, plus the one
 * action that was missing. Everything else is a read-out, because knowing
 * which model is default and whether the sandbox is wired up is genuinely
 * useful and costs nothing to show.
 */
export function SettingsDialog({
  open,
  onClose,
  auth,
  config,
  onOpenShortcuts,
}: {
  open: boolean;
  onClose: () => void;
  auth: Auth;
  config: BackendConfig | null;
  /** Hands off to the keyboard reference. Settings is where it is found. */
  onOpenShortcuts: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const motionOK = useMotionOK();

  /**
   * Account-level preferences.
   *
   * Loaded when the panel opens rather than at mount: nothing else in the app
   * reads them yet at that point, and a settings round trip on every page load
   * would be a request nobody asked for. An anonymous caller gets the empty
   * shape back from the server and every control below is disabled, which is
   * the honest rendering of "this is stored against an account you do not have".
   */
  const [prefs, setPrefs] = useState<Preferences>({
    user_id: null,
    default_model_id: null,
    theme: "dark",
    about_you: "",
    response_style: "",
    memory_enabled: false,
  });
  const [savingPrefs, setSavingPrefs] = useState(false);
  const [prefsError, setPrefsError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    fetchPreferences(auth.token)
      .then((p) => {
        if (live) setPrefs(p);
      })
      .catch(() => {
        // A settings panel that cannot read preferences still has to open —
        // signing out is the thing people come here for.
        if (live) setPrefsError("Could not load your saved preferences.");
      });
    return () => {
      live = false;
    };
  }, [open, auth.token]);

  const saveModel = async (value: string) => {
    // Optimistic: the select is the source of truth for what the user just
    // did, and reverting it under them on a slow round trip is worse than
    // showing the choice and reporting a failure if one comes.
    const previous = prefs.default_model_id;
    setPrefs((p) => ({ ...p, default_model_id: value || null }));
    setSavingPrefs(true);
    setPrefsError(null);
    try {
      const saved = await savePreferences({ default_model_id: value }, auth.token);
      setPrefs(saved);
    } catch {
      setPrefs((p) => ({ ...p, default_model_id: previous }));
      setPrefsError("Could not save that. Your previous choice is unchanged.");
    } finally {
      setSavingPrefs(false);
    }
  };

  /** What the build would open on if the user expressed no preference. */
  const defaultName =
    config?.models.find((m) => m.id === config.default_model_id)?.name ??
    config?.default_model_id ??
    "—";

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (open) setError(null);
  }, [open]);

  const signOut = async () => {
    setBusy(true);
    const message = await auth.signOut();
    setBusy(false);
    if (message) {
      setError(message);
      return;
    }
    // The auth screen takes the whole viewport, so leaving this open would
    // stack one modal on top of a screen that has already replaced the app.
    onClose();
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="settings"
          className="fixed inset-0 z-[65] grid place-items-center overflow-y-auto p-5"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: motionOK ? 0.16 : 0 }}
        >
          <button
            type="button"
            aria-label="Close settings"
            onClick={onClose}
            className="absolute inset-0 bg-black/62 backdrop-blur-[2px]"
          />

          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Settings"
            initial={motionOK ? { opacity: 0, y: 12, scale: 0.985 } : false}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={motionOK ? { opacity: 0, y: 8, scale: 0.99 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SOFT : { duration: 0 }}
            className="glass relative w-full max-w-[30rem] rounded-card"
          >
            <header className="flex items-center justify-between border-b border-line px-5 py-4">
              <h2 className="text-sm font-semibold tracking-[-0.005em] text-ink">
                Settings
              </h2>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="grid h-8 w-8 place-items-center rounded-ctl text-ink-faint
                           transition-colors duration-200 hover:bg-raised hover:text-ink"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </header>

            <div className="max-h-[70vh] overflow-y-auto scroll-thin px-5 py-5">
              <Group label="Account">
                <div className="flex items-center gap-3 pb-3">
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-raised-solid text-sm font-semibold text-ink">
                    {(auth.email?.[0] ?? "V").toUpperCase()}
                  </span>
                  <span className="flex min-w-0 flex-col leading-tight">
                    <span className="truncate text-sm font-medium text-ink">
                      {auth.signedIn
                        ? auth.email?.split("@")[0]
                        : "Anonymous session"}
                    </span>
                    <span className="truncate text-2xs text-ink-faint">
                      {auth.signedIn
                        ? auth.email
                        : authEnabled
                          ? "Not signed in — sessions stay on this device"
                          : "Accounts are not configured for this build"}
                    </span>
                  </span>
                </div>

                {/* What "signed in" actually gets you, stated where it is
                    relevant. The session is a real thing with real scope, and
                    the sign-out button below is easier to press confidently
                    when you can see what it ends. */}
                <Row
                  name="Session"
                  value={
                    auth.signedIn
                      ? "Signed in — history and settings follow this account"
                      : "Local only — this browser"
                  }
                />
                {auth.signedIn && auth.userId && (
                  <Row name="Account id" value={auth.userId} mono />
                )}

                {auth.signedIn ? (
                  <>
                    <button
                      type="button"
                      onClick={() => void signOut()}
                      disabled={busy}
                      className="mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-ctl
                                 border border-del/35 bg-del-bg text-sm font-medium text-del
                                 transition-colors duration-200 hover:bg-del/16
                                 disabled:cursor-not-allowed disabled:opacity-55"
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M15 17v1.5A2.5 2.5 0 0 1 12.5 21h-6A2.5 2.5 0 0 1 4 18.5v-13A2.5 2.5 0 0 1 6.5 3h6A2.5 2.5 0 0 1 15 5.5V7" />
                        <path d="M10 12h11m0 0-3-3m3 3-3 3" />
                      </svg>
                      {busy ? "Signing out…" : "Sign out"}
                    </button>
                    <p className="pt-2 text-2xs leading-relaxed text-ink-faint">
                      Ends the session on every device and returns you to the
                      sign-in screen. Your conversations are kept.
                    </p>
                  </>
                ) : (
                  authEnabled && (
                    <p className="pt-2 text-2xs leading-relaxed text-ink-faint">
                      Sign in from the account control at the foot of the rail
                      to carry sessions between devices.
                    </p>
                  )
                )}

                {error && (
                  <p className="pt-2 text-2xs text-del" role="alert">
                    {error}
                  </p>
                )}
              </Group>

              {/* ------------------------------------------------ preferences */}
              <Group label="Preferences">
                <label
                  htmlFor="settings-default-model"
                  className="flex items-baseline justify-between gap-4 pb-1.5"
                >
                  <span className="text-xs text-ink-muted">Default model</span>
                  <span className="text-2xs text-ink-faint">
                    {auth.signedIn ? "Saved to your account" : "Sign in to save"}
                  </span>
                </label>
                <select
                  id="settings-default-model"
                  value={prefs.default_model_id ?? ""}
                  disabled={!auth.signedIn || savingPrefs}
                  onChange={(e) => void saveModel(e.target.value)}
                  className="h-10 w-full rounded-ctl border border-line bg-inset px-3
                             font-sans text-xs text-ink transition-colors duration-200
                             focus:border-line-focus focus:outline-none
                             disabled:cursor-not-allowed disabled:opacity-55"
                >
                  {/* Empty is not "no model" — it is "no *preference*", which
                      is a different thing and has to stay expressible. A user
                      who has never chosen should keep following the build's
                      default when it moves; one who has chosen should not. */}
                  <option value="">
                    Follow this build ({defaultName})
                  </option>
                  <option value={AUTO_MODEL_ID}>Auto — route by task</option>
                  {(config?.models ?? []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
                <p className="pb-3 pt-2 text-2xs leading-relaxed text-ink-faint">
                  {auth.signedIn
                    ? "New sessions open on this model, on every device you sign in to."
                    : "This is stored against your account rather than this browser, so it needs a sign-in."}
                </p>

                <Row name="Theme" value="Dark only" />
                <p className="pb-3 pt-2 text-2xs leading-relaxed text-ink-faint">
                  {/* Item 10 asked for this to be settled rather than left
                      ambiguous, so it is settled: there is one theme, it is
                      stated, and there is no control implying otherwise. */}
                  Loom ships a single true-black theme. There is no light mode
                  and none is planned — the interface is designed around a dark
                  ground rather than themed onto one.
                </p>

                <button
                  type="button"
                  onClick={onOpenShortcuts}
                  className="flex h-10 w-full items-center justify-between rounded-ctl border
                             border-line bg-elevated px-3 text-xs text-ink
                             transition-colors duration-200 hover:border-accent-line hover:bg-raised"
                >
                  <span>Keyboard shortcuts</span>
                  <kbd className="grid h-6 min-w-[1.5rem] place-items-center rounded-[5px]
                                  border border-line bg-raised px-1.5 text-[11px] text-ink">
                    ?
                  </kbd>
                </button>

                {prefsError && (
                  <p className="pt-2 text-2xs text-warn" role="alert">
                    {prefsError}
                  </p>
                )}
              </Group>

              {/* -------------------------------------------- personalization */}
              <PersonalizationGroup prefs={prefs} setPrefs={setPrefs} auth={auth} />

              <Group label="This build">
                <Row
                  name="Build default model"
                  value={defaultName}
                />
                <Row
                  name="Auto routing"
                  value={
                    config?.auto_task_routing
                      ? "By task, per turn"
                      : "By section"
                  }
                />
                <Row
                  name="Models available"
                  value={String(config?.models.length ?? 0)}
                />
                <Row
                  name="Sandbox"
                  value={config?.e2b ? "Connected" : "Not configured"}
                />
              </Group>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Group({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6 last:mb-0">
      <h3 className="voice-label pb-3">{label}</h3>
      {children}
    </section>
  );
}

function Row({
  name,
  value,
  mono,
}: {
  name: string;
  value: string;
  /** For identifiers, which are read character by character or not at all. */
  mono?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-4 border-b border-line py-2.5",
        "last:border-b-0 last:pb-0",
      )}
    >
      <span className="shrink-0 text-xs text-ink-muted">{name}</span>
      <span
        className={cn(
          "truncate text-xs text-ink",
          mono && "font-mono text-[11px] text-ink-muted",
        )}
        title={mono ? value : undefined}
      >
        {value}
      </span>
    </div>
  );
}
