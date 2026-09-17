"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import type { ChatItem } from "@/lib/useAgentSocket";
import { cn } from "@/lib/cn";
import { SPRING, SPRING_SNAP, useMotionOK } from "../Anim";

type ApprovalItem = Extract<ChatItem, { kind: "approval" }>;

/**
 * The human approval gate — Agent 9's card, and the one every other agent
 * raises before it does something irreversible.
 *
 * Three decisions in its design, each with a reason:
 *
 * 1. **Inline, not modal.** The pause happened at a point in the conversation;
 *    a dialog over the whole screen would separate the decision from the
 *    reasoning that produced it. It is also the one card on the thread that
 *    can be scrolled back to afterwards and still say what was decided.
 * 2. **Parameters are shown, not summarised.** A person cannot approve what
 *    they cannot see. Editable fields are real inputs; everything else is
 *    rendered verbatim, including the parts that are long.
 * 3. **No default.** There is no pre-selected button and no timer that
 *    approves on expiry — the backend's timeout refuses. Nothing here can
 *    happen by inaction.
 */
export function ApprovalCard({
  item,
  onResolve,
}: {
  item: ApprovalItem;
  onResolve: (
    approvalId: string,
    decision: "approved" | "edited" | "rejected",
    parameters?: Record<string, unknown>,
  ) => void;
}) {
  const motionOK = useMotionOK();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [sent, setSent] = useState<string | null>(null);

  const settled = item.status === "settled";
  const decision = item.decision ?? sent;

  // Seeded from the proposal, so "edit" opens on what was actually proposed
  // rather than on empty fields the user has to retype.
  useEffect(() => {
    setDraft(
      Object.fromEntries(
        item.editable.map((key) => [key, stringify(item.parameters[key])]),
      ),
    );
  }, [item.editable, item.parameters]);

  const dirty = useMemo(
    () =>
      item.editable.some(
        (key) => (draft[key] ?? "") !== stringify(item.parameters[key]),
      ),
    [draft, item.editable, item.parameters],
  );

  const send = (verdict: "approved" | "edited" | "rejected") => {
    if (settled || sent) return;
    setSent(verdict);
    if (verdict === "edited") {
      // Only the keys the card exposed travel back, and each is coerced to
      // the shape the proposal used — an array field must not become a string
      // just because it was edited in a textarea.
      const parameters = Object.fromEntries(
        item.editable.map((key) => [
          key,
          coerce(draft[key] ?? "", item.parameters[key]),
        ]),
      );
      onResolve(item.approvalId, "edited", parameters);
      return;
    }
    onResolve(item.approvalId, verdict);
  };

  const risk = RISK[item.risk] ?? RISK.medium;

  return (
    <motion.div
      layout={motionOK ? "position" : false}
      initial={motionOK ? { opacity: 0, y: 8 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={motionOK ? SPRING : { duration: 0 }}
      role="group"
      aria-label={`Approval required: ${item.summary}`}
      className={cn(
        "overflow-hidden rounded-card border",
        settled ? "border-line bg-surface" : "border-rare-line bg-accent-rare/[0.055]",
      )}
      style={{ backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)" }}
    >
      <div className="flex items-start gap-3 px-4 pt-3.5">
        <span
          aria-hidden
          className={cn(
            "mt-1 sigil h-2.5 w-2.5 shrink-0",
            settled ? "bg-ink-dim" : "bg-accent-rare",
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="voice-label text-accent-rare">
              {settled ? "Approval" : "Waiting for you"}
            </span>
            <span className={cn("chip", risk.chip)}>{risk.label}</span>
            <span className="voice-machine text-ink-faint">{item.action}</span>
          </div>
          <p className="mt-1.5 font-sans text-[0.875rem] font-medium leading-snug text-ink">
            {item.summary}
          </p>
          {item.costNote && !settled && (
            <p className="mt-1 font-sans text-2xs text-warn">{item.costNote}</p>
          )}
        </div>
      </div>

      <div className="px-4 pb-3.5 pt-3">
        <div className="voice-label mb-1.5">
          {settled ? "what was proposed" : "exactly what will happen"}
        </div>
        <dl className="space-y-1.5 rounded-ctl border border-line bg-inset p-3">
          {Object.entries(item.parameters).map(([key, value]) => {
            const isEditable = !settled && editing && item.editable.includes(key);
            return (
              <div key={key} className="grid gap-1 sm:grid-cols-[7.5rem_1fr] sm:gap-3">
                <dt className="voice-machine pt-0.5 text-ink-faint">{key}</dt>
                <dd className="min-w-0">
                  {isEditable ? (
                    <textarea
                      value={draft[key] ?? ""}
                      onChange={(e) =>
                        setDraft((d) => ({ ...d, [key]: e.target.value }))
                      }
                      rows={stringify(value).length > 90 ? 5 : 1}
                      className="scroll-thin w-full resize-y rounded-[6px] border border-accent-line
                                 bg-elevated px-2 py-1 font-mono text-[0.75rem] leading-relaxed
                                 text-ink focus:outline-none focus:ring-1 focus:ring-accent-ring"
                    />
                  ) : (
                    <span className="voice-machine block whitespace-pre-wrap break-words text-ink/85">
                      {stringify(value) || <em className="text-ink-subtle">(empty)</em>}
                    </span>
                  )}
                </dd>
              </div>
            );
          })}
        </dl>

        {settled ? (
          <p
            className={cn(
              "mt-3 font-sans text-[0.8125rem]",
              decision === "approved" || decision === "edited"
                ? "text-add"
                : "text-ink-muted",
            )}
          >
            {OUTCOME[decision ?? "expired"] ?? `Resolved: ${decision}.`}
          </p>
        ) : (
          <>
            <AnimatePresence initial={false}>
              {editing && (
                <motion.p
                  initial={motionOK ? { height: 0, opacity: 0 } : false}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={motionOK ? { height: 0, opacity: 0 } : { opacity: 0 }}
                  transition={motionOK ? SPRING_SNAP : { duration: 0 }}
                  className="overflow-hidden font-sans text-2xs text-ink-faint"
                >
                  <span className="mt-2 block">
                    The agent will act on what you leave here, not on what it
                    proposed.
                  </span>
                </motion.p>
              )}
            </AnimatePresence>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => send(editing && dirty ? "edited" : "approved")}
                disabled={Boolean(sent)}
                className="h-8 rounded-ctl bg-gradient-to-br from-accent to-accent-alt px-3.5
                           font-sans text-2xs font-semibold text-accent-ink transition-[color,background-color,border-color,box-shadow,opacity,transform,filter]
                           duration-200 hover:brightness-110 active:scale-[0.97]
                           disabled:pointer-events-none disabled:opacity-50"
              >
                {editing && dirty ? "Approve with changes" : "Approve"}
              </button>

              {item.editable.length > 0 && (
                <button
                  type="button"
                  onClick={() => setEditing((e) => !e)}
                  disabled={Boolean(sent)}
                  className="h-8 rounded-ctl border border-line px-3 font-sans text-2xs
                             text-ink-muted transition-colors duration-200 hover:bg-raised
                             hover:text-ink disabled:pointer-events-none disabled:opacity-50"
                >
                  {editing ? "Discard changes" : "Edit first"}
                </button>
              )}

              <button
                type="button"
                onClick={() => send("rejected")}
                disabled={Boolean(sent)}
                className="h-8 rounded-ctl border border-del/35 px-3 font-sans text-2xs
                           text-del transition-colors duration-200 hover:bg-del/12
                           disabled:pointer-events-none disabled:opacity-50"
              >
                Reject
              </button>

              {sent && (
                <span className="voice-machine text-ink-faint">Sending…</span>
              )}
            </div>

            {item.options.length > 0 && (
              <ul className="mt-2.5 space-y-0.5">
                {item.options.map((option) => (
                  <li key={option.id} className="font-sans text-2xs text-ink-faint">
                    <span className="text-ink-muted">{option.label}</span> —{" "}
                    {option.detail}
                  </li>
                ))}
              </ul>
            )}

            <p className="mt-2 font-sans text-2xs text-ink-subtle">
              Nothing happens on its own. If this is left unanswered for{" "}
              {Math.round(item.timeoutSeconds / 60)} minutes it is refused, not
              approved.
            </p>
          </>
        )}
      </div>
    </motion.div>
  );
}

const RISK: Record<string, { label: string; chip: string }> = {
  low: { label: "low risk", chip: "border-line text-ink-faint" },
  medium: { label: "medium risk", chip: "border-warn/35 text-warn" },
  high: { label: "high risk", chip: "border-del/35 text-del" },
};

const OUTCOME: Record<string, string> = {
  approved: "Approved — the agent went ahead.",
  edited: "Approved with your changes — the agent used the edited parameters.",
  rejected: "Rejected. Nothing was sent, spent or changed.",
  expired: "No answer in time, so it was refused. Nothing happened.",
  cancelled: "The run was cancelled before this was answered.",
  disconnected: "The connection dropped, so it was refused. Nothing happened.",
};

function stringify(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

/**
 * Coerce an edited string back to the shape the proposal used.
 *
 * Without this, editing a recipient list would send `"a@b.com, c@d.com"` where
 * the tool expects an array, and the send would fail on a formatting detail
 * introduced by the very UI meant to prevent mistakes.
 */
function coerce(edited: string, original: unknown): unknown {
  if (Array.isArray(original)) {
    return edited
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  if (typeof original === "number") {
    const n = Number(edited);
    return Number.isFinite(n) ? n : original;
  }
  if (typeof original === "boolean") return /^(true|yes|1)$/i.test(edited.trim());
  if (original && typeof original === "object") {
    try {
      return JSON.parse(edited);
    } catch {
      // Keep the original rather than sending malformed JSON on to a tool
      // that will reject it with a message the user cannot act on.
      return original;
    }
  }
  return edited;
}
