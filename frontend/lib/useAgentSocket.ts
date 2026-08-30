"use client";

/**
 * The websocket -> UI state machine.
 *
 * Everything the interface renders (and every animation it plays) is derived
 * here from the event stream defined in `lib/events.ts`. Components stay dumb:
 * they read state and animate transitions, they never talk to the socket
 * except through the actions this hook returns.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import type {
  AgentToolMeta,
  FileNode,
  GitChange,
  GitCommit,
  BranchGroup,
  SearchResult,
  ServerEvent,
} from "./events";
import { fetchHistory, fetchWorkspaceTree, socketUrl } from "./api";
import { agentSocketUrl, fetchAgentHistory } from "./agents";
import { itemsFromHistory, usageFromHistory } from "./history";

export type AgentStatus =
  | "connecting"
  | "offline"
  | "idle"
  | "thinking"
  | "streaming"
  | "executing"
  | "error";

export type ToolStatus = "running" | "ok" | "error";

export type ChatItem =
  | { kind: "user"; id: string; text: string; files: string[] }
  | {
      kind: "assistant";
      id: string;
      thinking: string;
      text: string;
      streaming: boolean;
      thinkingActive: boolean;
    }
  | {
      kind: "tool";
      id: string;
      callId: string;
      tool: string;
      input: Record<string, unknown>;
      status: ToolStatus;
      output?: string;
      exitCode?: number;
      results?: SearchResult[];
      /**
       * Agents section: the structured half of a tool result — a generated
       * image, a ranked citation list. Rendered beside the tool card rather
       * than inside it, so `ToolCallCard` stays the same component the Chat
       * and Code sections use.
       */
      artifact?: unknown;
      /**
       * The tool could not run because its API key is unset. A distinct state
       * from `error`: nothing failed, the integration is simply absent, and
       * the card says so instead of showing a red exit code.
       */
      notConfigured?: boolean;
      startedAt: number;
      endedAt?: number;
    }
  /** Something went wrong or nearly did. Routing notes are not notices — they
   *  are session facts, and they go to the toast; see `ModelToast`. */
  | { kind: "notice"; id: string; level: "error" | "warn"; text: string }
  /**
   * Agents section. A paused run awaiting a human decision.
   *
   * Inline in the transcript rather than in a modal: the pause happened at a
   * point in the conversation, and a dialog over the whole screen would
   * detach the decision from the reasoning that led to it. `status` is
   * "waiting" until an `agent_resumed` frame settles it, which is also what
   * disables the buttons — so a decision cannot be sent twice.
   */
  | {
      kind: "approval";
      id: string;
      approvalId: string;
      action: string;
      summary: string;
      parameters: Record<string, unknown>;
      risk: "low" | "medium" | "high";
      editable: string[];
      options: Array<{ id: string; label: string; detail: string }>;
      costNote: string;
      timeoutSeconds: number;
      status: "waiting" | "settled";
      decision?: string;
    }
  /**
   * Agents section. A routing directive from Agent 8, offered as a button.
   * Nothing happens until the user clicks — that is the human-in-the-loop
   * requirement, and it lives in the fact that this is only ever a card.
   */
  | {
      kind: "handoff";
      id: string;
      fromAgent: string;
      nextAgent: string;
      nextAgentName: string;
      reason: string;
      context: string;
    };

/** Which specialist a session belongs to, announced on connect. */
export type AgentMetaState = {
  agentId: string;
  name: string;
  role: string;
  icon: string;
  accent: string;
  tools: AgentToolMeta[];
};

export type CreditsState = {
  balance: number;
  spentThisTurn: number;
  enabled: boolean;
};

export type TerminalLine = {
  id: string;
  callId: string;
  stream: "stdout" | "stderr" | "meta";
  text: string;
};

/**
 * A model switch, on its way to the toast.
 *
 * This used to be pushed into the transcript as a system note. It is not a
 * step the agent took, though — it is a change to the session, and putting it
 * on the thread meant a routing decision permanently interrupted the reading
 * order of the conversation it was made for. It now surfaces as a toast that
 * shows itself and leaves. Every announcement carries a fresh `id`, which is
 * what lets the toast re-arm its timer when switches arrive back to back.
 */
export type ModelToast = {
  id: string;
  text: string;
  /**
   * `route` is the auto-router's voice; `manual` is the user's own pick;
   * `fallback` is neither — the chosen model failed and the router finished
   * the request somewhere else.
   */
  tone: "route" | "manual" | "fallback" | "error";
};

export type ChangedFile = {
  path: string;
  diff: string;
  content: string;
  change: "created" | "modified";
  at: number;
};

/**
 * The live preview, as a small state machine.
 *
 * `status` is deliberately separate from `error`: a dev server that fails to
 * compile is still *live* — it serves the last good build, or its own error
 * overlay — so the iframe stays mounted and the message is raised over it.
 * Only a `fatal` failure moves the status to `stopped`.
 *
 *   idle ──start_dev_server──▶ starting ──preview_ready──▶ live
 *                                  │                        │
 *                                  └──── preview_error(fatal) ──▶ stopped
 */
export type PreviewStatus = "idle" | "starting" | "live" | "stopped";

export type PreviewState = {
  status: PreviewStatus;
  url: string | null;
  port: number | null;
  command: string;
  /** Build/runtime error text, or the reason the server went down. */
  error: string | null;
  /**
   * Bumped every time the iframe should reload. The panel keys its `<iframe>`
   * off this, which is the only reliable cross-origin way to force a reload —
   * we cannot reach into `contentWindow.location` on E2B's domain.
   */
  reloadKey: number;
  /**
   * Incremented on every `file_changed` while the preview is live. The panel
   * debounces off this rather than reloading per event: a single agent turn
   * can write a dozen files, and reloading on each one is a strobe.
   */
  changeSignal: number;
};

const idlePreview: PreviewState = {
  status: "idle",
  url: null,
  port: null,
  command: "",
  error: null,
  reloadKey: 0,
  changeSignal: 0,
};

/**
 * The session's git repository, as the History panel shows it.
 *
 * Pushed from the server on `git_state` rather than polled: any commit — the
 * agent's tool call or the panel's own button — funnels through the same
 * event, so the panel cannot drift from what the repository actually says.
 * `null` means we have not been told yet, which reads differently from
 * `{repo: false}` ("told, and there is no repository").
 */
export type GitState = {
  repo: boolean;
  branch: string;
  status: GitChange[];
  log: GitCommit[];
  path: string;
} | null;

export type AgentState = {
  status: AgentStatus;
  connected: boolean;
  items: ChatItem[];
  terminal: TerminalLine[];
  terminalBusy: boolean;
  tree: FileNode[];
  treeRoot: string;
  /** Repository state, or null until the server has told us. */
  git: GitState;
  changed: Record<string, ChangedFile>;
  /**
   * Files opened for reading that the agent never wrote — from the file tree or
   * the command palette. Kept apart from `changed` so "what the agent touched"
   * stays an honest list; the panel reads through to this when a path is not in
   * `changed`, which is what makes every file in the tree openable.
   */
  viewed: Record<string, ChangedFile>;
  /** path -> timestamp, drives the file-tree flash animation. */
  flash: Record<string, number>;
  activeFile: string | null;
  usage: { input: number; output: number; cost: number };
  iterations: number;
  lastError: string | null;
  modelId: string;
  modelName: string;
  modelSupportsTools: boolean;
  modelAvailable: boolean;
  /** Suppresses the "Switched to …" notice for the first announcement after
   *  (re)connecting — that is the session's existing model, not a switch. */
  modelAnnounced: boolean;
  /** How the current model was chosen. */
  routingMode: "manual" | "auto";
  /** In auto mode, the classifier hint behind the current model. */
  routingHint: string;
  /** The pending model-switch announcement, if one has not been shown out. */
  toast: ModelToast | null;
  /** The live preview of the sandbox's dev server. */
  preview: PreviewState;
  /**
   * Agents section only; null in Chat, Learn and Code, which never receive
   * `agent_meta`. Everything below is inert on those sockets.
   */
  agent: AgentMetaState | null;
  credits: CreditsState | null;
  /** True while a run is suspended on an approval, so the composer can say so. */
  awaitingApproval: boolean;
  /**
   * Which user turns have been edited, and what versions each has.
   *
   * Keyed by *user-turn ordinal* rather than by item id, which is the one
   * decision worth understanding here: the transcript is replayed from the
   * server's checkpoint and carries no stable per-message identity, so the
   * only coordinate both sides can agree on across a reload is "the nth
   * message you sent". `items.filter(i => i.kind === "user")` produces exactly
   * that ordering, and the backend's `user_turn_positions` produces the same
   * one from the other end.
   */
  branches: BranchGroup[];
};

const initialState: AgentState = {
  status: "connecting",
  connected: false,
  items: [],
  terminal: [],
  terminalBusy: false,
  tree: [],
  treeRoot: "/home/user",
  git: null,
  changed: {},
  viewed: {},
  flash: {},
  activeFile: null,
  usage: { input: 0, output: 0, cost: 0 },
  iterations: 0,
  lastError: null,
  modelId: "qwen3_7_plus",
  modelName: "Qwen 3.7 Plus",
  modelSupportsTools: true,
  modelAvailable: true,
  modelAnnounced: false,
  routingMode: "manual",
  routingHint: "",
  toast: null,
  preview: idlePreview,
  agent: null,
  credits: null,
  awaitingApproval: false,
  branches: [],
};

type Action =
  | { t: "event"; e: ServerEvent }
  | { t: "socket"; open: boolean }
  | { t: "sent"; text: string; files: string[] }
  | { t: "openFile"; path: string | null }
  /** A file read straight from the sandbox for viewing/editing. */
  | { t: "openContent"; file: ChangedFile }
  | { t: "clearTerminal" }
  | { t: "dismissToast"; id: string }
  | { t: "applyGitState"; git: NonNullable<GitState> }
  | { t: "reloadPreview" }
  | { t: "dismissPreviewError" }
  /** A file the user saved from the inline editor, folded into agent state. */
  | { t: "localEdit"; file: ChangedFile }
  /**
   * The checkpointed transcript.
   *
   * `replace` separates the two callers, which want opposite things. The
   * connect-time replay must *not* overwrite a conversation that is already on
   * screen — it can land after the user has started typing, and clobbering
   * live state with a stale read is the bug the guard in the reducer exists
   * for. A deliberate re-read after a branch switch is the whole point of the
   * call and has to win.
   */
  | {
      t: "hydrate";
      items: ChatItem[];
      usage: { input: number; output: number; cost: number };
      iterations: number;
      replace?: boolean;
    }
  /** A tree fetched over HTTP after the user changed the filesystem. */
  | { t: "applyTree"; path: string; nodes: FileNode[] }
  /** A path the user renamed (`to`) or deleted (`to: null`) in the tree. */
  | { t: "pathMoved"; from: string; to: string | null }
  | { t: "reset"; state?: Partial<AgentState> };

let seq = 0;
const uid = (p: string) => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

/** Find the assistant bubble currently being written to, if any. */
function openAssistant(items: ChatItem[]): ChatItem | null {
  const last = items[items.length - 1];
  return last && last.kind === "assistant" && last.streaming ? last : null;
}

function withOpenAssistant(items: ChatItem[]): ChatItem[] {
  if (openAssistant(items)) return items;
  return [
    ...items,
    {
      kind: "assistant",
      id: uid("a"),
      thinking: "",
      text: "",
      streaming: true,
      thinkingActive: false,
    },
  ];
}

function patchLast(items: ChatItem[], fn: (i: ChatItem) => ChatItem): ChatItem[] {
  const copy = items.slice();
  copy[copy.length - 1] = fn(copy[copy.length - 1]);
  return copy;
}

/**
 * Everything before the `turnIndex`-th user message.
 *
 * The ordinal is counted over user rows only, which is the same thing the
 * backend counts (`repository.user_turn_positions`) — a `tool_result` carrier
 * is a "user" message in the wire format but not one anybody typed. Counting
 * differently on the two sides is how a switcher ends up pointing at the wrong
 * turn the moment a conversation uses a tool.
 */
function truncateToUserTurn(items: ChatItem[], turnIndex: number): ChatItem[] {
  let seen = 0;
  for (let i = 0; i < items.length; i++) {
    if (items[i].kind !== "user") continue;
    if (seen === turnIndex) return items.slice(0, i);
    seen++;
  }
  return items;
}

function closeStreaming(items: ChatItem[]): ChatItem[] {
  const last = items[items.length - 1];
  if (last && last.kind === "assistant" && last.streaming) {
    return patchLast(items, (i) =>
      i.kind === "assistant" ? { ...i, streaming: false, thinkingActive: false } : i,
    );
  }
  return items;
}

function reducer(state: AgentState, action: Action): AgentState {
  switch (action.t) {
    case "socket":
      return {
        ...state,
        connected: action.open,
        status: action.open ? (state.status === "connecting" ? "idle" : state.status) : "offline",
        modelAnnounced: action.open ? false : state.modelAnnounced,
      };

    case "sent":
      return {
        ...state,
        status: "thinking",
        lastError: null,
        // "Spent this turn" has to mean *this* turn, so the counter resets
        // when a new one starts rather than accumulating across the session.
        credits: state.credits ? { ...state.credits, spentThisTurn: 0 } : null,
        items: [
          ...closeStreaming(state.items),
          { kind: "user", id: uid("u"), text: action.text, files: action.files },
        ],
      };

    case "openFile":
      return { ...state, activeFile: action.path };

    case "openContent":
      return {
        ...state,
        viewed: { ...state.viewed, [action.file.path]: action.file },
        activeFile: action.file.path,
      };

    case "clearTerminal":
      return { ...state, terminal: [] };

    // Id-guarded: a toast that has already been replaced by a newer switch
    // must not be dismissed by the outgoing one's timer.
    case "dismissToast":
      return state.toast?.id === action.id ? { ...state, toast: null } : state;

    // A REST hydration, not a socket frame: same shape, same slot, so a
    // reconnect and a push cannot disagree about repository state.
    case "applyGitState":
      return { ...state, git: action.git };

    case "reloadPreview":
      return {
        ...state,
        preview: { ...state.preview, reloadKey: state.preview.reloadKey + 1 },
      };

    case "dismissPreviewError":
      return { ...state, preview: { ...state.preview, error: null } };

    // A manual save is indistinguishable from an agent edit once it has landed
    // — same sandbox, same file — so it enters the same three pieces of state
    // rather than a parallel set the diff panel would then have to merge.
    case "localEdit":
      return {
        ...state,
        changed: { ...state.changed, [action.file.path]: action.file },
        viewed: { ...state.viewed, [action.file.path]: action.file },
        flash: { ...state.flash, [action.file.path]: action.file.at },
        activeFile: action.file.path,
        preview:
          state.preview.status === "live"
            ? { ...state.preview, changeSignal: state.preview.changeSignal + 1 }
            : state.preview,
      };

    // Late and unconditionally losing: the fetch races the socket, and if the
    // user has already sent something (or the backend replayed anything) the
    // live thread is the newer truth. Dropping the hydration is always safe;
    // overwriting a live turn would not be.
    case "hydrate":
      if (!action.replace && (state.items.length || !action.items.length)) {
        return state;
      }
      return {
        ...state,
        items: action.items,
        usage: action.usage,
        iterations: action.iterations,
      };

    case "applyTree":
      return { ...state, tree: action.nodes, treeRoot: action.path };

    // Renaming or deleting in the tree moves the file the rest of the UI is
    // keyed by. Without this the changed list, the diff panel and the flash map
    // would all keep pointing at a path that no longer exists — and the diff
    // panel would happily save an edit back to it, recreating the file the user
    // just deleted. Prefix-matched, so a renamed folder carries its files.
    case "pathMoved": {
      const moves = (record: Record<string, ChangedFile>) => {
        const next: Record<string, ChangedFile> = {};
        for (const [path, file] of Object.entries(record)) {
          const moved = movePath(path, action.from, action.to);
          if (moved) next[moved] = { ...file, path: moved };
        }
        return next;
      };
      const flash: Record<string, number> = {};
      for (const [path, at] of Object.entries(state.flash)) {
        const moved = movePath(path, action.from, action.to);
        if (moved) flash[moved] = at;
      }
      return {
        ...state,
        changed: moves(state.changed),
        viewed: moves(state.viewed),
        flash,
        activeFile: state.activeFile
          ? movePath(state.activeFile, action.from, action.to)
          : null,
      };
    }

    case "reset":
      return { ...initialState, ...action.state, connected: state.connected };

    case "event":
      return applyEvent(state, action.e);
  }
}

/**
 * Where `path` ends up when `from` is renamed to `to`, or null when `to` is
 * null (a delete) or when `path` was inside the thing that went away.
 * Untouched paths come back unchanged.
 */
function movePath(path: string, from: string, to: string | null): string | null {
  const inside = path === from || path.startsWith(from + "/");
  if (!inside) return path;
  if (to === null) return null;
  return to + path.slice(from.length);
}

function applyEvent(state: AgentState, e: ServerEvent): AgentState {
  switch (e.type) {
    case "connected":
      return { ...state, connected: true, status: "idle" };

    case "agent_thinking_start": {
      const items = withOpenAssistant(state.items);
      return {
        ...state,
        status: "thinking",
        items: patchLast(items, (i) =>
          i.kind === "assistant" ? { ...i, thinkingActive: true } : i,
        ),
      };
    }

    case "agent_thinking_delta": {
      const items = withOpenAssistant(state.items);
      return {
        ...state,
        status: "thinking",
        items: patchLast(items, (i) =>
          i.kind === "assistant" ? { ...i, thinking: i.thinking + e.content } : i,
        ),
      };
    }

    case "agent_thinking_end":
      return {
        ...state,
        items: patchLast(state.items, (i) =>
          i.kind === "assistant" ? { ...i, thinkingActive: false } : i,
        ),
      };

    case "agent_message_start":
      return { ...state, status: "streaming", items: withOpenAssistant(state.items) };

    case "agent_token": {
      const items = withOpenAssistant(state.items);
      return {
        ...state,
        status: "streaming",
        items: patchLast(items, (i) =>
          i.kind === "assistant" ? { ...i, text: i.text + e.content } : i,
        ),
      };
    }

    case "agent_message_end":
      return state;

    case "tool_call_start": {
      // Close the assistant bubble so the tool card lands *after* the prose
      // it was introduced by, and the next tokens start a fresh bubble.
      const items = closeStreaming(state.items);
      const isBash = e.tool === "bash_execute";
      // The preview's loading state starts here rather than waiting for an
      // event of its own: `preview_ready` only fires once the port answers,
      // which for a cold `next dev` is most of a minute away.
      const starting = e.tool === "start_dev_server";
      return {
        ...state,
        preview: starting
          ? {
              ...state.preview,
              status: "starting",
              error: null,
              command: String(e.input.command ?? state.preview.command),
              port: Number(e.input.port) || state.preview.port,
            }
          : state.preview,
        status: "executing",
        items: [
          ...items,
          {
            kind: "tool",
            id: uid("t"),
            callId: e.call_id,
            tool: e.tool,
            input: e.input,
            status: "running",
            startedAt: e.ts,
          },
        ],
        terminalBusy: isBash ? true : state.terminalBusy,
        terminal: isBash
          ? [
              ...state.terminal,
              {
                id: uid("l"),
                callId: e.call_id,
                stream: "meta",
                text: `$ ${String(e.input.command ?? "")}`,
              },
            ]
          : state.terminal,
      };
    }

    case "tool_output_chunk": {
      const lines = e.content.split("\n");
      // A chunk may not end on a newline; keep empty trailing pieces out.
      const additions: TerminalLine[] = lines
        .filter((l, idx) => l.length > 0 || idx < lines.length - 1)
        .map((text) => ({ id: uid("l"), callId: e.call_id, stream: e.stream, text }));
      return { ...state, terminal: [...state.terminal, ...additions].slice(-2000) };
    }

    case "tool_call_result": {
      const call = state.items.find(
        (i) => i.kind === "tool" && i.callId === e.call_id,
      ) as Extract<ChatItem, { kind: "tool" }> | undefined;
      const isBash = call?.tool === "bash_execute";
      // A dev server that failed to come up produces a failed tool result, not
      // a `preview_error` — there is no preview to have gone wrong yet. Without
      // this the panel would spin on "starting" until the session ended.
      const startFailed = call?.tool === "start_dev_server" && !e.success;
      return {
        ...state,
        preview: startFailed
          ? { ...state.preview, status: "stopped", url: null, error: e.output }
          : state.preview,
        status: "thinking",
        terminalBusy: isBash ? false : state.terminalBusy,
        terminal:
          isBash && e.meta?.exit_code !== undefined
            ? [
                ...state.terminal,
                {
                  id: uid("l"),
                  callId: e.call_id,
                  stream: e.success ? "meta" : "stderr",
                  text: `[exit ${e.meta.exit_code}]`,
                },
              ]
            : state.terminal,
        items: state.items.map((i) =>
          i.kind === "tool" && i.callId === e.call_id
            ? {
                ...i,
                status: e.success ? "ok" : "error",
                output: e.output,
                exitCode: e.meta?.exit_code,
                results: e.meta?.results,
                // Both are Agents-only: the Chat/Code tools never set either
                // key, so these stay undefined there.
                artifact: (e.meta as { artifact?: unknown } | undefined)?.artifact,
                notConfigured: Boolean(
                  (e.meta as { not_configured?: boolean } | undefined)?.not_configured,
                ),
                endedAt: e.ts,
              }
            : i,
        ),
      };
    }

    case "file_changed": {
      const file: ChangedFile = {
        path: e.path,
        diff: e.diff,
        content: e.content,
        change: e.change,
        at: e.ts,
      };
      return {
        ...state,
        changed: { ...state.changed, [e.path]: file },
        // If the user had this file open for reading, the agent's version
        // supersedes it — otherwise the editor would keep showing a copy that
        // is now stale, and saving it would silently undo the agent's work.
        viewed: state.viewed[e.path]
          ? { ...state.viewed, [e.path]: file }
          : state.viewed,
        flash: { ...state.flash, [e.path]: e.ts },
        // Touching a file opens the diff panel on it — the "right panel opens
        // when a file is touched" behaviour.
        activeFile: e.path,
        // Nudge the preview. The panel debounces the actual reload; this only
        // records that the site behind the iframe has changed.
        preview:
          state.preview.status === "live"
            ? { ...state.preview, changeSignal: state.preview.changeSignal + 1 }
            : state.preview,
      };
    }

    case "preview_ready":
      return {
        ...state,
        preview: {
          status: "live",
          url: e.url,
          port: e.port,
          command: e.command ?? "",
          error: null,
          // A fresh URL is a fresh iframe; the counter carries on so a reload
          // requested during startup is not silently swallowed.
          reloadKey: state.preview.reloadKey + 1,
          changeSignal: state.preview.changeSignal,
        },
      };

    case "preview_error":
      return {
        ...state,
        preview: {
          ...state.preview,
          // A non-fatal error leaves the server serving — the iframe stays and
          // the message is raised over it. Only a fatal one takes the site away.
          status: e.fatal ? "stopped" : state.preview.status,
          url: e.fatal ? null : state.preview.url,
          error: e.message,
        },
      };

    case "preview_stopped":
      return {
        ...state,
        preview: {
          ...idlePreview,
          reloadKey: state.preview.reloadKey,
          changeSignal: state.preview.changeSignal,
          status:
            // "replaced" is the first half of a restart: the new server's
            // `preview_ready` is already on its way, so a stopped state in
            // between would be a flash of the wrong answer.
            e.reason === "replaced"
              ? "starting"
              : // "absent" is the connect-time reconcile — the server is
                // saying there is no preview for this session, which is not
                // the same as one having stopped. `idle` invites the user to
                // start one; `stopped` would imply something broke.
                e.reason === "absent"
                ? "idle"
                : "stopped",
        },
      };

    case "file_tree":
      return { ...state, tree: e.nodes, treeRoot: e.path };

    case "git_state":
      return {
        ...state,
        git: {
          repo: e.repo,
          branch: e.branch,
          status: e.status,
          log: e.log,
          path: e.path,
        },
      };

    case "usage":
      return {
        ...state,
        usage: {
          input: e.input_tokens,
          output: e.output_tokens,
          cost: e.cost_estimate,
        },
      };

    case "agent_done":
      return {
        ...state,
        status: state.status === "error" ? "error" : "idle",
        iterations: e.iterations || state.iterations,
        terminalBusy: false,
        // The run is over, so nothing is waiting on an approval any more —
        // including the case where it ended *because* one was refused. Any
        // still-open card is settled below so its buttons stop inviting a
        // click that can no longer be answered.
        awaitingApproval: false,
        items: closeStreaming(state.items).map((i) =>
          i.kind === "approval" && i.status === "waiting"
            ? { ...i, status: "settled", decision: i.decision ?? "expired" }
            : i,
        ),
      };

    case "model_changed": {
      const auto = e.routing_mode === "auto";
      // An involuntary switch. This is the one case that must announce itself
      // even when it is the session's *first* model event and even when Auto
      // is what picked the failing model: the user's request completed on a
      // model nobody chose, and silence there is worse than the error it
      // replaced.
      const fellBack = Boolean(e.fallback_from);
      // Entering Auto is not itself a routing decision — the first real one
      // comes when the next turn is classified. Announcing the sentinel would
      // read as "Routed to Auto", which names no model.
      const isModeOnly = e.model_id === "auto";
      const isSwitch =
        state.modelAnnounced && state.modelId !== e.model_id && !isModeOnly;

      // The copy is unchanged from the inline note this replaces — only where
      // it lands has moved. Auto reroutes between turns as a matter of course,
      // so its wording is informational ("here's why"); a manual switch is the
      // user's own act, reported back to them.
      //
      // "GPT-OSS 120B is unavailable — switched to Nemotron 3 Ultra."
      // `note` already carries "<failed model> <what went wrong>" from the
      // backend, so the wording stays in one place rather than being
      // reconstructed from ids the client would have to map back to names.
      const text = fellBack
        ? `${e.note || "The selected model failed"} — switched to ${e.name}.`
        : auto
          ? e.reason
            ? `Routed to ${e.name} for ${e.reason}.`
            : `Routed to ${e.name}.`
          : e.note
            ? `Switched to ${e.name} — ${e.note}`
            : `Switched to ${e.name}.`;

      // A fallback announces itself and changes nothing else. It rescued one
      // call; it did not re-pick the model. Overwriting the selection here
      // would strand the user on the stand-in — the selector would show it,
      // and the next turn would use it — after a rate limit that has very
      // likely already cleared. The backend keeps the same split (see
      // `answered_by` in agent/graph.py): the stand-in is billed for the call
      // it answered, and the session stays on what the user chose.
      if (fellBack) {
        return {
          ...state,
          toast: { id: uid("toast"), text, tone: "fallback" },
        };
      }

      return {
        ...state,
        modelId: e.model_id,
        modelName: e.name,
        modelSupportsTools: e.supports_tools,
        modelAvailable: e.available,
        modelAnnounced: true,
        routingMode: e.routing_mode ?? state.routingMode,
        routingHint: auto ? e.routing_hint ?? "" : "",
        toast: isSwitch
          ? {
              id: uid("toast"),
              text,
              tone: !e.available ? "error" : auto ? "route" : "manual",
            }
          : state.toast,
      };
    }

    // --- Agentic Loop ----------------------------------------------------
    // These types only ever arrive on /ws/agent/{id}. On the Chat and Code
    // socket they never fire, so none of this affects those sections.

    case "agent_meta":
      return {
        ...state,
        agent: {
          agentId: e.agent_id,
          name: e.name,
          role: e.role,
          icon: e.icon,
          accent: e.accent,
          tools: e.tools,
        },
        credits:
          e.credits && typeof e.credits.balance === "number"
            ? {
                balance: e.credits.balance,
                spentThisTurn: 0,
                enabled: e.credits.enabled !== false,
              }
            : state.credits,
      };

    case "agent_paused":
      return {
        ...state,
        // The run is genuinely suspended, so it is not "executing" any more.
        // Saying `idle` would be worse — the composer would re-arm while a
        // decision is still outstanding.
        awaitingApproval: true,
        items: [
          ...closeStreaming(state.items),
          {
            kind: "approval",
            id: uid("ap"),
            approvalId: e.approval_id,
            action: e.action,
            summary: e.summary,
            parameters: e.parameters,
            risk: e.risk,
            editable: e.editable ?? [],
            options: e.options ?? [],
            costNote: e.cost_note ?? "",
            timeoutSeconds: e.timeout_seconds ?? 600,
            status: "waiting",
          },
        ],
      };

    case "agent_resumed":
      return {
        ...state,
        awaitingApproval: false,
        items: state.items.map((i) =>
          i.kind === "approval" && i.approvalId === e.approval_id
            ? { ...i, status: "settled", decision: e.decision }
            : i,
        ),
      };

    case "agent_handoff":
      return {
        ...state,
        items: [
          ...closeStreaming(state.items),
          {
            kind: "handoff",
            id: uid("ho"),
            fromAgent: e.from_agent,
            nextAgent: e.next_agent,
            nextAgentName: e.next_agent_name,
            reason: e.reason,
            context: e.context ?? "",
          },
        ],
      };

    case "credits":
      return {
        ...state,
        credits: {
          balance: e.balance,
          spentThisTurn: e.spent_this_turn,
          enabled: e.enabled,
        },
      };

    // The run continues — the agent is writing up without its tools — so this
    // only adds the notice explaining why the answer stops where it does.
    // Deliberately not a status change: `agent_done` still closes the turn.
    case "tool_budget_reached":
      return {
        ...state,
        items: [
          ...closeStreaming(state.items),
          { kind: "notice", id: uid("n"), level: "warn", text: e.message },
        ],
      };

    case "max_iterations":
      return {
        ...state,
        status: "idle",
        iterations: e.iterations,
        terminalBusy: false,
        items: [
          ...closeStreaming(state.items),
          { kind: "notice", id: uid("n"), level: "warn", text: e.message },
        ],
      };

    case "branches":
      return { ...state, branches: e.branches };

    case "history_replaced": {
      // A branch switch is refetched (from the socket handler — a reducer
      // cannot go and get anything) because it replaces a suffix rather than
      // truncating one. Nothing else to do here.
      if (e.reason === "branch" || e.turn_index < 0) {
        return { ...state, lastError: null };
      }
      // An edit or a regenerate cuts the conversation at a known turn and
      // immediately re-runs it. Cutting locally rather than refetching is not
      // an optimisation — it is the only correct option. The re-run has
      // already begun on the server, so a fetch issued now reads a checkpoint
      // that is being rewritten underneath it and comes back either empty or
      // still holding the turns that were just removed. Both were observed;
      // the second is worse, because the new reply then streams on underneath
      // the old one and the thread shows the question twice.
      return {
        ...state,
        status: "thinking",
        lastError: null,
        credits: state.credits ? { ...state.credits, spentThisTurn: 0 } : null,
        items: [
          ...truncateToUserTurn(state.items, e.turn_index),
          { kind: "user", id: uid("u"), text: e.content, files: [] },
        ],
      };
    }

    case "error":
      return {
        ...state,
        status: "error",
        lastError: e.message,
        terminalBusy: false,
        items: [
          ...closeStreaming(state.items),
          { kind: "notice", id: uid("n"), level: "error", text: e.message },
        ],
      };

    default:
      return state;
  }
}

/**
 * @param agentId When set, this is an Agentic Loop session: the socket is
 *   `/ws/agent/{id}`, the transcript is replayed from the agents endpoint, and
 *   the approval / handoff / credit events become live. Omit it and the hook
 *   behaves exactly as it always has for Chat and Code — same URL, same
 *   history call, same events — which is why the three existing sections are
 *   untouched by any of this.
 */
export function useAgentSocket(
  sessionId: string | null,
  token?: string | null,
  agentId?: string | null,
  section: "chat" | "code" = "chat",
) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const socketRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const closedByUs = useRef(false);
  /**
   * How to refetch this session's transcript, set by the connect effect below.
   *
   * An edit or a branch switch rewrites the conversation on the server, and
   * the change is not expressible as a patch — it can drop any number of turns
   * off the end. So the server says "it changed" and the client goes and reads
   * the new one, which is the same path a page reload already takes and
   * therefore the one already known to produce a correct transcript.
   */
  const reloadHistory = useRef<() => void>(() => {});

  useEffect(() => {
    if (!sessionId) return;
    closedByUs.current = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let heartbeat: ReturnType<typeof setInterval> | undefined;

    const connect = () => {
      const ws = new WebSocket(
        agentId
          ? agentSocketUrl(sessionId, agentId, token)
          : socketUrl(sessionId, token, section),
      );
      socketRef.current = ws;

      ws.onopen = () => {
        if (socketRef.current !== ws) {
          ws.close();
          return;
        }
        retryRef.current = 0;
        dispatch({ t: "socket", open: true });
        heartbeat = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, 25_000);
      };

      ws.onmessage = (msg) => {
        if (socketRef.current !== ws) return;
        try {
          const e = JSON.parse(msg.data) as ServerEvent;
          if (e.type === "pong") return;
          // Only a branch switch refetches; an edit or regenerate is applied
          // locally by the reducer, because a fetch there races the re-run
          // that has already started. See the reducer case.
          if (e.type === "history_replaced" && e.reason === "branch") {
            reloadHistory.current();
          }
          dispatch({ t: "event", e });
        } catch {
          /* a malformed frame is not worth tearing the session down */
        }
      };

      ws.onclose = () => {
        if (heartbeat) clearInterval(heartbeat);
        // A superseded socket (StrictMode remount, or a reconnect that already
        // won) must not report "offline" or schedule a rival reconnect.
        if (socketRef.current !== ws) return;
        dispatch({ t: "socket", open: false });
        if (closedByUs.current) return;
        // Exponential backoff, capped — the backend may be cold-starting.
        const delay = Math.min(1000 * 2 ** retryRef.current++, 15_000);
        timer = setTimeout(connect, delay);
      };

      ws.onerror = () => ws.close();
    };

    dispatch({ t: "reset" });
    connect();

    // Replay the checkpoint alongside the connect. The socket announces the
    // model and any live preview but never the transcript, so without this a
    // reload leaves the thread empty while the agent still remembers all of it.
    //
    // The two endpoints return the same shape; a specialist session simply
    // lives in a different graph, so it has to be read from that graph's
    // checkpointer. The `sandbox_id` branch below then does nothing for
    // agents, which is correct — they have no sandbox tree to fetch.
    let hydrated = false;
    /**
     * `first` distinguishes the connect-time replay from a re-read after an
     * edit or a branch switch. The first one is guarded against arriving twice
     * (a StrictMode remount races itself); the later ones must *not* be, since
     * re-reading is the entire point of them.
     */
    const readHistory = (first: boolean) => {
      const loading = agentId
        ? fetchAgentHistory(sessionId, token)
        : fetchHistory(sessionId, token);
      return loading.then((history) => {
        if (closedByUs.current || (first && hydrated)) return;
        if (first) hydrated = true;
        dispatch({
          t: "hydrate",
          items: itemsFromHistory(history),
          usage: usageFromHistory(history),
          iterations: history.checkpoint?.iterations ?? 0,
          replace: !first,
        });
        // The tree only when a sandbox is actually up. Reading it would
        // otherwise *create* one on every page load, for sessions nobody has
        // opened — `sandbox_id` is non-null exactly when one is live.
        if (!history.sandbox_id) return;
        fetchWorkspaceTree(sessionId, token)
          .then((tree) => {
            if (!closedByUs.current) {
              dispatch({ t: "applyTree", path: tree.path, nodes: tree.nodes });
            }
          })
          .catch(() => undefined);
      });
    };

    reloadHistory.current = () => {
      void readHistory(false).catch(() => undefined);
    };

    void readHistory(true).catch(() => {
      // A session with no checkpoint yet is the common case, not a failure.
    });

    return () => {
      closedByUs.current = true;
      if (timer) clearTimeout(timer);
      if (heartbeat) clearInterval(heartbeat);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [sessionId, token, agentId, section]);

  const send = useCallback((text: string, fileIds: string[] = []) => {
    const ws = socketRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ type: "user_message", content: text, file_ids: fileIds }));
    dispatch({ t: "sent", text, files: fileIds });
    return true;
  }, []);

  const cancel = useCallback(() => {
    const ws = socketRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "cancel" }));
  }, []);

  /**
   * Replace an earlier message and re-run from there.
   *
   * `turnIndex` is the ordinal among user turns — the same coordinate the
   * server uses. The transcript is not touched here: the server answers with
   * `history_replaced`, and the refetch that follows is what redraws it. Doing
   * it optimistically would mean guessing how many turns the rewind removes,
   * and being wrong about that leaves the screen disagreeing with the model.
   */
  const editMessage = useCallback(
    (turnIndex: number, content: string, fileIds: string[] = []) => {
      const ws = socketRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      ws.send(
        JSON.stringify({
          type: "edit_message",
          turn_index: turnIndex,
          content,
          file_ids: fileIds,
        }),
      );
      return true;
    },
    [],
  );

  /**
   * Re-run the last turn with a fresh model call.
   *
   * Nothing about the current model selection travels in this frame, and that
   * is deliberate rather than an omission: the server runs it on whatever the
   * session is set to *now*, so switching model and then regenerating does what
   * it looks like it does.
   */
  const regenerate = useCallback(() => {
    const ws = socketRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ type: "regenerate" }));
    return true;
  }, []);

  /** Move to another version of an edited turn. */
  const switchBranch = useCallback((turnIndex: number, version: number) => {
    const ws = socketRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(
      JSON.stringify({
        type: "switch_branch",
        turn_index: turnIndex,
        version,
      }),
    );
    return true;
  }, []);

  const setModel = useCallback((modelId: string) => {
    const ws = socketRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "set_model", model_id: modelId }));
    }
  }, []);

  /**
   * Answer a paused run. Agents section only.
   *
   * The socket is the fast path: the run is suspended on the server awaiting
   * exactly this frame. If the socket has dropped since the card appeared, the
   * REST route resolves the same in-process future, so a decision made after a
   * reconnect is not silently lost.
   */
  const resolveApproval = useCallback(
    (
      approvalId: string,
      decision: "approved" | "edited" | "rejected",
      parameters?: Record<string, unknown>,
    ) => {
      const ws = socketRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            type: "approval_resolve",
            approval_id: approvalId,
            decision,
            parameters,
          }),
        );
        return;
      }
      void import("./agents").then(({ resolveApproval: post }) =>
        post(approvalId, decision, parameters, token).catch(() => {
          // The run has already ended, which is the usual reason. The card
          // settles itself on `agent_done`, so there is nothing to report.
        }),
      );
    },
    [token],
  );

  const openFile = useCallback(
    (path: string | null) => dispatch({ t: "openFile", path }),
    [],
  );
  const openContent = useCallback(
    (file: ChangedFile) => dispatch({ t: "openContent", file }),
    [],
  );
  const clearTerminal = useCallback(() => dispatch({ t: "clearTerminal" }), []);
  const dismissToast = useCallback(
    (id: string) => dispatch({ t: "dismissToast", id }),
    [],
  );
  const applyGitState = useCallback(
    (snapshot: {
      repo: boolean;
      branch: string | null;
      status: GitChange[];
      log: GitCommit[];
      path: string;
    }) =>
      dispatch({
        t: "applyGitState",
        git: { ...snapshot, branch: snapshot.branch ?? "" },
      }),
    [],
  );
  const reloadPreview = useCallback(() => dispatch({ t: "reloadPreview" }), []);
  const dismissPreviewError = useCallback(
    () => dispatch({ t: "dismissPreviewError" }),
    [],
  );
  const applyLocalEdit = useCallback(
    (file: ChangedFile) => dispatch({ t: "localEdit", file }),
    [],
  );
  const applyTree = useCallback(
    (path: string, nodes: FileNode[]) => dispatch({ t: "applyTree", path, nodes }),
    [],
  );
  const applyPathMove = useCallback(
    (from: string, to: string | null) => dispatch({ t: "pathMoved", from, to }),
    [],
  );

  const busy = useMemo(
    // A run suspended on an approval is still a run: the composer must stay
    // disabled, or a second message would queue behind a decision nobody has
    // made yet.
    () =>
      ["thinking", "streaming", "executing"].includes(state.status) ||
      state.awaitingApproval,
    [state.status, state.awaitingApproval],
  );

  return {
    state,
    busy,
    send,
    cancel,
    editMessage,
    regenerate,
    switchBranch,
    setModel,
    resolveApproval,
    openFile,
    openContent,
    clearTerminal,
    dismissToast,
    applyGitState,
    reloadPreview,
    dismissPreviewError,
    applyLocalEdit,
    applyTree,
    applyPathMove,
  };
}
