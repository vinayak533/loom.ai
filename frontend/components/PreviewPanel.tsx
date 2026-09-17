"use client";

import { AnimatePresence, motion } from "framer-motion";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { PreviewState } from "@/lib/useAgentSocket";
import { cn } from "@/lib/cn";
import { AnimationBoundary, SPRING_SNAP, SPRING_SOFT, useMotionOK } from "./Anim";

/**
 * The live site, running in the session's sandbox.
 *
 * The panel's job is to be honest about four states and to never show a broken
 * iframe standing in for any of them:
 *
 *   idle      — nothing has been started. Says how to start one.
 *   starting  — the command is running but the port has not answered yet.
 *   live      — the real site, in an iframe.
 *   stopped   — it was up and is not any more, with the reason.
 *
 * The error banner is deliberately independent of that machine. A dev server
 * that fails to compile is still serving; taking the site away because the
 * *last* build broke would hide the working page the user is still looking at.
 * So a non-fatal error is a strip over the top of a live frame, and only a
 * fatal one changes the state underneath.
 */

/**
 * How long after the last file change we reload.
 *
 * One agent turn writes many files. Reloading per event would strobe; waiting
 * for the turn to end would feel dead. 900ms is comfortably longer than the gap
 * between writes inside one turn and shorter than the gap between turns.
 */
const RELOAD_DEBOUNCE_MS = 900;

/**
 * The longest we will hold a pending reload while edits keep arriving.
 *
 * A plain trailing-edge debounce starves under sustained input: a turn that
 * writes a file every few hundred ms resets the timer every time, so the
 * preview shows nothing new until the *whole turn* finishes — which is the
 * "feels dead" case the debounce above was meant to avoid. This is the
 * maximum-wait half of the pair. Once the oldest unreloaded change is this old
 * the reload fires on the next tick regardless, and the debounce window starts
 * over from there.
 *
 * 3s: long enough that a normal burst of writes still coalesces into one
 * reload, short enough that a long build-out visibly keeps up.
 */
const RELOAD_MAX_WAIT_MS = 3000;

type Viewport = "desktop" | "tablet" | "mobile";

/** Widths chosen to sit on the breakpoints a developer is actually testing. */
const VIEWPORTS: Record<Viewport, { width: number | null; label: string }> = {
  desktop: { width: null, label: "Full width" },
  tablet: { width: 834, label: "834px" },
  mobile: { width: 390, label: "390px" },
};

export const PreviewPanel = memo(function PreviewPanel({
  preview,
  onReload,
  onDismissError,
  onStop,
}: {
  preview: PreviewState;
  onReload: () => void;
  onDismissError: () => void;
  onStop?: () => void;
}) {
  const motionOK = useMotionOK();
  const [viewport, setViewport] = useState<Viewport>("desktop");
  const [loading, setLoading] = useState(false);

  const { status, url, error, reloadKey, changeSignal } = preview;
  const live = status === "live" && Boolean(url);

  // --- debounced auto-reload ------------------------------------------------
  // `changeSignal` counts file changes; the first render must not fire, or the
  // iframe would reload the instant the preview came up.
  const seenSignal = useRef(changeSignal);
  //: When the oldest change not yet reloaded arrived. Null when nothing is
  //: pending. This is what bounds the debounce — see RELOAD_MAX_WAIT_MS.
  const pendingSince = useRef<number | null>(null);

  useEffect(() => {
    if (!live) {
      // Not live means `changeSignal` cannot have moved (the reducer only
      // counts changes against a live preview), but resetting here keeps a
      // restart from inheriting a stale pending reload.
      seenSignal.current = changeSignal;
      pendingSince.current = null;
      return;
    }
    if (changeSignal === seenSignal.current) {
      pendingSince.current = null;
      return;
    }

    const now = Date.now();
    if (pendingSince.current === null) pendingSince.current = now;
    // Whatever is left of the max-wait budget, capped at the debounce window.
    // When the budget is spent this is 0 and the reload fires on the next tick
    // rather than being pushed out again by the edit that just arrived.
    const waited = now - pendingSince.current;
    const delay = Math.max(0, Math.min(RELOAD_DEBOUNCE_MS, RELOAD_MAX_WAIT_MS - waited));

    const timer = setTimeout(() => {
      seenSignal.current = changeSignal;
      pendingSince.current = null;
      onReload();
    }, delay);
    return () => clearTimeout(timer);
  }, [changeSignal, live, onReload]);

  // A fresh frame is loading until it says otherwise.
  useEffect(() => {
    if (live) setLoading(true);
  }, [live, reloadKey, url]);

  // --- fitting the frame ---------------------------------------------------
  /**
   * The context column is ~400px wide, and every preset except mobile is wider
   * than that. Clamping the iframe to the column would make the toggle a lie —
   * the page would report a 400px viewport whichever preset was selected, so
   * "tablet" and "desktop" would render identically and neither would be true.
   *
   * So the iframe is laid out at its *real* width and scaled down to fit. The
   * page inside genuinely lays out at 834 or 1280 CSS pixels and its media
   * queries fire accordingly; the user sees that proportionally shrunk, which
   * is the same trick device-mode does in browser devtools.
   */
  const stage = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const el = stage.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setStageSize({ width, height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [live]);

  const frame = useMemo(() => {
    const target = VIEWPORTS[viewport].width;
    if (!target || !stageSize.width) {
      return { width: "100%", height: "100%", scale: 1 };
    }
    // Never scale *up*: a 390px mobile frame in a 500px column should sit at
    // its own size with the bed showing around it, not stretch to fill.
    const scale = Math.min(1, stageSize.width / target);
    return {
      width: target,
      // Undo the scale vertically so the frame still reaches the bottom of the
      // column — otherwise a 0.47 scale would leave half the panel empty.
      height: stageSize.height ? stageSize.height / scale : "100%",
      scale,
    };
  }, [viewport, stageSize]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Toolbar
        preview={preview}
        viewport={viewport}
        onViewport={setViewport}
        onReload={onReload}
        onStop={onStop}
        loading={loading}
        scale={frame.scale}
      />

      <AnimatePresence initial={false}>
        {error && (
          <ErrorBanner
            key={error}
            message={error}
            fatal={status === "stopped"}
            onDismiss={onDismissError}
          />
        )}
      </AnimatePresence>

      <div
        className={cn(
          "relative min-h-0 flex-1 overflow-hidden",
          // A checkered-off bed behind the frame, so a narrowed viewport reads
          // as a device rather than as a page that failed to fill its column.
          viewport !== "desktop" && "bg-inset",
        )}
      >
        {live ? (
          <div ref={stage} className="flex h-full w-full justify-center overflow-hidden">
            <motion.div
              layout={motionOK}
              transition={motionOK ? SPRING_SOFT : { duration: 0 }}
              style={{
                width: frame.width,
                height: frame.height,
                transform: `scale(${frame.scale})`,
                transformOrigin: "top center",
              }}
              className={cn(
                "relative shrink-0 overflow-hidden bg-white",
                viewport !== "desktop" && "border-x border-line",
              )}
            >
              <iframe
                // Keyed on the reload counter: the frame is cross-origin, so
                // `contentWindow.location.reload()` is not available to us and
                // remounting is the only reliable way to force a fresh load.
                key={`${url}-${reloadKey}`}
                src={url ?? undefined}
                title="Live preview"
                onLoad={() => setLoading(false)}
                className="h-full w-full border-0"
                // Enough to run a real app; short of letting the previewed page
                // reach back into this one. `allow-same-origin` is required for
                // any framework that touches storage — it is scoped to the
                // sandbox's own origin, not ours.
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
                referrerPolicy="no-referrer"
              />

              <AnimatePresence>
                {loading && (
                  <motion.div
                    key="frame-loading"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={motionOK ? { duration: 0.18 } : { duration: 0 }}
                    className="pointer-events-none absolute inset-x-0 top-0 h-[2px] overflow-hidden"
                  >
                    <ProgressHairline />
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          </div>
        ) : (
          <EmptyFace status={status} command={preview.command} />
        )}
      </div>
    </div>
  );
});

// ---------------------------------------------------------------------------

function Toolbar({
  preview,
  viewport,
  onViewport,
  onReload,
  onStop,
  loading,
  scale,
}: {
  preview: PreviewState;
  viewport: Viewport;
  onViewport: (v: Viewport) => void;
  onReload: () => void;
  onStop?: () => void;
  loading: boolean;
  /** How much the frame is shrunk to fit the column. 1 when it fits as-is. */
  scale: number;
}) {
  const live = preview.status === "live" && Boolean(preview.url);
  const zoomed = live && scale < 0.995;

  return (
    <header className="flex h-bar-sub shrink-0 items-center gap-1.5 border-b border-line px-3">
      <StatusDot status={preview.status} erroring={Boolean(preview.error)} />

      <span
        className="voice-machine min-w-0 flex-1 truncate text-ink-faint"
        data-tip={preview.url ?? preview.command}
      >
        {live ? prettyUrl(preview.url!) : preview.command || "no server"}
      </span>

      {/* The frame is scaled, not clipped — say so, or a shrunken page reads as
          a styling bug rather than as a smaller viewport. */}
      {zoomed && (
        <span
          className="voice-machine shrink-0 text-2xs text-ink-subtle"
          data-tip={`Rendering at ${VIEWPORTS[viewport].label}, scaled to fit the panel`}
        >
          {Math.round(scale * 100)}%
        </span>
      )}

      {live && (
        <>
          <ViewportToggle value={viewport} onChange={onViewport} />
          <IconButton label="Reload preview" onClick={onReload} spinning={loading}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 12a8 8 0 1 1-2.6-5.9" />
              <path d="M20 4v4.5h-4.5" />
            </svg>
          </IconButton>
          <IconButton label="Open in a new tab" href={preview.url!}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 4h6v6" />
              <path d="M20 4 11 13" />
              <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
            </svg>
          </IconButton>
          {onStop && (
            <IconButton label="Stop the dev server" onClick={onStop} danger>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round">
                <rect x="6.5" y="6.5" width="11" height="11" rx="1.5" />
              </svg>
            </IconButton>
          )}
        </>
      )}
    </header>
  );
}

function StatusDot({
  status,
  erroring,
}: {
  status: PreviewState["status"];
  erroring: boolean;
}) {
  const motionOK = useMotionOK();
  const starting = status === "starting";
  const tone = erroring
    ? "bg-del"
    : status === "live"
      ? "bg-add"
      : starting
        ? "bg-warn"
        : "bg-ink-dim";

  return (
    <span className="grid h-3.5 w-3.5 shrink-0 place-items-center" aria-hidden>
      {starting && motionOK && (
        <motion.span
          className={cn("sigil absolute h-2.5 w-2.5", tone)}
          animate={{ scale: [0.8, 1.7], opacity: [0.4, 0] }}
          transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
        />
      )}
      <span className={cn("sigil relative h-[7px] w-[7px]", tone)} />
    </span>
  );
}

function ViewportToggle({
  value,
  onChange,
}: {
  value: Viewport;
  onChange: (v: Viewport) => void;
}) {
  const motionOK = useMotionOK();
  return (
    <div
      role="group"
      aria-label="Preview width"
      className="flex shrink-0 items-center gap-0.5 rounded-ctl bg-inset p-0.5"
    >
      {(Object.keys(VIEWPORTS) as Viewport[]).map((key) => {
        const on = key === value;
        return (
          <button
            key={key}
            type="button"
            onClick={() => onChange(key)}
            aria-pressed={on}
            data-tip={`${key} — ${VIEWPORTS[key].label}`}
            className={cn(
              "relative grid h-6 w-6 place-items-center rounded-[calc(var(--r-ctl)-2px)]",
              "transition-colors duration-200",
              on ? "text-ink" : "text-ink-faint hover:text-ink-muted",
            )}
          >
            {on && (
              <motion.span
                layoutId={motionOK ? "preview-viewport" : undefined}
                transition={motionOK ? SPRING_SNAP : { duration: 0 }}
                className="absolute inset-0 rounded-[calc(var(--r-ctl)-2px)] bg-raised"
                aria-hidden
              />
            )}
            <span className="relative">
              <ViewportIcon kind={key} />
            </span>
          </button>
        );
      })}
    </div>
  );
}

function ViewportIcon({ kind }: { kind: Viewport }) {
  const box =
    kind === "desktop"
      ? { x: 2.5, y: 5, w: 19, h: 13, r: 2 }
      : kind === "tablet"
        ? { x: 5.5, y: 3.5, w: 13, h: 17, r: 2 }
        : { x: 8, y: 3, w: 8, h: 18, r: 2 };
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
      <rect x={box.x} y={box.y} width={box.w} height={box.h} rx={box.r} />
      {kind === "desktop" && <path d="M9 21h6" strokeLinecap="round" />}
    </svg>
  );
}

function IconButton({
  label,
  onClick,
  href,
  children,
  spinning,
  danger,
}: {
  label: string;
  onClick?: () => void;
  href?: string;
  children: React.ReactNode;
  spinning?: boolean;
  danger?: boolean;
}) {
  const motionOK = useMotionOK();
  const className = cn(
    "grid h-7 w-7 shrink-0 place-items-center rounded-ctl transition-colors duration-200",
    danger
      ? "text-ink-faint hover:bg-del/12 hover:text-del"
      : "text-ink-faint hover:bg-raised hover:text-ink",
  );

  const body = (
    <motion.span
      animate={spinning && motionOK ? { rotate: 360 } : { rotate: 0 }}
      transition={
        spinning && motionOK
          ? { duration: 1.1, repeat: Infinity, ease: "linear" }
          : { duration: 0 }
      }
      className="grid place-items-center"
    >
      {children}
    </motion.span>
  );

  if (href) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer noopener"
        data-tip={label}
        aria-label={label}
        className={className}
      >
        {body}
      </a>
    );
  }
  return (
    <button type="button" onClick={onClick} data-tip={label} aria-label={label} className={className}>
      {body}
    </button>
  );
}

/**
 * The compile-error strip.
 *
 * Sits above the frame rather than over it, so it never covers the part of the
 * page the error is about. Collapsed to one line by default because build
 * errors are paragraphs and this is a 400px column; the whole thing is one
 * click away.
 */
function ErrorBanner({
  message,
  fatal,
  onDismiss,
}: {
  message: string;
  fatal: boolean;
  onDismiss: () => void;
}) {
  const motionOK = useMotionOK();
  const [open, setOpen] = useState(false);
  const multiline = message.includes("\n") || message.length > 90;

  return (
    <motion.div
      initial={motionOK ? { height: 0, opacity: 0 } : { opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={motionOK ? { height: 0, opacity: 0 } : { opacity: 0 }}
      transition={motionOK ? SPRING_SNAP : { duration: 0 }}
      className={cn(
        "shrink-0 overflow-hidden border-b",
        fatal ? "border-del/30 bg-del-bg" : "border-warn/30 bg-[rgba(240,181,74,0.09)]",
      )}
    >
      <div className="flex items-start gap-2 px-3 py-2">
        <span
          aria-hidden
          className={cn("sigil mt-[5px] h-[7px] w-[7px] shrink-0", fatal ? "bg-del" : "bg-warn")}
        />
        <div className="min-w-0 flex-1">
          <p
            className={cn(
              "font-mono text-2xs leading-relaxed",
              fatal ? "text-del" : "text-warn",
              !open && "truncate",
              open && "whitespace-pre-wrap break-words",
            )}
          >
            {message}
          </p>
          {multiline && (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="voice-label mt-1 text-ink-faint transition-colors hover:text-ink-muted"
            >
              {open ? "Less" : "Full error"}
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss error"
          className="grid h-5 w-5 shrink-0 place-items-center rounded-ctl text-ink-faint
                     transition-colors hover:bg-raised hover:text-ink"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
            <path d="m6 6 12 12M18 6 6 18" />
          </svg>
        </button>
      </div>
    </motion.div>
  );
}

/**
 * What fills the panel when there is no frame to show.
 *
 * Three distinct messages rather than one empty state: "nothing started",
 * "starting", and "it stopped" are different situations and lead to different
 * next actions.
 */
function EmptyFace({
  status,
  command,
}: {
  status: PreviewState["status"];
  command: string;
}) {
  const motionOK = useMotionOK();

  const copy =
    status === "starting"
      ? {
          title: "Starting the dev server",
          body: command
            ? `Running ${command}. The preview appears as soon as the port answers — a cold build can take a minute.`
            : "The preview appears as soon as the port answers.",
        }
      : status === "stopped"
        ? {
            title: "Preview stopped",
            body: "The dev server is no longer running. Ask the agent to start it again, and it will reappear here.",
          }
        : {
            title: "No preview running",
            body: "Ask the agent to start the dev server — “run the dev server” is enough — and the live site appears here beside the code.",
          };

  return (
    <div className="grid h-full place-items-center px-8 text-center">
      <div className="max-w-[30ch]">
        <AnimationBoundary>
          <div className="mb-4 grid place-items-center">
            {status === "starting" ? (
              <motion.span
                className="sigil h-3 w-3 bg-warn"
                animate={motionOK ? { opacity: [0.35, 1, 0.35] } : { opacity: 1 }}
                transition={
                  motionOK
                    ? { duration: 2, repeat: Infinity, ease: "easeInOut" }
                    : { duration: 0 }
                }
                aria-hidden
              />
            ) : (
              <span
                className={cn(
                  "sigil h-3 w-3",
                  status === "stopped" ? "bg-ink-dim" : "bg-accent/40",
                )}
                aria-hidden
              />
            )}
          </div>
        </AnimationBoundary>
        <p className="font-sans text-[0.8125rem] font-medium text-ink">{copy.title}</p>
        <p className="mt-1.5 font-sans text-xs leading-relaxed text-ink-faint">
          {copy.body}
        </p>
      </div>
    </div>
  );
}

/** A hairline that travels while a frame loads — the same restraint as the
 *  trace spine's head, borrowed rather than reinvented. */
function ProgressHairline() {
  const motionOK = useMotionOK();
  if (!motionOK) {
    return <span className="block h-full w-full bg-accent/40" />;
  }
  return (
    <motion.span
      className="block h-full w-1/3 bg-gradient-to-r from-transparent via-accent to-transparent"
      animate={{ x: ["-100%", "300%"] }}
      transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
    />
  );
}

/** `https://3000-abc.e2b.app/` → `3000-abc.e2b.app` — the scheme is noise. */
function prettyUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.host + (parsed.pathname === "/" ? "" : parsed.pathname);
  } catch {
    return url;
  }
}
