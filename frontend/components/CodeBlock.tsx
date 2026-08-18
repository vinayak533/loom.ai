"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";

/**
 * A read-only, syntax-highlighted code block with copy-to-clipboard.
 *
 * Lives at the top level rather than under `agents/` because it is no longer
 * an agent-output component: it is what a fenced block in *any* streamed
 * answer renders as, which is the pattern both reference products use — a
 * header bar naming the language, a copy affordance, and a body clearly
 * separated from the prose around it.
 *
 * CodeMirror does the highlighting, but *not* as an editor: `highlightTree`
 * walks a parsed syntax tree and hands back spans, which is a fraction of the
 * work (and none of the DOM) that mounting an EditorView would cost for text
 * nobody is going to type in. The whole of CodeMirror is still lazily imported
 * on first render, exactly as `CodeEditor` does it, so an agent that never
 * emits code never pays for the grammar.
 *
 * The colours are the same map `CodeEditor` uses, restated here as plain CSS
 * rather than a `HighlightStyle` — the point is that a snippet in the chat and
 * the same snippet in the editor look identical.
 */

type Span = { text: string; cls: string };

export function CodeBlock({
  code,
  language,
  maxHeight = "26rem",
  highlight = true,
  className,
}: {
  code: string;
  language?: string;
  /** `none` lets the block flow, which is what a chat answer wants. */
  maxHeight?: string;
  /**
   * False while the fence is still open mid-stream. Parsing a half-written
   * program on every token is both wasted work and visually noisy — the
   * colours churn as the parser's guess changes — so a streaming block renders
   * in the same chrome as plain monospace and colours in once it closes.
   */
  highlight?: boolean;
  className?: string;
}) {
  const [spans, setSpans] = useState<Span[] | null>(null);
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => () => clearTimeout(timer.current), []);

  useEffect(() => {
    let live = true;
    // Long files are left unhighlighted rather than parsed on the main thread:
    // past a few thousand lines the parse is visible as a stall, and plain
    // monospace is a perfectly good fallback.
    if (!highlight || !language || code.length > 120_000) {
      setSpans(null);
      return;
    }
    void highlightCode(code, language).then((result) => {
      if (live) setSpans(result);
    });
    return () => {
      live = false;
    };
  }, [code, language, highlight]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard access can be denied outright. The code is on screen and
      // selectable, so there is nothing worth interrupting the user over.
    }
  };

  const lines = code.split("\n").length;

  return (
    <div
      className={cn(
        "group relative overflow-hidden rounded-card border border-line bg-inset",
        className,
      )}
    >
      {/* The header is lifted a step above the well beneath it. On a true-black
          floor an inset body and its own header are otherwise the same value,
          and the bar reads as a floating row of text rather than as chrome. */}
      <div className="flex items-center gap-2 border-b border-line bg-white/[0.028] px-3 py-1.5">
        <span className="voice-label select-none">{language || "text"}</span>
        {/* Only worth saying when the number is actually informative — on a
            four-line snippet it is noise sitting next to the thing it counts. */}
        {lines > 6 && (
          <span className="voice-machine ml-auto hidden select-none text-ink-dim sm:inline">
            {lines} lines
          </span>
        )}
        <button
          type="button"
          onClick={copy}
          aria-label={copied ? "Copied" : "Copy code"}
          className={cn(
            "flex items-center gap-1.5 rounded-[6px] px-1.5 py-1 font-sans text-[0.625rem]",
            "text-ink-faint transition-colors duration-200",
            "hover:bg-white/[0.06] hover:text-ink",
            lines > 6 ? "ml-1" : "ml-auto",
            copied && "text-add hover:text-add",
          )}
        >
          {copied ? (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m5 13 4 4L19 7" />
            </svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="9" width="11" height="11" rx="2.5" />
              <path d="M5 15V6.5A2.5 2.5 0 0 1 7.5 4H15" />
            </svg>
          )}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre
        className="scroll-thin overflow-auto p-3.5 font-mono text-[0.8125rem] leading-[1.7]"
        style={maxHeight === "none" ? undefined : { maxHeight }}
      >
        <code className="text-ink/85">
          {spans
            ? spans.map((span, i) =>
                span.cls ? (
                  <span key={i} className={span.cls}>
                    {span.text}
                  </span>
                ) : (
                  <span key={i}>{span.text}</span>
                ),
              )
            : code}
        </code>
      </pre>
    </div>
  );
}

/**
 * Tag name → Tailwind class.
 *
 * Mirrors `highlightStyle` in CodeEditor.tsx. Keyed by the *class name*
 * `classHighlighter` emits (`tok-keyword`, `tok-string`, …) rather than by the
 * tag objects, because those only exist once CodeMirror has loaded and this
 * table has to be readable without it.
 *
 * Every colour here is a fixed hue. Keywords, functions, tags, headings and
 * links used to resolve through `--acc` / `--acc-2`, which meant the same
 * snippet was violet-on-blue in Chat, teal-on-amber in Learn and cyan-on-green
 * in Code — a syntax theme that changed when you switched tabs, and the
 * clearest instance of the accent being spent on something that carries no
 * meaning. The section accent now stops at the block's border; the code inside
 * it reads identically everywhere, which is what both reference products do.
 */
const TOKEN_CLASS: Record<string, string> = {
  "tok-keyword": "text-[#B190FF]",
  "tok-controlKeyword": "text-[#B190FF]",
  "tok-moduleKeyword": "text-[#B190FF]",
  "tok-string": "text-[#5BE0A0]",
  "tok-string2": "text-[#5BE0A0]",
  "tok-number": "text-[#F0B54A]",
  "tok-bool": "text-[#F0B54A]",
  "tok-atom": "text-[#F0B54A]",
  "tok-null": "text-[#F0B54A]",
  "tok-comment": "italic text-[#5F6470]",
  "tok-lineComment": "italic text-[#5F6470]",
  "tok-blockComment": "italic text-[#5F6470]",
  "tok-function": "text-[#8AA9FF]",
  "tok-variableName": "text-ink",
  "tok-definition": "text-ink",
  "tok-typeName": "text-[#84D2E8]",
  "tok-className": "text-[#84D2E8]",
  "tok-namespace": "text-[#84D2E8]",
  "tok-propertyName": "text-ink-muted",
  "tok-attributeName": "text-ink-muted",
  "tok-attributeValue": "text-[#5BE0A0]",
  "tok-tagName": "text-[#8AA9FF]",
  "tok-angleBracket": "text-ink-faint",
  "tok-operator": "text-ink-faint",
  "tok-punctuation": "text-ink-faint",
  "tok-separator": "text-ink-faint",
  "tok-bracket": "text-ink-faint",
  "tok-squareBracket": "text-ink-faint",
  "tok-paren": "text-ink-faint",
  "tok-brace": "text-ink-faint",
  "tok-heading": "font-semibold text-[#8AA9FF]",
  "tok-link": "text-[#B190FF] underline",
  "tok-url": "text-[#B190FF] underline",
  "tok-invalid": "text-del",
};

function toClass(tokens: string): string {
  // CodeMirror emits several space-separated token classes for one span; the
  // most specific one is last, so the last match wins.
  const parts = tokens.split(" ").filter(Boolean);
  for (let i = parts.length - 1; i >= 0; i--) {
    const mapped = TOKEN_CLASS[parts[i]];
    if (mapped) return mapped;
  }
  return "";
}

async function highlightCode(code: string, language: string): Promise<Span[] | null> {
  try {
    const [{ highlightTree, classHighlighter }, support] = await Promise.all([
      import("@lezer/highlight"),
      loadLanguage(language),
    ]);
    if (!support) return null;

    // `.language.parser` is the raw lezer parser behind a LanguageSupport.
    const parser = (support as any).language?.parser;
    if (!parser) return null;
    const tree = parser.parse(code);

    const spans: Span[] = [];
    let cursor = 0;
    highlightTree(tree, classHighlighter, (from, to, classes) => {
      if (from > cursor) spans.push({ text: code.slice(cursor, from), cls: "" });
      spans.push({ text: code.slice(from, to), cls: toClass(classes) });
      cursor = to;
    });
    if (cursor < code.length) spans.push({ text: code.slice(cursor), cls: "" });
    return spans;
  } catch {
    // A missing grammar or a parse failure falls through to plain monospace,
    // which is a much better outcome than refusing to show the code.
    return null;
  }
}

/** The same six grammars `CodeEditor` wires, keyed by fence label not extension. */
async function loadLanguage(language: string) {
  const lang = language.toLowerCase().trim();
  try {
    switch (lang) {
      case "js":
      case "javascript":
      case "mjs":
        return (await import("@codemirror/lang-javascript")).javascript();
      case "jsx":
        return (await import("@codemirror/lang-javascript")).javascript({ jsx: true });
      case "ts":
      case "typescript":
        return (await import("@codemirror/lang-javascript")).javascript({ typescript: true });
      case "tsx":
        return (await import("@codemirror/lang-javascript")).javascript({
          typescript: true,
          jsx: true,
        });
      case "py":
      case "python":
        return (await import("@codemirror/lang-python")).python();
      case "html":
      case "xml":
      case "vue":
      case "svelte":
        return (await import("@codemirror/lang-html")).html();
      case "css":
      case "scss":
        return (await import("@codemirror/lang-css")).css();
      case "json":
        return (await import("@codemirror/lang-json")).json();
      case "md":
      case "markdown":
        return (await import("@codemirror/lang-markdown")).markdown();
      default:
        return null;
    }
  } catch {
    return null;
  }
}
