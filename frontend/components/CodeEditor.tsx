"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";

/**
 * The inline editor — CodeMirror 6, loaded on demand.
 *
 * Everything about this file is arranged around one constraint: the editor is
 * ~180KB of JavaScript that most sessions never open. So it is imported
 * dynamically inside an effect rather than at module scope, which keeps it out
 * of the initial bundle entirely; until it resolves the component renders a
 * read-only view of the same text, so the panel is never blank and the content
 * survives the editor failing to load at all.
 *
 * CodeMirror rather than Monaco: Monaco wants web workers and a bundler
 * configuration of its own, which is a large amount of machinery for "let me
 * fix this one line without asking the agent".
 *
 * Editing autosaves. Typing stops for `AUTOSAVE_MS` and the buffer goes to the
 * sandbox, with the save bar reporting each state as it happens — there is no
 * step where a user has made an edit and does not know whether it took. ⌘S and
 * the Save button remain, as accelerators rather than as the only way through.
 *
 * The one race worth naming: the agent may write the same file while a buffer
 * is dirty. That is handled where it happens (see the external-updates effect
 * below) rather than by refusing to autosave — the agent's version is adopted
 * only into a *clean* buffer, and a dirty one is left alone and says so.
 */

export type EditorStatus = "idle" | "dirty" | "saving" | "saved" | "error";

/**
 * How long typing has to stop before the buffer is written. Long enough that a
 * pause mid-thought is not a save, short enough that looking away and looking
 * back means it is already on disk.
 */
const AUTOSAVE_MS = 900;

export function CodeEditor({
  path,
  value,
  onSave,
  className,
  destination = "the sandbox",
  readOnly = false,
}: {
  /** Drives syntax highlighting, and re-seeds the buffer when it changes. */
  path: string;
  value: string;
  /** Resolves when the save has landed. Rejects with a message. */
  onSave: (content: string) => Promise<void>;
  className?: string;
  /**
   * Where a save goes, named in the status line.
   *
   * This editor was written for sandbox files and said so in three places. It
   * now also edits artifacts, which save as a new version in the database and
   * never touch the sandbox at all — so a hardcoded "Autosaves to the sandbox"
   * was simply false half the time it appeared. Naming the destination is
   * cheaper than two editors.
   */
  destination?: string;
  /** Read-only, for content that exists but must not be edited here. */
  readOnly?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<any>(null);
  /** The saved content, so "dirty" is a comparison rather than a guess. */
  const baseline = useRef(value);
  const [status, setStatus] = useState<EditorStatus>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // Kept in a ref so the CodeMirror keymap — built once, at mount — always
  // calls the current handler rather than the one captured on the first render.
  const saveRef = useRef(onSave);
  saveRef.current = onSave;
  // Same reason as `saveRef`: the CodeMirror state is built once, at mount, so
  // anything it reads has to come through a ref rather than a closure over the
  // first render's props.
  const readOnlyRef = useRef(readOnly);
  readOnlyRef.current = readOnly;

  /** The pending autosave. One at a time, always for the latest document. */
  const autosaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** `commit` is redefined every render; the debounce must call the live one. */
  const commitRef = useRef<(content: string) => Promise<void>>();

  const scheduleAutosave = () => {
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => {
      autosaveTimer.current = null;
      // Read the document at fire time rather than closing over the keystroke
      // that scheduled this — the point of the debounce is the ones after it.
      const text = view.current?.state.doc.toString();
      if (text !== undefined) void commitRef.current?.(text);
    }, AUTOSAVE_MS);
  };

  const cancelAutosave = () => {
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = null;
  };

  useEffect(() => cancelAutosave, []);

  // --- mount ---------------------------------------------------------------
  useEffect(() => {
    let disposed = false;

    (async () => {
      const [{ EditorState }, cmView, cmCommands, cmLanguage, cmSearch, { tags }] =
        await Promise.all([
          import("@codemirror/state"),
          import("@codemirror/view"),
          import("@codemirror/commands"),
          import("@codemirror/language"),
          import("@codemirror/search"),
          import("@lezer/highlight"),
        ]);

      if (disposed || !host.current) return;

      const { EditorView, keymap, lineNumbers, highlightActiveLine, drawSelection,
        highlightActiveLineGutter, rectangularSelection, crosshairCursor } = cmView;
      const { defaultKeymap, history, historyKeymap, indentWithTab } = cmCommands;
      const {
        indentOnInput,
        bracketMatching,
        foldGutter,
        foldKeymap,
        syntaxHighlighting,
      } = cmLanguage;

      const language = await loadLanguage(path);

      const save = () => {
        const current = view.current?.state.doc.toString() ?? "";
        void commit(current);
        return true;
      };

      const state = EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          highlightActiveLine(),
          drawSelection(),
          rectangularSelection(),
          crosshairCursor(),
          history(),
          foldGutter(),
          indentOnInput(),
          bracketMatching(),
          syntaxHighlighting(highlightStyle(cmLanguage.HighlightStyle, tags)),
          cmSearch.highlightSelectionMatches(),
          EditorState.allowMultipleSelections.of(true),
          // Read-only means the document cannot be *changed*, not that it
          // cannot be reached: `readOnly` leaves the cursor, selection and
          // copying intact, which `editable.of(false)` would take away. A
          // history version has to stay selectable to be useful at all.
          EditorState.readOnly.of(readOnlyRef.current),
          EditorView.lineWrapping,
          keymap.of([
            // Save comes first so it wins over anything the defaults bind.
            { key: "Mod-s", preventDefault: true, run: save },
            ...defaultKeymap,
            ...historyKeymap,
            ...foldKeymap,
            ...cmSearch.searchKeymap,
            indentWithTab,
          ]),
          EditorView.updateListener.of((update: any) => {
            if (!update.docChanged) return;
            const text = update.state.doc.toString();
            setStatus((prev) =>
              text === baseline.current
                ? "idle"
                : prev === "saving"
                  ? prev
                  : "dirty",
            );
            // Typing back to the saved content is not a save — but a pending
            // autosave for the edit it undid has to go with it.
            if (text === baseline.current) cancelAutosave();
            else scheduleAutosave();
          }),
          language ?? [],
          theme(EditorView),
        ],
      });

      view.current = new EditorView({ state, parent: host.current });
      setReady(true);
    })().catch(() => {
      // The read-only fallback below stays up. A missing editor is a missing
      // convenience; a missing file would be a lost one.
      if (!disposed) setReady(false);
    });

    return () => {
      disposed = true;
      cancelAutosave();
      view.current?.destroy();
      view.current = null;
    };
    // Remounting per file is deliberate: a fresh document means fresh history,
    // and carrying an undo stack across files would let Ctrl+Z type one file's
    // content into another.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  // --- external updates ----------------------------------------------------
  // The agent may rewrite the file we are looking at. If the buffer is clean we
  // adopt its version; if it is dirty we leave the user's work alone and say so.
  useEffect(() => {
    if (value === baseline.current) return;
    const editor = view.current;
    if (!editor) {
      baseline.current = value;
      return;
    }
    const current = editor.state.doc.toString();
    if (current !== baseline.current) {
      setMessage("The agent changed this file. Your unsaved edits are still here.");
      baseline.current = value;
      return;
    }
    editor.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    });
    baseline.current = value;
    setStatus("idle");
  }, [value]);

  // --- saving --------------------------------------------------------------
  const commit = async (content: string) => {
    if (content === baseline.current) return;
    cancelAutosave();
    setStatus("saving");
    setMessage(null);
    try {
      await saveRef.current(content);
      baseline.current = content;
      setStatus("saved");
      setMessage(null);
      // Settle back to neutral; "saved" is a confirmation, not a resting state.
      setTimeout(
        () => setStatus((s) => (s === "saved" ? "idle" : s)),
        1600,
      );
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Could not save.");
    }
  };

  commitRef.current = commit;

  const saveNow = () =>
    void commit(view.current?.state.doc.toString() ?? value);

  const revert = () => {
    const editor = view.current;
    if (!editor) return;
    cancelAutosave();
    editor.dispatch({
      changes: { from: 0, to: editor.state.doc.length, insert: baseline.current },
    });
    setStatus("idle");
    setMessage(null);
  };

  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <div className="scroll-thin relative min-h-0 flex-1 overflow-auto">
        <div ref={host} className="min-h-full" />
        {!ready && (
          // Not a spinner: the file itself, unstyled and read-only, so the panel
          // shows content from the first frame and simply becomes editable.
          <pre className="pointer-events-none absolute inset-0 overflow-auto whitespace-pre px-3 py-2 font-mono text-xs leading-[1.65] text-ink/70">
            {value}
          </pre>
        )}
      </div>

      <SaveBar
        status={status}
        message={message}
        onSave={saveNow}
        onRevert={revert}
        disabled={!ready || readOnly}
        destination={destination}
      />
    </div>
  );
}

function SaveBar({
  destination,
  status,
  message,
  onSave,
  onRevert,
  disabled,
}: {
  status: EditorStatus;
  message: string | null;
  onSave: () => void;
  onRevert: () => void;
  disabled: boolean;
  destination: string;
}) {
  const dirty = status === "dirty" || status === "error";

  return (
    <div className="flex h-9 shrink-0 items-center gap-2 border-t border-line px-3">
      <span
        aria-hidden
        className={cn(
          "sigil h-[7px] w-[7px] shrink-0",
          status === "error"
            ? "bg-del"
            : status === "saved"
              ? "bg-add"
              : dirty
                ? "bg-warn"
                : "bg-ink-dim",
        )}
      />
      <span
        className={cn(
          "min-w-0 flex-1 truncate font-sans text-2xs",
          status === "error" ? "text-del" : "text-ink-faint",
        )}
        title={message ?? undefined}
      >
        {message ??
          (status === "saving"
            ? "Saving…"
            : status === "saved"
              ? `Saved to ${destination}`
              : dirty
                ? "Unsaved — saving shortly"
                : `Autosaves to ${destination}`)}
      </span>

      {dirty && (
        <button
          type="button"
          onClick={onRevert}
          className="voice-label shrink-0 text-ink-faint transition-colors hover:text-ink-muted"
        >
          Revert
        </button>
      )}
      <button
        type="button"
        onClick={onSave}
        disabled={disabled || status === "saving" || (!dirty && status !== "idle")}
        className={cn(
          "shrink-0 rounded-ctl px-2.5 py-1 text-2xs font-medium transition-all duration-200",
          dirty
            ? "bg-accent text-accent-ink hover:brightness-110 active:scale-[0.96]"
            : "bg-raised text-ink-faint",
          "disabled:pointer-events-none disabled:opacity-45",
        )}
      >
        Save
      </button>
    </div>
  );
}

// --- theme -----------------------------------------------------------------

/**
 * The editor's colours come from the app's own tokens rather than from a
 * CodeMirror theme package, so the panel changes accent with the section and
 * sits on the same surface ramp as everything around it.
 */
function theme(EditorView: any) {
  return EditorView.theme(
    {
      "&": {
        color: "rgb(233 234 238 / 0.85)",
        backgroundColor: "transparent",
        fontSize: "0.75rem",
        height: "100%",
      },
      ".cm-content": {
        fontFamily: "var(--font-mono), ui-monospace, monospace",
        lineHeight: "1.65",
        padding: "8px 0",
        caretColor: "rgb(var(--acc))",
      },
      ".cm-scroller": { fontFamily: "inherit", overflow: "visible" },
      ".cm-gutters": {
        backgroundColor: "transparent",
        color: "#4C505A",
        border: "none",
        paddingRight: "4px",
      },
      ".cm-activeLineGutter": { backgroundColor: "rgba(255,255,255,0.04)", color: "#7B808C" },
      ".cm-activeLine": { backgroundColor: "rgba(255,255,255,0.028)" },
      ".cm-cursor, .cm-dropCursor": { borderLeftColor: "rgb(var(--acc))", borderLeftWidth: "2px" },
      "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
        backgroundColor: "rgb(var(--acc) / 0.24)",
      },
      ".cm-selectionMatch": { backgroundColor: "rgb(var(--acc) / 0.12)" },
      ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": {
        backgroundColor: "rgb(var(--acc) / 0.18)",
        outline: "none",
      },
      ".cm-foldPlaceholder": {
        backgroundColor: "rgba(255,255,255,0.1)",
        border: "none",
        color: "#A8ADB8",
      },
      "&.cm-focused": { outline: "none" },
      ".cm-panels": {
        backgroundColor: "#1C1C1C",
        color: "#E9EAEE",
        border: "none",
      },
      ".cm-searchMatch": { backgroundColor: "rgb(var(--acc) / 0.22)" },
      ".cm-searchMatch.cm-searchMatch-selected": {
        backgroundColor: "rgb(var(--acc) / 0.42)",
      },
    },
    { dark: true },
  );
}

/**
 * Syntax colours, drawn from the same palette the diff viewer uses so code
 * reads identically in both. `HighlightStyle` and the tag set are passed in
 * rather than imported here — both arrive with the lazily loaded editor, and
 * importing them at module scope would pull CodeMirror into the main bundle
 * through the back door.
 */
function highlightStyle(HighlightStyle: any, t: any) {
  return HighlightStyle.define([
    // Fixed hues throughout — see the note on `TOKEN_CLASS` in CodeBlock.tsx.
    // These five used to resolve through `--acc` / `--acc-2` and so repainted
    // the whole syntax theme on every section switch.
    { tag: [t.keyword, t.moduleKeyword, t.controlKeyword], color: "#B190FF" },
    { tag: [t.string, t.special(t.string)], color: "#5BE0A0" },
    { tag: [t.number, t.bool, t.null, t.atom], color: "#F0B54A" },
    { tag: [t.comment, t.lineComment, t.blockComment], color: "#5F6470", fontStyle: "italic" },
    { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "#8AA9FF" },
    { tag: [t.definition(t.variableName), t.definition(t.propertyName)], color: "#E9EAEE" },
    { tag: [t.typeName, t.className, t.namespace], color: "#84D2E8" },
    { tag: [t.propertyName, t.attributeName], color: "#A8ADB8" },
    { tag: [t.tagName], color: "#8AA9FF" },
    { tag: [t.operator, t.punctuation, t.separator, t.bracket], color: "#7B808C" },
    { tag: [t.heading], color: "#8AA9FF", fontWeight: "600" },
    { tag: [t.link, t.url], color: "#B190FF", textDecoration: "underline" },
    { tag: [t.invalid], color: "#F0736E" },
  ]);
}

// --- languages -------------------------------------------------------------

/**
 * Load the grammar for a path's extension, or none.
 *
 * Only the six that actually come up in generated projects are wired: each is
 * a separate chunk, and a file type nobody edits here is not worth the import.
 * Anything unrecognised still gets the full editor — line numbers, search,
 * undo, save — just without colours, which is a far better outcome than
 * refusing to open it.
 */
async function loadLanguage(path: string) {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  try {
    switch (ext) {
      case "js":
      case "mjs":
      case "cjs":
      case "jsx":
        return (await import("@codemirror/lang-javascript")).javascript({
          jsx: ext === "jsx",
        });
      case "ts":
      case "mts":
      case "cts":
        return (await import("@codemirror/lang-javascript")).javascript({
          typescript: true,
        });
      case "tsx":
        return (await import("@codemirror/lang-javascript")).javascript({
          typescript: true,
          jsx: true,
        });
      case "py":
        return (await import("@codemirror/lang-python")).python();
      case "html":
      case "htm":
      case "vue":
      case "svelte":
        return (await import("@codemirror/lang-html")).html();
      case "css":
      case "scss":
      case "less":
        return (await import("@codemirror/lang-css")).css();
      case "json":
        return (await import("@codemirror/lang-json")).json();
      case "md":
      case "mdx":
      case "markdown":
        return (await import("@codemirror/lang-markdown")).markdown();
      default:
        return null;
    }
  } catch {
    return null;
  }
}
