"use client";

import { AnimatePresence, motion } from "framer-motion";
import { memo } from "react";
import type { FileNode } from "@/lib/events";
import type { AgentStatus, ChangedFile } from "@/lib/useAgentSocket";
import { cn, shortPath } from "@/lib/cn";
import { AnimationBoundary, SPRING, useMotionOK } from "./Anim";
import { FileTree, type TreeActions } from "./FileTree";
import { ScanBars } from "./TraceSpine";

/**
 * Project Pulse — what the context column shows when there is no diff.
 *
 * The old right-hand panel spent most of its life as an empty rectangle
 * waiting for a file to be touched. This is the fix: the column is never
 * blank, and what fills it is not filler. It is the session's vital signs —
 * what the agent is doing right now, what it has spent, which model is
 * answering and why, what it has changed, and what the sandbox looks like.
 *
 * When a file becomes active the diff slides over this; closing the diff
 * returns here. The panel therefore has two faces and no empty state.
 */

const STATE_COPY: Record<AgentStatus, string> = {
  connecting: "Connecting",
  offline: "Disconnected",
  idle: "Standing by",
  thinking: "Reasoning",
  streaming: "Composing",
  executing: "In the sandbox",
  error: "Halted",
};

const STATE_TONE: Record<AgentStatus, string> = {
  connecting: "text-ink-faint",
  offline: "text-ink-faint",
  idle: "text-ink-muted",
  thinking: "text-accent",
  streaming: "text-accent",
  executing: "text-warn",
  error: "text-del",
};

export const ProjectPulse = memo(function ProjectPulse({
  status,
  connected,
  iterations,
  usage,
  changed,
  activeFile,
  tree,
  treeRoot,
  flash,
  modelName,
  routingMode,
  routingHint,
  exporting,
  onSelectFile,
  onExport,
  treeActions,
}: {
  status: AgentStatus;
  connected: boolean;
  iterations: number;
  usage: { input: number; output: number; cost: number };
  changed: Record<string, ChangedFile>;
  activeFile: string | null;
  tree: FileNode[];
  treeRoot: string;
  flash: Record<string, number>;
  modelName: string;
  routingMode: "manual" | "auto";
  routingHint: string;
  /** True while the zip is being packaged in the sandbox. */
  exporting: boolean;
  onSelectFile: (path: string) => void;
  onExport: () => void;
  /** Rename / delete / create, straight from the tree. Absent = read-only. */
  treeActions?: TreeActions;
}) {
  const changedPaths = Object.keys(changed).sort((a, b) => changed[b].at - changed[a].at);
  const working = status === "thinking" || status === "streaming" || status === "executing";

  return (
    <div className="scroll-thin flex h-full min-h-0 flex-col overflow-y-auto">
      {/* ------------------------------------------------------------ state */}
      <section className="relative shrink-0 overflow-hidden px-5 pb-6 pt-5">
        <AnimationBoundary>
          <Constellation working={working} />
        </AnimationBoundary>

        <p className="voice-label relative">Pulse</p>

        <div className="relative mt-3 flex items-center gap-2.5">
          <AnimationBoundary
            fallback={<span className="sigil h-3 w-3 bg-accent" aria-hidden />}
          >
            <StateMark status={status} />
          </AnimationBoundary>
          <span
            className={cn(
              "font-sans text-[0.9375rem] font-medium tracking-[-0.01em]",
              STATE_TONE[status],
            )}
          >
            {STATE_COPY[status]}
          </span>
          {status === "executing" && <ScanBars />}
        </div>

        {!connected && (
          <p className="relative mt-2 text-xs leading-relaxed text-warn">
            No socket. The FastAPI server may not be running.
          </p>
        )}
      </section>

      {/* ------------------------------------------------------------ route */}
      <Block label="Route">
        <div
          className={cn(
            "mx-3 rounded-ctl border px-3 py-2.5",
            // The reserved accent earns its weight by being scarce, not loud:
            // it gets the border, the mark and the label, but only a whisper
            // of fill — a fully tinted card would read as an alert.
            routingMode === "auto"
              ? "border-rare-line bg-accent-rare/[0.05]"
              : "border-line bg-elevated",
          )}
        >
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "sigil h-2.5 w-2.5 shrink-0",
                routingMode === "auto" ? "auto-orb animate-auto-breathe bg-accent-rare" : "bg-ink-faint",
              )}
              aria-hidden
            />
            <span className="truncate font-sans text-[0.8125rem] font-medium text-ink">
              {/* Before the first turn is classified the backend's announced
                  name *is* the sentinel, and "Auto — AUTO" says nothing. Name
                  the behaviour instead until there is a real model to name. */}
              {routingMode === "auto" && /^auto$/i.test(modelName)
                ? "Choosing per task"
                : modelName}
            </span>
            <span
              className={cn(
                "voice-label ml-auto shrink-0",
                routingMode === "auto" ? "text-accent-rare" : "text-ink-faint",
              )}
            >
              {routingMode === "auto" ? "Auto" : "Pinned"}
            </span>
          </div>
          {routingMode === "auto" && routingHint && (
            <p className="mt-1.5 truncate font-mono text-2xs text-ink-faint">
              matched · {routingHint.replace(/_/g, " ")}
            </p>
          )}
        </div>
      </Block>

      {/* ------------------------------------------------------------ spend */}
      <Block label="This session">
        <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-ctl border border-line bg-line mx-3">
          <Stat label="steps" value={iterations.toLocaleString()} />
          <Stat
            label="tokens"
            value={compact(usage.input + usage.output)}
            title={`${usage.input.toLocaleString()} in · ${usage.output.toLocaleString()} out`}
          />
          <Stat label="cost" value={`$${usage.cost.toFixed(3)}`} />
        </dl>
      </Block>

      {/* ---------------------------------------------------------- changed */}
      <Block label={`Changed${changedPaths.length ? ` · ${changedPaths.length}` : ""}`}>
        {changedPaths.length === 0 ? (
          <p className="px-3 text-xs leading-relaxed text-ink-faint">
            Nothing written yet. Files the agent creates or edits are listed here
            the moment they land.
          </p>
        ) : (
          <ul className="px-1.5">
            <AnimatePresence initial={false}>
              {changedPaths.map((p) => (
                <ChangedRow
                  key={p}
                  path={p}
                  file={changed[p]}
                  root={treeRoot}
                  active={activeFile === p}
                  onSelect={onSelectFile}
                />
              ))}
            </AnimatePresence>
          </ul>
        )}
      </Block>

      {/* ---------------------------------------------------------- project */}
      {/* Taking the project *out* of the sandbox. Export is real; Deploy is
          named and visibly not built, because a plausible-looking button that
          does nothing is worse than an honest gap — and leaving the idea out
          entirely would hide that it is planned at all. */}
      <Block label="Project">
        <div className="flex gap-1.5 px-3">
          <button
            type="button"
            onClick={onExport}
            disabled={exporting}
            className="flex h-8 flex-1 items-center justify-center gap-1.5 rounded-ctl border
                       border-line bg-elevated text-2xs font-medium text-ink-muted
                       transition-all duration-200 hover:border-accent-line hover:bg-raised
                       hover:text-ink active:scale-[0.985] disabled:pointer-events-none
                       disabled:opacity-55"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M12 4v11" />
              <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
              <path d="M5 19h14" />
            </svg>
            {exporting ? "Packaging…" : "Export .zip"}
          </button>

          <span
            title="Vercel deployment is not wired up yet."
            className="flex h-8 flex-1 cursor-not-allowed items-center justify-center gap-1.5
                       rounded-ctl border border-line bg-inset text-2xs font-medium text-ink-dim"
          >
            Deploy
            <span className="voice-label text-[0.5rem] text-ink-dim">soon</span>
          </span>
        </div>
      </Block>

      {/* ---------------------------------------------------------- sandbox */}
      <Block label="Sandbox">
        <FileTree
          nodes={tree}
          root={treeRoot}
          flash={flash}
          activePath={activeFile}
          changed={changed}
          onSelect={onSelectFile}
          actions={treeActions}
        />
      </Block>

      <div className="h-4 shrink-0" />
    </div>
  );
});

function ChangedRow({
  path,
  file,
  root,
  active,
  onSelect,
}: {
  path: string;
  file: ChangedFile;
  root: string;
  active: boolean;
  onSelect: (p: string) => void;
}) {
  const motionOK = useMotionOK();
  return (
    <motion.li
      layout={motionOK}
      initial={motionOK ? { opacity: 0, x: -8 } : false}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0 }}
      transition={motionOK ? SPRING : { duration: 0 }}
    >
      <button
        type="button"
        onClick={() => onSelect(path)}
        className={cn(
          "relative flex w-full items-center gap-2 rounded-ctl px-1.5 py-1 text-left transition-colors duration-200",
          active ? "bg-accent/[0.08]" : "hover:bg-elevated",
        )}
      >
        {active && (
          <span
            aria-hidden
            className="sigil absolute -left-[3px] top-1/2 h-1.5 w-1.5 -translate-y-1/2 bg-accent"
          />
        )}
        <span
          className={cn(
            "voice-machine w-3 shrink-0 text-center",
            file.change === "created" ? "text-add" : "text-warn",
          )}
        >
          {file.change === "created" ? "A" : "M"}
        </span>
        <span
          className={cn(
            "voice-machine truncate",
            active ? "text-accent" : "text-ink/75",
          )}
        >
          {shortPath(path, root)}
        </span>
      </button>
    </motion.li>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="shrink-0 border-t border-line py-4">
      <h3 className="voice-label mb-2.5 px-5">{label}</h3>
      {children}
    </section>
  );
}

function Stat({
  label,
  value,
  title,
}: {
  label: string;
  value: string;
  title?: string;
}) {
  return (
    <div className="bg-surface-solid px-2.5 py-2" title={title}>
      <dd className="voice-machine text-[0.8125rem] text-ink">{value}</dd>
      <dt className="voice-label mt-1 text-[0.5625rem]">{label}</dt>
    </div>
  );
}

function compact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}m`;
}

/**
 * The state mark: the same rhombus as the trace spine's nodes, breathing at a
 * pace set by what the agent is doing. Idle it simply sits there.
 */
function StateMark({ status }: { status: AgentStatus }) {
  const motionOK = useMotionOK();
  const working = status === "thinking" || status === "streaming" || status === "executing";
  const tone =
    status === "executing"
      ? "bg-warn"
      : status === "error"
        ? "bg-del"
        : working
          ? "bg-accent"
          : status === "idle"
            ? "bg-add"
            : "bg-ink-faint";

  return (
    <span className="relative grid h-3 w-3 shrink-0 place-items-center">
      {working && motionOK && (
        <motion.span
          aria-hidden
          className={cn("sigil absolute h-3 w-3", tone)}
          // The two working rhythms are still distinct — the sandbox is the
          // quicker of the pair — but neither halo now grows past the line of
          // text it sits beside.
          animate={{ scale: [0.85, 1.6], opacity: [0.32, 0] }}
          transition={{
            duration: status === "executing" ? 1.5 : 2.1,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />
      )}
      <span className={cn("sigil relative h-2 w-2", tone)} aria-hidden />
    </span>
  );
}

/**
 * A slow drift of the product's mark behind the state block — the "ambient
 * visual" half of the brief's contextual-panel idea. Six elements, transform
 * and opacity only, and it brightens when the agent is working so even the
 * decoration is reporting something.
 */
function Constellation({ working }: { working: boolean }) {
  const motionOK = useMotionOK();

  // Smaller and dimmer than they were: this is wallpaper behind a status
  // block, and it brightens when the agent works — which means it has to be
  // faint enough that brightening is a whisper rather than an entrance.
  const marks = [
    { x: 14, y: 12, s: 24, d: 0 },
    { x: 62, y: 30, s: 13, d: 2.4 },
    { x: 84, y: 8, s: 18, d: 1.1 },
    { x: 40, y: 62, s: 10, d: 3.2 },
    { x: 92, y: 58, s: 14, d: 0.6 },
  ];

  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 overflow-hidden"
      style={{
        maskImage: "linear-gradient(to bottom, black, transparent)",
        WebkitMaskImage: "linear-gradient(to bottom, black, transparent)",
      }}
    >
      {marks.map((m, i) => (
        <motion.span
          key={i}
          className="sigil absolute bg-accent"
          style={{
            left: `${m.x}%`,
            top: `${m.y}%`,
            width: m.s,
            height: m.s,
            willChange: motionOK ? "transform, opacity" : undefined,
          }}
          initial={false}
          animate={
            motionOK
              ? {
                  opacity: working ? [0.04, 0.1, 0.04] : [0.02, 0.05, 0.02],
                  y: [0, -4, 0],
                }
              : { opacity: working ? 0.08 : 0.04 }
          }
          transition={
            motionOK
              ? {
                  duration: working ? 7 : 13,
                  repeat: Infinity,
                  ease: "easeInOut",
                  delay: m.d,
                }
              : { duration: 0 }
          }
        />
      ))}
    </div>
  );
}
