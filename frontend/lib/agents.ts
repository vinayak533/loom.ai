/**
 * The Agentic Loop's client surface.
 *
 * The ten agents' metadata is fetched rather than duplicated here: the backend
 * registry is the single source of truth for personas, tools and their
 * configured state, and a second copy in the bundle would be a second thing to
 * keep in step. What lives locally is only what the browser genuinely owns —
 * the socket URL, the localStorage keys, and the icon lookup.
 */

import type { AgentToolMeta } from "./events";
import { HTTP_BASE } from "./api";
import type { SessionRow } from "./api";

const WS_BASE = process.env.NEXT_PUBLIC_BACKEND_WS_URL ?? "ws://localhost:8000";

/** Which agent's session is open, and the last agent used, across reloads. */
export const AGENT_SESSION_KEY = "coding-agent:agent-session";
export const AGENT_ACTIVE_KEY = "coding-agent:agent-active";

export type AgentSummary = {
  id: string;
  name: string;
  role: string;
  description: string;
  tagline: string;
  /** A Lucide icon name, validated server-side against the installed build. */
  icon: string;
  accent: string;
  suggestions: string[];
  tools: AgentToolMeta[];
  reasoning_tools: Array<{ name: string; note: string }>;
  /**
   * Hard ceiling on tool calls per user message, or 0 when uncapped. Set on
   * agents whose tools bill per call; hitting it emits `tool_budget_reached`.
   */
  max_tool_calls_per_turn?: number;
  /** False when at least one of this agent's tools is missing its API key. */
  fully_configured: boolean;
};

export type AgentDetail = AgentSummary & { system_prompt: string };

/**
 * The gallery's grouping: what kind of work each specialist is for.
 *
 * Ten equal cards gave a new user no basis for choosing between them; the
 * gallery's own empty-state copy ("pick the one whose job this is") assumed
 * a knowledge the cards did not supply. Grouped by the work rather than by
 * the persona, the choice becomes "what am I trying to do", which is the
 * question the user actually arrived with. Order is the order shown. An
 * agent the backend adds without an entry here lands under "Other".
 */
export const AGENT_GROUPS: Array<{ label: string; ids: string[] }> = [
  { label: "Writing", ids: ["document_summarizer", "email_copywriter", "seo_content_creator"] },
  { label: "Design", ids: ["ui_component_designer", "graphic_poster_creator", "creative_prompt_engineer"] },
  { label: "Research", ids: ["research_fact_checker"] },
  { label: "Engineering", ids: ["code_refactoring_assistant"] },
  { label: "Orchestration", ids: ["system_logic_router", "human_approval_gatekeeper"] },
];

export function groupAgents(agents: AgentSummary[]): Array<{ label: string; agents: AgentSummary[] }> {
  const byId = new Map(agents.map((a) => [a.id, a]));
  const seen = new Set<string>();
  const groups = AGENT_GROUPS.map((g) => ({
    label: g.label,
    agents: g.ids.flatMap((id) => {
      const a = byId.get(id);
      if (!a) return [];
      seen.add(id);
      return [a];
    }),
  })).filter((g) => g.agents.length > 0);
  const rest = agents.filter((a) => !seen.has(a.id));
  if (rest.length) groups.push({ label: "Other", agents: rest });
  return groups;
}

export type CreditBalance = {
  user_id: string;
  balance: number;
  granted: number;
  spent: number;
  enabled: boolean;
  credit_usd: number;
  minimum_to_start: number;
  /**
   * Where balances are actually being kept right now. `durable: false` with a
   * `degraded_since` means the credit store is unreachable: charging continues
   * in memory, every movement is buffered to disk, and the backlog is flushed
   * when it recovers. Nothing is lost — but the state is worth showing, since
   * the last time it happened silently it cost ~910 unrecorded credits.
   */
  store?: {
    durable: boolean;
    reason?: string;
    degraded_since?: string;
    buffered_movements?: number;
    buffered_credits?: number;
    retry_in_seconds?: number;
    detail?: string;
  };
};

export type LedgerEntry = {
  id: string;
  kind: "grant" | "llm" | "tool" | "refund";
  amount: number;
  reason: string | null;
  agent_id: string | null;
  model_id: string | null;
  created_at: string;
};

function authHeaders(token?: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${detail ? ` — ${detail}` : ""}`);
  }
  return (await res.json()) as T;
}

/**
 * The socket for one specialist conversation.
 *
 * `agent` seeds a *new* session only. The backend reads the agent off the
 * session row when there is one, so a reconnect cannot repoint an existing
 * transcript at a different persona.
 */
export function agentSocketUrl(
  sessionId: string,
  agentId: string,
  _token?: string | null,
): string {
  const base = WS_BASE.replace(/\/$/, "");
  const params = new URLSearchParams({ agent: agentId });
  // The token travels as a subprotocol; see `socketProtocols` in api.ts.
  return `${base}/ws/agent/${encodeURIComponent(sessionId)}?${params.toString()}`;
}

export async function fetchAgents(): Promise<AgentSummary[]> {
  const data = await json<{ agents: AgentSummary[] }>(
    await fetch(`${HTTP_BASE}/api/agents`, { cache: "no-store" }),
  );
  return data.agents;
}

export async function fetchAgentDetail(agentId: string): Promise<AgentDetail> {
  return json(
    await fetch(`${HTTP_BASE}/api/agents/${agentId}`, { cache: "no-store" }),
  );
}

export async function createAgentSession(
  agentId: string,
  token?: string | null,
  modelId?: string | null,
): Promise<SessionRow> {
  const search = modelId ? `?model_id=${encodeURIComponent(modelId)}` : "";
  return json(
    await fetch(`${HTTP_BASE}/api/agents/${agentId}/sessions${search}`, {
      method: "POST",
      headers: authHeaders(token),
    }),
  );
}

export async function listAgentSessions(
  agentId: string,
  token?: string | null,
  archived = false,
): Promise<SessionRow[]> {
  const search = archived ? "?archived=true" : "";
  return json(
    await fetch(`${HTTP_BASE}/api/agents/${agentId}/sessions${search}`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function fetchAgentHistory(sessionId: string, token?: string | null) {
  return json<{
    checkpoint: {
      messages: Array<{ role: string; content: unknown }>;
      iterations: number;
      usage: { input_tokens?: number; output_tokens?: number; cost_estimate?: number };
    };
    log: Array<{
      id: string;
      role: string;
      content: string | null;
      tool_calls: Record<string, unknown> | null;
      created_at: string;
    }>;
    agent_id: string | null;
    sandbox_id: string | null;
  }>(
    await fetch(`${HTTP_BASE}/api/agents/sessions/${sessionId}/messages`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function fetchCredits(token?: string | null): Promise<CreditBalance> {
  return json(
    await fetch(`${HTTP_BASE}/api/credits`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
}

export async function fetchLedger(
  token?: string | null,
  limit = 25,
): Promise<LedgerEntry[]> {
  const data = await json<{ entries: LedgerEntry[] }>(
    await fetch(`${HTTP_BASE}/api/credits/ledger?limit=${limit}`, {
      headers: authHeaders(token),
      cache: "no-store",
    }),
  );
  return data.entries;
}

/**
 * Answer a paused run over HTTP.
 *
 * The websocket is the normal path. This is the fallback for a decision made
 * after the socket has reconnected, which would otherwise be sent down a
 * connection the awaiting run no longer knows about.
 */
export async function resolveApproval(
  approvalId: string,
  decision: "approved" | "edited" | "rejected",
  parameters?: Record<string, unknown>,
  token?: string | null,
): Promise<void> {
  await json(
    await fetch(`${HTTP_BASE}/api/approvals/${approvalId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(token) },
      body: JSON.stringify({ decision, parameters }),
    }),
  );
}

/** How a credit amount reads in the UI. Whole numbers below 1000, else k. */
export function formatCredits(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 10_000) return `${(value / 1000).toFixed(1)}k`;
  if (abs >= 10) return Math.round(value).toLocaleString();
  // Under ten, the decimals are the whole story — a turn that cost 0.4 credits
  // rounding to "0" would read as free.
  return value.toFixed(abs >= 1 ? 1 : 2);
}
