/**
 * Rebuilding a transcript from the checkpoint.
 *
 * A session survives a reload in every way that matters on the server — the
 * LangGraph checkpoint, the sandbox, a running preview — but the *thread* was
 * being rendered purely from the live event stream, so a refresh left the user
 * looking at an empty conversation the agent could still remember. This turns
 * the checkpointed messages back into the same `ChatItem[]` the socket builds.
 *
 * The checkpoint is the source rather than the `messages` table because it is
 * what the agent itself will read on the next turn: replaying it means the
 * screen and the model agree about what has been said. Its shape is the
 * router's internal block format — assistant turns carry `text` and `tool_use`
 * blocks, and the tool *results*
 * arrive in the following user turn as `tool_result` blocks, which is why the
 * pass below has to look ahead to finish a tool card.
 */

import type { ChatItem, ToolStatus } from "./useAgentSocket";
import type { SessionHistory } from "./api";

type Block = {
  type?: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  tool_use_id?: string;
  content?: unknown;
  is_error?: boolean;
  title?: string;
};

type Message = { role?: string; content?: unknown };

let seq = 0;
const uid = (p: string) => `h-${p}-${(seq++).toString(36)}`;

function asBlocks(content: unknown): Block[] {
  return Array.isArray(content) ? (content as Block[]) : [];
}

function resultText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => (typeof b === "string" ? b : (b as Block)?.text ?? ""))
      .join("\n");
  }
  return content == null ? "" : String(content);
}

/**
 * Turn the checkpointed conversation into chat items.
 *
 * Ordering follows the live stream's own rule: an assistant bubble closes
 * before the tool cards it introduced, so prose and calls interleave in the
 * order they happened rather than being grouped by kind.
 */
export function itemsFromHistory(history: SessionHistory): ChatItem[] {
  const messages = (history?.checkpoint?.messages ?? []) as Message[];
  const items: ChatItem[] = [];
  /** call_id -> the tool item awaiting its result from a later user turn. */
  const pending = new Map<string, Extract<ChatItem, { kind: "tool" }>>();

  for (const message of messages) {
    const blocks = asBlocks(message.content);

    if (message.role === "user") {
      if (typeof message.content === "string") {
        if (message.content.trim()) {
          items.push({
            kind: "user",
            id: uid("u"),
            text: message.content,
            files: [],
          });
        }
        continue;
      }

      // Results first: they belong to cards already on the thread, not to a
      // new row of their own.
      for (const block of blocks) {
        if (block.type !== "tool_result") continue;
        const card = pending.get(block.tool_use_id ?? "");
        if (!card) continue;
        card.status = block.is_error ? "error" : "ok";
        card.output = resultText(block.content);
        pending.delete(block.tool_use_id ?? "");
      }

      const text = blocks
        .filter((b) => b.type === "text")
        .map((b) => b.text ?? "")
        .join("\n")
        .trim();
      const files = blocks.filter(
        (b) => b.type === "image" || b.type === "document",
      );
      if (text || files.length) {
        items.push({
          kind: "user",
          id: uid("u"),
          text,
          // The ids are gone — they were upload references, consumed at send
          // time — but the count is what the bubble actually renders.
          files: files.map((_, i) => `attachment-${i}`),
        });
      }
      continue;
    }

    if (message.role !== "assistant") continue;

    if (typeof message.content === "string") {
      if (message.content.trim()) {
        items.push({
          kind: "assistant",
          id: uid("a"),
          thinking: "",
          text: message.content,
          streaming: false,
          thinkingActive: false,
        });
      }
      continue;
    }

    const text = blocks
      .filter((b) => b.type === "text")
      .map((b) => b.text ?? "")
      .join("")
      .trim();
    // `reasoning` blocks are what a thinking-mode model produced; the live
    // stream shows the same text under the collapsed Reasoning disclosure.
    const thinking = blocks
      .filter((b) => b.type === "reasoning" || b.type === "thinking")
      .map((b) => b.text ?? "")
      .join("\n")
      .trim();

    if (text || thinking) {
      items.push({
        kind: "assistant",
        id: uid("a"),
        thinking,
        text,
        streaming: false,
        thinkingActive: false,
      });
    }

    for (const block of blocks) {
      if (block.type !== "tool_use") continue;
      const card: Extract<ChatItem, { kind: "tool" }> = {
        kind: "tool",
        id: uid("t"),
        callId: block.id ?? uid("c"),
        tool: block.name ?? "tool",
        input: block.input ?? {},
        // Replaced when the following user turn's result is read. A call whose
        // result never arrived — the run was cancelled, or the backend died
        // mid-tool — stays "running", which is the truth about it.
        status: "running" as ToolStatus,
        startedAt: 0,
      };
      items.push(card);
      pending.set(card.callId, card);
    }
  }

  return items;
}

export function usageFromHistory(history: SessionHistory): {
  input: number;
  output: number;
  cost: number;
} {
  const u = history?.checkpoint?.usage ?? {};
  return {
    input: u.input_tokens ?? 0,
    output: u.output_tokens ?? 0,
    cost: u.cost_estimate ?? 0,
  };
}
