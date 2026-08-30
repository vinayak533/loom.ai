"use client";

import { AnimatePresence, motion } from "framer-motion";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BranchGroup } from "@/lib/events";
import type { AgentState, ChatItem } from "@/lib/useAgentSocket";
import {
  addYouTubeSource,
  uploadFileTracked,
  type LearningSource,
  type ModelOption,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { useFeedback } from "@/lib/useFeedback";
import { makeThumbnail } from "@/lib/thumbnail";
import {
  extractVideoId,
  greeting,
  SECTION_META,
  YOUTUBE_RE,
  type Section,
} from "@/lib/sections";
import { BREATH, EASE_BREATH, SPRING, SPRING_SNAP, useMotionOK } from "./Anim";
import { AttachMenu, type AttachMenuHandle, type ImportSummary } from "./AttachMenu";
import { SectionEmptyState } from "./EmptyState";
import { Markdown } from "./Markdown";
import { ToolCallCard } from "./ToolCallCard";
import { ModelSelector } from "./ModelSelector";
import { SourceChips } from "./SourceChips";
import { FileChip, FileChipRow, chipKindFor, type ChipKind, type ChipState } from "./FileChip";
import { TraceRow, TraceTail } from "./TraceSpine";
import {
  AssistantActions,
  FeedbackButtons,
  MessageEditor,
  UserActions,
} from "./MessageActions";
import {
  OutputActions,
  SLIDES_PROMPT,
  SlideDeck,
  STUDY_NOTES_PROMPT,
} from "./LearningOutputs";

/**
 * A file the user has added to the next message.
 *
 * `fileId` only exists once the upload has landed, which is exactly what makes
 * a separate type worth having: the chip has to be able to describe a file
 * that is still on its way, or that never arrived, and `LearningSource` can
 * only describe one that did.
 */
type Attachment = {
  /** Stable for the chip's whole life, including before the server has an id. */
  id: string;
  name: string;
  kind: ChipKind;
  size: number;
  state: ChipState;
  /** The upload id the backend turns into a document/image block. */
  fileId?: string;
  /**
   * A small data-URI preview, when one could be made - see `lib/thumbnail`.
   * Absent for types with no natural visual (CSV, code, archives) and for a
   * PDF with no embedded raster, both of which keep their type icon.
   */
  thumbnail?: string;
};

export function ChatPanel({
  sessionId,
  token,
  section,
  userName,
  state,
  busy,
  onSend,
  onCancel,
  onEditMessage,
  onRegenerate,
  onSwitchBranch,
  models,
  modelChoice,
  autoTargetId,
  taskRouted,
  onSelectModel,
  onImported,
  onOpenArtifact,
}: {
  sessionId: string;
  token?: string | null;
  section: Section;
  /**
   * Reopen an artifact by key. Absent on a surface with no artifacts, which is
   * why the strip below renders nothing rather than a dead row of chips.
   */
  onOpenArtifact?: (key: string) => void;
  userName: string;
  state: AgentState;
  busy: boolean;
  onSend: (text: string, fileIds: string[]) => boolean;
  onCancel: () => void;
  /**
   * Replace user turn `turnIndex` and re-run from there. `turnIndex` is the
   * ordinal among user messages, which is the coordinate the backend also
   * uses — see `AgentState.branches`.
   */
  onEditMessage?: (turnIndex: number, text: string) => void;
  /** Re-run the most recent turn on the currently selected model. */
  onRegenerate?: () => void;
  /** Move an edited turn to another of its versions. */
  onSwitchBranch?: (turnIndex: number, version: number) => void;
  models: ModelOption[];
  /** null = Auto */
  modelChoice: string | null;
  autoTargetId: string;
  /** True when Auto routes per turn by task rather than per section. */
  taskRouted?: boolean;
  onSelectModel: (id: string | null) => void;
  /** Files landed in the sandbox via "+": the tree needs to catch up. */
  onImported?: (summary: ImportSummary) => void;
}) {
  const [draft, setDraft] = useState("");
  const [sources, setSources] = useState<LearningSource[]>([]);
  const [ingesting, setIngesting] = useState(false);
  /**
   * Attachments, from the moment they are picked rather than from the moment
   * they land. A file used to exist only once the POST came back — so a large
   * PDF produced a composer that sat there looking idle, with no evidence
   * anything was happening and no way to change your mind.
   */
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  /** id -> the controller that can stop that upload. */
  const aborters = useRef(new Map<string, AbortController>());
  const [usedOutputs, setUsedOutputs] = useState<Set<"notes" | "slides">>(new Set());
  /** The user turn currently open for editing, by ordinal. Null when none is. */
  const [editingTurn, setEditingTurn] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pdfInput = useRef<HTMLInputElement>(null);
  /** So the empty state can trigger the folder import the menu already owns. */
  const attachMenu = useRef<AttachMenuHandle>(null);
  const motionOK = useMotionOK();

  const meta = SECTION_META[section];
  const empty = state.items.length === 0;

  useEffect(() => {
    const el = scroller.current;
    if (!el || !pinned.current) return;
    el.scrollTop = el.scrollHeight;
  }, [state.items]);

  useEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 24), 200)}px`;
  }, [draft]);

  // Switching sections starts a clean composer — sources belong to a mode.
  useEffect(() => {
    setSources([]);
    setDraft("");
    setAttachments([]);
    setUsedOutputs(new Set());
    setEditingTurn(null);
  }, [section]);

  // --- source ingestion ----------------------------------------------------

  /** A pasted YouTube URL becomes a chip and leaves the textarea. */
  const captureYouTube = async (value: string) => {
    const match = value.match(YOUTUBE_RE);
    if (!match) return value;
    const url = match[0];
    const stripped = value.replace(url, "").replace(/\s{2,}/g, " ").trim();

    setIngesting(true);
    try {
      const src = await addYouTubeSource(url, sessionId, token);
      setSources((prev) =>
        prev.some((s) => s.id === src.id) ? prev : [...prev, src],
      );
    } catch {
      const id = extractVideoId(url) ?? url;
      setSources((prev) => [
        ...prev,
        {
          kind: "youtube",
          id: `yt:${id}`,
          title: `youtu.be/${id}`,
          thumbnail: `https://i.ytimg.com/vi/${id}/mqdefault.jpg`,
          url,
          error: "Could not reach the transcript service.",
        },
      ]);
    } finally {
      setIngesting(false);
    }
    return stripped;
  };

  const onDraftChange = async (value: string) => {
    if (section === "learning" && YOUTUBE_RE.test(value)) {
      setDraft(await captureYouTube(value));
      return;
    }
    setDraft(value);
  };

  /**
   * PDFs, CSVs and images: uploaded now, carried with the next message.
   *
   * The chip goes up *before* the request does, so the file is on screen the
   * instant it is chosen, and the progress it shows is bytes actually sent
   * rather than a spinner standing in for one.
   */
  const ingestFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const picked = Array.from(files);
    setIngesting(true);

    await Promise.all(
      picked.map(async (file) => {
        const id = `att:${crypto.randomUUID()}`;
        const controller = new AbortController();
        aborters.current.set(id, controller);
        setAttachments((prev) => [
          ...prev,
          {
            id,
            name: file.name,
            kind: chipKindFor(file.name, file.type),
            size: file.size,
            state: { phase: "uploading", progress: 0 },
          },
        ]);

        // Rendered alongside the upload rather than before it. A thumbnail is
        // decoration on a chip that already exists; making the chip wait for a
        // canvas would undo the thing the chip is for, which is showing that
        // something is happening the instant a file is chosen.
        void makeThumbnail(file).then((thumbnail) => {
          if (!thumbnail) return;
          setAttachments((prev) =>
            prev.map((a) => (a.id === id ? { ...a, thumbnail } : a)),
          );
        });

        const patch = (state: ChipState, fileId?: string) =>
          setAttachments((prev) =>
            prev.map((a) => (a.id === id ? { ...a, state, ...(fileId ? { fileId } : {}) } : a)),
          );

        try {
          const up = await uploadFileTracked(sessionId, file, token, {
            signal: controller.signal,
            onProgress: (fraction) =>
              setAttachments((prev) =>
                prev.map((a) =>
                  a.id === id && a.state.phase === "uploading"
                    ? { ...a, state: { phase: "uploading", progress: fraction } }
                    : a,
                ),
              ),
          });
          patch({ phase: "ready" }, up.id);
        } catch (err) {
          // A cancel already removed the chip; re-adding an error for it would
          // undo the thing the user just asked for.
          if (err instanceof Error && err.name === "AbortError") return;
          patch({
            phase: "error",
            message: err instanceof Error ? err.message : "Upload failed",
          });
        } finally {
          aborters.current.delete(id);
        }
      }),
    );

    setIngesting(false);
  };

  /** Cancel an upload in flight, or take a finished one off. Same control. */
  const dropAttachment = (id: string) => {
    aborters.current.get(id)?.abort();
    aborters.current.delete(id);
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  };

  // --- send ----------------------------------------------------------------

  /** Nothing may be sent while a file is still on its way to the server. */
  const uploading = attachments.some((a) => a.state.phase === "uploading");
  const readyAttachments = attachments.filter((a) => a.state.phase === "ready");
  const armed =
    !uploading &&
    (draft.trim().length > 0 ||
      sources.length > 0 ||
      readyAttachments.length > 0);

  const dispatch = (text: string, extraSources = sources) => {
    // YouTube transcripts ride inline; files travel as upload ids the backend
    // turns into native document/image blocks.
    const transcripts = extraSources.filter((s) => s.kind === "youtube" && s.text);
    const fileIds = [
      ...extraSources
        .filter((s) => s.kind === "pdf" && s.fileId)
        .map((s) => s.fileId!),
      ...readyAttachments.map((a) => a.fileId!),
    ];

    let payload = text;
    if (transcripts.length) {
      const blocks = transcripts
        .map(
          (s, i) =>
            `<source index="${i + 1}" kind="youtube" title="${s.title}">\n${s.text}\n</source>`,
        )
        .join("\n");
      payload = `<sources>\n${blocks}\n</sources>\n\n${text}`;
    }
    return onSend(payload, fileIds);
  };

  const submit = () => {
    const text = draft.trim();
    if (busy || uploading) return;
    if (!text && sources.length === 0 && readyAttachments.length === 0) return;
    if (dispatch(text || "Summarise the attached file(s).")) {
      setDraft("");
      setSources([]);
      // Anything that failed goes with the message it was meant for; leaving a
      // dead chip behind after a send is how a composer accumulates litter.
      setAttachments([]);
      setUsedOutputs(new Set());
      pinned.current = true;
    }
  };

  // --- message actions -----------------------------------------------------
  //
  // All five are `useCallback`ed because `MemoItem` is memoized on referential
  // equality — the whole transcript re-renders per streamed token otherwise,
  // and each row is a framer-motion `layout` element that then re-measures.

  const startEdit = useCallback((turnIndex: number) => {
    setEditingTurn(turnIndex);
  }, []);

  const cancelEdit = useCallback(() => setEditingTurn(null), []);

  const submitEdit = useCallback(
    (turnIndex: number, text: string) => {
      setEditingTurn(null);
      // The transcript is not touched here. The server answers with
      // `history_replaced` and the refetch that follows redraws it — guessing
      // locally how many turns the rewind removes is how the screen ends up
      // disagreeing with the model.
      onEditMessage?.(turnIndex, text);
      pinned.current = true;
    },
    [onEditMessage],
  );

  const regenerate = useCallback(() => {
    onRegenerate?.();
    pinned.current = true;
  }, [onRegenerate]);

  const switchBranch = useCallback(
    (turnIndex: number, version: number) => {
      setEditingTurn(null);
      onSwitchBranch?.(turnIndex, version);
    },
    [onSwitchBranch],
  );

  const generate = (kind: "notes" | "slides") => {
    if (busy) return;
    if (onSend(kind === "notes" ? STUDY_NOTES_PROMPT : SLIDES_PROMPT, [])) {
      setUsedOutputs((prev) => new Set(prev).add(kind));
      pinned.current = true;
    }
  };

  // --- the trace -----------------------------------------------------------

  /**
   * An assistant bubble is created the moment the turn opens, before any
   * token has landed. It renders nothing, so it must not occupy a node on the
   * spine either — the thread would grow a step that says nothing. Filtering
   * here (rather than returning null from the row) also keeps first/last
   * honest, which is what decides where the thread starts and stops.
   */
  const visible = useMemo(
    () =>
      state.items.filter(
        (i) => !(i.kind === "assistant" && !i.text && !i.thinking),
      ),
    [state.items],
  );

  /**
   * Which user turn each visible row is, and which of them have alternates.
   *
   * The ordinal is counted over user rows only, because that is exactly what
   * the backend counts (`repository.user_turn_positions`): a `tool_result`
   * carrier is a "user" message in the wire format but is not a thing anybody
   * typed, and numbering by array position would put the two sides out of step
   * the moment a turn used a tool.
   *
   * `visible` differs from `state.items` only by dropping empty assistant
   * bubbles, so counting here and counting there give the same answer.
   */
  const turnIndexOf = useMemo(() => {
    const map = new Map<string, number>();
    let n = 0;
    for (const item of visible) {
      if (item.kind === "user") map.set(item.id, n++);
    }
    return map;
  }, [visible]);

  const branchAt = useMemo(() => {
    const map = new Map<number, BranchGroup>();
    for (const group of state.branches) map.set(group.turn_index, group);
    return map;
  }, [state.branches]);

  /**
   * The last assistant row, which is the only one that offers Regenerate. Held
   * as an id rather than an index so the check inside the row stays a cheap
   * equality test and does not re-key on every token.
   */
  const { ratings, rate } = useFeedback(sessionId, token);

  /**
   * Which assistant reply each row is, counting assistant rows from zero.
   *
   * A second ordinal alongside `turnIndexOf`, deliberately not shared with it.
   * Feedback is about a *reply*; branches are about a *question*; and in a
   * conversation where a turn used tools the two counts diverge. Storing a
   * verdict against the wrong one of them would silently mis-attribute it.
   */
  const assistantIndexOf = useMemo(() => {
    const map = new Map<string, number>();
    let n = 0;
    for (const item of visible) {
      if (item.kind === "assistant") map.set(item.id, n++);
    }
    return map;
  }, [visible]);

  const onRate = useCallback(
    (assistantIndex: number, rating: "up" | "down" | null) =>
      rate(assistantIndex, rating, {
        modelId: state.modelId,
        section,
      }),
    [rate, state.modelId, section],
  );

  const lastAssistantId = useMemo(() => {
    for (let i = visible.length - 1; i >= 0; i--) {
      if (visible[i].kind === "assistant") return visible[i].id;
    }
    return null;
  }, [visible]);

  const streamingAssistant = hasOpenAssistant(state.items);
  /** The thread ends in a live head rather than in the last rendered step. */
  const tail = busy && !streamingAssistant;
  const lastIndex = visible.length - 1;

  /** Was the previous turn source-backed? Then offer the two output modes. */
  const offerOutputs = useMemo(
    () => section === "learning" && lastTurnHadSources(state.items),
    [section, state.items],
  );

  // The conversation is the widest, airiest column in the app — that is the
  // asymmetry. Code runs a little wider because its content is monospaced.
  const measure = section === "code" ? "max-w-[54rem]" : "max-w-[48rem]";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        ref={scroller}
        onScroll={() => {
          const el = scroller.current;
          if (!el) return;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
        className={cn("scroll-thin min-h-0 overflow-y-auto", empty ? "flex-none" : "flex-1")}
      >
        <div className={cn("trace-flow mx-auto w-full px-6 py-9 sm:px-8", measure)}>
          {visible.map((item, i) => {
            const turn = turnIndexOf.get(item.id);
            return (
              <MemoItem
                key={item.id}
                item={item}
                section={section}
                first={i === 0}
                last={!tail && i === lastIndex}
                extending={busy && !tail && i === lastIndex}
                busy={busy}
                turnIndex={turn}
                branch={turn === undefined ? undefined : branchAt.get(turn)}
                editing={turn !== undefined && turn === editingTurn}
                isLastAssistant={item.id === lastAssistantId}
                assistantIndex={assistantIndexOf.get(item.id)}
                rating={
                  assistantIndexOf.has(item.id)
                    ? ratings[assistantIndexOf.get(item.id)!] ?? null
                    : null
                }
                onRate={onRate}
                onStartEdit={startEdit}
                onCancelEdit={cancelEdit}
                onSubmitEdit={submitEdit}
                onRegenerate={regenerate}
                onSwitchBranch={switchBranch}
              />
            );
          })}

          <AnimatePresence>
            {tail && (
              <motion.div
                key="tail"
                initial={motionOK ? { opacity: 0 } : false}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={motionOK ? { duration: 0.18 } : { duration: 0 }}
              >
                <TraceTail status={state.status} first={visible.length === 0} />
              </motion.div>
            )}
          </AnimatePresence>

          {offerOutputs && !busy && (
            <OutputActions onGenerate={generate} used={usedOutputs} disabled={busy} />
          )}
        </div>
      </div>

      <div className={cn("shrink-0", empty && "flex flex-1 flex-col justify-center")}>
        {/* No mode eyebrow on the greeting — the header already names the
            section, and saying it twice on one screen is the sort of thing
            that makes an interface feel assembled rather than designed. */}
        {empty && (
          <div className={cn("mx-auto w-full px-6 pb-7 sm:px-8", measure)}>
            <h1 className="text-display font-semibold text-ink">
              {greeting()},{" "}
              <span className="bg-gradient-to-br from-accent to-accent-alt bg-clip-text text-transparent">
                {userName}
              </span>
            </h1>
            <p className="mt-3 max-w-[44ch] font-sans text-[0.9375rem] leading-relaxed text-ink-muted">
              {meta.greetingSub}
            </p>
          </div>
        )}

        {/* The home indicator on a notched phone sits over the last ~34px of
            the screen, which is exactly where the composer's send button was.
            The inset is added to the padding rather than replacing it, so a
            device without one is unaffected. */}
        <div
          className={cn("mx-auto w-full px-6 pb-5 sm:px-8", measure)}
          style={{ paddingBottom: "calc(1.25rem + env(safe-area-inset-bottom))" }}
        >
          <div className="relative">
            <div
              className={cn(
                "glass relative rounded-card transition-colors duration-200",
                "hover:border-line-strong",
                "focus-within:border-line-focus focus-within:shadow-[0_0_0_3px_rgba(255,255,255,0.055),0_24px_60px_-20px_rgba(0,0,0,0.82)]",
              )}
            >
              {/* Sources are a Learn/Code idea — a YouTube transcript pasted
                  into the thread. Attachments are every section's, including
                  Chat's, which is where a file used to upload and then show
                  nothing at all. */}
              {section !== "chat" && sources.length > 0 && (
                <div className="px-3 pt-3">
                  <SourceChips
                    sources={sources}
                    onRemove={(id) =>
                      setSources((prev) => prev.filter((s) => s.id !== id))
                    }
                  />
                </div>
              )}

              {/* Artifacts the model has written this session.
                  Above the composer rather than in the transcript: an artifact
                  outlives the turn that produced it, and burying the way back
                  to it under a scroll of later messages is what makes a canvas
                  feel like it lost your work. */}
              {onOpenArtifact && Object.keys(state.artifacts).length > 0 && (
                <div className="flex flex-wrap gap-1.5 px-3 pt-3">
                  {Object.values(state.artifacts).map((a) => (
                    <button
                      key={a.key}
                      type="button"
                      onClick={() => onOpenArtifact(a.key)}
                      title={`${a.title} · version ${a.version}`}
                      className="flex max-w-full items-center gap-1.5 rounded-full border
                                 border-line bg-inset px-2.5 py-1 text-2xs text-ink-muted
                                 transition-colors duration-150 hover:border-accent-line
                                 hover:text-ink"
                    >
                      <span className="sigil h-1.5 w-1.5 shrink-0 bg-accent" aria-hidden />
                      <span className="truncate">{a.title}</span>
                      {a.version > 1 && (
                        <span className="shrink-0 text-ink-faint">v{a.version}</span>
                      )}
                    </button>
                  ))}
                </div>
              )}

              {attachments.length > 0 && (
                <FileChipRow className="px-3 pt-3">
                  {attachments.map((a) => (
                    <FileChip
                      key={a.id}
                      kind={a.kind}
                      title={a.name}
                      subtitle={formatSize(a.size)}
                      state={a.state}
                      thumbnail={a.thumbnail}
                      onDismiss={() => dropAttachment(a.id)}
                    />
                  ))}
                </FileChipRow>
              )}

              <textarea
                ref={textarea}
                value={draft}
                onChange={(e) => void onDraftChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submit();
                  }
                }}
                rows={1}
                placeholder={
                  state.connected ? meta.placeholder : "Reconnecting to the agent…"
                }
                disabled={!state.connected}
                className={cn(
                  "scroll-thin block w-full resize-none bg-transparent px-4 pt-3.5",
                  // The JS auto-grow sets an inline `height`; `min-height`
                  // outranks it, so this raises the floor on touch without
                  // fighting the resize.
                  "touch:min-h-[44px]",
                  "text-ink placeholder:text-ink-faint focus:outline-none focus-visible:shadow-none disabled:opacity-50",
                  section === "code"
                    ? "font-mono text-[0.8125rem] leading-relaxed"
                    : "font-sans text-[0.9375rem] leading-relaxed",
                )}
              />

              <div className="flex items-center gap-1 px-3 pb-2.5 pt-2">
                {/* Leftmost, ahead of the model control: what you are adding to
                    the session is a more frequent decision than what is going
                    to read it, and every tool with this affordance puts it
                    first. */}
                {(section === "code" || section === "chat") && (
                  <AttachMenu
                    ref={attachMenu}
                    sessionId={sessionId}
                    token={token}
                    disabled={!state.connected}
                    // Chat has no sandbox, so it gets the document attach and
                    // not the two import-into-the-filesystem entries.
                    sandbox={section === "code"}
                    onImported={(summary) => onImported?.(summary)}
                    onAttach={(files) => void ingestFiles(files)}
                  />
                )}

                <ModelSelector
                  models={models}
                  currentId={modelChoice}
                  autoTargetId={autoTargetId}
                  taskRouted={taskRouted}
                  // Which model Auto is on right now is only knowable from
                  // the backend's announcements.
                  activeName={state.modelName}
                  activeId={state.modelId}
                  onSelect={onSelectModel}
                />

                {section === "learning" && (
                  <>
                    <button
                      type="button"
                      onClick={() => pdfInput.current?.click()}
                      disabled={ingesting}
                      aria-label="Upload a PDF source"
                      className="flex h-8 items-center gap-1.5 rounded-ctl px-2.5 text-2xs font-medium
                                 text-ink-faint transition-colors duration-200
                                 hover:bg-raised hover:text-ink disabled:opacity-50"
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M6 3h7l5 5v13H6z" />
                        <path d="M13 3v5h5" />
                        <path d="M12 12v6m0-6-2.2 2.2M12 12l2.2 2.2" />
                      </svg>
                      PDF
                    </button>
                    <input
                      ref={pdfInput}
                      type="file"
                      accept="application/pdf,.pdf"
                      multiple
                      hidden
                      onChange={(e) => {
                        void ingestFiles(e.target.files);
                        e.target.value = "";
                      }}
                    />
                  </>
                )}

                {/* Deliberately not `voice-label`: an uppercase, wide-tracked
                    hint shouts, and this is the least important text on the
                    screen. */}
                {/* `ink-dim` is a non-text token — it measures ~2.2:1 here.
                    Quiet is `ink-faint`, which still clears 4.5:1. */}
                <span className="ml-auto hidden select-none font-sans text-2xs text-ink-faint sm:inline">
                  ⏎ send · ⇧⏎ newline
                </span>

                {busy ? (
                  <button
                    type="button"
                    onClick={onCancel}
                    className="ml-2 rounded-ctl border border-line px-3 py-1.5 text-xs text-ink-muted
                               transition-colors duration-200 hover:bg-raised hover:text-ink"
                  >
                    Stop
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={submit}
                    disabled={!armed || !state.connected}
                    aria-label="Send message"
                    className={cn(
                      "ml-2 grid h-[34px] w-[34px] place-items-center rounded-ctl transition-all duration-200",
                      "touch:h-11 touch:w-11",
                      armed && state.connected
                        ? "bg-gradient-to-br from-accent to-accent-alt text-accent-ink hover:brightness-110 active:scale-[0.92]"
                        : "cursor-not-allowed bg-raised text-ink-dim opacity-55",
                    )}
                  >
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 20V5m0 0-6 6m6-6 6 6" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          </div>

          {empty && (
            <SectionEmptyState
              className="mt-4"
              section={section}
              onUse={(prompt) => {
                setDraft(prompt);
                textarea.current?.focus();
              }}
              // Code only: a first-run entry point that does the thing rather
              // than describing it. The menu behind "+" owns the walk-and-
              // upload pipeline, so this reaches into it rather than growing a
              // second copy that would get truncation and skips subtly wrong.
              onOpenFolder={
                section === "code"
                  ? () => attachMenu.current?.openFolder()
                  : undefined
              }
            />
          )}

          {!state.connected && (
            <p className="mt-3 text-center text-2xs text-warn">
              Not connected to the backend. Check that the FastAPI server is running.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function hasOpenAssistant(items: ChatItem[]): boolean {
  const last = items[items.length - 1];
  return Boolean(last && last.kind === "assistant" && last.streaming);
}

/** The most recent user turn carried a source block or an attachment. */
function lastTurnHadSources(items: ChatItem[]): boolean {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind !== "user") continue;
    return item.files.length > 0 || item.text.includes("<sources>");
  }
  return false;
}

/**
 * Every streamed token produces a new `state.items` array, so without this the
 * whole transcript re-rendered — and, because each item is a framer-motion
 * `layout` element, re-measured — on every token. The reducer only ever
 * replaces the item it is writing to, so referential equality is exactly the
 * right test: unchanged items bail out and the streaming bubble is the only
 * thing React touches. `first`/`last`/`extending` change for at most the final
 * two rows, so the guard still holds for the whole scrollback.
 */
const MemoItem = memo(Item);

type RowProps = {
  item: ChatItem;
  section: Section;
  first: boolean;
  last: boolean;
  extending: boolean;
  /** A turn is in flight; every action that would start another is disabled. */
  busy: boolean;
  /** Ordinal among user turns. Undefined for anything that is not one. */
  turnIndex?: number;
  branch?: BranchGroup;
  editing: boolean;
  isLastAssistant: boolean;
  /** Ordinal among assistant replies. Undefined for anything that is not one. */
  assistantIndex?: number;
  rating: "up" | "down" | null;
  onRate: (assistantIndex: number, rating: "up" | "down" | null) => void;
  onStartEdit: (turnIndex: number) => void;
  onCancelEdit: () => void;
  onSubmitEdit: (turnIndex: number, text: string) => void;
  onRegenerate: () => void;
  onSwitchBranch: (turnIndex: number, version: number) => void;
};

function Item({
  item,
  section,
  first,
  last,
  extending,
  busy,
  turnIndex,
  branch,
  editing,
  isLastAssistant,
  assistantIndex,
  rating,
  onRate,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
  onRegenerate,
  onSwitchBranch,
}: RowProps) {
  switch (item.kind) {
    case "user": {
      // Strip the machine-facing source block from what the user sees.
      const visible = item.text.replace(/<sources>[\s\S]*?<\/sources>\s*/g, "").trim();
      if (editing && turnIndex !== undefined) {
        return (
          <TraceRow kind="user" first={first} last={last} extending={extending} align="end">
            <MessageEditor
              initial={visible}
              onSave={(text) => onSubmitEdit(turnIndex, text)}
              onCancel={onCancelEdit}
            />
          </TraceRow>
        );
      }
      return (
        <TraceRow kind="user" first={first} last={last} extending={extending} align="end">
          {/* `group/msg` is what the action row hovers off. Named rather than
              bare so a nested group — a code block's own copy button, say —
              cannot reveal it by accident. */}
          <div className="group/msg relative flex max-w-[80%] flex-col items-end gap-2">
            {item.files.length > 0 && (
              <span className="chip">
                {item.files.length} attachment{item.files.length === 1 ? "" : "s"}
              </span>
            )}
            {/* 12px of vertical padding rather than 10: at 15px/1.55 the old
                figure left the text optically closer to the top edge than the
                bottom, which is the sort of half-pixel wrongness that reads as
                "default" without ever being noticed. The hairline and the
                raised fill are unchanged — this is padding and a shadow, not a
                new treatment. */}
            {visible && (
              <div
                className="max-w-full rounded-bubble rounded-br-[7px] border border-line
                           bg-raised px-4 py-3 shadow-e1"
              >
                <p className="voice-said whitespace-pre-wrap">{visible}</p>
              </div>
            )}
            {turnIndex !== undefined && (
              <UserActions
                text={visible}
                branch={branch}
                busy={busy}
                onEdit={() => onStartEdit(turnIndex)}
                onSwitch={(version) => onSwitchBranch(turnIndex, version)}
              />
            )}
          </div>
        </TraceRow>
      );
    }

    case "assistant":
      return (
        <TraceRow
          kind="agent"
          first={first}
          last={last}
          extending={extending}
          live={item.streaming}
        >
          <div className="group/msg relative min-w-0">
            <AssistantBody item={item} section={section} />
            {!item.streaming && item.text && (
              <AssistantActions
                text={item.text}
                canRegenerate={isLastAssistant}
                busy={busy}
                onRegenerate={onRegenerate}
                extra={
                  assistantIndex === undefined ? null : (
                    <FeedbackButtons
                      rating={rating}
                      onRate={(next) => onRate(assistantIndex, next)}
                    />
                  )
                }
              />
            )}
          </div>
        </TraceRow>
      );

    case "tool":
      return (
        <TraceRow
          kind="tool"
          tool={item.tool}
          status={item.status}
          first={first}
          last={last}
          extending={extending}
        >
          <ToolCallCard item={item} />
        </TraceRow>
      );

    case "notice":
      // Only warnings and errors reach the thread. Model switches — the
      // auto-router's voice included — are session facts rather than steps,
      // and they surface as a toast that withdraws itself.
      return (
        <TraceRow
          kind="notice"
          level={item.level}
          first={first}
          last={last}
          extending={extending}
        >
          <div
            className={cn(
              "rounded-ctl border px-3.5 py-2.5 font-sans text-[0.8125rem]",
              item.level === "error"
                ? "border-del/35 bg-del-bg text-del"
                : "border-warn/35 bg-[rgba(240,181,74,0.1)] text-warn",
            )}
          >
            {item.text}
          </div>
        </TraceRow>
      );
  }
}

function AssistantBody({
  item,
  section,
}: {
  item: Extract<ChatItem, { kind: "assistant" }>;
  section: Section;
}) {
  const [showThinking, setShowThinking] = useState(false);
  const motionOK = useMotionOK();

  // A deck-shaped reply in Learning renders as a carousel instead of prose.
  const isDeck =
    section === "learning" && !item.streaming && looksLikeDeck(item.text);

  return (
    <div className="min-w-0">
      {item.thinking && (
        <div className="mb-2.5">
          <button
            type="button"
            onClick={() => setShowThinking((s) => !s)}
            aria-expanded={showThinking}
            // The label is 10px tall; the control it sits in must not be. The
            // negative margin keeps the row's visual position unchanged while
            // giving the disclosure a real hit area.
            className="voice-label -my-2 flex min-h-[36px] items-center gap-1.5 transition-colors
                       hover:text-ink-muted touch:min-h-[44px]"
          >
            <motion.span
              animate={{ rotate: showThinking ? 90 : 0 }}
              transition={motionOK ? SPRING_SNAP : { duration: 0 }}
              aria-hidden
            >
              ›
            </motion.span>
            Reasoning
            {item.thinkingActive && motionOK && (
              <motion.span
                className="sigil ml-1 h-1 w-1 bg-accent"
                animate={{ opacity: [0.3, 0.9, 0.3] }}
                transition={{ duration: BREATH, repeat: Infinity, ease: EASE_BREATH }}
                aria-hidden
              />
            )}
          </button>
          <AnimatePresence initial={false}>
            {showThinking && (
              <motion.div
                initial={motionOK ? { height: 0, opacity: 0 } : false}
                animate={{ height: "auto", opacity: 1 }}
                exit={motionOK ? { height: 0, opacity: 0 } : { opacity: 0 }}
                transition={motionOK ? SPRING : { duration: 0 }}
                className="overflow-hidden"
              >
                <p className="voice-reason mt-2.5 whitespace-pre-wrap border-l border-accent-line pl-3.5">
                  {item.thinking}
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {item.text &&
        (isDeck ? (
          <SlideDeck markdown={item.text} />
        ) : (
          <div className="relative">
            <Markdown source={item.text} />
            {item.streaming && (
              <span className="ml-0.5 inline-block h-4 w-[7px] translate-y-[3px] animate-caret bg-accent align-baseline" />
            )}
          </div>
        ))}
    </div>
  );
}

/** Three or more `## Heading` blocks each followed by bullets. */
function looksLikeDeck(text: string): boolean {
  const headings = text.match(/^#{2,3}\s+.+$/gm)?.length ?? 0;
  const bullets = text.match(/^[-*+]\s+.+$/gm)?.length ?? 0;
  return headings >= 3 && bullets >= headings * 2;
}

/** Bytes, for a chip's second line. Two significant figures is plenty here. */
function formatSize(bytes: number): string {
  if (!bytes) return "Attached";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
