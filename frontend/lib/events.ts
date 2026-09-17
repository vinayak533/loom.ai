/**
 * Mirror of `backend/app/events.py`. This file and that one are a matched
 * pair — if you change a `type` string in one, change it in the other.
 *
 * Everything the UI animates is keyed off these types. There is no other
 * channel between the agent and the browser.
 */

export const PROTOCOL_VERSION = 1;

export type FileNode = {
  name: string;
  path: string;
  type: "dir" | "file";
  children?: FileNode[];
};

export type SearchResult = { title: string; url: string; snippet: string };

/**
 * A document the model wrote beside the conversation.
 *
 * `key` is the stable identity across versions; `version` counts up within it.
 * The content travels on the event rather than behind a fetch, because the
 * user is watching when it arrives and a round trip to show what the server
 * already had is a round trip they would sit through.
 */
export type ArtifactPayload = {
  artifact_id: string;
  key: string;
  version: number;
  kind: "markdown" | "code" | "html" | "svg" | "mermaid" | string;
  title: string;
  /** Only meaningful for `kind: "code"`. "" otherwise. */
  language: string;
  content: string;
  /** Who wrote this version. A user edit adds a version, never overwrites. */
  created_by: "agent" | "user" | string;
};

/** One entry from `git status --porcelain`, already decoded. */
export type GitChange = {
  path: string;
  /** Set only on a rename. */
  old_path: string | null;
  /** Label for the index (staged) side, "" when unchanged there. */
  index: string;
  /** Label for the worktree side, "" when unchanged there. */
  worktree: string;
  staged: boolean;
  untracked: boolean;
};

export type GitCommit = {
  sha: string;
  short: string;
  author: string;
  /** ISO 8601, from `%aI`. */
  date: string;
  subject: string;
};

export type ServerEvent =
  | { type: "connected"; ts: number; session_id: string; protocol: number }
  | { type: "pong" }
  | { type: "agent_thinking_start"; ts: number }
  | { type: "agent_thinking_delta"; ts: number; content: string }
  | { type: "agent_thinking_end"; ts: number }
  | { type: "agent_message_start"; ts: number }
  | { type: "agent_token"; ts: number; content: string }
  | { type: "agent_message_end"; ts: number }
  | {
      type: "tool_call_start";
      ts: number;
      tool: string;
      input: Record<string, unknown>;
      call_id: string;
    }
  | {
      type: "tool_output_chunk";
      ts: number;
      call_id: string;
      stream: "stdout" | "stderr";
      content: string;
    }
  | {
      type: "tool_call_result";
      ts: number;
      call_id: string;
      output: string;
      success: boolean;
      meta: { exit_code?: number; results?: SearchResult[] };
    }
  | {
      type: "file_changed";
      ts: number;
      path: string;
      diff: string;
      content: string;
      change: "created" | "modified";
    }
  | { type: "file_tree"; ts: number; path: string; nodes: FileNode[] }
  | ({ type: "artifact_created"; ts: number } & ArtifactPayload)
  | ({ type: "artifact_updated"; ts: number } & ArtifactPayload)
  | {
      type: "git_state";
      ts: number;
      /** False when the session has no repository yet. */
      repo: boolean;
      branch: string;
      status: GitChange[];
      log: GitCommit[];
      path: string;
      /** URL of `origin`, or null when no remote has been set. */
      remote: string | null;
      /** Whether this repository commits and pushes at the end of every turn. */
      auto_push: boolean;
    }
  /**
   * A command the user typed into the terminal panel, not one the agent ran.
   * Its output arrives as `tool_output_chunk` frames with the same `call_id`;
   * these two frames bracket it and keep it out of the transcript.
   */
  | { type: "terminal_started"; ts: number; call_id: string; command: string }
  | {
      type: "terminal_exit";
      ts: number;
      call_id: string;
      exit_code: number;
      timed_out: boolean;
    }
  /**
   * The end-of-turn auto commit of an opted-in Code repository — the turn
   * summary. `sha` is empty when the commit itself failed, and then `error`
   * says why; `pushed: false` with a `sha` means the commit landed and the
   * push did not.
   */
  | {
      type: "turn_commit";
      ts: number;
      sha: string;
      short: string;
      subject: string;
      branch: string;
      /** Absolute sandbox paths, so the tree can open to them. */
      files: string[];
      pushed: boolean;
      remote: string | null;
      error: string;
    }
  | {
      type: "preview_ready";
      ts: number;
      url: string;
      port: number;
      command?: string;
    }
  | {
      type: "preview_error";
      ts: number;
      message: string;
      /**
       * `true` — the server is gone and the iframe shows nothing; drop to the
       * stopped state. `false` — it is still serving but failed to compile, so
       * keep the iframe and raise a banner over it.
       */
      fatal: boolean;
      port?: number | null;
    }
  | { type: "preview_stopped"; ts: number; port?: number | null; reason?: string }
  | {
      type: "usage";
      ts: number;
      input_tokens: number;
      output_tokens: number;
      cost_estimate: number;
    }
  | { type: "agent_done"; ts: number; iterations: number; reason: string }
  /**
   * Which user turns have been edited, and what versions each one has. Sent on
   * connect and after every edit, regenerate or branch switch — always the
   * whole picture rather than a delta, because there are only ever a handful
   * of entries and a delta protocol for something this small is a bug farm.
   *
   * `turn_index` counts *user turns*, not entries in the message array: a
   * `tool_result` carrier has role "user" too. The client counts the same
   * thing (items of kind "user"), which is what lets both sides agree on
   * "the third message I sent" without exchanging ids.
   */
  | { type: "branches"; ts: number; branches: BranchGroup[] }
  /**
   * The conversation on the server is no longer the one on screen.
   *
   * For "edit" and "regenerate" the frame names the turn that was cut
   * (`turn_index`, an ordinal among user turns) and the text replacing it, and
   * the client truncates its own transcript there. Refetching instead is a
   * race it loses: the re-run has already started, so the checkpoint being
   * read is mid-rewrite.
   *
   * For "branch" both are absent (`turn_index` is -1) and the client refetches
   * — no run follows a switch, and the change is a suffix replacement rather
   * than a truncation.
   */
  | {
      type: "history_replaced";
      ts: number;
      reason: "edit" | "regenerate" | "branch";
      turn_index: number;
      content: string;
    }
  | { type: "max_iterations"; ts: number; iterations: number; message: string }
  /**
   * A per-turn tool-call ceiling was reached (a cost control — see
   * `AgentDef.max_tool_calls_per_turn` on the backend). The run does not end
   * here: the agent loses its tools and writes up what it has, so this is the
   * reason the answer that follows is partial.
   */
  | {
      type: "tool_budget_reached";
      ts: number;
      agent_name: string;
      budget: number;
      used: number;
      message: string;
    }
  | {
      type: "model_changed";
      ts: number;
      model_id: string;
      name: string;
      supports_tools: boolean;
      available: boolean;
      note?: string;
      /** "auto" when the router chose this model rather than the user. */
      routing_mode?: "manual" | "auto";
      /** The classifier's verdict, e.g. "code_editing". Empty when manual. */
      routing_hint?: string;
      /** Human phrasing of the hint, e.g. "code editing". */
      reason?: string;
      /**
       * Non-empty only when the switch was NOT a choice: the selected model
       * errored (rate limit, 5xx, unavailable) and the router retried on this
       * one. Holds the id of the model that failed. `reason` then reads "hit a
       * rate limit" rather than naming a routing hint.
       */
      fallback_from?: string;
    }
  | { type: "error"; ts: number; message: string }
  /* ---------------------------------------------------------------------
   * The Agentic Loop section only. These travel on /ws/agent/{id} and are
   * never emitted on the Chat/Code socket, so the reducer branches on them
   * without any risk to the three existing sections.
   * ------------------------------------------------------------------- */
  | {
      type: "agent_meta";
      ts: number;
      agent_id: string;
      name: string;
      role: string;
      icon: string;
      accent: string;
      tools: AgentToolMeta[];
      credits: { balance?: number; enabled?: boolean };
    }
  | {
      type: "agent_paused";
      ts: number;
      approval_id: string;
      agent_id: string;
      action: string;
      summary: string;
      parameters: Record<string, unknown>;
      risk: "low" | "medium" | "high";
      /** Parameter names the card lets the user rewrite before approving. */
      editable: string[];
      options: Array<{ id: string; label: string; detail: string }>;
      cost_note?: string;
      timeout_seconds?: number;
    }
  | {
      type: "agent_resumed";
      ts: number;
      approval_id: string;
      agent_id: string;
      decision: string;
    }
  | {
      type: "agent_handoff";
      ts: number;
      from_agent: string;
      next_agent: string;
      next_agent_name: string;
      reason: string;
      context?: string;
    }
  | {
      type: "credits";
      ts: number;
      balance: number;
      spent_this_turn: number;
      enabled: boolean;
    };

/** One agent tool, as the backend reports it. Never carries a key value. */
/** One alternate version of a user turn, for the `‹ 1/2 ›` switcher. */
export type BranchVersion = {
  version: number;
  label: string;
  created_at?: string;
};

/** Every version recorded at one user turn, and which is currently live. */
export type BranchGroup = {
  turn_index: number;
  active: number;
  versions: BranchVersion[];
};

export type AgentToolMeta = {
  name: string;
  summary: string;
  /** Always "real" — prompt-engineered capabilities are listed separately. */
  kind: "real";
  configured: boolean;
  requires_key: string | null;
  /** Configured, but working from a fallback because its key is absent. */
  degraded_without_key?: boolean;
  without_it: string | null;
  /**
   * Extra credits per call, on top of the turn's model cost. `null` when the
   * server cannot price the tool — today that means an image model with no
   * published rate, which the tool itself refuses to run rather than billing a
   * guess. Falsy either way, so a `> 0` check reads correctly.
   */
  credit_surcharge: number | null;
};

export type ClientEvent =
  | { type: "user_message"; content: string; file_ids?: string[] }
  /** `model_id: "auto"` selects the routing mode rather than a model. */
  | { type: "set_model"; model_id: string }
  /**
   * A request to stop, not an interrupt. The run finishes the frame it is on,
   * keeps its partial answer, closes any tool call it had not started, bills
   * the tokens actually generated, and ends with
   * `agent_done.reason === "cancelled"`.
   */
  | { type: "cancel" }
  /** Replace user turn `turn_index` and re-run the conversation from there. */
  | {
      type: "edit_message";
      turn_index: number;
      content: string;
      file_ids?: string[];
    }
  /**
   * Re-run the most recent user turn. Uses whichever model is selected *now*,
   * which need not be the one that answered the first time.
   */
  | { type: "regenerate" }
  /** Make a stored branch the live conversation again. */
  | { type: "switch_branch"; turn_index: number; version: number }
  | { type: "ping" }
  /**
   * A shell command typed into the Code section's terminal panel. Runs in
   * the session's sandbox and streams back as terminal frames. One at a
   * time: a second command while one is running is refused, not queued.
   */
  | { type: "terminal_command"; command: string }
  /**
   * Answers a paused run. Agents section only. `parameters` is read for
   * "edited" and only the keys present are applied, so a card that exposes
   * two of five fields cannot blank the other three.
   */
  | {
      type: "approval_resolve";
      approval_id: string;
      decision: "approved" | "edited" | "rejected";
      parameters?: Record<string, unknown>;
    };

/** Human labels for tool names, used in `ToolCallCard` headers. */
export const TOOL_LABEL: Record<string, string> = {
  bash_execute: "Terminal",
  read_file: "Read",
  write_file: "Write",
  edit_file: "Edit",
  list_files: "List files",
  web_search: "Web search",
  start_dev_server: "Dev server",
  stop_dev_server: "Stop server",
  // The Agentic Loop's tools. In the same map as the rest because the tool
  // card is one component: a second label table would be a second place to
  // forget to add a tool.
  parse_source: "Parse source",
  chunk_document: "Chunk",
  count_tokens: "Count tokens",
  list_email_templates: "Template",
  score_subject_line: "Score subject",
  check_spam_words: "Spam check",
  send_email: "Send email",
  lookup_tailwind: "Tailwind",
  lookup_lucide_icons: "Lucide icons",
  normalize_aspect_ratio: "Aspect ratio",
  list_style_modifiers: "Style terms",
  generate_image: "Generate image",
  resize_image: "Resize",
  search_web: "Web search",
  read_url: "Read page",
  rank_domain_trust: "Source trust",
  keyword_density: "Keyword density",
  readability_score: "Readability",
  validate_json_schema: "Validate schema",
  match_patterns: "Match patterns",
  evaluate_conditions: "Evaluate rules",
  route_to_agent: "Route",
  request_approval: "Approval",
  list_pending_approvals: "Pending approvals",
  lint_code: "Lint",
  parse_ast: "Parse AST",
  run_code: "Run",
};

/** The single most informative argument for each tool, shown in the card header. */
export function toolSubtitle(tool: string, input: Record<string, unknown>): string {
  switch (tool) {
    case "bash_execute":
      return String(input.command ?? "");
    case "web_search":
    case "search_web":
      return String(input.query ?? "");
    case "start_dev_server":
      return input.port ? `${input.command} · :${input.port}` : String(input.command ?? "");
    case "stop_dev_server":
    case "list_pending_approvals":
      return "";
    case "read_url":
      return String(input.url ?? "");
    case "count_tokens":
    case "chunk_document":
    case "readability_score":
      // These take a whole document. Its length is the informative part; the
      // first eighty characters of prose tell you nothing about the call.
      return `${String(input.text ?? "").length.toLocaleString()} chars`;
    case "keyword_density":
      return asList(input.keywords);
    case "lookup_lucide_icons":
      return String(input.query ?? "") || asList(input.names);
    case "lookup_tailwind":
      return String(input.query ?? "") || asList(input.classes);
    case "normalize_aspect_ratio":
      return String(input.value ?? "");
    case "list_style_modifiers":
      return String(input.category ?? "all categories");
    case "list_email_templates":
      return String(input.audience ?? "all audiences");
    case "score_subject_line":
      return String(input.subject ?? "");
    case "send_email":
      return `${asList(input.to)} — ${String(input.subject ?? "")}`;
    case "generate_image":
      return String(input.prompt ?? "");
    case "resize_image":
      return [input.width, input.height].filter(Boolean).join("×") || String(input.asset_id ?? "");
    case "rank_domain_trust":
      return asList(input.urls);
    case "route_to_agent":
      return String(input.next_agent ?? "");
    case "request_approval":
      return String(input.summary ?? input.action ?? "");
    case "match_patterns":
      return `${String(input.text ?? "").length.toLocaleString()} chars`;
    case "lint_code":
    case "parse_ast":
    case "run_code":
      return `${String(input.language ?? "python")} · ${String(input.code ?? "").split("\n").length} lines`;
    case "parse_source":
      return String(input.url ?? input.file_id ?? "pasted text");
    default:
      return String(input.path ?? "");
  }
}

function asList(value: unknown): string {
  if (Array.isArray(value)) return value.slice(0, 3).map(String).join(", ");
  return value === undefined || value === null ? "" : String(value);
}
