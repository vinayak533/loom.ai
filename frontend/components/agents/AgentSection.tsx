"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BackendConfig, ModelOption, SessionRow } from "@/lib/api";
import {
  AUTO_MODEL_ID,
  DEFAULT_MODEL_ID,
  deleteSession,
  updateSession,
} from "@/lib/api";
import type { AgentSummary, CreditBalance } from "@/lib/agents";
import {
  clearLive,
  forgetPersistedLive,
  readLive,
  writeLive,
} from "@/lib/liveSession";
import {
  AGENT_ACTIVE_KEY,
  AGENT_SESSION_KEY,
  createAgentSession,
  fetchAgents,
  fetchCredits,
  listAgentSessions,
} from "@/lib/agents";
import { useAgentSocket } from "@/lib/useAgentSocket";
import { FALLBACK_ROUTES } from "@/lib/sections";
import { AgentChat } from "./AgentChat";
import { AgentGallery } from "./AgentGallery";
import { AgentSessionShelf } from "./AgentSessionShelf";

/**
 * The Agentic Loop section.
 *
 * Two views: the gallery of ten, and one specialist's conversation. It owns
 * the state that spans them — which agent is open, which session, the catalogue
 * and the credit balance — and hands each view exactly what it needs.
 *
 * Sessions are keyed per agent. A conversation belongs to the specialist it
 * was started with (the backend pins `agent_id` onto the row and refuses to
 * repoint it), so "resume where I left off" has to remember one session id per
 * agent rather than one for the section.
 */
export function AgentSection({
  token,
  config,
  models,
  jumpTo = null,
  onJumpConsumed,
}: {
  token?: string | null;
  config: BackendConfig | null;
  models: ModelOption[];
  /**
   * A specific conversation to open, named from outside — the page's
   * cross-surface search, which can turn up an Agents session while the user
   * is somewhere else entirely. Consumed once and then cleared, the same
   * contract a handoff's seed prompt has: it is an instruction that has been
   * carried out, not a state this section should keep mirroring.
   */
  jumpTo?: { agentId: string; sessionId: string } | null;
  onJumpConsumed?: () => void;
}) {
  const [catalogue, setCatalogue] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [credits, setCredits] = useState<CreditBalance | null>(null);

  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  /** agent id -> its open session id. Persisted, so a reload resumes. */
  const [sessions, setSessions] = useState<Record<string, string>>({});
  const [shelf, setShelf] = useState<SessionRow[]>([]);
  const [shelfOpen, setShelfOpen] = useState(false);
  /** Context carried in from a handoff, handed to the composer once. */
  const [seed, setSeed] = useState<string | null>(null);
  /** null = Auto. Kept per agent: a model that suits the router suits nothing
   *  else, and one section-wide pick would be wrong most of the time. */
  const [modelChoice, setModelChoice] = useState<Record<string, string | null>>({});

  const sessionId = activeAgent ? sessions[activeAgent] ?? null : null;

  const {
    state,
    busy,
    send,
    cancel,
    editMessage,
    regenerate,
    switchBranch,
    setModel,
    resolveApproval,
  } = useAgentSocket(sessionId, token, activeAgent);

  // --- bootstrap -----------------------------------------------------------

  const loadCatalogue = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchAgents()
      .then((agents) => {
        setCatalogue(agents);
        setError(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "the backend did not answer"),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(loadCatalogue, [loadCatalogue]);

  const refreshCredits = useCallback(() => {
    fetchCredits(token)
      .then(setCredits)
      .catch(() => setCredits(null));
  }, [token]);

  useEffect(refreshCredits, [refreshCredits]);

  // The socket reports the balance as it changes during a turn; this keeps the
  // gallery's pill honest without polling, by folding the live number into the
  // fetched record whenever a run spends something.
  const liveBalance = state.credits?.balance;
  useEffect(() => {
    if (typeof liveBalance !== "number") return;
    setCredits((c) => (c && c.balance !== liveBalance ? { ...c, balance: liveBalance } : c));
  }, [liveBalance]);

  // Per browsing session, not forever — the same rule Chat and Code follow.
  // A reload puts you back with the specialist you were talking to; opening
  // the app afresh puts you back in the gallery, with every past conversation
  // still one click away in the shelf. See `lib/liveSession`.
  useEffect(() => {
    forgetPersistedLive(AGENT_SESSION_KEY, AGENT_ACTIVE_KEY);
    try {
      const stored = readLive(AGENT_SESSION_KEY);
      if (stored) setSessions(JSON.parse(stored));
      const active = readLive(AGENT_ACTIVE_KEY);
      if (active) setActiveAgent(active);
    } catch {
      // A corrupt value is not worth failing the section over; the gallery is
      // a perfectly good place to land.
    }
  }, []);

  useEffect(() => {
    if (Object.keys(sessions).length) {
      writeLive(AGENT_SESSION_KEY, JSON.stringify(sessions));
    }
  }, [sessions]);

  // --- session management --------------------------------------------------

  const openAgent = useCallback(
    async (agentId: string, seedContext?: string) => {
      setActiveAgent(agentId);
      writeLive(AGENT_ACTIVE_KEY, agentId);
      setShelfOpen(false);
      if (seedContext) setSeed(seedContext);

      // Reuse this agent's existing session if it has one; otherwise start a
      // fresh row so the backend can pin `agent_id` onto it before the socket
      // opens. A locally-minted uuid is the offline fallback — the socket's
      // `agent` query parameter is enough to bootstrap it.
      if (sessions[agentId]) return;
      try {
        const row = await createAgentSession(agentId, token, DEFAULT_MODEL_ID);
        setSessions((s) => ({ ...s, [agentId]: row.id }));
      } catch {
        setSessions((s) => ({ ...s, [agentId]: crypto.randomUUID() }));
      }
    },
    [sessions, token],
  );

  const newSession = useCallback(async () => {
    if (!activeAgent) return;
    try {
      const row = await createAgentSession(activeAgent, token, DEFAULT_MODEL_ID);
      setSessions((s) => ({ ...s, [activeAgent]: row.id }));
    } catch {
      setSessions((s) => ({ ...s, [activeAgent]: crypto.randomUUID() }));
    }
    setShelfOpen(false);
  }, [activeAgent, token]);

  // Open what the page asked for. Written straight into this agent's slot
  // rather than through `openAgent`, which would reuse whatever session that
  // specialist already had open and quietly ignore the one actually chosen.
  useEffect(() => {
    if (!jumpTo) return;
    setActiveAgent(jumpTo.agentId);
    writeLive(AGENT_ACTIVE_KEY, jumpTo.agentId);
    setSessions((s) => ({ ...s, [jumpTo.agentId]: jumpTo.sessionId }));
    setShelfOpen(false);
    onJumpConsumed?.();
  }, [jumpTo, onJumpConsumed]);

  const refreshShelf = useCallback(() => {
    if (!activeAgent) {
      setShelf([]);
      return;
    }
    listAgentSessions(activeAgent, token)
      .then(setShelf)
      .catch(() => setShelf([]));
  }, [activeAgent, token]);

  useEffect(refreshShelf, [refreshShelf, sessionId]);

  const sessionAction = useCallback(
    async (id: string, action: "rename" | "pin" | "archive" | "delete") => {
      if (action === "rename") return; // renaming lives in the shared dialog
      if (action === "delete") {
        await deleteSession(id, token).catch(() => undefined);
        setShelf((s) => s.filter((r) => r.id !== id));
        if (id === sessionId) await newSession();
        return;
      }
      const row = shelf.find((s) => s.id === id);
      if (!row) return;
      const patch =
        action === "pin"
          ? { is_pinned: !row.is_pinned }
          : { is_archived: !row.is_archived };
      setShelf((current) =>
        action === "archive"
          ? current.filter((s) => s.id !== id)
          : current.map((s) => (s.id === id ? { ...s, ...patch } : s)),
      );
      await updateSession(id, patch, token).catch(() => undefined);
      refreshShelf();
    },
    [shelf, sessionId, token, newSession, refreshShelf],
  );

  // --- model routing -------------------------------------------------------

  // An agent that has never had a model picked for it runs on the default
  // model, not on Auto. `modelChoice` only ever holds an *explicit* choice, so
  // a stored null still means the user asked for Auto and is honoured as such.
  const selection = activeAgent
    ? activeAgent in modelChoice
      ? modelChoice[activeAgent] ?? AUTO_MODEL_ID
      : DEFAULT_MODEL_ID
    : DEFAULT_MODEL_ID;
  const autoTargetId = config?.auto_routes?.agents ?? FALLBACK_ROUTES.agents;
  const taskRouted = config?.auto_task_routing ?? false;

  // Same guard as the main page's: in Auto the backend reports the concrete
  // model it routed to, which would otherwise read as a difference and bounce
  // the session back and forth.
  const sentSelection = useRef<string | null>(null);
  useEffect(() => {
    if (!state.connected) return;
    if (sentSelection.current === selection) return;
    const settled =
      selection === AUTO_MODEL_ID
        ? state.routingMode === "auto"
        : state.modelId === selection;
    if (settled) {
      sentSelection.current = selection;
      return;
    }
    setModel(selection);
    sentSelection.current = selection;
  }, [selection, state.connected, state.modelId, state.routingMode, setModel]);

  useEffect(() => {
    if (!state.connected) sentSelection.current = null;
  }, [state.connected]);

  const chooseModel = useCallback(
    (id: string | null) => {
      if (!activeAgent) return;
      setModelChoice((prev) => ({ ...prev, [activeAgent]: id }));
    },
    [activeAgent],
  );

  // --- handoff -------------------------------------------------------------

  /**
   * Accept a routing directive, or Agent 4's "send to the Graphic Creator".
   *
   * Opens the target agent with the context in its composer — *not* sent. The
   * user reads and edits what travels before the next specialist sees it,
   * which is the second half of keeping a human in the loop.
   */
  const handoff = useCallback(
    (agentId: string, context: string) => {
      void openAgent(agentId, context);
    },
    [openAgent],
  );

  const backToGallery = useCallback(() => {
    setActiveAgent(null);
    clearLive(AGENT_ACTIVE_KEY);
    setShelfOpen(false);
    refreshCredits();
  }, [refreshCredits]);

  const agent = useMemo(
    () => catalogue.find((a) => a.id === activeAgent) ?? null,
    [catalogue, activeAgent],
  );

  // The catalogue is still loading, or the stored agent id is one this build
  // does not have. Either way the gallery is the right place to be.
  if (!activeAgent || !agent) {
    return (
      <AgentGallery
        agents={catalogue}
        loading={loading}
        error={error}
        credits={credits}
        onSelect={(id) => void openAgent(id)}
        onRetry={loadCatalogue}
      />
    );
  }

  if (!sessionId) {
    return (
      <div className="grid h-full place-items-center">
        <p className="font-sans text-[0.8125rem] text-ink-faint">
          Opening {agent.name}…
        </p>
      </div>
    );
  }

  return (
    <div className="relative flex h-full min-h-0">
      <div className="min-w-0 flex-1">
        <AgentChat
          agent={agent}
          catalogue={catalogue}
          sessionId={sessionId}
          token={token}
          state={state}
          busy={busy}
          models={models}
          modelChoice={
            activeAgent in modelChoice
              ? modelChoice[activeAgent]
              : DEFAULT_MODEL_ID
          }
          autoTargetId={autoTargetId}
          taskRouted={taskRouted}
          seedPrompt={seed}
          onSeedConsumed={() => setSeed(null)}
          onSend={send}
          onCancel={cancel}
          onEditMessage={editMessage}
          onRegenerate={regenerate}
          onSwitchBranch={switchBranch}
          onSelectModel={chooseModel}
          onResolveApproval={resolveApproval}
          onHandoff={handoff}
          onBack={backToGallery}
        />
      </div>

      <AgentSessionShelf
        agent={agent}
        sessions={shelf}
        activeId={sessionId}
        open={shelfOpen}
        token={token}
        onToggle={() => setShelfOpen((o) => !o)}
        onSelect={(id) => {
          setSessions((s) => ({ ...s, [agent.id]: id }));
          setShelfOpen(false);
        }}
        onNew={() => void newSession()}
        onAction={sessionAction}
      />
    </div>
  );
}
