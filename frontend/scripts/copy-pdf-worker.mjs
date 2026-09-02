/**
 * Put `pdfjs-dist`'s worker where the browser can fetch it.
 *
 * The obvious `new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url)`
 * does not survive this app's build: Next hands the `.mjs` to SWC, which
 * parses an already-bundled worker as source and fails on its `export`. So the
 * worker is copied into `public/` and loaded by URL, which is also the form
 * pdf.js documents.
 *
 * Copied at `predev` and `prebuild` rather than committed, so the served file
 * cannot drift from the installed version — bumping the dependency is enough.
 * The copy is skipped when the destination already matches, which keeps `next
 * dev` restarts quiet.
 */
import { copyFileSync, mkdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const from = join(here, "..", "node_modules", "pdfjs-dist", "build", "pdf.worker.min.mjs");
const to = join(here, "..", "public", "pdf.worker.min.mjs");

try {
  const src = statSync(from);
  let fresh = false;
  try {
    const dst = statSync(to);
    fresh = dst.size === src.size && dst.mtimeMs >= src.mtimeMs;
  } catch {
    fresh = false;
  }
  if (!fresh) {
    mkdirSync(dirname(to), { recursive: true });
    copyFileSync(from, to);
    console.log(`pdf.worker.min.mjs -> public/ (${(src.size / 1024).toFixed(0)} KB)`);
  }
} catch (err) {
  // Not fatal. Without the worker, PDF thumbnails fall back to the type icon,
  // which is what they did before pdfjs was added at all — not a reason to
  // fail a build.
  console.warn(`Could not stage pdf.worker.min.mjs: ${err.message}`);
}
