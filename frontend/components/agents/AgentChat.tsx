"use client";

import { AnimatePresence, motion } from "framer-motion";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { ModelOption } from "@/lib/api";
import { uploadFile } from "@/lib/api";
import type { AgentSummary } from "@/lib/agents";
import { formatCredits } from "@/lib/agents";
import type { AgentState, ChatItem } from "@/lib/useAgentSocket";
import { cn } from "@/lib/cn";
import { SPRING, SPRING_SNAP, useMotionOK } from "../Anim";
import { Markdown } from "../Markdown";
import { ModelSelector } from "../ModelSelector";
import { SourceChips } from "../SourceChips";
import { ToolCallCard } from "../ToolCallCard";
import { TraceRow, TraceTail } from "../TraceSpine";
import { AgentIcon } from "./AgentIcon";
import { AgentOutput } from "./AgentOutputs";
import { ApprovalCard } from "./ApprovalCard";
import { ArtifactCard, artifactOf } from "./ArtifactCard";
import { HandoffCard } from "./HandoffCard";
import type { LearningSource } from "@/lib/api";

/**
 * One specialist's conversation.
 *
 * The same furniture as Chat and Code — trace spine, streaming bubbles, tool
 * cards, model selector — reused rather than rebuilt, so a tool call looks the
 * same wherever it happens. What is added is what is genuinely new here: the
 * agent's identity in the header, its tool roster with configured state, the
 * approval gate, handoff cards, and the credit readout.
 *
 * No file tree, terminal or preview panel: none of the ten needs one. Agent
 * 3's live component preview is inline in its own answer, and Agent 10's
 * sandbox output arrives in its tool card, which is where the Code section
 * puts execution results too.
 */
export function AgentChat({
  agent,
  catalogue,
  sessionId,
  token,
  state,
  busy,
  models,
  modelChoice,
  autoTargetId,
  taskRouted,
  seedPrompt,
  onSeedConsumed,
  onSend,
  onCancel,
  onSelectModel,
  onResolveApproval,
  onHandoff,
  onBack,
}: {
  agent: AgentSummary;
  catalogue: AgentSummary[];
  sessionId: string;
  token?: string | null;
  state: AgentState;
  busy: boolean;
  models: ModelOption[];
  modelChoice: string | null;
  autoTargetId: string;
  taskRouted?: boolean;
  /** Context carried in from a handoff, dropped into the composer once. */
  seedPrompt?: string | null;
  onSeedConsumed?: () => void;
  onSend: (text: string, fileIds: string[]) => boolean;
  onCancel: () => void;
  onSelectModel: (id: string | null) => void;
  onResolveApproval: (
    approvalId: string,
    decision: "approved" | "edited" | "rejected",
    parameters?: Record<string, unknown>,
  ) => void;
  onHandoff: (agentId: string, context: string) => void;
  onBack: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [sources, setSources] = useState<LearningSource[]>([]);
  const [uploading, setUploading] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const motionOK = useMotionOK();

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
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 24), 220)}px`;
  }, [draft]);

  // A handoff lands its context in the composer rather than sending it. The
  // human-in-the-loop rule does not stop at accepting the route — the user
  // still reads and edits what travels before the next agent sees it.
  useEffect(() => {
    if (!seedPrompt) return;
    setDraft(seedPrompt);
    onSeedConsumed?.();
    textarea.current?.focus();
  }, [seedPrompt, onSeedConsumed]);

  // Switching specialists starts a clean composer — a draft written for one
  // agent rarely means the same thing to another.
  useEffect(() => {
    setDraft("");
    setSources([]);
  }, [agent.id]);

  const ingest = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
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
    setUploading(false);
  };

  const armed = draft.trim().length > 0 || sources.length > 0;

  const submit = () => {
    const text = draft.trim();
    if ((!text && !sources.length) || busy) return;
    const fileIds = sources.filter((s) => s.fileId).map((s) => s.fileId!);
    if (onSend(text || "Work with the attached file(s).", fileIds)) {
      setDraft("");
      setSources([]);
      pinned.current = true;
    }
  };

  const visible = useMemo(
    () =>
      state.items.filter((i) => !(i.kind === "assistant" && !i.text && !i.thinking)),
    [state.items],
  );

  const streamingAssistant = hasOpenAssistant(state.items);
  const tail = busy && !streamingAssistant && !state.awaitingApproval;
  const lastIndex = visible.length - 1;

  const missingTools = agent.tools.filter((t) => !t.configured);
  const credits = state.credits;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ------------------------------------------------------- the header */}
      {/* Which agent is speaking is stated permanently, not once at the top of
          the scrollback. Scroll far enough into any conversation and the only
          thing telling you which of ten specialists you are talking to would
          otherwise be the tone of the answers. */}
      <header className="flex shrink-0 items-start gap-3 border-b border-line px-5 py-3 sm:px-7">
        <button
          type="button"
          onClick={onBack}
          aria-label="Back to all specialists"
          className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-ctl text-ink-faint
                     transition-colors duration-200 hover:bg-elevated hover:text-ink"
        >
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 5.5 8.5 12l6.5 6.5" />
          </svg>
        </button>

        <span
          className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-ctl border"
          style={{
            borderColor: `${agent.accent}38`,
            backgroundColor: `${agent.accent}14`,
            color: agent.accent,
          }}
        >
          <AgentIcon name={agent.icon} size={18} />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <h2 className="font-sans text-[0.9375rem] font-semibold leading-tight text-ink">
              {agent.name}
            </h2>
            <span className="voice-label" style={{ color: agent.accent }}>
              {agent.role}
            </span>
          </div>
          <p className="mt-0.5 truncate font-sans text-2xs text-ink-faint">
            {agent.tagline || agent.description}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {credits?.enabled && (
            <span
              title={`${credits.balance.toFixed(2)} credits left · ${credits.spentThisTurn.toFixed(2)} spent this turn`}
              className="hidden items-center gap-1.5 rounded-ctl border border-line bg-elevated
                         px-2.5 py-1 sm:flex"
            >
              <span aria-hidden className="sigil h-[6px] w-[6px] bg-accent" />
              <span className="font-mono text-2xs tabular-nums text-ink-muted">
                {formatCredits(credits.balance)}
              </span>
              {credits.spentThisTurn > 0 && (
                <span className="font-mono text-2xs tabular-nums text-ink-dim">
                  −{formatCredits(credits.spentThisTurn)}
                </span>
              )}
            </span>
          )}

          <button
            type="button"
            onClick={() => setShowTools((s) => !s)}
            aria-expanded={showTools}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded-ctl border px-2.5 font-sans text-2xs",
              "transition-colors duration-200",
              missingTools.length
                ? "border-warn/40 text-warn hover:bg-[rgba(240,181,74,0.1)]"
                : "border-line text-ink-faint hover:bg-elevated hover:text-ink",
            )}
          >
            {agent.tools.length} tool{agent.tools.length === 1 ? "" : "s"}
            {missingTools.length > 0 && ` · ${missingTools.length} off`}
          </button>
        </div>
      </header>

      <AnimatePresence initial={false}>
        {showTools && (
          <motion.div
            initial={motionOK ? { height: 0, opacity: 0 } : false}
            animate={{ height: "auto", opacity: 1 }}
            exit={motionOK ? { height: 0, opacity: 0 } : { opacity: 0 }}
            transition={motionOK ? SPRING : { duration: 0 }}
            className="shrink-0 overflow-hidden border-b border-line bg-inset"
          >
            <div className="space-y-2 px-5 py-3 sm:px-7">
              {agent.tools.map((tool) => (
                <div key={tool.name} className="flex items-start gap-2.5">
                  <span
                    aria-hidden
                    className={cn(
                      "mt-[0.45em] sigil h-1.5 w-1.5 shrink-0",
                      tool.configured ? "bg-add" : "bg-warn",
                    )}
                  />
                  <div className="min-w-0">
                    <p className="voice-machine text-ink">
                      {tool.name}
                      {(tool.credit_surcharge ?? 0) > 0 && (
                        <span className="ml-2 text-warn">
                          +{tool.credit_surcharge} credits per call
                        </span>
                      )}
                    </p>
                    <p className="font-sans text-2xs leading-relaxed text-ink-faint">
                      {tool.configured ? (
                        tool.degraded_without_key && tool.requires_key ? (
                          <>
                            {tool.summary}{" "}
                            <span className="text-warn">
                              {tool.requires_key} is unset — {tool.without_it}
                            </span>
                          </>
                        ) : (
                          tool.summary
                        )
                      ) : (
                        <span className="text-warn">
                          Not configured. {tool.requires_key} is unset in
                          backend/.env. {tool.without_it}
                        </span>
                      )}
                    </p>
                  </div>
                </div>
              ))}

              {agent.reasoning_tools.map((tool) => (
                <div key={tool.name} className="flex items-start gap-2.5">
                  <span
                    aria-hidden
                    className="mt-[0.45em] h-1.5 w-1.5 shrink-0 rotate-45 border border-ink-dim"
                  />
                  <div className="min-w-0">
                    <p className="voice-machine text-ink-muted">
                      {tool.name}
                      <span className="ml-2 text-ink-dim">reasoning, not a function</span>
                    </p>
                    <p className="font-sans text-2xs leading-relaxed text-ink-faint">
                      {tool.note}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ------------------------------------------------------- the thread */}
      <div
        ref={scroller}
        onScroll={() => {
          const el = scroller.current;
          if (!el) return;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
        className={cn("scroll-thin min-h-0 overflow-y-auto", empty ? "flex-none" : "flex-1")}
      >
        <div className="trace-flow mx-auto w-full max-w-[52rem] px-6 py-8 sm:px-8">
          {visible.map((item, i) => (
            <MemoRow
              key={item.id}
              item={item}
              agent={agent}
              catalogue={catalogue}
              first={i === 0}
              last={!tail && i === lastIndex}
              extending={busy && !tail && i === lastIndex}
              onResolveApproval={onResolveApproval}
              onHandoff={onHandoff}
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
        </div>
      </div>

      {/* ----------------------------------------------------- the composer */}
      <div className={cn("shrink-0", empty && "flex flex-1 flex-col justify-center")}>
        {empty && (
          <div className="mx-auto w-full max-w-[52rem] px-6 pb-6 sm:px-8">
            <p className="font-sans text-[0.9375rem] leading-relaxed text-ink-muted">
              {agent.description}
            </p>
            {missingTools.length > 0 && (
              <p className="mt-3 rounded-ctl border border-warn/25 bg-[rgba(240,181,74,0.07)] px-3.5 py-2 font-sans text-2xs leading-relaxed text-warn">
                {missingTools.length === 1
                  ? `${missingTools[0].name} is not configured — ${missingTools[0].requires_key} is unset. ${missingTools[0].without_it}`
                  : `${missingTools.length} of this agent's tools are not configured: ${missingTools.map((t) => t.name).join(", ")}. It will say so rather than faking a result.`}
              </p>
            )}
            {agent.suggestions.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {agent.suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setDraft(s)}
                    className="flex h-9 items-center rounded-ctl border border-line bg-elevated px-3.5
                               font-sans text-[0.8125rem] text-ink-muted transition-all duration-200
                               hover:border-accent-line hover:bg-raised hover:text-ink active:scale-[0.98]"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="mx-auto w-full max-w-[52rem] px-6 pb-5 sm:px-8">
          <div
            className={cn(
              "glass relative rounded-card transition-colors duration-200",
              "hover:border-line-strong",
              "focus-within:border-accent-line focus-within:shadow-[0_0_0_3px_rgb(var(--acc)/0.10),0_24px_60px_-20px_rgba(0,0,0,0.82)]",
            )}
          >
            {sources.length > 0 && (
              <div className="px-3 pt-3">
                <SourceChips
                  sources={sources}
                  onRemove={(id) => setSources((prev) => prev.filter((s) => s.id !== id))}
                />
              </div>
            )}

            <textarea
              ref={textarea}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              rows={1}
              placeholder={
                !state.connected
                  ? "Reconnecting…"
                  : state.awaitingApproval
                    ? "Waiting on your decision above…"
                    : `Message ${agent.name}…`
              }
              disabled={!state.connected}
              className="scroll-thin block w-full resize-none bg-transparent px-4 pt-3.5
                         font-sans text-[0.9375rem] leading-relaxed text-ink
                         placeholder:text-ink-faint focus:outline-none disabled:opacity-50"
            />

            <div className="flex items-center gap-1 px-3 pb-2.5 pt-2">
              {/* Only where an attachment means something. The summarizer reads
                  files; the router and the gatekeeper have nothing to do with
                  one, and an inert button is worse than no button. */}
              {ACCEPTS_FILES.has(agent.id) && (
                <>
                  <button
                    type="button"
                    onClick={() => fileInput.current?.click()}
                    disabled={uploading || !state.connected}
                    aria-label="Attach a PDF or image"
                    className="flex h-8 items-center gap-1.5 rounded-ctl px-2.5 font-sans text-2xs
                               font-medium text-ink-faint transition-colors duration-200
                               hover:bg-raised hover:text-ink disabled:opacity-50"
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M18 9.5 11 16.5a3.5 3.5 0 0 1-5-5l7.5-7.5a2.4 2.4 0 0 1 3.4 3.4L9.5 15" />
                    </svg>
                    {uploading ? "Uploading…" : "Attach"}
                  </button>
                  <input
                    ref={fileInput}
                    type="file"
                    accept="application/pdf,.pdf,image/png,image/jpeg,image/gif,image/webp"
                    multiple
                    hidden
                    onChange={(e) => {
                      void ingest(e.target.files);
                      e.target.value = "";
                    }}
                  />
                </>
              )}

              <ModelSelector
                models={models}
                currentId={modelChoice}
                autoTargetId={autoTargetId}
                taskRouted={taskRouted}
                activeName={state.modelName}
                onSelect={onSelectModel}
              />

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

          {!state.connected && (
            <p className="mt-3 text-center text-2xs text-warn">
              Not connected. Check that the FastAPI server is running.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/** Agents for which an attachment is meaningful input. */
const ACCEPTS_FILES = new Set([
  "document_summarizer",
  "seo_content_creator",
  "research_fact_checker",
  "ui_component_designer",
  "creative_prompt_engineer",
]);

function hasOpenAssistant(items: ChatItem[]): boolean {
  const last = items[items.length - 1];
  return Boolean(last && last.kind === "assistant" && last.streaming);
}

/**
 * Same reasoning as `ChatPanel`'s `MemoItem`: every streamed token produces a
 * new items array, and without this the whole transcript — every one a
 * framer-motion `layout` subtree — re-renders and re-measures per token.
 */
const MemoRow = memo(Row);

function Row({
  item,
  agent,
  catalogue,
  first,
  last,
  extending,
  onResolveApproval,
  onHandoff,
}: {
  item: ChatItem;
  agent: AgentSummary;
  catalogue: AgentSummary[];
  first: boolean;
  last: boolean;
  extending: boolean;
  onResolveApproval: (
    approvalId: string,
    decision: "approved" | "edited" | "rejected",
    parameters?: Record<string, unknown>,
  ) => void;
  onHandoff: (agentId: string, context: string) => void;
}) {
  switch (item.kind) {
    case "user":
      return (
        <TraceRow kind="user" first={first} last={last} extending={extending} align="end">
          <div className="flex max-w-[80%] flex-col items-end gap-2">
            {item.files.length > 0 && (
              <span className="chip">
                {item.files.length} attachment{item.files.length === 1 ? "" : "s"}
              </span>
            )}
            {item.text && (
              <div className="max-w-full rounded-bubble rounded-br-[6px] border border-line bg-raised px-4 py-2.5">
                <p className="voice-said whitespace-pre-wrap">{item.text}</p>
              </div>
            )}
          </div>
        </TraceRow>
      );

    case "assistant":
      return (
        <TraceRow
          kind="agent"
          first={first}
          last={last}
          extending={extending}
          live={item.streaming}
        >
          <AssistantBody item={item} agentId={agent.id} onHandoff={onHandoff} />
        </TraceRow>
      );

    case "tool": {
      const artifact = artifactOf({ artifact: item.artifact });
      return (
        <TraceRow
          kind="tool"
          tool={item.tool}
          status={item.status}
          first={first}
          last={last}
          extending={extending}
        >
          <div className="space-y-2.5">
            {item.notConfigured && (
              // Distinct from a failure: nothing broke, the integration is
              // absent. Saying that plainly is the whole point — the
              // alternative the brief warned against is a silent fake.
              <div className="rounded-ctl border border-warn/30 bg-[rgba(240,181,74,0.08)] px-3.5 py-2">
                <p className="font-sans text-[0.8125rem] leading-relaxed text-warn">
                  <span className="font-semibold">Not configured.</span>{" "}
                  {item.tool} needs an API key that is not set in backend/.env.
                  Nothing was faked — the agent continues without it.
                </p>
              </div>
            )}
            <ToolCallCard item={item} />
            {artifact && <ArtifactCard artifact={artifact} />}
          </div>
        </TraceRow>
      );
    }

    case "approval":
      return (
        <TraceRow
          kind="gate"
          first={first}
          last={last}
          extending={extending}
          live={item.status === "waiting"}
        >
          <ApprovalCard item={item} onResolve={onResolveApproval} />
        </TraceRow>
      );

    case "handoff":
      return (
        <TraceRow kind="gate" first={first} last={last} extending={extending}>
          <HandoffCard
            item={item}
            target={catalogue.find((a) => a.id === item.nextAgent)}
            onAccept={onHandoff}
          />
        </TraceRow>
      );

    case "notice":
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
  agentId,
  onHandoff,
}: {
  item: Extract<ChatItem, { kind: "assistant" }>;
  agentId: string;
  onHandoff: (agentId: string, context: string) => void;
}) {
  const [showThinking, setShowThinking] = useState(false);
  const motionOK = useMotionOK();

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
                transition={{ duration: 2.1, repeat: Infinity, ease: "easeInOut" }}
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

      {item.text && (
        <div className="relative">
          <AgentOutput
            agentId={agentId}
            text={item.text}
            streaming={item.streaming}
            onSendToAgent={onHandoff}
          />
          {item.streaming && (
            <span className="ml-0.5 inline-block h-4 w-[7px] translate-y-[3px] animate-caret bg-accent align-baseline" />
          )}
        </div>
      )}
    </div>
  );
}

// Referenced so a future reader does not mistake the import for dead weight:
// prose that does not match an agent's output contract falls back to this.
export { Markdown };
