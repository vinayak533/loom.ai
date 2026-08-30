"use client";

import { motion } from "framer-motion";
import { useMemo, useState } from "react";
import type { AgentSummary, CreditBalance } from "@/lib/agents";
import { formatCredits } from "@/lib/agents";
import { cn } from "@/lib/cn";
import { SPRING_SNAP, useMotionOK } from "../Anim";
import { AgentIcon } from "./AgentIcon";

/**
 * The section's landing view: ten specialists as a grid of cards.
 *
 * Each card has to answer three questions before a click — who is this, what
 * does it do, and can it actually do it right now. The third is the one most
 * galleries skip: an agent whose API key is missing is shown as such *here*,
 * so the user learns it before spending a turn discovering it.
 *
 * Each card wears its own accent rather than the section's. The ten are peers,
 * not variations on one thing, and a grid of ten identically-tinted cards is
 * a list wearing a costume.
 */
export function AgentGallery({
  agents,
  loading,
  error,
  credits,
  onSelect,
  onRetry,
}: {
  agents: AgentSummary[];
  loading: boolean;
  error: string | null;
  credits: CreditBalance | null;
  onSelect: (agentId: string) => void;
  onRetry: () => void;
}) {
  const motionOK = useMotionOK();
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter((a) =>
      [a.name, a.role, a.description, a.tagline, ...a.tools.map((t) => t.name)]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [agents, query]);

  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-[68rem] px-6 py-9 sm:px-8">
        <header className="mb-7">
          <p className="voice-label text-ink-faint">The Agentic Loop</p>
          <h1 className="mt-2 text-display font-semibold text-ink">
            Ten{" "}
            <span className="bg-gradient-to-br from-accent to-accent-alt bg-clip-text text-transparent">
              specialists
            </span>
          </h1>
          <p className="mt-3 max-w-[52ch] font-sans text-[0.9375rem] leading-relaxed text-ink-muted">
            Each one is a separate agent with its own persona and its own tools
            — not one assistant wearing ten labels. Pick the one whose job this
            is.
          </p>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <label className="relative flex-1 sm:max-w-xs">
              <span className="sr-only">Filter the specialists</span>
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
              >
                <circle cx="11" cy="11" r="6.5" />
                <path d="m16 16 4 4" />
              </svg>
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter by name, role or tool…"
                className="h-9 touch:h-11 w-full rounded-ctl border border-line bg-elevated pl-9 pr-3
                           font-sans text-[0.8125rem] text-ink placeholder:text-ink-faint
                           transition-colors duration-200 hover:border-line-strong
                           focus:border-line-focus focus:outline-none"
              />
            </label>

            {credits?.enabled && <CreditPill credits={credits} />}
          </div>
        </header>

        {error && (
          <div className="mb-6 rounded-ctl border border-del/35 bg-del-bg px-4 py-3">
            <p className="font-sans text-[0.8125rem] text-del">
              Could not load the specialists — {error}
            </p>
            <button
              type="button"
              onClick={onRetry}
              className="mt-2 rounded-ctl border border-line px-3 py-1.5 text-2xs text-ink-muted
                         transition-colors duration-200 hover:bg-raised hover:text-ink"
            >
              Try again
            </button>
          </div>
        )}

        {loading && agents.length === 0 && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="h-[170px] animate-pulse rounded-card border border-line bg-surface"
              />
            ))}
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((agent, i) => (
            <motion.button
              key={agent.id}
              type="button"
              onClick={() => onSelect(agent.id)}
              initial={motionOK ? { opacity: 0, y: 10 } : false}
              animate={{ opacity: 1, y: 0 }}
              transition={
                motionOK
                  ? { ...SPRING_SNAP, delay: Math.min(i * 0.028, 0.28) }
                  : { duration: 0 }
              }
              className={cn(
                "group relative flex flex-col gap-2.5 overflow-hidden rounded-card border",
                "border-line bg-surface p-4 text-left transition-all duration-200",
                "hover:border-line-strong hover:bg-elevated active:scale-[0.99]",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-ring",
              )}
              style={{ backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)" }}
            >
              {/* The accent wash. Sits behind the content at very low opacity
                  and lifts slightly on hover — enough for the card to feel
                  like it belongs to this agent, not enough to tint the text. */}
              <span
                aria-hidden
                className="pointer-events-none absolute inset-0 opacity-[0.05] transition-opacity
                           duration-300 group-hover:opacity-[0.11]"
                style={{
                  background: `radial-gradient(120% 90% at 0% 0%, ${agent.accent}, transparent 62%)`,
                }}
              />

              <div className="relative flex items-start gap-3">
                <span
                  className="grid h-9 w-9 touch:h-11 touch:w-11 shrink-0 place-items-center rounded-ctl border"
                  style={{
                    borderColor: `${agent.accent}38`,
                    backgroundColor: `${agent.accent}14`,
                    color: agent.accent,
                  }}
                >
                  <AgentIcon name={agent.icon} size={18} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-sans text-[0.875rem] font-semibold leading-snug text-ink">
                    {agent.name}
                  </p>
                  <p
                    className="voice-label mt-1 truncate"
                    style={{ color: agent.accent }}
                    title={agent.role}
                  >
                    {agent.role}
                  </p>
                </div>
                {!agent.fully_configured && <NotConfiguredChip agent={agent} />}
              </div>

              <p className="relative font-sans text-[0.8125rem] leading-relaxed text-ink-muted">
                {agent.description}
              </p>

              <div className="relative mt-auto flex flex-wrap gap-1 pt-1">
                {agent.tools.map((tool) => (
                  <span
                    key={tool.name}
                    title={
                      tool.configured
                        ? `${tool.summary}${
                            tool.credit_surcharge
                              ? ` · ${tool.credit_surcharge} extra credits per call`
                              : ""
                          }`
                        : `Not configured — ${tool.requires_key} is unset. ${tool.without_it ?? ""}`
                    }
                    className={cn(
                      "rounded-[5px] border px-1.5 py-0.5 font-mono text-[0.5625rem] leading-[1.5]",
                      tool.configured
                        ? "border-line text-ink-faint"
                        : "border-warn/40 text-warn line-through decoration-warn/50",
                    )}
                  >
                    {tool.name}
                  </span>
                ))}
                {agent.reasoning_tools.map((tool) => (
                  <span
                    key={tool.name}
                    title={`Prompt-engineered, not a callable tool. ${tool.note}`}
                    className="rounded-[5px] border border-dashed border-line px-1.5 py-0.5
                               font-mono text-[0.5625rem] leading-[1.5] text-ink-dim"
                  >
                    {tool.name}
                  </span>
                ))}
              </div>
            </motion.button>
          ))}
        </div>

        {!loading && filtered.length === 0 && agents.length > 0 && (
          <p className="py-10 text-center font-sans text-[0.8125rem] text-ink-faint">
            No specialist matches “{query}”.
          </p>
        )}

        {/* Stated once, at the foot of the grid, rather than repeated on every
            card: solid outline is a real function, dashed is a reasoning step
            the persona performs. Making that legible was an explicit ask. */}
        <p className="mt-7 font-sans text-2xs leading-relaxed text-ink-faint">
          <span className="mr-1.5 inline-block rounded-[5px] border border-line px-1.5 py-0.5 font-mono text-[0.5625rem] text-ink-faint">
            solid
          </span>
          is a real callable tool.
          <span className="mx-1.5 inline-block rounded-[5px] border border-dashed border-line px-1.5 py-0.5 font-mono text-[0.5625rem] text-ink-dim">
            dashed
          </span>
          is a capability the agent performs by reasoning — there is no function
          behind it. A struck-through tool needs an API key that is not set.
        </p>
      </div>
    </div>
  );
}

function NotConfiguredChip({ agent }: { agent: AgentSummary }) {
  const missing = agent.tools.filter((t) => !t.configured);
  return (
    <span
      title={missing
        .map((t) => `${t.name} needs ${t.requires_key}. ${t.without_it ?? ""}`)
        .join("\n")}
      className="shrink-0 rounded-[5px] border border-warn/40 bg-[rgba(240,181,74,0.1)]
                 px-1.5 py-0.5 font-sans text-[0.5625rem] font-medium leading-[1.6] text-warn"
    >
      {missing.length} tool{missing.length === 1 ? "" : "s"} off
    </span>
  );
}

function CreditPill({ credits }: { credits: CreditBalance }) {
  const low = credits.balance < credits.minimum_to_start * 20;
  // The meter is charging into memory because the credit store is unreachable.
  // Surfaced rather than hidden: this state used to be invisible, and an
  // 11-minute outage went unnoticed until the ledger was audited afterwards.
  // Nothing is being lost now — the buffered count is what will be written
  // back — but "degraded and recovering" and "healthy" must not look alike.
  const degraded = credits.store?.durable === false && !!credits.store?.degraded_since;
  const buffered = credits.store?.buffered_movements ?? 0;
  const alert = degraded || low;
  return (
    <span
      title={
        degraded
          ? credits.store?.detail ??
            `Credit store unreachable — ${buffered} movement(s) buffered.`
          : `${credits.balance.toFixed(2)} credits · ${credits.spent.toFixed(2)} spent all time`
      }
      className={cn(
        "flex h-9 shrink-0 items-center gap-2 rounded-ctl border px-3",
        alert ? "border-warn/40 bg-[rgba(240,181,74,0.08)]" : "border-line bg-elevated",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "sigil h-[7px] w-[7px]",
          alert ? "bg-warn" : "bg-accent",
          degraded && "animate-pulse",
        )}
      />
      <span
        className={cn(
          "font-mono text-2xs tabular-nums",
          alert ? "text-warn" : "text-ink-muted",
        )}
      >
        {formatCredits(credits.balance)} credits
      </span>
      {degraded && (
        <span className="font-mono text-2xs text-warn">
          · buffering {buffered}
        </span>
      )}
    </span>
  );
}
