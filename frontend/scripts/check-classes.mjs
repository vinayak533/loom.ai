/**
 * The conformance check the design system was missing.
 *
 * Every audit finding that was not a hard defect came from a token being
 * reached past: a literal hex where `accent` should be, a `duration-250`
 * that Tailwind never emitted, `text-ink-dim` used as text. The system knew
 * what right looked like; nothing failed the build when a component
 * disagreed. This does. It runs as part of `npm run lint`.
 *
 * Rules, each with the file(s) allowed to break it:
 *   1. No `duration-N` outside Tailwind's scale plus the ones declared in
 *      tailwind.config.ts. (An unknown utility emits no CSS and no error.)
 *   2. No `text-ink-dim`. The token is non-text; `text-ink-subtle` is the
 *      recessed text step.
 *   3. No raw `#rrggbb` inside a className. `lib/sections.ts` is the one
 *      place the four accents may be written as hex, because SectionNav has
 *      to show all four at once and reads them from there.
 *   4. No type below 11px.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const DIRS = ["app", "components", "lib"].map((d) => join(ROOT, d));

const KNOWN_DURATIONS = new Set([0, 75, 100, 150, 200, 250, 300, 500, 700, 1000]);

const rules = [
  {
    name: "unknown transition duration",
    re: /\bduration-(\d+)\b/g,
    bad: (m) => !KNOWN_DURATIONS.has(Number(m[1])),
    exempt: () => false,
  },
  {
    name: "text-ink-dim used as text (use text-ink-subtle)",
    re: /\btext-ink-dim\b/g,
    bad: () => true,
    exempt: () => false,
  },
  {
    name: "raw hex in className (use a token)",
    re: /className=\{?["'`][^"'`]*#[0-9a-fA-F]{6}\b/g,
    bad: () => true,
    exempt: (file) => /lib[\\/]sections\.ts$/.test(file),
  },
  {
    name: "type below the 11px floor",
    re: /\btext-\[0\.(5|5625|625)rem\]/g,
    bad: () => true,
    exempt: () => false,
  },
];

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(tsx?|css)$/.test(name)) out.push(p);
  }
  return out;
}

let failures = 0;
for (const file of DIRS.flatMap((d) => walk(d))) {
  const src = readFileSync(file, "utf8");
  for (const rule of rules) {
    if (rule.exempt(file)) continue;
    for (const m of src.matchAll(rule.re)) {
      if (!rule.bad(m)) continue;
      const line = src.slice(0, m.index).split("\n").length;
      console.error(`${relative(ROOT, file)}:${line}  ${rule.name}: ${m[0].trim()}`);
      failures++;
    }
  }
}

if (failures) {
  console.error(
    `\n${failures} token violation(s). See scripts/check-classes.mjs for the rules.`,
  );
  process.exit(1);
}
console.log("check-classes: ok");
