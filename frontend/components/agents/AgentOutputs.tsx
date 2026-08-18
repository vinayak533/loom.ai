"use client";

import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { Markdown } from "../Markdown";
import { CodeBlock } from "../CodeBlock";
import { ComponentPreview } from "./ComponentPreview";

/**
 * Agent-shaped rendering of an assistant turn.
 *
 * Several agents have an output *contract* — Agent 2 emits JSON, Agent 3 emits
 * a component, Agent 4 emits a prompt string — and honouring those contracts in
 * the UI is what makes them worth having. A JSON blob dumped as prose is the
 * failure this file exists to prevent.
 *
 * The rule for all of them: parse opportunistically, fall back to Markdown
 * without complaint. A model that does not follow its contract on some turn
 * should degrade to ordinary prose, never to an error or an empty bubble.
 */
export function AgentOutput({
  agentId,
  text,
  streaming,
  onSendToAgent,
}: {
  agentId: string;
  text: string;
  streaming: boolean;
  /** Offer the Agent 4 → Agent 5 handoff from a finished prompt. */
  onSendToAgent?: (agentId: string, context: string) => void;
}) {
  // While tokens are still landing, a half-written JSON block is not JSON and
  // a half-written component is not renderable. Prose until the turn closes.
  if (streaming) return <Markdown source={text} />;

  if (agentId === "email_copywriter") {
    const draft = parseEmail(text);
    if (draft) return <EmailDraft draft={draft} prose={stripFences(text)} />;
  }

  if (agentId === "ui_component_designer") {
    const block = firstFence(text, ["html", "jsx", "tsx", "svg"]);
    if (block) return <ComponentOutput text={text} block={block} />;
  }

  if (agentId === "creative_prompt_engineer") {
    const blocks = allFences(text);
    if (blocks.length) {
      return (
        <PromptOutput text={text} blocks={blocks} onSendToAgent={onSendToAgent} />
      );
    }
  }

  if (agentId === "code_refactoring_assistant") {
    const block = firstFence(text);
    if (block) return <CodeOutput text={text} block={block} />;
  }

  return <Markdown source={text} />;
}

// ---------------------------------------------------------------- agent 2

type EmailDraft = {
  audience?: string;
  subject_line?: string;
  subject_variant_b?: string;
  variant_rationale?: string;
  email_body?: string;
  call_to_action?: string;
  spam_flags?: string[];
};

/**
 * Agent 2's structured draft, as a card with per-field copy.
 *
 * Per-field rather than one "copy all" button because that is how the output
 * is actually used: the subject goes in one box and the body in another, and
 * copying a JSON envelope into a mail client helps nobody.
 */
function EmailDraft({ draft, prose }: { draft: EmailDraft; prose: string }) {
  return (
    <div className="space-y-3">
      {prose && <Markdown source={prose} />}

      <div className="overflow-hidden rounded-card border border-line bg-surface">
        <div className="flex items-center gap-2 border-b border-line px-3.5 py-2">
          <span className="voice-label">draft</span>
          {draft.audience && (
            <span className="chip border-accent-line text-accent">
              {draft.audience.replace(/_/g, " ")}
            </span>
          )}
        </div>

        <div className="divide-y divide-line">
          <Field label="Subject" value={draft.subject_line} />
          {draft.subject_variant_b && (
            <Field
              label="Variant B"
              value={draft.subject_variant_b}
              note={draft.variant_rationale}
            />
          )}
          <Field label="Body" value={draft.email_body} multiline />
          <Field label="Call to action" value={draft.call_to_action} />
        </div>

        {draft.spam_flags && draft.spam_flags.length > 0 && (
          <div className="border-t border-warn/25 bg-[rgba(240,181,74,0.07)] px-3.5 py-2">
            <p className="font-sans text-2xs text-warn">
              Kept despite the spam checker flagging it:{" "}
              {draft.spam_flags.join(", ")}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  note,
  multiline,
}: {
  label: string;
  value?: string;
  note?: string;
  multiline?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Denied clipboard permission. The text is selectable on screen.
    }
  };

  return (
    <div className="group px-3.5 py-2.5">
      <div className="flex items-center gap-2">
        <span className="voice-label">{label}</span>
        <button
          type="button"
          onClick={copy}
          className={cn(
            "ml-auto rounded-[6px] border border-line px-2 py-0.5 font-sans text-[0.5625rem]",
            "text-ink-faint opacity-0 transition-all duration-200",
            "group-hover:opacity-100 focus-visible:opacity-100 hover:border-line-strong hover:text-ink",
            copied && "border-add/40 text-add opacity-100",
          )}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <p
        className={cn(
          "mt-1 font-sans text-[0.875rem] leading-relaxed text-ink",
          multiline && "whitespace-pre-wrap",
        )}
      >
        {value}
      </p>
      {note && <p className="mt-1 font-sans text-2xs text-ink-faint">{note}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- agent 3

function ComponentOutput({
  text,
  block,
}: {
  text: string;
  block: { lang: string; body: string };
}) {
  const before = text.slice(0, text.indexOf("```")).trim();
  const after = text.slice(text.lastIndexOf("```") + 3).trim();

  return (
    <div className="space-y-3">
      {before && <Markdown source={before} />}
      <ComponentPreview markup={block.body} language={block.lang} />
      {after && <Markdown source={after} />}
    </div>
  );
}

// ---------------------------------------------------------------- agent 4

function PromptOutput({
  text,
  blocks,
  onSendToAgent,
}: {
  text: string;
  blocks: Array<{ lang: string; body: string }>;
  onSendToAgent?: (agentId: string, context: string) => void;
}) {
  const before = text.slice(0, text.indexOf("```")).trim();
  const after = text.slice(text.lastIndexOf("```") + 3).trim();
  const prompt = blocks[0]?.body ?? "";
  const negative = blocks[1]?.body ?? "";

  return (
    <div className="space-y-3">
      {before && <Markdown source={before} />}

      <CodeBlock code={prompt} language="text" maxHeight="14rem" />
      {negative && (
        <div>
          <p className="voice-label mb-1.5">negative prompt</p>
          <CodeBlock code={negative} language="text" maxHeight="10rem" />
        </div>
      )}

      {/* The natural next step. Agent 4 writes prompts and Agent 5 renders
          them, so the chain is offered where it is obvious — still as a click,
          never automatically. */}
      {onSendToAgent && prompt && (
        <button
          type="button"
          onClick={() =>
            onSendToAgent(
              "graphic_poster_creator",
              negative
                ? `Render this prompt.\n\nPrompt:\n${prompt}\n\nNegative prompt:\n${negative}`
                : `Render this prompt.\n\nPrompt:\n${prompt}`,
            )
          }
          className="h-8 rounded-ctl border border-accent-line bg-accent/[0.08] px-3.5
                     font-sans text-2xs font-medium text-accent transition-colors
                     duration-200 hover:bg-accent/[0.14]"
        >
          Send to the Graphic Creator →
        </button>
      )}

      {after && <Markdown source={after} />}
    </div>
  );
}

// --------------------------------------------------------------- agent 10

function CodeOutput({
  text,
  block,
}: {
  text: string;
  block: { lang: string; body: string };
}) {
  const before = text.slice(0, text.indexOf("```")).trim();
  const after = text.slice(text.lastIndexOf("```") + 3).trim();
  return (
    <div className="space-y-3">
      {before && <Markdown source={before} />}
      <CodeBlock code={block.body} language={block.lang || "python"} />
      {after && <Markdown source={after} />}
    </div>
  );
}

// ------------------------------------------------------------------ utils

const FENCE_RE = /```(\w*)\n([\s\S]*?)```/g;

function allFences(text: string): Array<{ lang: string; body: string }> {
  const out: Array<{ lang: string; body: string }> = [];
  for (const match of text.matchAll(FENCE_RE)) {
    out.push({ lang: match[1] ?? "", body: match[2].trimEnd() });
  }
  return out;
}

function firstFence(
  text: string,
  langs?: string[],
): { lang: string; body: string } | null {
  const fences = allFences(text);
  if (!fences.length) return null;
  if (!langs) return fences[0];
  return fences.find((f) => langs.includes(f.lang.toLowerCase())) ?? null;
}

function stripFences(text: string): string {
  return text.replace(FENCE_RE, "").trim();
}

/**
 * Pull Agent 2's draft out of the reply.
 *
 * A fenced ```json block is the contract, but a model that emits a bare object
 * has still done the work, so a raw-object fallback is worth the six lines.
 * Anything that does not parse into a recognisable draft returns null and the
 * turn renders as ordinary prose.
 */
function parseEmail(text: string): EmailDraft | null {
  const candidates: string[] = [];
  const fenced = firstFence(text, ["json"]);
  if (fenced) candidates.push(fenced.body);
  const bare = text.match(/\{[\s\S]*\}/);
  if (bare) candidates.push(bare[0]);

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate) as EmailDraft;
      if (parsed && (parsed.subject_line || parsed.email_body)) return parsed;
    } catch {
      // Try the next candidate.
    }
  }
  return null;
}

/** Agent 7's citations, rendered from the tool result rather than the prose. */
export function CitationList({
  sources,
}: {
  sources: Array<{ url: string; domain: string; tier: string; reason: string }>;
}) {
  const grouped = useMemo(() => {
    const order = ["high", "medium", "low", "unknown"];
    return order
      .map((tier) => ({ tier, items: sources.filter((s) => s.tier === tier) }))
      .filter((g) => g.items.length);
  }, [sources]);

  if (!sources.length) return null;

  return (
    <div className="space-y-2.5">
      {grouped.map((group) => (
        <div key={group.tier}>
          <p className={cn("voice-label mb-1.5", TIER_TONE[group.tier])}>
            {group.tier} trust · {group.items.length}
          </p>
          <ul className="space-y-1.5">
            {group.items.map((source) => (
              <li key={source.url} className="flex gap-2">
                <span
                  aria-hidden
                  className={cn("mt-[0.5em] sigil h-1.5 w-1.5 shrink-0", TIER_MARK[group.tier])}
                />
                <div className="min-w-0">
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="break-all font-sans text-[0.8125rem] text-accent hover:text-accent-hover"
                  >
                    {source.domain || source.url}
                  </a>
                  <p className="font-sans text-2xs leading-relaxed text-ink-faint">
                    {source.reason}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <p className="font-sans text-2xs leading-relaxed text-ink-dim">
        Tiers come from a domain heuristic that knows nothing about the page
        itself. A high-trust domain still publishes wrong things.
      </p>
    </div>
  );
}

const TIER_TONE: Record<string, string> = {
  high: "text-add",
  medium: "text-ink-faint",
  low: "text-warn",
  unknown: "text-ink-dim",
};

const TIER_MARK: Record<string, string> = {
  high: "bg-add",
  medium: "bg-ink-faint",
  low: "bg-warn",
  unknown: "bg-ink-dim",
};
