"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useProjects } from "@/lib/useProjects";
import { ProjectPanel } from "@/components/projects/ProjectPanel";
import { ArtifactPanel } from "@/components/artifacts/ArtifactPanel";
import { listArtifacts, toPayload } from "@/lib/artifacts";
import {
  AUTO_MODEL_ID,
  DEFAULT_MODEL_ID,
  defaultModelFor,
  autoNameSession,
  createSession,
  deleteSession as apiDeleteSession,
  exportProject,
  fetchConfig,
  fetchPreferences,
  fetchWorkspaceTree,
  listSessions,
  readWorkspaceFile,
  branchOp,
  commitWorkspace,
  fetchBranches,
  fetchGitState,
  initRepo,
  stagePaths,
  suggestCommitMessage,
  type GitBranch,
  stopPreview as apiStopPreview,
  updateSession,
  workspaceFs,
  writeWorkspaceFile,
  type BackendConfig,
  type SessionRow,
} from "@/lib/api";
import { useAgentSocket } from "@/lib/useAgentSocket";
import { DOCKED_QUERY, useMediaQuery } from "@/lib/useMediaQuery";
import { AmbientField } from "@/components/AmbientField";
import { SPRING_SNAP, SPRING_SOFT, useMotionOK } from "@/components/Anim";
import { CodeIntro, useCodeIntro } from "@/components/CodeIntro";
import { LoomMark } from "@/components/LoomMark";
import { ChatPanel } from "@/components/ChatPanel";
import { CommandPalette, type Command } from "@/components/CommandPalette";
import { ContextColumn, type ContextFace } from "@/components/ContextColumn";
import { RenameDialog } from "@/components/RenameDialog";
import { SessionSidebar, type ShelfView } from "@/components/SessionSidebar";
import type { HistoryAction } from "@/components/SessionHistoryMenu";
import { LearnSection } from "@/components/learn/LearnSection";
import { AgentSection } from "@/components/agents/AgentSection";
import { SectionNav } from "@/components/SectionNav";
import { StatusIndicator } from "@/components/StatusIndicator";
import { ToastHost } from "@/components/Toast";
import { AuthPanel } from "@/components/AuthPanel";
import { AuthScreen } from "@/components/AuthScreen";
import { SettingsDialog } from "@/components/SettingsDialog";
import { ShortcutsDialog } from "@/components/ShortcutsDialog";
import { useAuth } from "@/lib/useAuth";
import { useKeyboardInset } from "@/lib/useKeyboardInset";
import { cn } from "@/lib/cn";
import { forgetPersistedLive, readLive, writeLive } from "@/lib/liveSession";
import {
  FALLBACK_ROUTES,
  SECTIONS,
  SECTION_META,
  type Section,
} from "@/lib/sections";

/**
 * One live session id *per conversational surface*.
 *
 * Chat and Code used to share a single key, which meant they shared a single
 * live session — switching section carried the conversation across, and the
 * one history list showed both. They are separate shelves now, so they need
 * separate current-session pointers.
 *
 * These are read and written through `lib/liveSession`, which is backed by
 * `sessionStorage` rather than `localStorage`. See that module for why: a
 * reload has to keep you in the conversation, and a fresh visit has to start a
 * new one, and only a per-browsing-session store tells those two apart.
 */
const SESSION_KEYS: Record<ConvSection, string> = {
  chat: "coding-agent:session:chat",
  code: "coding-agent:session:code",
};
/**
 * The same three keys, as the previous scheme left them in `localStorage`.
 * Cleared once on boot; see `forgetPersistedLive`.
 */
/** Which identity minted the ids above. See the identity effect below. */
const SESSION_OWNER_KEY = "coding-agent:session:owner";
const STALE_SESSION_KEYS = [
  "coding-agent:session",
  SESSION_KEYS.chat,
  SESSION_KEYS.code,
];
// Bumped whenever the default model changes. A persisted per-section choice is
// replayed verbatim on load, so without a new key every existing browser would
// keep selecting the *previous* default — including one that has since been
// removed from the registry — and the change would never visibly take effect.
// Bump it on every default change, and on every provider removal: a browser
// that keeps replaying a model the backend no longer registers gets it
// silently reassigned on each load, and the accompanying notice never stops
// appearing. v4 is the current default, Qwen 3.7 Plus.
const MODEL_KEY = "coding-agent:model-choice:v4";
const SECTION_KEY = "coding-agent:section";
const USER_NAME = "Vinayak";

/**
 * The two sections that run a conversation against the shared session table.
 * Learn and Agents own their own surfaces and their own sessions, so they are
 * not part of this — but they still have to resolve to *something* while they
 * are the active section, and Chat is the sensible home for the shelf that is
 * hidden underneath them anyway.
 */
type ConvSection = "chat" | "code";
const convSectionOf = (s: Section): ConvSection => (s === "code" ? "code" : "chat");

/**
 * The Ledger layout.
 *
 * Three regions of deliberately unequal weight, rather than three equal boxes:
 *
 *   · a 72px glyph rail — permanent, never expands in place; the session list
 *     it used to hold now flies out *over* the content on demand;
 *   · the conversation, which takes every pixel the other two do not want and
 *     carries the largest type in the app;
 *   · a recessed context column at roughly a third, which is never empty —
 *     Project Pulse by default, the diff when a file is in play, the sandbox
 *     terminal docked at its foot.
 *
 * Nothing here is a flat rectangle butted against its neighbour. The rail and
 * the conversation float over the ambient field; the context column sits
 * *behind* them in tone and blur. Depth is doing the separating that borders
 * used to do.
 */
export default function Page() {
  /**
   * One owner for the session, rather than one per component that happens to
   * need it. Signing out has to take the whole app back to a sign-in screen,
   * which is not something a control buried in the rail can do on its own.
   */
  const auth = useAuth();
  useKeyboardInset();
  const token = auth.token;
  const projects = useProjects(token, auth.signedIn);
  /** Which project's panel is open, or null. Not a route: it is a dialog. */
  const [projectPanel, setProjectPanel] = useState<string | null>(null);
  /**
   * Which artifact is open.
   *
   * Owned here rather than in socket state, so that a revision arriving while
   * the user has the panel closed does not re-open it. The socket only raises
   * a one-shot `artifactToOpen` signal on *creation*; this consumes it.
   */
  const [openArtifact, setOpenArtifact] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** The keyboard reference, opened with `?` or from Settings. */
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  /**
   * One live session per conversational surface, never one shared between
   * them. Keeping both mounted (rather than swapping a single id) is also what
   * lets you leave a Code run going, read something in Chat, and come back to
   * it still there.
   */
  const [sessionIds, setSessionIds] = useState<Record<ConvSection, string | null>>({
    chat: null,
    code: null,
  });
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  /**
   * The shelf, narrowed to the selected project.
   *
   * Filtered on the client rather than refetched: the shelf is already in
   * memory and capped at a page, so a round trip per chip click would be a
   * network request to hide rows the browser is holding. The server-side
   * filter exists too (`list_sessions(project_scoped=True)`) and is what the
   * project panel uses, where the whole project's history is wanted rather
   * than the current page of the shelf.
   */
  const visibleSessions = useMemo(
    () =>
      projects.filter
        ? sessions.filter((s) => s.project_id === projects.filter)
        : sessions,
    [sessions, projects.filter],
  );
  const [config, setConfig] = useState<BackendConfig | null>(null);
  const [section, setSection] = useState<Section>("chat");
  /** Which shelf the current section reads and writes. */
  const convSection = convSectionOf(section);
  const sessionId = sessionIds[convSection];

  const setSessionId = useCallback(
    (id: string | null) =>
      setSessionIds((prev) => ({ ...prev, [convSectionOf(section)]: id })),
    [section],
  );
  /** Which shelf the flyout is showing: the active list, or the archive. */
  const [shelf, setShelf] = useState<ShelfView>("active");
  /** The sessions panel, which floats over the conversation. */
  const [flyoutOpen, setFlyoutOpen] = useState(false);
  /** The rail itself, off-canvas below `sm`. */
  const [railOpen, setRailOpen] = useState(false);
  /** Below `xl` the context column floats; this is whether it is showing. */
  const [contextOpen, setContextOpen] = useState(false);
  const [contextFace, setContextFace] = useState<ContextFace>("pulse");
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  /** Which session the rename dialog is pointed at — not always the active one. */
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameGenerating, setRenameGenerating] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const docked = useMediaQuery(DOCKED_QUERY);
  const motionOK = useMotionOK();

  /**
   * The Code section's once-per-session welcome. Held here rather than inside
   * the overlay so the interface underneath can come up in step with the veil
   * lifting — the two halves of one hand-off, not an overlay that vanishes off
   * a finished screen.
   */
  const { playing: introPlaying, dismiss: dismissIntro } = useCodeIntro(
    section === "code",
  );

  /**
   * null = Auto for that section. A manual pick persists per section for the
   * session; it does not leak across modes.
   *
   * Every section starts on the default model rather than on Auto. Auto is
   * still in the dropdown, still the same code path, and still routes per turn
   * when OpenCode is configured — this only changes which entry is selected
   * before anyone touches the control.
   *
   * The starting value is now **per section** rather than one model for all
   * four: Code opens on MiMo V2.5 and Chat on DeepSeek V4 Flash. These are the
   * pre-config values; `config.default_model_ids` replaces them the moment
   * `/api/config` lands, and a stored pick from a previous visit wins over
   * both.
   */
  const [modelChoice, setModelChoice] = useState<Record<Section, string | null>>({
    chat: defaultModelFor("chat"),
    learning: defaultModelFor("learning"),
    code: defaultModelFor("code"),
    // Agents keeps its own per-agent selection inside `AgentSection` — one
    // pick across ten specialists would be wrong for most of them — so this
    // entry exists only to satisfy the record's shape.
    agents: defaultModelFor("agents"),
  });
  /**
   * Whether the user has actually chosen a model for a section this visit (or
   * in a previous one, via localStorage). Untouched sections follow the
   * backend's per-section default when config arrives; a section the user has
   * picked in is never moved out from under them.
   */
  const touchedModel = useRef<Partial<Record<Section, true>>>({});
  /**
   * Sections whose model the user has changed *since this page loaded*.
   *
   * Distinct from `touchedModel`, which also counts picks restored from
   * localStorage — and that distinction is the whole point. The account
   * preference has to be able to win over a stale per-browser remnant (that is
   * what "follows you across devices" means) while never yanking the model out
   * from under someone who has just chosen one on this screen.
   */
  const touchedThisLoad = useRef<Partial<Record<Section, true>>>({});
  /**
   * The account's default model, once read. `undefined` while unknown, `null`
   * when the user has expressed no preference — which is not the same as
   * choosing the current default, and must stay distinguishable so that a
   * build whose default moves carries along the people who never chose.
   */
  const [accountModel, setAccountModel] = useState<string | null | undefined>(
    undefined,
  );
  /** The token whose preference has already been applied, so it applies once. */
  const appliedPrefFor = useRef<string | null | undefined>(undefined);

  const {
    state,
    busy,
    send,
    cancel,
    editMessage,
    regenerate,
    switchBranch,
    setModel,
    openFile,
    openContent,
    clearTerminal,
    dismissToast,
    reloadPreview,
    dismissPreviewError,
    applyGitState,
    applyLocalEdit,
    applyTree,
    applyPathMove,
    seedArtifacts,
    clearArtifactSignal,
  } = useAgentSocket(sessionId, token, null, convSection);

  // --- bootstrap -----------------------------------------------------------
  useEffect(() => {
    // A reload finds these still set and resumes the conversation. A new
    // browsing session finds them gone and mints fresh ids, which is what
    // makes opening the app land on an empty composer instead of on last
    // week's thread. The previous ids were kept in `localStorage`, which never
    // expires, so *every* visit resumed — clear those out on the way past.
    forgetPersistedLive(...STALE_SESSION_KEYS);
    setSessionIds({
      chat: readLive(SESSION_KEYS.chat) ?? crypto.randomUUID(),
      code: readLive(SESSION_KEYS.code) ?? crypto.randomUUID(),
    });

    const s = localStorage.getItem(SECTION_KEY) as Section | null;
    if (s && SECTIONS.includes(s)) setSection(s);
    try {
      const raw = localStorage.getItem(MODEL_KEY);
      if (raw) {
        const stored = JSON.parse(raw) as Partial<Record<Section, string | null>>;
        // A stored pick is a pick: mark it so the config-defaults effect below
        // leaves it alone. Without this, restoring "I always use Auto in Code"
        // and then having config land would silently replace it.
        for (const key of Object.keys(stored) as Section[]) {
          if (stored[key] !== undefined) touchedModel.current[key] = true;
        }
        setModelChoice((prev) => ({ ...prev, ...stored }));
      }
    } catch {
      /* corrupt value is not worth failing boot over */
    }
  }, []);

  useEffect(() => {
    for (const key of ["chat", "code"] as const) {
      const id = sessionIds[key];
      if (id) writeLive(SESSION_KEYS[key], id);
    }
  }, [sessionIds]);

  /**
   * A change of identity starts new sessions.
   *
   * Sessions belong to whoever created them, and the backend enforces that:
   * the socket closes with 4403 when the caller does not own the id in the
   * path. So carrying the id across a sign-in — from the anonymous shelf to a
   * named account, or between two accounts — hands the socket an id the new
   * caller has no claim to, and the app sits on "Disconnected" through a
   * reconnect loop that can never succeed. Signing in used to do exactly that.
   *
   * Minting fresh ids is also the right *product* behaviour: the conversation
   * you were having as one identity is not the conversation you want handed to
   * the next one. The old sessions are untouched and remain in the previous
   * account's history.
   *
   * Who the live ids belong to is recorded beside them, in the same
   * per-browsing-session store, rather than in a ref. A ref only knows what
   * happened since this component mounted, and the case that actually breaks
   * is the one it cannot see: signing in, then *reloading*, which brings back
   * ids minted while anonymous under an identity that does not own them.
   *
   * A reload by the same identity therefore matches and changes nothing, which
   * is what keeps a refresh mid-conversation in that conversation.
   */
  const identity = auth.ready ? (auth.userId ?? "anonymous") : null;
  useEffect(() => {
    if (identity === null) return;
    if (readLive(SESSION_OWNER_KEY) === identity) return;
    writeLive(SESSION_OWNER_KEY, identity);
    setSessionIds({ chat: crypto.randomUUID(), code: crypto.randomUUID() });
    setContextFace("pulse");
    setContextOpen(false);
    setFlyoutOpen(false);
  }, [identity]);

  // The section is persisted by the handler that changes it, not by an effect
  // watching it. An effect would fire once on mount with the *initial* value
  // and overwrite the stored section before the bootstrap above has restored
  // it — and under StrictMode's double-invoked effects the second bootstrap
  // pass then reads back the value that clobber just wrote, so the app always
  // reopened on Chat no matter which section you left it in. See
  // `selectSection`.

  useEffect(() => {
    localStorage.setItem(MODEL_KEY, JSON.stringify(modelChoice));
  }, [modelChoice]);

  useEffect(() => {
    fetchConfig().then(setConfig).catch(() => setConfig(null));
  }, []);

  // Reconcile the *persisted* model picks against what this backend actually
  // offers. `modelChoice` is restored from localStorage and outlives any
  // change of server config, so a pick whose provider key has since been
  // removed (or a model that has been retired) stays selected forever: the
  // selector renders a raw id it cannot find a display name for, and every
  // turn silently runs on something else because the backend resolves past it.
  // Reset those sections to the backend's default and leave valid picks —
  // and the Auto sentinel, which is a mode rather than a model — untouched.
  /**
   * Read the account's default model whenever the identity changes.
   *
   * Anonymous callers get `null` back from the server rather than a shared
   * row: an anonymous session is per-device by definition, and pooling those
   * preferences would let one browser change another's.
   */
  useEffect(() => {
    if (!auth.ready) return;
    let live = true;
    fetchPreferences(auth.token)
      .then((p) => {
        if (live) setAccountModel(p.default_model_id ?? null);
      })
      .catch(() => {
        if (live) setAccountModel(null);
      });
    return () => {
      live = false;
    };
  }, [auth.ready, auth.token]);

  /**
   * Apply the account preference across every section, once per identity.
   *
   * Sections the user has touched *on this screen* are left alone — a
   * preference is a starting point, not an override of a live decision. A
   * section they merely have a stale localStorage value for is not protected,
   * because the account is meant to be the more authoritative of the two.
   */
  useEffect(() => {
    if (accountModel === undefined) return;
    if (appliedPrefFor.current === auth.token) return;
    appliedPrefFor.current = auth.token;
    if (!accountModel) return;
    // `null` is Auto in this state; the server stores it as the string "auto".
    const pick = accountModel === AUTO_MODEL_ID ? null : accountModel;
    setModelChoice((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const key of Object.keys(next) as Section[]) {
        if (touchedThisLoad.current[key]) continue;
        if (next[key] === pick) continue;
        next[key] = pick;
        touchedModel.current[key] = true;
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [accountModel, auth.token]);

  useEffect(() => {
    if (!config) return;
    const offered = new Set(config.models.map((m) => m.id));
    setModelChoice((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const key of Object.keys(next) as Section[]) {
        const pick = next[key];
        if (pick === null || pick === AUTO_MODEL_ID) continue;
        if (!offered.has(pick)) {
          // The pick names a model this backend does not serve — a retired id,
          // or one whose provider key has been removed since it was stored.
          // Reset to the section's default rather than leaving the selector
          // showing an id it cannot name.
          next[key] = defaultModelFor(key, config);
          changed = true;
          continue;
        }
        // A valid pick the user never made is still only a placeholder: it is
        // whatever this bundle guessed before config answered. The backend's
        // per-section default is the better answer, so adopt it.
        if (!touchedModel.current[key]) {
          const preferred = defaultModelFor(key, config);
          if (preferred !== pick && offered.has(preferred)) {
            next[key] = preferred;
            changed = true;
          }
        }
      }
      return changed ? next : prev;
    });
  }, [config]);

  // Scoped to the active surface. This is the query half of the section fix:
  // Chat asks for Chat's sessions and Code asks for Code's, so neither list can
  // contain the other's rows no matter what is in the table.
  const refreshSessions = useCallback(() => {
    listSessions(token, shelf === "archived", convSection)
      .then(setSessions)
      .catch(() => setSessions([]));
  }, [token, shelf, convSection]);

  useEffect(refreshSessions, [refreshSessions, sessionId]);

  // A file the agent just touched turns the context column to its code face.
  // Docked, that is the whole gesture; floating, the column has to come out
  // too — which is why this needs to know which layout it is in.
  //
  // Preview is the one face this does not interrupt: while the user is watching
  // the running site, an edit is the thing they are watching *for*, and yanking
  // the panel to a diff every time the agent saves a file would make the
  // preview unusable during exactly the turn it exists to cover.
  const previewFace = useRef(false);
  previewFace.current = contextFace === "preview";
  useEffect(() => {
    if (!state.activeFile) return;
    if (previewFace.current) return;
    setContextFace("code");
    if (!docked) setContextOpen(true);
  }, [state.activeFile, docked]);

  useEffect(() => {
    if (state.terminalBusy) setTerminalOpen(true);
  }, [state.terminalBusy]);

  // A preview coming up is worth showing unprompted — it is the payoff of the
  // whole turn, and the user asked for a running site rather than a diff.
  const previewStatus = state.preview.status;
  useEffect(() => {
    if (previewStatus !== "live") return;
    setContextFace("preview");
    if (!docked) setContextOpen(true);
  }, [previewStatus, docked]);

  // --- model routing -------------------------------------------------------

  /** Auto's fallback target for the section — backend is authoritative. */
  const autoTargetId = useMemo(
    () => config?.auto_routes?.[section] ?? FALLBACK_ROUTES[section],
    [config, section],
  );

  /** True once OpenCode is configured: Auto routes per turn, not per section. */
  const taskRouted = config?.auto_task_routing ?? false;

  /**
   * What we tell the backend. A manual pick is the model id; Auto sends the
   * sentinel rather than a resolved model, because in task-routed mode only
   * the backend can see the state the decision depends on.
   */
  const selection = modelChoice[section] ?? AUTO_MODEL_ID;

  // Push the selection to the agent whenever it changes. In Auto the backend
  // reports the concrete model it routed to, which would otherwise read as a
  // difference and bounce the session back — so compare against the mode.
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

  // A reconnect re-announces the session's stored selection; clear the guard
  // so the next effect run re-sends if the user changed it while offline.
  useEffect(() => {
    if (!state.connected) sentSelection.current = null;
  }, [state.connected]);

  const chooseModel = useCallback(
    (id: string | null) => {
      // Record that this section's model is now the user's, not a default.
      // The config-reconciliation effect reads this and stops substituting.
      touchedModel.current[section] = true;
      // And that it happened *on this screen*, which is what protects it from
      // the account preference landing a moment later and replacing it.
      touchedThisLoad.current[section] = true;
      setModelChoice((prev) => ({ ...prev, [section]: id }));
    },
    [section],
  );

  // --- sessions ------------------------------------------------------------

  // The callbacks below are all `useCallback`ed for one reason: a streamed
  // token re-renders this component, and any handler rebuilt here would defeat
  // the `memo` on the panels it is passed to — they would re-render (and, being
  // framer-motion subtrees, re-measure) on every token of every answer.

  const newSession = useCallback(async () => {
    try {
      // A new session starts on whatever the user has chosen here, Auto
      // included — the sentinel persists exactly like a model id.
      const row = await createSession(token, selection, convSection);
      setSessionId(row.id);
      // A session started while a project filter is on belongs to that
      // project. Assigning after creation rather than at creation keeps the
      // session endpoint unaware of projects, and the extra round trip is off
      // the critical path — nothing on screen is waiting for it.
      if (projects.filter) void projects.assign(row.id, projects.filter);
    } catch {
      setSessionId(crypto.randomUUID());
    }
    setContextFace("pulse");
    setContextOpen(false);
    setTerminalOpen(false);
    setFlyoutOpen(false);
    setTimeout(refreshSessions, 400);
  }, [token, selection, convSection, setSessionId, refreshSessions, projects]);

  /**
   * File a conversation into a project, or out of one.
   *
   * The local row is updated first so the filtered list reacts on the click
   * rather than a round trip later — with a filter active, moving a session
   * *out* has to make it leave the list immediately or the action looks like
   * it did nothing.
   */
  const moveSessionToProject = useCallback(
    async (id: string, projectId: string | null) => {
      setSessions((current) =>
        current.map((s) => (s.id === id ? { ...s, project_id: projectId } : s)),
      );
      await projects.assign(id, projectId);
      setTimeout(refreshSessions, 300);
    },
    [projects, refreshSessions],
  );

  // A newly created artifact opens itself; a revision does not. The signal is
  // consumed immediately so it cannot re-fire on an unrelated re-render.
  useEffect(() => {
    if (!state.artifactToOpen) return;
    setOpenArtifact(state.artifactToOpen);
    clearArtifactSignal();
  }, [state.artifactToOpen, clearArtifactSignal]);

  /**
   * Artifacts written in earlier sessions of this conversation.
   *
   * The socket carries everything written *during* a conversation, but a
   * session reopened after a reload has artifacts nobody is going to re-emit —
   * so they are fetched once per session and seeded into socket state.
   */
  useEffect(() => {
    if (!sessionId) return;
    let live = true;
    listArtifacts(sessionId, token)
      .then((rows) => {
        if (live && rows.length) seedArtifacts(rows.map(toPayload));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [sessionId, token, seedArtifacts]);

  const removeSession = useCallback(
    async (id: string) => {
      await apiDeleteSession(id, token).catch(() => undefined);
      setSessions((s) => s.filter((x) => x.id !== id));
      if (id === sessionId) await newSession();
    },
    [token, sessionId, newSession],
  );

  /**
   * Pin / archive / delete, for whichever list raised it — the flyout is the
   * same component in Chat and in Code.
   *
   * The optimistic update is what makes the row move under the pointer rather
   * than after a round trip. Pinning re-sorts in place; archiving removes the
   * row from the shelf it is on (the two shelves are disjoint), and the
   * refresh that follows reconciles with the backend's own ordering.
   */
  const sessionAction = useCallback(
    async (id: string, action: HistoryAction) => {
      if (action === "delete") {
        await removeSession(id);
        return;
      }
      if (action === "rename") {
        setRenameTarget(id);
        setRenameError(null);
        setRenameOpen(true);
        return;
      }

      const row = sessions.find((s) => s.id === id);
      if (!row) return;

      const patch =
        action === "pin"
          ? { is_pinned: !row.is_pinned }
          : { is_archived: !row.is_archived };

      setSessions((current) => {
        if (action === "archive") return current.filter((s) => s.id !== id);
        const next = current.map((s) => (s.id === id ? { ...s, ...patch } : s));
        // Mirror the server's rule locally so the row lands where it will
        // still be after the refresh: pinned first, then by activity.
        return [
          ...next.filter((s) => s.is_pinned),
          ...next.filter((s) => !s.is_pinned),
        ];
      });

      await updateSession(id, patch, token).catch(() => undefined);
      refreshSessions();
    },
    [sessions, token, removeSession, refreshSessions],
  );

  const selectShelf = useCallback((next: ShelfView) => setShelf(next), []);

  // --- workspace -----------------------------------------------------------

  /**
   * Save a manual edit into the sandbox, then fold it into the same state the
   * agent's own edits land in — one changed-files list, one diff, one flash.
   * The result comes back over HTTP rather than the socket precisely so it can
   * be applied here without echoing into the editor the user is still in.
   */
  const saveFile = useCallback(
    async (path: string, content: string) => {
      if (!sessionId) throw new Error("No session to save into.");
      const saved = await writeWorkspaceFile(sessionId, path, content, token);
      applyLocalEdit({
        path: saved.path,
        content: saved.content,
        diff: saved.diff,
        change: saved.change,
        at: saved.ts,
      });
    },
    [sessionId, token, applyLocalEdit],
  );

  /**
   * Re-read the sandbox tree.
   *
   * The backend also pushes a `file_tree` over the socket after each of these
   * operations, which is what keeps a second tab honest. This is the same walk
   * fetched directly, so the tab that *did* the thing does not depend on its
   * own socket being up to see the result.
   */
  const refreshTree = useCallback(async () => {
    if (!sessionId) return;
    try {
      const tree = await fetchWorkspaceTree(sessionId, token);
      applyTree(tree.path, tree.nodes);
    } catch {
      // The socket's own `file_tree` is the fallback; a failed walk here is
      // not worth surfacing over the operation that just succeeded.
    }
  }, [sessionId, token, applyTree]);

  /**
   * Rename / delete / create, raised by the sandbox tree's context menu.
   *
   * Each one moves a path the rest of the UI is keyed by, so the local state
   * is moved with it before the tree is re-read — otherwise the diff panel
   * spends a round trip pointed at a file that no longer exists.
   */
  const treeActions = useMemo(
    () =>
      sessionId
        ? {
            onRename: async (path: string, name: string) => {
              const result = await workspaceFs(sessionId, "rename", path, name, token);
              applyPathMove(path, result.path);
              await refreshTree();
            },
            onDelete: async (path: string) => {
              await workspaceFs(sessionId, "delete", path, undefined, token);
              applyPathMove(path, null);
              await refreshTree();
            },
            onCreate: async (parent: string, name: string, kind: "file" | "dir") => {
              await workspaceFs(
                sessionId,
                kind === "dir" ? "new_dir" : "new_file",
                `${parent.replace(/\/$/, "")}/${name}`,
                undefined,
                token,
              );
              await refreshTree();
            },
          }
        : undefined,
    [sessionId, token, applyPathMove, refreshTree],
  );

  /** A folder or files opened through the composer's "+". */
  const onImported = useCallback(() => {
    void refreshTree();
    setContextFace("pulse");
    if (!docked) setContextOpen(true);
  }, [refreshTree, docked]);

  const exportZip = useCallback(async () => {
    if (!sessionId || exporting) return;
    setExporting(true);
    try {
      await exportProject(sessionId, token);
    } catch {
      // The button returns to rest; the sandbox being empty or gone is the
      // usual cause and is already visible in the Pulse.
    } finally {
      setExporting(false);
    }
  }, [sessionId, token, exporting]);

  // Hydrate the repository state when a session opens. `git_state` is pushed
  // on every change, but a browser that connects to a session with existing
  // history has missed all of them — same reason the preview status is
  // refetched on connect rather than assumed absent.
  useEffect(() => {
    if (!sessionId || section !== "code" || !state.connected) return;
    let cancelled = false;
    void fetchGitState(sessionId, token)
      .then((snapshot) => {
        if (!cancelled) applyGitState(snapshot);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, section, state.connected, token, applyGitState]);

  const stopThePreview = useCallback(() => {
    if (sessionId) void apiStopPreview(sessionId, token);
  }, [sessionId, token]);

  // --- git -----------------------------------------------------------------
  //
  // Both actions go over REST rather than through the agent: committing your
  // own edit should not cost a turn. The backend answers by broadcasting
  // `git_state` on this session's socket, so the panel updates through exactly
  // the same path an agent-driven commit takes — there is one source of truth
  // for what history looks like, and no refetch to get out of step with.
  const [gitBusy, setGitBusy] = useState(false);
  const [gitError, setGitError] = useState<string | null>(null);

  const [branches, setBranches] = useState<GitBranch[]>([]);

  /**
   * Refresh the branch list.
   *
   * Not part of the `git_state` broadcast: that event carries status and log,
   * which change on every agent turn, and branches change on approximately
   * none of them. Refetching them on every file write would be a round trip
   * per turn to redraw a list that is almost always identical.
   */
  const refreshBranches = useCallback(() => {
    if (!sessionId) return;
    void fetchBranches(sessionId, token)
      .then(setBranches)
      .catch(() => setBranches([]));
  }, [sessionId, token]);

  const commitChanges = useCallback(
    (message: string, paths: string[] = []) => {
      if (!sessionId || gitBusy) return;
      setGitBusy(true);
      setGitError(null);
      // An empty selection means "everything", which is what the server does
      // with no paths and no use_index. A non-empty one is staged first and
      // then committed from the index, so nothing unticked can ride along.
      void commitWorkspace(sessionId, message, token, paths, false)
        .then((result) => {
          if (!result.committed && result.reason) setGitError(result.reason);
          // The socket broadcast is the normal path; applying the response as
          // well keeps the panel correct if the socket is mid-reconnect.
          if (result.snapshot) applyGitState(result.snapshot);
        })
        .catch((err: unknown) =>
          setGitError(err instanceof Error ? err.message : "Commit failed."),
        )
        .finally(() => {
          setGitBusy(false);
          refreshBranches();
        });
    },
    [sessionId, token, gitBusy, applyGitState, refreshBranches],
  );

  const stageChanges = useCallback(
    (paths: string[], mode: "stage" | "unstage" | "discard") => {
      if (!sessionId || gitBusy) return;
      setGitBusy(true);
      setGitError(null);
      void stagePaths(sessionId, paths, mode, token)
        .then((result) => {
          if (result.snapshot) applyGitState(result.snapshot);
        })
        .catch((err: unknown) =>
          setGitError(err instanceof Error ? err.message : "That did not work."),
        )
        .finally(() => setGitBusy(false));
    },
    [sessionId, token, gitBusy, applyGitState],
  );

  const runBranchOp = useCallback(
    (name: string, action: "create" | "checkout" | "merge") => {
      if (!sessionId || gitBusy) return;
      setGitBusy(true);
      setGitError(null);
      void branchOp(sessionId, name, action, token)
        .then((result) => {
          if (result.snapshot) applyGitState(result.snapshot);
          // A conflicted merge resolves rather than throws — the merge really
          // happened and the tree really has markers in it, so it is reported
          // here rather than being mistaken for a clean result.
          if (result.conflicted?.length) {
            setGitError(
              `Merged with ${result.conflicted.length} conflict(s): ` +
                result.conflicted.slice(0, 3).join(", ") +
                (result.conflicted.length > 3 ? "…" : "") +
                ". Resolve them, then commit.",
            );
          }
        })
        .catch((err: unknown) =>
          setGitError(err instanceof Error ? err.message : "That did not work."),
        )
        .finally(() => {
          setGitBusy(false);
          refreshBranches();
        });
    },
    [sessionId, token, gitBusy, applyGitState, refreshBranches],
  );

  const suggestMessage = useCallback(async (): Promise<string> => {
    if (!sessionId) return "";
    try {
      return await suggestCommitMessage(sessionId, token);
    } catch (err: unknown) {
      setGitError(
        err instanceof Error ? err.message : "Could not write a message.",
      );
      return "";
    }
  }, [sessionId, token]);

  const startRepo = useCallback(() => {
    if (!sessionId || gitBusy) return;
    setGitBusy(true);
    setGitError(null);
    void initRepo(sessionId, token)
      .then((result) => {
        if (result.snapshot) applyGitState(result.snapshot);
      })
      .catch((err: unknown) =>
        setGitError(err instanceof Error ? err.message : "Could not start a repository."),
      )
      .finally(() => {
        setGitBusy(false);
        refreshBranches();
      });
  }, [sessionId, token, gitBusy, applyGitState, refreshBranches]);

  // Once per session, not per turn: see `refreshBranches`.
  useEffect(refreshBranches, [refreshBranches]);

  // --- naming --------------------------------------------------------------

  const renameRow = useMemo(
    () => sessions.find((s) => s.id === (renameTarget ?? sessionId)) ?? null,
    [sessions, renameTarget, sessionId],
  );

  const openRename = useCallback((id?: string) => {
    setRenameTarget(id ?? null);
    setRenameError(null);
    setRenameOpen(true);
  }, []);

  const saveName = useCallback(
    async (title: string, description: string) => {
      const id = renameTarget ?? sessionId;
      if (!id) return;
      setRenameBusy(true);
      setRenameError(null);
      // Optimistic: the row is in front of the user and the round trip is not.
      setSessions((current) =>
        current.map((s) => (s.id === id ? { ...s, title, description } : s)),
      );
      try {
        await updateSession(id, { title, description }, token);
        setRenameOpen(false);
        refreshSessions();
      } catch (err) {
        setRenameError(
          err instanceof Error ? err.message : "Could not save the name.",
        );
      } finally {
        setRenameBusy(false);
      }
    },
    [renameTarget, sessionId, token, refreshSessions],
  );

  /** Fill the dialog's fields from the transcript. The user still presses Save. */
  const generateName = useCallback(async () => {
    const id = renameTarget ?? sessionId;
    if (!id) return;
    setRenameGenerating(true);
    setRenameError(null);
    try {
      const row = await autoNameSession(id, token);
      setSessions((current) =>
        current.map((s) =>
          s.id === id
            ? { ...s, title: row.title, description: row.description ?? "" }
            : s,
        ),
      );
    } catch (err) {
      setRenameError(
        err instanceof Error
          ? err.message.replace(/^\d+\s+\w+\s+—\s+/, "")
          : "Could not generate a name.",
      );
    } finally {
      setRenameGenerating(false);
    }
  }, [renameTarget, sessionId, token]);

  const toggleTerminal = useCallback(() => setTerminalOpen((o) => !o), []);
  const closeContext = useCallback(() => setContextOpen(false), []);
  const selectSection = useCallback((s: Section) => {
    setSection(s);
    localStorage.setItem(SECTION_KEY, s);
    setRailOpen(false);
    setFlyoutOpen(false);
  }, []);
  const selectSession = useCallback((id: string) => {
    setSessionId(id);
    setFlyoutOpen(false);
  }, []);
  // Read through refs so `selectFile` is not rebuilt on every file the agent
  // touches — it is passed to memoized panels, and a new identity per event
  // would re-render the whole context column mid-stream.
  const changedRef = useRef(state.changed);
  changedRef.current = state.changed;
  const viewedRef = useRef(state.viewed);
  viewedRef.current = state.viewed;

  /**
   * Open a file in the code panel.
   *
   * Files the agent wrote are already in state with their diff. Anything else —
   * a click in the sandbox tree, a hit in the command palette — has to be read
   * out of the sandbox first, which is what makes the whole tree openable
   * rather than only the handful of paths that happen to have been edited.
   */
  const selectFile = useCallback(
    (p: string) => {
      openFile(p);
      setContextFace("code");
      if (!docked) setContextOpen(true);
      if (!sessionId || changedRef.current[p] || viewedRef.current[p]) return;
      readWorkspaceFile(sessionId, p, token)
        .then((file) =>
          openContent({
            path: file.path,
            content: file.content,
            // No diff: nothing changed it. The panel opens on `file` for these,
            // and an empty diff is what tells it so.
            diff: "",
            change: "modified",
            at: Date.now(),
          }),
        )
        .catch(() => {
          // Binary, too large, or gone. The panel keeps whatever it was
          // showing rather than blanking on a click that could not be honoured.
        });
    },
    [openFile, openContent, sessionId, token, docked],
  );

  // --- keyboard ------------------------------------------------------------

  /**
   * The global shortcut layer.
   *
   * Two rules keep it out of the way. It never fires while the user is typing —
   * except for the palette and Escape, which are how you *leave* a field — and
   * it never fires in Learn, which is not a session and has none of these
   * surfaces. Both are checked here rather than in each handler, so a shortcut
   * added later cannot forget them.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const target = e.target as HTMLElement | null;
      const typing =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);

      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }

      if (e.key === "Escape") {
        // Deepest surface first, so one press never closes two things.
        if (paletteOpen || renameOpen || settingsOpen || shortcutsOpen) return;
        if (contextOpen) {
          setContextOpen(false);
          return;
        }
        if (flyoutOpen) {
          setFlyoutOpen(false);
          return;
        }
        // Nothing left to close, so Escape means the other thing it means
        // everywhere else: stop what is happening. Last in the chain rather
        // than first — a panel open over a streaming answer should close on
        // the first press, not silently kill the generation behind it.
        //
        // This is deliberately allowed while the composer has focus, unlike
        // every other binding here. Stopping a runaway answer is the one
        // action you want *most* when your hands are already on the keys.
        if (busy) cancel();
        return;
      }

      // The shortcuts reference. `?` is the near-universal binding for it, and
      // it needs no modifier because the `typing` guard below already keeps it
      // out of the composer's way.
      if (e.key === "?" && !typing && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        setShortcutsOpen((o) => !o);
        return;
      }

      if (typing || ownsOwnSurface(section)) return;

      if (mod && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setTerminalOpen((o) => !o);
      } else if (mod && e.shiftKey && e.key.toLowerCase() === "o") {
        e.preventDefault();
        void newSession();
      } else if (mod && e.key === "\\") {
        e.preventDefault();
        setContextOpen((o) => !o);
      } else if (mod && e.key.toLowerCase() === "b") {
        e.preventDefault();
        setFlyoutOpen((o) => !o);
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    paletteOpen,
    renameOpen,
    settingsOpen,
    shortcutsOpen,
    contextOpen,
    flyoutOpen,
    section,
    newSession,
    busy,
    cancel,
  ]);

  const activeFile = state.activeFile
    ? state.changed[state.activeFile] ?? state.viewed[state.activeFile] ?? null
    : null;
  // The sandbox panels are Code-mode furniture; they never appear in Chat/Learn.
  const showCodePanels = section === "code";
  /**
   * Learn is the one section that is not a conversation with the agent. It
   * owns its whole content area — library, workspace, course — and none of the
   * session furniture applies to it: a notebook is not a session, so the
   * rail's session controls and the agent status chip are hidden while it is
   * open rather than shown pointing at nothing.
   */
  const isLearn = section === "learning";
  /**
   * Agents is the same case for the same reason: it owns its content area and
   * runs its own sessions, one per specialist, on their own socket. The rail's
   * session controls and the page-level history flyout point at the *general*
   * session this component holds, which an agent conversation is not — so they
   * are hidden here too, and the section supplies its own history shelf.
   */
  const isAgents = section === "agents";
  /** Sections that own their content area and manage their own sessions. */
  const ownsSurface = isLearn || isAgents;

  /**
   * What the palette can do.
   *
   * Assembled here because these are the same actions the rail, the header and
   * the shortcut layer already perform — the palette is a third way to reach
   * them, not a fourth implementation of them. Hints repeat the shortcut where
   * one exists, so the palette teaches its own replacements.
   */
  const commands = useMemo<Command[]>(() => {
    const mod = isApplePlatform() ? "⌘" : "Ctrl";
    const codeOnly = section === "code";

    return [
      {
        id: "session.new",
        group: "Session",
        label: "New session",
        hint: `${mod}⇧O`,
        keywords: "start fresh reset",
        run: () => void newSession(),
      },
      {
        id: "session.rename",
        group: "Session",
        label: "Name this project…",
        keywords: "rename title describe description",
        disabled: !sessionId,
        run: () => openRename(sessionId ?? undefined),
      },
      {
        id: "app.settings",
        group: "Session",
        label: "Settings",
        keywords: "account sign out log out logout preferences profile model theme",
        run: () => setSettingsOpen(true),
      },
      {
        id: "app.shortcuts",
        group: "Session",
        label: "Keyboard shortcuts",
        hint: "?",
        keywords: "keys bindings help hotkeys reference",
        run: () => setShortcutsOpen(true),
      },
      {
        id: "session.list",
        group: "Session",
        label: "Search sessions",
        hint: `${mod}B`,
        keywords: "history switch open recent",
        run: () => setFlyoutOpen(true),
      },
      {
        id: "panel.preview",
        group: "Panels",
        label: "Show preview",
        keywords: "live site iframe browser dev server",
        disabled: !codeOnly,
        run: () => {
          setContextFace("preview");
          if (!docked) setContextOpen(true);
        },
      },
      {
        id: "panel.code",
        group: "Panels",
        label: "Show code",
        keywords: "diff file editor changes",
        disabled: !codeOnly,
        run: () => {
          setContextFace("code");
          if (!docked) setContextOpen(true);
        },
      },
      {
        id: "panel.history",
        group: "Panels",
        label: "Show version history",
        keywords: "git commit log branch diff changes version control",
        disabled: !codeOnly,
        run: () => {
          setContextFace("history");
          if (!docked) setContextOpen(true);
        },
      },
      {
        id: "panel.pulse",
        group: "Panels",
        label: "Show pulse",
        keywords: "status usage cost model tree",
        disabled: !codeOnly,
        run: () => {
          setContextFace("pulse");
          if (!docked) setContextOpen(true);
        },
      },
      {
        id: "panel.terminal",
        group: "Panels",
        label: terminalOpen ? "Hide terminal" : "Show terminal",
        hint: `${mod}J`,
        keywords: "shell output logs sandbox stdout",
        disabled: !codeOnly,
        run: () => setTerminalOpen((o) => !o),
      },
      {
        id: "preview.reload",
        group: "Preview",
        label: "Reload the preview",
        keywords: "refresh iframe reload site",
        disabled: state.preview.status !== "live",
        run: reloadPreview,
      },
      {
        id: "preview.open",
        group: "Preview",
        label: "Open the preview in a new tab",
        keywords: "browser external window",
        disabled: state.preview.status !== "live" || !state.preview.url,
        run: () => window.open(state.preview.url!, "_blank", "noopener"),
      },
      {
        id: "preview.stop",
        group: "Preview",
        label: "Stop the dev server",
        keywords: "kill shutdown quit",
        disabled: state.preview.status !== "live",
        run: stopThePreview,
      },
      {
        id: "project.export",
        group: "Project",
        label: exporting ? "Preparing the download…" : "Download project as .zip",
        keywords: "export save archive files download",
        disabled: !codeOnly || !sessionId || exporting,
        run: () => void exportZip(),
      },
      {
        id: "project.deploy",
        group: "Project",
        label: "Deploy to Vercel",
        hint: "coming soon",
        keywords: "publish ship host production",
        disabled: true,
        run: () => undefined,
      },
      ...(config?.models ?? []).slice(0, 8).map((m) => ({
        id: `model.${m.id}`,
        group: "Model",
        label: `Switch to ${m.name}`,
        hint: m.group,
        keywords: `${m.provider} ${m.group} ${m.detail ?? ""}`,
        run: () => chooseModel(m.id),
      })),
      {
        id: "model.auto",
        group: "Model",
        label: "Switch to Auto",
        hint: taskRouted ? "routes per task" : "routes per section",
        keywords: "automatic router choose",
        run: () => chooseModel(null),
      },
    ];
  }, [
    section,
    sessionId,
    docked,
    terminalOpen,
    exporting,
    config,
    taskRouted,
    state.preview.status,
    state.preview.url,
    newSession,
    openRename,
    reloadPreview,
    stopThePreview,
    exportZip,
    chooseModel,
  ]);

  /** Everything the context column needs, in one place — it has two mounts. */
  const contextProps = {
    face: contextFace,
    onFace: setContextFace,
    status: state.status,
    connected: state.connected,
    iterations: state.iterations,
    usage: state.usage,
    changed: state.changed,
    activeFile: state.activeFile,
    activeFileData: activeFile,
    tree: state.tree,
    treeRoot: state.treeRoot,
    flash: state.flash,
    modelName: state.modelName,
    routingMode: state.routingMode,
    routingHint: state.routingHint,
    terminal: state.terminal,
    terminalBusy: state.terminalBusy,
    terminalOpen,
    preview: state.preview,
    git: state.git,
    gitBusy,
    gitError,
    onCommit: commitChanges,
    onInitRepo: startRepo,
    branches,
    onStage: stageChanges,
    onBranch: runBranchOp,
    onSuggestMessage: suggestMessage,
    sessionId,
    token,
    exporting,
    onToggleTerminal: toggleTerminal,
    onClearTerminal: clearTerminal,
    onSelectFile: selectFile,
    onExport: exportZip,
    onSaveFile: sessionId ? saveFile : undefined,
    onReloadPreview: reloadPreview,
    onDismissPreviewError: dismissPreviewError,
    onStopPreview: stopThePreview,
    treeActions,
  };

  return (
    <div
      data-section={section}
      className="relative flex h-dvh overflow-hidden"
      style={{
        // `dvh` already tracks collapsing browser chrome; this is the keyboard,
        // which on iOS it does not. Taking it off the height (rather than
        // padding the bottom) keeps the composer's own sticky positioning
        // correct instead of pushing it into a padded strip.
        height: "calc(100dvh - var(--kb-inset, 0px))",
        paddingLeft: "env(safe-area-inset-left)",
        paddingRight: "env(safe-area-inset-right)",
      }}
    >
      <AmbientField status={state.status} />

      <CodeIntro playing={introPlaying} onDismiss={dismissIntro} />

      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        auth={auth}
        config={config}
        onOpenShortcuts={() => {
          // One panel at a time. Settings is where the reference is
          // *discovered*; `?` is how it is reached once you know it exists.
          setSettingsOpen(false);
          setShortcutsOpen(true);
        }}
      />

      <AnimatePresence>
        {openArtifact && state.artifacts[openArtifact] && sessionId && (
          <ArtifactPanel
            key={openArtifact}
            artifact={state.artifacts[openArtifact]}
            sessionId={sessionId}
            token={token}
            onClose={() => setOpenArtifact(null)}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {projectPanel && (
          <ProjectPanel
            key={projectPanel}
            projectId={projectPanel}
            projects={projects}
            token={token}
            onClose={() => setProjectPanel(null)}
            onOpenSession={(id) => {
              setSessionId(id);
              setFlyoutOpen(false);
            }}
          />
        )}
      </AnimatePresence>

      <ShortcutsDialog
        open={shortcutsOpen}
        onClose={() => setShortcutsOpen(false)}
        section={section}
      />

      {/* Only after an explicit sign-out — never merely because nobody has
          signed in. See `lib/useAuth` for why those are different questions.
          The anonymous escape is offered exactly when the backend will accept
          an anonymous caller, so a deployment with REQUIRE_AUTH=1 gets a wall
          and a local one does not. */}
      {auth.ready && auth.signedOut && (
        <AuthScreen
          onContinueAnonymous={
            config?.require_auth ? undefined : auth.continueAnonymously
          }
        />
      )}

      {/* Model switches announce themselves here rather than on the thread.
          Mounted at the layout root so the one card is shared by every
          section and outlives the panel that caused it. */}
      <ToastHost toast={state.toast} onDismiss={dismissToast} />

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={commands}
        files={showCodePanels ? state.tree : []}
        treeRoot={state.treeRoot}
        onSelectFile={selectFile}
      />

      <RenameDialog
        open={renameOpen}
        title={renameRow?.title ?? ""}
        description={renameRow?.description ?? ""}
        busy={renameBusy}
        generating={renameGenerating}
        error={renameError}
        onGenerate={generateName}
        onSave={saveName}
        onClose={() => setRenameOpen(false)}
      />

      {/* Scrim for anything floating: the <sm rail, and the sessions flyout. */}
      <AnimatePresence>
        {(railOpen || flyoutOpen) && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={motionOK ? { duration: 0.22 } : { duration: 0 }}
            onClick={() => {
              setRailOpen(false);
              setFlyoutOpen(false);
            }}
            className={cn(
              "fixed inset-0 z-30 bg-black/50 backdrop-blur-[2px]",
              !railOpen && "hidden sm:block",
            )}
          />
        )}
      </AnimatePresence>

      {/* ------------------------------------------------------------- rail */}
      <nav
        aria-label="Primary"
        className={cn(
          "z-40 flex w-[72px] shrink-0 flex-col items-center gap-2 border-r border-line px-2.5 py-3",
          "bg-surface backdrop-blur-xl transition-transform duration-250 ease-out",
          "fixed inset-y-0 left-0 sm:static sm:translate-x-0",
          railOpen ? "translate-x-0 shadow-lift" : "-translate-x-full",
        )}
      >
        <Logo />

        {!ownsSurface && (
          <RailButton label="New session" onClick={newSession}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </RailButton>
        )}

        <div className="my-1 h-px w-7 bg-line" />

        <SectionNav active={section} collapsed onSelect={selectSection} />

        {!ownsSurface && (
          <>
            <div className="my-1 h-px w-7 bg-line" />

            <RailButton
              label="Sessions"
              active={flyoutOpen}
              onClick={() => setFlyoutOpen((o) => !o)}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 6h16M4 12h16M4 18h10" />
              </svg>
            </RailButton>
          </>
        )}

        <div className="mt-auto w-full">
          <AuthPanel
            auth={auth}
            onOpenSettings={() => setSettingsOpen(true)}
            rail
          />
        </div>
      </nav>

      {/* --------------------------------------------------------- flyout */}
      <AnimatePresence>
        {flyoutOpen && (
          <motion.aside
            key="flyout"
            initial={motionOK ? { opacity: 0, x: -16 } : { opacity: 0 }}
            animate={{ opacity: 1, x: 0 }}
            exit={motionOK ? { opacity: 0, x: -16 } : { opacity: 0 }}
            transition={motionOK ? SPRING_SNAP : { duration: 0 }}
            // On a phone this took a fixed 272px and left a useless 39px
            // strip of conversation beside it. It now fills the width the rail
            // is not using, and keeps its measured column from `sm` up.
            className="glass fixed inset-y-3 left-[80px] right-3 z-40 overflow-hidden rounded-panel
                       sm:right-auto sm:w-[272px]"
          >
            <SessionSidebar
              sessions={visibleSessions}
              activeId={sessionId}
              view={shelf}
              onView={selectShelf}
              onSelectSession={selectSession}
              onNewSession={newSession}
              onSessionAction={sessionAction}
              onClose={() => setFlyoutOpen(false)}
              token={token}
              section={convSection}
              projects={projects}
              signedIn={auth.signedIn}
              onOpenProject={setProjectPanel}
              onMoveToProject={moveSessionToProject}
            />
          </motion.aside>
        )}
      </AnimatePresence>

      {/* ----------------------------------------------------- conversation */}
      <div className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 px-5 sm:px-7">
          <button
            type="button"
            onClick={() => setRailOpen(true)}
            aria-label="Open navigation"
            className="grid h-11 w-11 place-items-center rounded-ctl text-ink-muted
                       transition-colors duration-200 hover:bg-elevated hover:text-ink sm:hidden"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>

          <div className="flex items-center gap-2.5">
            <span className="sigil h-2 w-2 bg-accent" aria-hidden />
            <span className="voice-label text-ink-muted">
              {SECTION_META[section].name}
            </span>
          </div>

          {/* No model control here by design — it lives inside the composer. */}
          <div className="ml-auto flex items-center gap-3">
            {/* Both read from the *general* session's socket, which an agent
                conversation is not — it runs on its own. Showing them here
                would report an idle status and a zero cost beside a specialist
                that is mid-run. The agent's own header carries its status and
                its credit spend instead. */}
            {!ownsSurface && (
              <StatusIndicator
                status={state.status}
                iterations={state.iterations}
                usage={state.usage}
              />
            )}

            {!ownsSurface && (
              <button
                type="button"
                onClick={() => setPaletteOpen(true)}
                aria-label="Open the command palette"
                title="Command palette"
                className="hidden h-[30px] items-center gap-2 rounded-ctl border border-line
                           bg-elevated pl-2.5 pr-1.5 text-2xs text-ink-faint transition-all
                           duration-200 hover:border-line-strong hover:text-ink-muted sm:flex"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
                  <circle cx="11" cy="11" r="6.5" />
                  <path d="m16 16 4 4" />
                </svg>
                <kbd className="rounded-[5px] bg-raised px-1.5 py-0.5 font-mono text-[0.5625rem] text-ink-faint">
                  {isApplePlatform() ? "⌘K" : "Ctrl K"}
                </kbd>
              </button>
            )}

            {showCodePanels && (
              <ExportButton busy={exporting} onClick={exportZip} />
            )}

            {showCodePanels && (
              <button
                type="button"
                onClick={() => setContextOpen((o) => !o)}
                aria-label="Toggle context panel"
                className={cn(
                  "grid h-[34px] w-[34px] touch:h-11 touch:w-11 place-items-center rounded-ctl text-ink-faint",
                  "transition-colors duration-200 hover:bg-elevated hover:text-ink xl:hidden",
                  contextOpen && "bg-elevated text-ink",
                )}
              >
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round">
                  <rect x="3" y="4" width="18" height="16" rx="3" />
                  <path d="M15 4v16" />
                </svg>
              </button>
            )}
          </div>
        </header>

        {config && <SetupBanner config={config} />}

        {/* The other half of the intro hand-off. While the veil is up this is
            held at zero; as it lifts, the interface fades in behind it on a
            slightly longer curve with a small delay, so the two cross rather
            than swap. Outside the Code intro this is a no-op at opacity 1. */}
        <motion.div
          className="min-h-0 flex-1"
          animate={{ opacity: introPlaying ? 0 : 1 }}
          transition={
            motionOK
              ? {
                  duration: introPlaying ? 0 : 0.55,
                  ease: [0.2, 0, 0, 1],
                  delay: introPlaying ? 0 : 0.12,
                }
              : { duration: 0 }
          }
        >
          {isLearn && <LearnSection token={token} />}

          {isAgents && (
            <AgentSection
              token={token}
              config={config}
              models={config?.models ?? []}
            />
          )}

          {!ownsSurface && sessionId && (
            <ChatPanel
              sessionId={sessionId}
              token={token}
              section={section}
              userName={USER_NAME}
              state={state}
              busy={busy}
              onSend={send}
              onCancel={cancel}
              onEditMessage={editMessage}
              onRegenerate={regenerate}
              onSwitchBranch={switchBranch}
              models={config?.models ?? []}
              modelChoice={modelChoice[section]}
              autoTargetId={autoTargetId}
              taskRouted={taskRouted}
              onSelectModel={chooseModel}
              onImported={onImported}
              onOpenArtifact={setOpenArtifact}
            />
          )}

          {!ownsSurface && !sessionId && <BootMark />}
        </motion.div>
      </div>

      {/* -------------------------------------------------- context column */}
      {/* Docked. Recessed in tone and blur so the conversation floats over it. */}
      {showCodePanels && (
        <aside className="glass-recessed hidden w-[clamp(360px,31vw,520px)] shrink-0 border-l xl:block">
          <ContextColumn {...contextProps} />
        </aside>
      )}

      {/* Floating, below xl. Same component, same state — it only changes how
          it is attached to the page. */}
      <AnimatePresence>
        {showCodePanels && contextOpen && !docked && (
          <>
            <motion.div
              key="ctx-scrim"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={motionOK ? { duration: 0.2 } : { duration: 0 }}
              onClick={closeContext}
              className="fixed inset-0 z-30 bg-black/45 backdrop-blur-[2px]"
            />
            <motion.aside
              key="ctx-sheet"
              initial={motionOK ? { opacity: 0, x: 24 } : { opacity: 0 }}
              animate={{ opacity: 1, x: 0 }}
              exit={motionOK ? { opacity: 0, x: 24 } : { opacity: 0 }}
              transition={motionOK ? SPRING_SOFT : { duration: 0 }}
              className="glass fixed inset-y-3 right-3 z-40 w-[min(440px,calc(100vw-2rem))]
                         overflow-hidden rounded-panel"
            >
              <ContextColumn {...contextProps} onDismiss={closeContext} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

/**
 * The mark, at the top of the rail.
 *
 * This was an accent-gradient rhombus — the same shape the trace spine's nodes
 * wear — which made the brand mark a *derivative* of the section accent and so
 * a different colour in each of the four sections. A logo that changes colour
 * when you change tabs is not a logo. The rhombus keeps its job on the spine,
 * where it means "a step"; the brand now has its own constant mark.
 *
 * `alt` rather than an `aria-label` on the wrapper: the accessible name
 * belongs on the image, and a labelled `span` needs an explicit `role` to be
 * announced at all.
 */
function Logo() {
  return (
    <span className="mb-1 grid h-9 w-9 shrink-0 place-items-center">
      <LoomMark size={32} alt="Loom" />
    </span>
  );
}

/**
 * The gap before a session id exists.
 *
 * `sessionId` is assigned in a mount effect, so this is normally one frame and
 * the right thing to show is nothing — a mark that flashes for 16ms is worse
 * than an empty box. The 350ms delay on the fade is what buys that: a fast
 * boot unmounts this before it has begun to appear, and only a boot that
 * actually makes someone wait ever renders a visible mark.
 */
function BootMark() {
  return (
    <div className="grid h-full place-items-center">
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 0.55 }}
        transition={{ duration: 0.35, delay: 0.35, ease: "easeOut" }}
      >
        <LoomMark size={44} />
      </motion.div>
    </div>
  );
}

/**
 * Download the sandbox project.
 *
 * Sits in the header rather than in the context column because it is about the
 * *session*, not about whichever panel happens to be showing. The busy state is
 * the same restrained mark used everywhere else — a pulsing sigil, not a
 * spinner — and the button stays in place rather than being replaced, so the
 * pointer does not lose it mid-export.
 */
function ExportButton({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  const motionOK = useMotionOK();
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-label="Download the project as a zip"
      title="Download project (.zip)"
      className={cn(
        "grid h-[34px] w-[34px] touch:h-11 touch:w-11 place-items-center rounded-ctl text-ink-faint",
        "transition-colors duration-200 hover:bg-elevated hover:text-ink",
        "disabled:pointer-events-none",
      )}
    >
      {busy ? (
        <motion.span
          className="sigil h-2.5 w-2.5 bg-accent"
          animate={motionOK ? { opacity: [0.35, 1, 0.35] } : { opacity: 1 }}
          transition={
            motionOK
              ? { duration: 1.4, repeat: Infinity, ease: "easeInOut" }
              : { duration: 0 }
          }
          aria-hidden
        />
      ) : (
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 4v11" />
          <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
          <path d="M5 19h14" />
        </svg>
      )}
    </button>
  );
}

/**
 * Sections that own their whole content area and run their own sessions.
 *
 * The global shortcut layer is scoped out of both: every one of its bindings
 * (new session, terminal, context panel, session flyout) acts on the general
 * session this component holds, which neither a notebook nor an agent
 * conversation is. A shortcut that appears to work and quietly targets
 * something else is worse than one that does nothing.
 */
function ownsOwnSurface(section: Section): boolean {
  return section === "learning" || section === "agents";
}

/**
 * Whether to label shortcuts with ⌘ or Ctrl.
 *
 * `navigator.platform` is deprecated but is the only signal available
 * synchronously in every browser we support; `userAgentData` is Chromium-only.
 * Getting this wrong costs a wrong glyph in a tooltip, so the cheap check wins.
 */
function isApplePlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);
}

function RailButton({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "grid h-10 w-10 shrink-0 place-items-center rounded-ctl transition-all duration-200",
        "touch:h-11 touch:w-11",
        "active:scale-[0.94]",
        active
          ? "bg-raised text-ink"
          : "text-ink-muted hover:bg-elevated hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

/**
 * What an unset provider key actually costs the user, in the order it matters.
 *
 * This once said "copy .env.example and restart" for a missing key, which read
 * as "the app is broken" when it is not: the backend resolves past an unkeyed
 * model to one it can run, so a missing key costs you those models and nothing
 * else. Naming the consequence rather than the remedy keeps the banner honest
 * — and lets a genuinely disabling absence (no sandbox, no web search) say so
 * in the same place.
 *
 * `OPENCODE_API_KEY` leads because it is the one whose absence changes how the
 * app behaves rather than just shortening a list: it holds the default model
 * *and* the whole Auto pool, so without it Auto stops routing by task and
 * degrades to the per-section table.
 */
function SetupBanner({ config }: { config: BackendConfig }) {
  const fallback =
    config.models.find((m) => m.id === config.default_model_id)?.name ??
    config.default_model_id;

  const notes = [
    !config.opencode &&
      `Task-based Auto routing is off (OPENCODE_API_KEY unset) — sessions use ${fallback} and Auto falls back to per-section routing.`,
    !config.e2b && "The Code sandbox is unavailable (E2B_API_KEY unset).",
    !config.exa && "Web search is unavailable (EXA_API_KEY unset).",
  ].filter(Boolean) as string[];

  if (notes.length === 0) return null;

  return (
    <div className="mx-5 mb-1 shrink-0 rounded-ctl border border-warn/25 bg-[rgba(240,181,74,0.08)] sm:mx-7">
      <p className="px-3.5 py-2 text-xs text-warn">
        {notes.join(" ")} Add the key to{" "}
        <code className="font-mono">backend/.env</code> and restart the server to
        enable it.
      </p>
    </div>
  );
}
