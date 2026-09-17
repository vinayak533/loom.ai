"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { CodeBlock } from "../CodeBlock";

/**
 * A live render of Agent 3's markup, beside its source.
 *
 * Deliberately *not* the Code section's E2B preview. That runs a real dev
 * server in a real sandbox, which is right for a project and absurd for a
 * button — a container spun up to render forty lines of HTML. This is the
 * lightweight equivalent: a sandboxed iframe with the document written into
 * `srcdoc`, plus Tailwind's CDN build so the utility classes the agent emitted
 * actually resolve.
 *
 * Security
 * --------
 * The iframe carries `sandbox="allow-scripts"` and, critically, NOT
 * `allow-same-origin`. Those two together would be no sandbox at all — the
 * frame could reach back into this document and undo its own restrictions.
 * Without `allow-same-origin` the frame is a unique opaque origin: it cannot
 * touch this page, its cookies, or its storage. `allow-scripts` is granted
 * because Tailwind's CDN build *is* a script; nothing else needs it, and no
 * form, popup, download or top-level navigation is permitted.
 *
 * The markup still comes from a language model, so it is treated as untrusted
 * regardless: it is never inserted into this document, only into the frame.
 */

const TAILWIND_CDN = "https://cdn.tailwindcss.com";

/** Viewport widths for the responsive check the agent is asked to design for. */
const WIDTHS = [
  { id: "mobile", label: "375", width: 375 },
  { id: "tablet", label: "768", width: 768 },
  { id: "full", label: "full", width: 0 },
] as const;

type WidthId = (typeof WIDTHS)[number]["id"];

export function ComponentPreview({
  markup,
  language,
}: {
  markup: string;
  language: string;
}) {
  const [view, setView] = useState<"preview" | "code">("preview");
  const [width, setWidth] = useState<WidthId>("full");
  const [height, setHeight] = useState(220);
  const frame = useRef<HTMLIFrameElement>(null);

  // Only markup is renderable. A JSON schema or a JSX fragment is shown as
  // code, because a preview that silently renders nothing is worse than no
  // preview button at all.
  const renderable = ["html", "svg", "xml", ""].includes(language.toLowerCase());

  const doc = useMemo(() => buildDocument(markup), [markup]);

  // The frame reports its own content height so the panel fits the component
  // instead of imposing an arbitrary box on it. postMessage rather than
  // reaching into contentDocument, which the opaque origin forbids — by
  // design, and this is the supported way across it.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return;
      const data = event.data as { type?: string; height?: number };
      if (data?.type === "agent-preview-height" && typeof data.height === "number") {
        setHeight(Math.min(Math.max(data.height + 32, 120), 720));
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const active = WIDTHS.find((w) => w.id === width) ?? WIDTHS[2];

  return (
    <div className="overflow-hidden rounded-card border border-line bg-surface">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-1.5">
        <Segment
          value={view}
          onChange={setView}
          options={[
            { id: "preview" as const, label: "Preview", disabled: !renderable },
            { id: "code" as const, label: "Code" },
          ]}
        />

        {view === "preview" && renderable && (
          <div className="ml-auto flex items-center gap-1">
            <span className="voice-label mr-1 select-none">width</span>
            {WIDTHS.map((option) => (
              <button
                key={option.id}
                type="button"
                onClick={() => setWidth(option.id)}
                className={cn(
                  "rounded-[6px] px-2 py-0.5 font-mono text-2xs transition-colors duration-150",
                  width === option.id
                    ? "bg-raised text-ink"
                    : "text-ink-faint hover:bg-elevated hover:text-ink-muted",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {view === "code" || !renderable ? (
        <CodeBlock
          code={markup}
          language={language || "html"}
          className="rounded-none border-0"
        />
      ) : (
        <div className="bg-surface-solid p-3">
          <div
            className="mx-auto overflow-hidden rounded-ctl bg-white transition-[width] duration-200"
            style={{ width: active.width ? `min(${active.width}px, 100%)` : "100%" }}
          >
            <iframe
              ref={frame}
              title="Component preview"
              srcDoc={doc}
              // No `allow-same-origin`: see the note at the top of this file.
              // Granting it alongside `allow-scripts` would let the frame
              // remove its own sandbox.
              sandbox="allow-scripts"
              referrerPolicy="no-referrer"
              className="w-full border-0"
              style={{ height }}
            />
          </div>
          <p className="mt-2 text-center font-sans text-2xs text-ink-subtle">
            Sandboxed render on a white ground, with Tailwind from a CDN. Not
            the Code section’s dev server — enough to see the component, no
            build step behind it.
          </p>
        </div>
      )}
    </div>
  );
}

function Segment<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: Array<{ id: T; label: string; disabled?: boolean }>;
}) {
  return (
    <div className="flex items-center gap-0.5 rounded-ctl bg-inset p-0.5">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          disabled={option.disabled}
          onClick={() => onChange(option.id)}
          className={cn(
            "rounded-[6px] px-2.5 py-1 font-sans text-2xs transition-colors duration-150",
            value === option.id && !option.disabled
              ? "bg-raised text-ink"
              : "text-ink-faint hover:text-ink-muted",
            option.disabled && "cursor-not-allowed opacity-40",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Wrap the agent's markup in a minimal document.
 *
 * A light ground rather than the app's black one: the agent is asked for
 * responsive, accessible layouts for the open web, and judging its contrast
 * choices against this app's dark theme would be judging the wrong thing.
 */
function buildDocument(markup: string): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="${TAILWIND_CDN}"></script>
<style>
  html, body { margin: 0; }
  body { padding: 16px; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
</style>
</head>
<body>
${markup}
<script>
  // Report the rendered height up so the host panel can size itself. The
  // parent cannot measure this document directly — it is a distinct opaque
  // origin — so the frame volunteers the number instead.
  function report() {
    var h = Math.max(
      document.body.scrollHeight,
      document.documentElement.scrollHeight
    );
    parent.postMessage({ type: 'agent-preview-height', height: h }, '*');
  }
  window.addEventListener('load', report);
  // Tailwind's CDN build rewrites styles after it parses the document, and
  // web fonts settle later still, so one measurement at load is usually taken
  // before the layout is final.
  new ResizeObserver(report).observe(document.body);
  setTimeout(report, 120);
  setTimeout(report, 600);
</script>
</body>
</html>`;
}
