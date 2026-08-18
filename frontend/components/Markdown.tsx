"use client";

import { useMemo } from "react";
import { CodeBlock } from "./CodeBlock";

/**
 * A deliberately small Markdown renderer.
 *
 * Agent prose is mostly paragraphs, lists, inline code and fenced blocks — so
 * we handle exactly those rather than pulling in a full parser. Nothing is
 * rendered as raw HTML, which also keeps model output from injecting markup.
 *
 * The spacing here is the point of this file, not an afterthought. A uniform
 * gap between every block — which is what `space-y-3.5` was doing — is the
 * single most reliable way to make generated prose look generated: a heading
 * ends up as far from the paragraph it introduces as from the one it follows,
 * so nothing groups and the whole answer reads as undifferentiated. Margins
 * are therefore chosen from the *pair* of blocks (see `topMargin`), which is
 * how a heading gets a large space above and a small one below, and how a list
 * sits closer to its lead-in than to the next section.
 *
 * Only top margins are set. Spacing a stack from one direction means two
 * adjacent rules can never disagree about the gap between them.
 */

type Block =
  | { t: "code"; lang: string; body: string; closed: boolean }
  | { t: "p"; body: string }
  | { t: "ul"; items: string[] }
  | { t: "ol"; items: string[] }
  | { t: "h"; level: number; body: string };

function parse(src: string): Block[] {
  const blocks: Block[] = [];
  const lines = src.split("\n");
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // fenced code — kept open while streaming so the block appears immediately
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      const lang = fence[1] || "";
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) body.push(lines[i++]);
      // Whether we stopped on a closing fence or ran out of input. This is what
      // tells the block it is safe to highlight: a half-written program
      // re-parsed on every token both wastes the work and visibly churns its
      // own colours as the parser's guess changes.
      const closed = i < lines.length;
      i++; // closing fence (may be absent mid-stream)
      blocks.push({ t: "code", lang, body: body.join("\n"), closed });
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      blocks.push({ t: "h", level: heading[1].length, body: heading[2] });
      i++;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      blocks.push({ t: "ul", items });
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      blocks.push({ t: "ol", items });
      continue;
    }

    if (!line.trim()) {
      i++;
      continue;
    }

    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^```/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^#{1,4}\s/.test(lines[i])
    ) {
      para.push(lines[i++]);
    }
    blocks.push({ t: "p", body: para.join("\n") });
  }

  return blocks;
}

/**
 * The gap above a block, given what precedes it.
 *
 * Three rules, in order:
 *   1. Nothing above the first block — the answer starts at the node's line.
 *   2. A heading owns the block beneath it: whatever follows hugs it at 8px,
 *      so the pair reads as one unit rather than two floating rows.
 *   3. Otherwise the gap comes from what is arriving. A new heading opens a
 *      section and takes the largest space in the system; a list is a
 *      continuation of the sentence that introduced it and takes the smallest.
 */
function topMargin(prev: Block | undefined, cur: Block): string {
  if (!prev) return "";
  if (prev.t === "h") return "mt-2";

  switch (cur.t) {
    case "h":
      return cur.level <= 2 ? "mt-7" : "mt-6";
    case "ul":
    case "ol":
      return "mt-3";
    default:
      return "mt-4";
  }
}

const HEADING_CLASS: Record<number, string> = {
  1: "text-[1.125rem]",
  2: "text-[1.0625rem]",
  3: "text-[1rem]",
  4: "text-[1rem]",
};

/** Inline: `code`, **bold**, *italic*. Rendered as React nodes, never HTML. */
function inline(text: string, keyBase: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;

  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      out.push(
        // Neutral, not accent. Inline code is the most frequent span in an
        // agent's prose — every path, flag and identifier is one — and
        // painting all of them in the section accent spent the app's single
        // loudest colour on its most common element. A tinted well with a
        // hairline separates it from the prose just as clearly, and leaves the
        // accent meaning something when it does appear.
        <code
          key={`${keyBase}-c${k++}`}
          className="rounded-[5px] border border-white/[0.07] bg-white/[0.055]
                     px-[0.35em] py-[0.1em] font-mono text-[0.855em] text-ink/90"
        >
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("**")) {
      out.push(
        <strong key={`${keyBase}-b${k++}`} className="font-semibold text-ink">
          {tok.slice(2, -2)}
        </strong>,
      );
    } else {
      out.push(
        <em key={`${keyBase}-i${k++}`} className="italic">
          {tok.slice(1, -1)}
        </em>,
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ source }: { source: string }) {
  const blocks = useMemo(() => parse(source), [source]);

  return (
    // `voice-agent` is the widest, airiest voice in the type system — this is
    // the agent talking, and it is meant to read differently from what the
    // user said and from anything the machine printed.
    <div className="voice-agent">
      {blocks.map((b, idx) => {
        const key = `b${idx}`;
        const gap = topMargin(blocks[idx - 1], b);

        switch (b.t) {
          case "code":
            return (
              <div key={key} className={gap}>
                {/* `maxHeight: none` — a chat answer should flow. Capping the
                    block puts a second scrollbar inside a surface that is
                    already scrolling, which is the one thing neither reference
                    product does. */}
                <CodeBlock
                  code={b.body}
                  language={b.lang || undefined}
                  highlight={b.closed}
                  maxHeight="none"
                />
              </div>
            );

          case "h": {
            // Real heading elements, offset by two: the page's own greeting is
            // the h1, so an answer's top-level heading is an h3.
            const Tag = `h${Math.min(b.level + 2, 6)}` as "h3" | "h4" | "h5" | "h6";
            return (
              <Tag
                key={key}
                className={`${gap} ${HEADING_CLASS[b.level] ?? "text-[1rem]"}
                            font-semibold tracking-[-0.011em] text-ink`}
              >
                {inline(b.body, key)}
              </Tag>
            );
          }

          case "ul":
            return (
              <ul key={key} className={`${gap} space-y-1.5`}>
                {b.items.map((it, j) => (
                  <li key={j} className="flex gap-2.5">
                    {/* 0.72em drops the 4px dot onto the optical centre of a
                        16px/1.72 line. It used to sit at 0.55em — about 3px
                        high, which on a bulleted list of any length reads as
                        the bullets floating off their text. */}
                    <span className="mt-[0.72em] h-1 w-1 shrink-0 rounded-full bg-ink-faint" />
                    <span className="min-w-0">{inline(it, `${key}-${j}`)}</span>
                  </li>
                ))}
              </ul>
            );

          case "ol":
            return (
              <ol key={key} className={`${gap} space-y-1.5`}>
                {b.items.map((it, j) => (
                  <li key={j} className="flex gap-2.5">
                    <span
                      className="w-4 shrink-0 pt-[0.1em] text-right font-mono text-xs text-ink-faint"
                      style={{ fontVariantNumeric: "tabular-nums" }}
                    >
                      {j + 1}.
                    </span>
                    <span className="min-w-0">{inline(it, `${key}-${j}`)}</span>
                  </li>
                ))}
              </ol>
            );

          default:
            return (
              <p key={key} className={`${gap} whitespace-pre-wrap`}>
                {inline(b.body, key)}
              </p>
            );
        }
      })}
    </div>
  );
}
