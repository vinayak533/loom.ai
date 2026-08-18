"use client";

import { AnimatePresence, motion } from "framer-motion";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { AgentState, ChatItem } from "@/lib/useAgentSocket";
import {
  addYouTubeSource,
  uploadFile,
  type LearningSource,
  type ModelOption,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  extractVideoId,
  greeting,
  SECTION_META,
  YOUTUBE_RE,
  type Section,
} from "@/lib/sections";
import { BREATH, EASE_BREATH, SPRING, SPRING_SNAP, useMotionOK } from "./Anim";
import { AttachMenu, type ImportSummary } from "./AttachMenu";
import { Markdown } from "./Markdown";
import { ToolCallCard } from "./ToolCallCard";
import { ModelSelector } from "./ModelSelector";
import { SourceChips } from "./SourceChips";
import { TraceRow, TraceTail } from "./TraceSpine";
import {
  OutputActions,
  SLIDES_PROMPT,
  SlideDeck,
  STUDY_NOTES_PROMPT,
} from "./LearningOutputs";

export function ChatPanel({
  sessionId,
  token,
  section,
  userName,
  state,
  busy,
  onSend,
  onCancel,
  models,
  modelChoice,
  autoTargetId,
  taskRouted,
  onSelectModel,
  onImported,
}: {
  sessionId: string;
  token?: string | null;
  section: Section;
  userName: string;
  state: AgentState;
  busy: boolean;
  onSend: (text: string, fileIds: string[]) => boolean;
  onCancel: () => void;
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
  const [usedOutputs, setUsedOutputs] = useState<Set<"notes" | "slides">>(new Set());
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pdfInput = useRef<HTMLInputElement>(null);
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
    setUsedOutputs(new Set());
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

  /** PDFs and images: uploaded, then carried with the next message as blocks. */
  const ingestPdfs = async (files: FileList | null) => {
    if (!files?.length) return;
    setIngesting(true);
    for (const file of Array.from(files)) {
      try {
        const up = await uploadFile(sessionId, file, token);
        setSources((prev) => [
          ...prev,
          {
            kind: "pdf",
            id: `pdf:${up.id}`,
            title: up.filename,
            fileId: up.id,
            // An image has no character count to report, and labelling its
            // byte size as one would be a small lie in the chip's subtitle.
            chars: up.file_type === "application/pdf" ? up.size : undefined,
          },
        ]);
      } catch (err) {
        setSources((prev) => [
          ...prev,
          {
            kind: "pdf",
            id: `pdf:err:${file.name}:${Date.now()}`,
            title: file.name,
            error: err instanceof Error ? err.message : "Upload failed",
          },
        ]);
      }
    }
    setIngesting(false);
  };

  // --- send ----------------------------------------------------------------

  const armed = draft.trim().length > 0 || sources.length > 0;

  const dispatch = (text: string, extraSources = sources) => {
    // YouTube transcripts ride inline; PDFs travel as upload ids the backend
    // turns into native document blocks.
    const transcripts = extraSources.filter((s) => s.kind === "youtube" && s.text);
    const fileIds = extraSources
      .filter((s) => s.kind === "pdf" && s.fileId)
      .map((s) => s.fileId!);

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
    if ((!text && sources.length === 0) || busy) return;
    if (dispatch(text || "Summarise the attached source(s).")) {
      setDraft("");
      setSources([]);
      setUsedOutputs(new Set());
      pinned.current = true;
    }
  };

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
          {visible.map((item, i) => (
            <MemoItem
              key={item.id}
              item={item}
              section={section}
              first={i === 0}
              last={!tail && i === lastIndex}
              extending={busy && !tail && i === lastIndex}
            />
          ))}

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

        <div className={cn("mx-auto w-full px-6 pb-5 sm:px-8", measure)}>
          <div className="relative">
            <div
              className={cn(
                "glass relative rounded-card transition-colors duration-200",
                "hover:border-line-strong",
                "focus-within:border-accent-line focus-within:shadow-[0_0_0_3px_rgb(var(--acc)/0.10),0_24px_60px_-20px_rgba(0,0,0,0.82)]",
              )}
            >
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
                  "text-ink placeholder:text-ink-faint focus:outline-none disabled:opacity-50",
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
                    sessionId={sessionId}
                    token={token}
                    disabled={!state.connected}
                    // Chat has no sandbox, so it gets the document attach and
                    // not the two import-into-the-filesystem entries.
                    sandbox={section === "code"}
                    onImported={(summary) => onImported?.(summary)}
                    onAttach={(files) => void ingestPdfs(files)}
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
                        void ingestPdfs(e.target.files);
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
            <div className="mt-4 flex flex-wrap gap-2">
              {meta.suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setDraft(s)}
                  className={cn(
                    "flex h-9 items-center gap-2 border border-line bg-elevated px-3.5 text-ink-muted",
                    "transition-all duration-200 hover:border-accent-line hover:bg-raised hover:text-ink active:scale-[0.98]",
                    section === "code"
                      ? "rounded-ctl font-mono text-2xs"
                      : "rounded-full font-sans text-[0.8125rem]",
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
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
};

function Item({ item, section, first, last, extending }: RowProps) {
  switch (item.kind) {
    case "user": {
      // Strip the machine-facing source block from what the user sees.
      const visible = item.text.replace(/<sources>[\s\S]*?<\/sources>\s*/g, "").trim();
      return (
        <TraceRow kind="user" first={first} last={last} extending={extending} align="end">
          <div className="flex max-w-[80%] flex-col items-end gap-2">
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
          <AssistantBody item={item} section={section} />
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
            className="voice-label flex items-center gap-1.5 transition-colors hover:text-ink-muted"
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
