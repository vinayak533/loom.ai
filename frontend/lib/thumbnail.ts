"use client";

/**
 * Thumbnails for the composer's attachment chips.
 *
 * A chip that says "report.pdf · PDF" tells you what you already knew when you
 * picked the file. A chip showing the first page tells you whether it is the
 * *right* report — which is the actual question when you have three of them in
 * a downloads folder.
 *
 * Everything here runs on the file the user just chose, in their browser,
 * before any upload. Nothing is fetched and nothing leaves the page.
 *
 * ## Why PDF rendering is done by hand
 *
 * `pdfjs-dist` renders any PDF perfectly and costs about 350 KB of JavaScript
 * plus a worker. That is a real price for a 30×30 pixel image on a chip, and
 * it is a dependency decision rather than a coding one — so this takes the
 * cheaper path and is honest about its limits.
 *
 * A PDF can embed a page image directly, and a scanned document (the common
 * case for "I dragged a PDF in to ask about it") is exactly that: a JPEG
 * wrapped in PDF structure. `pdfFirstImage` finds the first embedded JPEG by
 * scanning for its byte signature and hands it to the browser's own decoder.
 *
 * When a PDF has no embedded raster — a LaTeX paper, a vector-only export —
 * this returns null and the chip keeps its type icon, which is the existing,
 * correct fallback. So the feature degrades to exactly what was there before
 * rather than to something broken.
 */

/** The longest side of a generated thumbnail, in device pixels. */
const MAX_EDGE = 96;

/** Files past this are not previewed. Decoding a 40 MB image to draw a 30 px
 *  square is a real freeze on a mid-range laptop, for no visible benefit. */
const MAX_BYTES = 25 * 1024 * 1024;

/** How far into a PDF to look for an embedded image before giving up. */
const PDF_SCAN_BYTES = 8 * 1024 * 1024;

/**
 * A small data-URI thumbnail for `file`, or null when there is nothing to show.
 *
 * Never throws: a preview is a nicety, and a chip with an icon is a perfectly
 * good chip. Every failure path returns null.
 */
export async function makeThumbnail(file: File): Promise<string | null> {
  if (file.size > MAX_BYTES) return null;
  try {
    if (file.type.startsWith("image/")) {
      // SVG is an image the browser can draw, but drawing an untrusted one to
      // a canvas taints it in some browsers and `toDataURL` then throws. It
      // also has no natural raster size to scale from. Not worth the edge.
      if (file.type === "image/svg+xml") return null;
      return await rasterise(await blobToBitmapSource(file));
    }
    if (file.type === "application/pdf" || /\.pdf$/i.test(file.name)) {
      const embedded = await pdfFirstImage(file);
      if (!embedded) return null;
      return await rasterise(await blobToBitmapSource(embedded));
    }
  } catch {
    return null;
  }
  return null;
}

/**
 * Decode a blob to something drawable.
 *
 * `createImageBitmap` where available — it decodes off the main thread, which
 * is the difference between a chip appearing and the composer hitching when
 * someone attaches five photos at once. `HTMLImageElement` is the fallback.
 */
async function blobToBitmapSource(
  blob: Blob,
): Promise<ImageBitmap | HTMLImageElement> {
  if (typeof createImageBitmap === "function") {
    return await createImageBitmap(blob);
  }
  const url = URL.createObjectURL(blob);
  try {
    return await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("decode failed"));
      img.src = url;
    });
  } finally {
    // Revoked on the next tick rather than immediately: Safari has been known
    // to drop a decode still reading from the URL.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

/** Draw a decoded image down to thumbnail size and return a data URI. */
async function rasterise(
  source: ImageBitmap | HTMLImageElement,
): Promise<string | null> {
  const width = "width" in source ? source.width : 0;
  const height = "height" in source ? source.height : 0;
  if (!width || !height) return null;

  const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));

  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.drawImage(source as CanvasImageSource, 0, 0, canvas.width, canvas.height);
  if ("close" in source && typeof source.close === "function") source.close();

  // JPEG, not PNG: the chip is 30 px and these strings sit in React state for
  // as long as the composer holds the attachment. A screenshot thumbnail is
  // ~2 KB as JPEG and ~14 KB as PNG.
  try {
    return canvas.toDataURL("image/jpeg", 0.72);
  } catch {
    // A tainted canvas. Cannot happen for a local File, but the throw is
    // silent-failure territory if it ever does.
    return null;
  }
}

/**
 * The first embedded JPEG in a PDF, as a Blob.
 *
 * PDFs store a DCTDecode (JPEG) image stream as the raw JPEG bytes, framed by
 * `stream` / `endstream`. So the whole job is: find `/DCTDecode`, find the
 * `stream` keyword after it, and take from the JPEG's own `FFD8` start marker
 * to its `FFD9` end marker. The markers are searched for rather than trusting
 * the dictionary's `/Length`, because `/Length` is frequently an indirect
 * reference this scanner cannot resolve.
 *
 * This is a heuristic and is meant to be one — see the module note. It finds
 * the page image in a scanned document and returns null for everything else.
 */
async function pdfFirstImage(file: File): Promise<Blob | null> {
  const head = new Uint8Array(
    await file.slice(0, Math.min(file.size, PDF_SCAN_BYTES)).arrayBuffer(),
  );

  const marker = indexOfAscii(head, "/DCTDecode");
  if (marker < 0) return null;

  const streamAt = indexOfAscii(head, "stream", marker);
  if (streamAt < 0) return null;

  // JPEG SOI. Skipping to it steps over the EOL after `stream` without having
  // to know whether the producer wrote CR, LF or CRLF.
  const start = indexOfBytes(head, [0xff, 0xd8, 0xff], streamAt);
  if (start < 0 || start - streamAt > 32) return null;

  const end = lastIndexOfBytes(head, [0xff, 0xd9], start);
  if (end < 0) return null;

  return new Blob([head.slice(start, end + 2)], { type: "image/jpeg" });
}

function indexOfAscii(hay: Uint8Array, needle: string, from = 0): number {
  const bytes = Array.from(needle, (c) => c.charCodeAt(0));
  return indexOfBytes(hay, bytes, from);
}

function indexOfBytes(hay: Uint8Array, needle: number[], from = 0): number {
  outer: for (let i = Math.max(0, from); i <= hay.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (hay[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

/**
 * The *last* occurrence at or after `from`.
 *
 * The end marker specifically, because `FFD9` also appears inside JPEG entropy
 * data and inside a thumbnail embedded in the image's own EXIF. Taking the
 * last one means a truncated-early image is never produced; taking too much
 * trailing PDF structure is harmless, since the decoder stops at the real end
 * marker anyway.
 */
function lastIndexOfBytes(hay: Uint8Array, needle: number[], from: number): number {
  for (let i = hay.length - needle.length; i >= from; i--) {
    let hit = true;
    for (let j = 0; j < needle.length; j++) {
      if (hay[i + j] !== needle[j]) {
        hit = false;
        break;
      }
    }
    if (hit) return i;
  }
  return -1;
}
