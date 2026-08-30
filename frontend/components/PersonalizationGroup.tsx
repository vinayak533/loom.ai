"use client";

import { Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Preferences } from "@/lib/api";
import { savePreferences } from "@/lib/api";
import type { Memory } from "@/lib/projects";
import { clearMemories, deleteMemory, fetchMemories } from "@/lib/projects";
import type { Auth } from "@/lib/useAuth";
import { cn } from "@/lib/cn";

/**
 * The Personalization section of Settings: custom instructions, and what Loom
 * has remembered.
 *
 * Three decisions worth stating, because each of them is a place this could
 * reasonably have gone the other way.
 *
 * **The two boxes are two questions, not one.** "What should Loom know about
 * you" and "how should Loom respond" are answered in different registers, and
 * a single box asking for both gets a paragraph that does neither well. They
 * are also injected under separate headings, so keeping them separate here
 * matches what actually reaches the model.
 *
 * **Saving is on blur, not on every keystroke.** A debounced autosave on a
 * textarea sends a request per pause and makes the failure case ("did that
 * save?") impossible to answer honestly. Blur is a moment the user has already
 * decided they are done, and the state under the box says which of the three
 * things is true — unsaved, saving, saved.
 *
 * **Memory is listed, not summarised.** A count would be smaller, and would
 * also be the one design that makes the feature untrustworthy: the whole
 * question a person has about a memory feature is *what exactly does it know*,
 * and anything short of the list refuses to answer it.
 */
export function PersonalizationGroup({
  prefs,
  setPrefs,
  auth,
}: {
  prefs: Preferences;
  setPrefs: (next: Preferences) => void;
  auth: Auth;
}) {
  const signedIn = auth.signedIn;
  const [memories, setMemories] = useState<Memory[]>([]);
  const [memoryError, setMemoryError] = useState<string | null>(null);
  const [loadingMemories, setLoadingMemories] = useState(false);

  const load = useCallback(() => {
    if (!signedIn) {
      setMemories([]);
      return;
    }
    setLoadingMemories(true);
    fetchMemories(auth.token)
      .then((state) => setMemories(state.memories))
      .catch(() => setMemoryError("Could not load what Loom remembers."))
      .finally(() => setLoadingMemories(false));
  }, [signedIn, auth.token]);

  useEffect(load, [load]);

  return (
    <section className="mb-6 last:mb-0">
      <h3 className="voice-label pb-3">Personalization</h3>

      <InstructionField
        id="settings-about-you"
        label="What should Loom know about you?"
        hint="Your role, your stack, the things you never want to have to say twice."
        placeholder="I'm a backend engineer working mostly in Python and Postgres."
        value={prefs.about_you}
        disabled={!signedIn}
        onSave={async (value) => {
          const saved = await savePreferences({ about_you: value }, auth.token);
          setPrefs(saved);
        }}
      />

      <InstructionField
        id="settings-response-style"
        label="How should Loom respond?"
        hint="Tone, length, formatting. This one is about the writing, not about you."
        placeholder="Be terse. Skip the preamble. Show code before explaining it."
        value={prefs.response_style}
        disabled={!signedIn}
        onSave={async (value) => {
          const saved = await savePreferences({ response_style: value }, auth.token);
          setPrefs(saved);
        }}
      />

      {/* ------------------------------------------------------------ memory */}
      <div className="mt-5 border-t border-line pt-4">
        <label className="flex items-start justify-between gap-4">
          <span className="min-w-0">
            <span className="block text-xs text-ink">Remember across conversations</span>
            <span className="mt-1 block text-2xs leading-relaxed text-ink-faint">
              Loom notices durable facts — what you work on, how you like
              answers — and carries them into later conversations. Turning this
              off stops it learning anything new. It does not clear the two
              boxes above; you wrote those on purpose.
            </span>
          </span>
          <Toggle
            checked={prefs.memory_enabled && signedIn}
            disabled={!signedIn}
            label="Remember across conversations"
            onChange={async (next) => {
              const previous = prefs.memory_enabled;
              setPrefs({ ...prefs, memory_enabled: next });
              try {
                const saved = await savePreferences(
                  { memory_enabled: next },
                  auth.token,
                );
                setPrefs(saved);
              } catch {
                setPrefs({ ...prefs, memory_enabled: previous });
                setMemoryError("Could not change that setting.");
              }
            }}
          />
        </label>

        {signedIn && (
          <div className="mt-4">
            <div className="flex items-baseline justify-between gap-4 pb-2">
              <span className="text-2xs uppercase tracking-wide text-ink-faint">
                {memories.length > 0
                  ? `${memories.length} remembered`
                  : "Nothing remembered yet"}
              </span>
              {memories.length > 0 && (
                <button
                  type="button"
                  onClick={async () => {
                    // No confirmation dialog: the list is right there, every
                    // row has its own delete, and this is recoverable in the
                    // only sense that matters — Loom re-learns from use.
                    setMemories([]);
                    try {
                      await clearMemories(auth.token);
                    } catch {
                      setMemoryError("Could not forget everything.");
                      load();
                    }
                  }}
                  className="text-2xs text-ink-faint transition-colors hover:text-del"
                >
                  Forget everything
                </button>
              )}
            </div>

            {loadingMemories && (
              <p className="text-2xs text-ink-faint">Loading…</p>
            )}

            {!loadingMemories && memories.length === 0 && (
              <p className="text-2xs leading-relaxed text-ink-faint">
                {prefs.memory_enabled
                  ? "Keep using Loom and things worth remembering will appear here."
                  : "Memory is off, so nothing new is being learned."}
              </p>
            )}

            <ul className="flex flex-col gap-1.5">
              {memories.map((m) => (
                <li
                  key={m.id}
                  className="group flex items-start justify-between gap-3 rounded-ctl
                             border border-line bg-inset px-3 py-2"
                >
                  <span className="min-w-0 text-2xs leading-relaxed text-ink-muted">
                    {m.content}
                  </span>
                  <button
                    type="button"
                    aria-label={`Forget: ${m.content}`}
                    onClick={async () => {
                      setMemories((list) => list.filter((x) => x.id !== m.id));
                      try {
                        await deleteMemory(m.id, auth.token);
                      } catch {
                        setMemoryError("Could not forget that.");
                        load();
                      }
                    }}
                    /* Present but invisible until hover or focus, and never
                       unmounted — hiding with opacity is what keeps it
                       reachable by keyboard and to a screen reader. */
                    className="shrink-0 rounded-[5px] p-1 text-ink-faint opacity-0
                               transition-opacity duration-150 hover:text-del
                               focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    <Trash2 size={13} strokeWidth={1.75} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {!signedIn && (
          <p className="mt-3 text-2xs leading-relaxed text-ink-faint">
            Personalization is stored against an account rather than this
            browser, so it needs a sign-in.
          </p>
        )}

        {memoryError && (
          <p className="pt-2 text-2xs text-warn" role="alert">
            {memoryError}
          </p>
        )}
      </div>
    </section>
  );
}

/**
 * One custom-instruction textarea.
 *
 * Local state while typing, saved on blur, with the status line under it
 * saying which of unsaved / saving / saved is true. The value is re-synced
 * from the prop only when the box is not focused, so a save landing while the
 * user is mid-sentence cannot overwrite what they are typing.
 */
function InstructionField({
  id,
  label,
  hint,
  placeholder,
  value,
  disabled,
  onSave,
}: {
  id: string;
  label: string;
  hint: string;
  placeholder: string;
  value: string;
  disabled?: boolean;
  onSave: (value: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState(value);
  const [state, setState] = useState<"clean" | "dirty" | "saving" | "saved" | "error">(
    "clean",
  );
  const focused = useRef(false);

  useEffect(() => {
    if (!focused.current) {
      setDraft(value);
      setState("clean");
    }
  }, [value]);

  const commit = async () => {
    focused.current = false;
    if (draft === value) {
      setState("clean");
      return;
    }
    setState("saving");
    try {
      await onSave(draft);
      setState("saved");
    } catch {
      setState("error");
    }
  };

  return (
    <div className="pb-4">
      <label htmlFor={id} className="block pb-1.5 text-xs text-ink-muted">
        {label}
      </label>
      <textarea
        id={id}
        rows={3}
        value={draft}
        disabled={disabled}
        placeholder={placeholder}
        onFocus={() => {
          focused.current = true;
        }}
        onChange={(e) => {
          setDraft(e.target.value);
          setState("dirty");
        }}
        onBlur={() => void commit()}
        className="w-full resize-y rounded-ctl border border-line bg-inset px-3 py-2
                   font-sans text-xs leading-relaxed text-ink transition-colors duration-200
                   placeholder:text-ink-faint focus:border-line-focus focus:outline-none
                   disabled:cursor-not-allowed disabled:opacity-55"
      />
      <p className="pt-1.5 text-2xs leading-relaxed text-ink-faint">
        {state === "saving" && "Saving…"}
        {state === "saved" && "Saved."}
        {state === "error" && (
          <span className="text-warn">Could not save that. Your text is still here.</span>
        )}
        {state === "dirty" && "Click outside the box to save."}
        {state === "clean" && hint}
      </p>
    </div>
  );
}

/** A switch. A real checkbox underneath, so it is keyboard- and AT-reachable. */
function Toggle({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange: (next: boolean) => void;
}) {
  return (
    <span className="relative mt-0.5 inline-flex shrink-0">
      <input
        type="checkbox"
        role="switch"
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="peer absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0
                   disabled:cursor-not-allowed"
      />
      <span
        aria-hidden
        className={cn(
          "grid h-5 w-9 items-center rounded-full border px-0.5 transition-colors duration-200",
          checked ? "border-accent-line bg-accent/25" : "border-line bg-inset",
          disabled && "opacity-55",
          "peer-focus-visible:border-line-focus",
        )}
      >
        <span
          className={cn(
            "h-3.5 w-3.5 rounded-full transition-transform duration-200",
            checked ? "translate-x-4 bg-accent" : "translate-x-0 bg-ink-faint",
          )}
        />
      </span>
    </span>
  );
}
