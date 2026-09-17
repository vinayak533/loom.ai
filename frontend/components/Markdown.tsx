"use client";

import { useMemo } from "react";
import { CodeBlock } from "./CodeBlock";

/**
 * A deliberately small Markdown renderer.
 *
 * Agent prose is paragraphs, lists, inline code, fenced blocks, and (because
 * the agent is the product and a comparison is the most common thing it is
 * asked for) tables, links and quotes. Those are handled here, by hand,
 * rather than by pulling in a full parser: nothing is ever rendered as raw
 * HTML, which is what keeps model output from injecting markup, and the two
 * things a library would not give us are kept: a fence stays open while it
 * streams, and spacing is chosen from the *pair* of adjacent blocks.
 *
 * Links are the one place model output reaches outside the page, so the href
 * is checked before it is rendered: only `http`, `https` and `mailto` get
 * through, and every link is `rel="noopener noreferrer"` in a new tab. A
 * `javascript:` URL renders as plain text.
 *
 * The spacing here is the point of this file, not an afterthought. A uniform
 * gap between every block is the single most reliable way to make generated
 * prose look generated: a heading ends up as far from the paragraph it
 * introduces as from the one it follows, so nothing groups. Margins are
 * therefore chosen from the pair of blocks (see `topMargin`), which is how a
 * heading gets a large space above and a small one below, and how a list
 * sits closer to its lead-in than to the next section.
 *
 * Only top margins are set. Spacing a stack from one direction means two
 * adjacent rules can never disagree about the gap between them.
 */

type ListItem = { body: string; children?: Block };

type Block =
  | { t: "code"; lang: string; body: string; closed: boolean }
  | { t: "p"; body: string }
  | { t: "ul"; items: ListItem[] }
  | { t: "ol"; items: ListItem[]; start: number }
  | { t: "h"; level: number; body: string }
  | { t: "quote"; blocks: Block[] }
  | { t: "table"; head: string[]; align: Array<"l" | "c" | "r">; rows: string[][] }
  | { t: "hr" };

const UL_RE = /^(\s*)[-*+]\s+(.*)$/;
const OL_RE = /^(\s*)(\d+)[.)]\s+(.*)$/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

function isBlockStart(line: string): boolean {
  return (
    /^```/.test(line) ||
    UL_RE.test(line) ||
    OL_RE.test(line) ||
    /^#{1,4}\s/.test(line) ||
    /^\s*>/.test(line) ||
    /^\s*([-*_])(\s*\1){2,}\s*$/.test(line)
  );
}

/** Split a table row on unescaped pipes, dropping the outer ones. */
function splitRow(line: string): string[] {
  const cells: string[] = [];
  let cur = "";
  let inCode = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "\\" && line[i + 1] === "|") {
      cur += "|";
      i++;
      continue;
    }
    if (ch === "`") inCode = !inCode;
    if (ch === "|" && !inCode) {
      cells.push(cur);
      cur = "";
      continue;
    }
    cur += ch;
  }
  cells.push(cur);
  if (cells.length && !cells[0].trim() && line.trimStart().startsWith("|")) cells.shift();
  if (cells.length && !cells[cells.length - 1].trim() && line.trimEnd().endsWith("|")) {
    cells.pop();
  }
  return cells.map((c) => c.trim());
}

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

    const heading = line.match(/^(#{1,4})\s+(.*?)\s*#*\s*$/);
    if (heading) {
      blocks.push({ t: "h", level: heading[1].length, body: heading[2] });
      i++;
      continue;
    }

    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      blocks.push({ t: "hr" });
      i++;
      continue;
    }

    // blockquote: consecutive `>` lines, parsed recursively so a quoted list
    // or code block still renders as one.
    if (/^\s*>/.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        inner.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      blocks.push({ t: "quote", blocks: parse(inner.join("\n")) });
      continue;
    }

    // GFM table: a header row, a separator row, then body rows. Recognised
    // only once the separator has arrived, so a streaming header line is a
    // paragraph until the next line confirms what it is.
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1])) {
      const head = splitRow(line);
      const align = splitRow(lines[i + 1]).map((c) => {
        const l = c.startsWith(":");
        const r = c.endsWith(":");
        return l && r ? "c" : r ? "r" : "l";
      }) as Array<"l" | "c" | "r">;
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push({ t: "table", head, align, rows });
      continue;
    }

    if (UL_RE.test(line) || OL_RE.test(line)) {
      const ordered = OL_RE.test(line);
      const re = ordered ? OL_RE : UL_RE;
      const baseIndent = (line.match(re) as RegExpMatchArray)[1].length;
      const start = ordered ? Number((line.match(OL_RE) as RegExpMatchArray)[2]) || 1 : 1;
      const items: ListItem[] = [];

      while (i < lines.length) {
        const m = lines[i].match(re);
        if (!m || m[1].length !== baseIndent) break;
        let body = ordered ? m[3] : m[2];
        i++;
        // One level of nesting: lines indented deeper than this item's marker
        // that are themselves list items become the item's child list.
        // Deeper indentation than that flattens into the child, which is the
        // right failure mode for prose that was never meant to be an outline.
        const nested: string[] = [];
        while (i < lines.length) {
          const nm = lines[i].match(UL_RE) ?? lines[i].match(OL_RE);
          if (nm && nm[1].length > baseIndent) {
            nested.push(lines[i].slice(nm[1].length));
            i++;
            continue;
          }
          // A continuation line (indented, not a marker) joins the item, or
          // the nested list if one has started.
          if (lines[i].trim() && /^\s+/.test(lines[i]) && !isBlockStart(lines[i].trim())) {
            if (nested.length) nested.push(lines[i].trim());
            else body = `${body}\n${lines[i].trim()}`;
            i++;
            continue;
          }
          break;
        }
        const children = nested.length ? parse(nested.join("\n"))[0] : undefined;
        items.push(children ? { body, children } : { body });
      }
      blocks.push(ordered ? { t: "ol", items, start } : { t: "ul", items });
      continue;
    }

    if (!line.trim()) {
      i++;
      continue;
    }

    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) {
      // A table can begin on the line after a paragraph without a blank line.
      if (
        para.length &&
        lines[i].includes("|") &&
        i + 1 < lines.length &&
        TABLE_SEP_RE.test(lines[i + 1])
      ) {
        break;
      }
      para.push(lines[i++]);
    }
    if (para.length) blocks.push({ t: "p", body: para.join("\n") });
    else i++;
  }

  return blocks;
}

/**
 * The gap above a block, given what precedes it.
 *
 * Three rules, in order:
 *   1. Nothing above the first block: the answer starts at the node's line.
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
    case "hr":
      return "mt-6";
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

/** Only these schemes render as links; anything else is left as text. */
function safeHref(raw: string): string | null {
  const url = raw.trim().replace(/^<|>$/g, "");
  if (/^https?:\/\//i.test(url)) return url;
  if (/^mailto:[^\s]+@[^\s]+$/i.test(url)) return url;
  return null;
}

function ExternalGlyph() {
  return (
    <svg
      aria-hidden
      width="0.75em"
      height="0.75em"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="ml-[0.2em] inline-block -translate-y-[0.1em] opacity-60"
    >
      <path d="M14 4h6v6M20 4l-9 9M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5" />
    </svg>
  );
}

/**
 * Inline: `code`, **bold**, *italic*, ~~struck~~, [links](url) and bare URLs.
 * Rendered as React nodes, never HTML.
 */
function inline(text: string, keyBase: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)|(~~[^~]+~~)|(\[[^\]\n]+\]\([^)\s]+(?:\s+"[^"]*")?\))|(<?https?:\/\/[^\s<>)\]]+>?)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;

  const link = (label: React.ReactNode[], raw: string) => {
    const href = safeHref(raw);
    if (!href) {
      out.push(...label);
      return;
    }
    out.push(
      <a
        key={`${keyBase}-a${k++}`}
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="break-words text-accent underline decoration-accent/40 underline-offset-[0.2em]
                   transition-colors duration-200 hover:decoration-accent"
      >
        {label}
        <ExternalGlyph />
      </a>,
    );
  };

  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      out.push(
        // Neutral, not accent. Inline code is the most frequent span in an
        // agent's prose, and painting all of them in the section accent spent
        // the app's single loudest colour on its most common element.
        <code
          key={`${keyBase}-c${k++}`}
          className="rounded-inner border border-white/[0.07] bg-white/[0.055]
                     px-[0.35em] py-[0.1em] font-mono text-[0.855em] text-ink/90"
        >
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("**") || tok.startsWith("__")) {
      out.push(
        <strong key={`${keyBase}-b${k++}`} className="font-semibold text-ink">
          {inline(tok.slice(2, -2), `${keyBase}-b${k}`)}
        </strong>,
      );
    } else if (tok.startsWith("~~")) {
      out.push(
        <s key={`${keyBase}-s${k++}`} className="text-ink-muted">
          {tok.slice(2, -2)}
        </s>,
      );
    } else if (tok.startsWith("[")) {
      const close = tok.indexOf("](");
      const label = tok.slice(1, close);
      const target = tok.slice(close + 2, -1).replace(/\s+"[^"]*"$/, "");
      link(inline(label, `${keyBase}-l${k}`), target);
    } else if (/^<?https?:/i.test(tok)) {
      // A bare URL. Trailing punctuation belongs to the sentence, not the link.
      const trimmed = tok.replace(/^<|>$/g, "").replace(/[.,;:!?]+$/, "");
      const rest = tok.replace(/^<|>$/g, "").slice(trimmed.length);
      link([trimmed], trimmed);
      if (rest) out.push(rest);
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

const ALIGN: Record<"l" | "c" | "r", string> = {
  l: "text-left",
  c: "text-center",
  r: "text-right",
};

function Blocks({ blocks, keyBase }: { blocks: Block[]; keyBase: string }) {
  return (
    <>
      {blocks.map((b, idx) => {
        const key = `${keyBase}b${idx}`;
        const gap = topMargin(blocks[idx - 1], b);

        switch (b.t) {
          case "code":
            return (
              <div key={key} className={gap}>
                {/* `maxHeight: none`: a chat answer should flow. Capping the
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

          case "hr":
            return <hr key={key} className={`${gap} border-0 border-t border-line`} />;

          case "quote":
            return (
              <blockquote
                key={key}
                className={`${gap} border-l-2 border-line-strong pl-4 text-ink-muted`}
              >
                <Blocks blocks={b.blocks} keyBase={`${key}-q`} />
              </blockquote>
            );

          case "table":
            return (
              // Its own horizontal scroll container, so a wide table never
              // moves the page sideways; the transcript stays put at 320px.
              <div key={key} className={`${gap} scroll-thin -mx-1 overflow-x-auto px-1`}>
                <table className="w-max min-w-full border-collapse text-[0.9375rem] leading-[1.5]">
                  <thead>
                    <tr>
                      {b.head.map((cell, c) => (
                        <th
                          key={c}
                          scope="col"
                          className={`voice-label whitespace-nowrap border-b border-line-strong
                                      px-3 py-2 align-bottom font-medium text-ink-muted ${ALIGN[b.align[c] ?? "l"]}`}
                        >
                          {inline(cell, `${key}-h${c}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((row, r) => (
                      <tr key={r} className="border-b border-line last:border-b-0">
                        {b.head.map((_, c) => (
                          <td
                            key={c}
                            className={`px-3 py-2 align-top ${ALIGN[b.align[c] ?? "l"]}`}
                            style={{ fontVariantNumeric: "tabular-nums" }}
                          >
                            {inline(row[c] ?? "", `${key}-r${r}c${c}`)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );

          case "ul":
            return (
              <ul key={key} className={`${gap} space-y-1.5`}>
                {b.items.map((it, j) => (
                  <li key={j} className="flex gap-2.5">
                    {/* 0.72em drops the 4px dot onto the optical centre of a
                        16px/1.72 line. */}
                    <span className="mt-[0.72em] h-1 w-1 shrink-0 rounded-full bg-ink-faint" />
                    <span className="min-w-0 flex-1">
                      {inline(it.body, `${key}-${j}`)}
                      {it.children && (
                        <Blocks blocks={[it.children]} keyBase={`${key}-${j}-n`} />
                      )}
                    </span>
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
                      {b.start + j}.
                    </span>
                    <span className="min-w-0 flex-1">
                      {inline(it.body, `${key}-${j}`)}
                      {it.children && (
                        <Blocks blocks={[it.children]} keyBase={`${key}-${j}-n`} />
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            );

          default:
            return (
              <p key={key} className={`${gap} whitespace-pre-wrap break-words`}>
                {inline(b.body, key)}
              </p>
            );
        }
      })}
    </>
  );
}

export function Markdown({ source }: { source: string }) {
  const blocks = useMemo(() => parse(source), [source]);

  return (
    // `voice-agent` is the widest, airiest voice in the type system: this is
    // the agent talking, and it is meant to read differently from what the
    // user said and from anything the machine printed.
    <div className="voice-agent">
      <Blocks blocks={blocks} keyBase="" />
    </div>
  );
}
