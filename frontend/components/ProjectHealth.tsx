"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import type { ProjectScan } from "@/lib/api";
import { fetchProjectScan, runProjectAnalysis } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Markdown } from "./Markdown";

/**
 * What is actually in this sandbox — languages, size, tests, TODOs.
 *
 * Two costs, kept visibly apart, because the difference is the whole design.
 * The **numbers** come from one shell command: deterministic, free, and safe to
 * refresh whenever a file changes. The **narrative** is a model call, so it
 * happens on a button and says so.
 *
 * The refresh is debounced against `changedCount` rather than run on a timer.
 * A timer polls a sandbox that is usually idle; the agent writing a file is the
 * only thing that can change these numbers, and that is exactly the signal the
 * column already has. The debounce matters because a single agent turn can
 * write twenty files, and twenty `find` passes over a repository would be a
 * self-inflicted denial of service on the user's own sandbox.
 */
const REFRESH_DEBOUNCE_MS = 1500;

export const ProjectHealth = memo(function ProjectHealth({
  sessionId,
  token,
  changedCount,
}: {
  sessionId: string | null;
  token?: string | null;
  /**
   * Bumps whenever the agent touches a file. Its *value* is never read — only
   * the fact that it changed — so anything monotonic works.
   */
  changedCount: number;
}) {
  const [scan, setScan] = useState<ProjectScan | null>(null);
  const [loading, setLoading] = useState(false);
  const [narrative, setNarrative] = useState<string | null>(null);
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(() => {
    if (!sessionId) return;
    setLoading(true);
    fetchProjectScan(sessionId, token)
      .then(setScan)
      .catch(() => setScan({ ok: false, reason: "The scan could not run." }))
      .finally(() => setLoading(false));
  }, [sessionId, token]);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(refresh, REFRESH_DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [refresh, changedCount]);

  if (!sessionId) return null;

  return (
    <section className="border-t border-line px-5 py-5">
      <div className="flex items-baseline justify-between gap-3">
        <p className="voice-label">Codebase</p>
        {loading && <span className="text-2xs text-ink-faint">measuring…</span>}
      </div>

      {scan && !scan.ok && (
        <p className="mt-3 text-2xs leading-relaxed text-ink-faint">{scan.reason}</p>
      )}

      {scan?.ok && scan.file_count === 0 && (
        <p className="mt-3 text-2xs leading-relaxed text-ink-faint">
          Nothing here yet. Ask the agent to build something, or open a folder.
        </p>
      )}

      {scan?.ok && scan.file_count > 0 && (
        <>
          <div className="mt-3 flex items-baseline gap-3">
            <span className="font-sans text-lg tracking-[-0.02em] text-ink">
              {scan.total_lines.toLocaleString()}
            </span>
            <span className="text-2xs text-ink-faint">
              lines across {scan.file_count.toLocaleString()} files
            </span>
          </div>

          <LanguageBar languages={scan.languages} total={scan.total_lines} />

          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2">
            <Stat
              label="Tests"
              value={scan.tests.files > 0 ? `${scan.tests.files} files` : "none found"}
              tone={scan.tests.files > 0 ? "ok" : "warn"}
            />
            <Stat
              label="Lint / types"
              value={
                scan.lint_configs.length > 0
                  ? `${scan.lint_configs.length} config${scan.lint_configs.length === 1 ? "" : "s"}`
                  : "none found"
              }
              tone={scan.lint_configs.length > 0 ? "ok" : "warn"}
            />
            <Stat
              label="TODO markers"
              value={String(scan.todos.count)}
              tone={scan.todos.count > 30 ? "warn" : "plain"}
            />
            <Stat
              label="Stack"
              value={scan.ecosystems.join(", ") || "unrecognised"}
              tone="plain"
            />
          </dl>

          {/* ------------------------------------------------- the narrative */}
          <div className="mt-4">
            {narrative ? (
              <div className="rounded-ctl border border-line bg-inset px-3 py-2.5">
                <Markdown source={narrative} />
              </div>
            ) : (
              <button
                type="button"
                disabled={writing}
                onClick={async () => {
                  setWriting(true);
                  setError(null);
                  try {
                    // `write_file` puts it in LOOM.md too, which is read back
                    // into the prompt on later turns — that is what makes the
                    // analysis worth paying for more than once.
                    const result = await runProjectAnalysis(sessionId, token, true);
                    setNarrative(result.text);
                  } catch (err: unknown) {
                    // `json()` throws "<status> <statusText> — <detail>".
                    // The status text is often two words ("Payment Required"),
                    // so match up to the em dash rather than a single word.
                    setError(
                      err instanceof Error
                        ? err.message.replace(/^\d+\s+[^—]*—\s*/, "")
                        : "The analysis could not run.",
                    );
                  } finally {
                    setWriting(false);
                  }
                }}
                className="w-full rounded-ctl border border-line bg-elevated px-3 py-2
                           text-2xs text-ink-muted transition-colors duration-200
                           hover:border-accent-line hover:text-ink disabled:opacity-55"
              >
                {writing ? "Reading the project…" : "Explain this project"}
              </button>
            )}
            <p className="pt-1.5 text-2xs leading-relaxed text-ink-faint">
              {narrative
                ? "Saved as LOOM.md, and read back on every later turn in this session."
                : "Costs one model call. The numbers above are free."}
            </p>
          </div>

          {error && (
            <p className="pt-2 text-2xs text-warn" role="alert">
              {error}
            </p>
          )}
        </>
      )}
    </section>
  );
});

/**
 * Language mix as one bar.
 *
 * Sized by lines, not by file count — a project with one 4,000-line module and
 * forty 20-line configs is a project in that first language, and the file-count
 * version of this bar says the opposite. Anything under 2% is folded into
 * "other" rather than drawn as a sliver nobody can see or hover.
 */
function LanguageBar({
  languages,
  total,
}: {
  languages: { name: string; lines: number; files: number }[];
  total: number;
}) {
  if (!total || languages.length === 0) return null;
  const major = languages.filter((l) => l.lines / total >= 0.02);
  const restLines = total - major.reduce((n, l) => n + l.lines, 0);

  const TONES = [
    "bg-accent",
    "bg-accent/70",
    "bg-accent/45",
    "bg-ink-dim",
    "bg-ink-faint",
  ];

  return (
    <>
      <div
        className="mt-3 flex h-1.5 overflow-hidden rounded-full bg-inset"
        role="img"
        aria-label={major
          .map((l) => `${l.name} ${Math.round((l.lines / total) * 100)}%`)
          .join(", ")}
      >
        {major.map((l, i) => (
          <span
            key={l.name}
            className={TONES[i % TONES.length]}
            style={{ width: `${(l.lines / total) * 100}%` }}
          />
        ))}
        {restLines > 0 && (
          <span className="bg-ink-faint" style={{ width: `${(restLines / total) * 100}%` }} />
        )}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        {major.slice(0, 5).map((l, i) => (
          <li key={l.name} className="flex items-center gap-1.5 text-2xs text-ink-faint">
            <span
              aria-hidden
              className={cn("h-1.5 w-1.5 rounded-full", TONES[i % TONES.length])}
            />
            {l.name} {Math.round((l.lines / total) * 100)}%
          </li>
        ))}
      </ul>
    </>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "ok" | "warn" | "plain";
}) {
  return (
    <div>
      <dt className="text-2xs text-ink-faint">{label}</dt>
      <dd
        className={cn(
          "truncate text-2xs",
          tone === "ok" && "text-add",
          tone === "warn" && "text-warn",
          tone === "plain" && "text-ink-muted",
        )}
        data-tip={value}
      >
        {value}
      </dd>
    </div>
  );
}
