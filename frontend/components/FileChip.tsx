"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  File as FileGlyph,
  FileSpreadsheet,
  FileText,
  Folder,
  Image as ImageGlyph,
  Music,
  Video,
  X,
} from "lucide-react";
import { SPRING, SPRING_SNAP, useMotionOK } from "./Anim";
import { cn } from "@/lib/cn";

/**
 * One attachment, in the composer.
 *
 * There was no such thing before: a PDF attached in Chat produced no visible
 * object at all — it uploaded, it was carried with the next message, and the
 * user's only evidence of any of it was that the send button had gone live.
 * Learn had a chip, Code had a chip, Chat had nothing, and all of them drew the
 * same PDF page for a CSV, a screenshot and a folder alike.
 *
 * So this is the single vocabulary all three now speak, and it has to hold the
 * whole life of an attachment rather than only its happy ending:
 *
 *   uploading → ready        the bar fills, then the icon takes its place
 *   uploading → (cancelled)  the same control, doing the opposite thing
 *   uploading → error        it stays on the rail, saying what went wrong
 *
 * The dismiss control is present in every state, and means the honest thing in
 * each: stop this, or take this off.
 */

export type ChipKind =
  | "pdf"
  | "csv"
  | "image"
  | "audio"
  | "video"
  | "folder"
  | "youtube"
  | "file";

export type ChipState =
  | { phase: "uploading"; progress?: number }
  | { phase: "ready" }
  | { phase: "error"; message: string };

/** Best guess at what a file *is*, from the two things a File tells us. */
export function chipKindFor(name: string, mime?: string): ChipKind {
  const type = (mime ?? "").toLowerCase();
  const ext = name.toLowerCase().split(".").pop() ?? "";
  if (type.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg", "avif"].includes(ext)) {
    return "image";
  }
  if (type.startsWith("audio/") || ["mp3", "wav", "m4a", "ogg", "flac"].includes(ext)) {
    return "audio";
  }
  if (type.startsWith("video/") || ["mp4", "mov", "webm", "mkv"].includes(ext)) {
    return "video";
  }
  if (type === "application/pdf" || ext === "pdf") return "pdf";
  if (type.includes("csv") || ["csv", "tsv", "xlsx", "xls"].includes(ext)) return "csv";
  return "file";
}

const GLYPH: Record<ChipKind, typeof FileGlyph> = {
  pdf: FileText,
  csv: FileSpreadsheet,
  image: ImageGlyph,
  audio: Music,
  video: Video,
  folder: Folder,
  youtube: Video,
  file: FileGlyph,
};

export function FileChip({
  kind,
  title,
  subtitle,
  state,
  thumbnail,
  onDismiss,
  className,
}: {
  kind: ChipKind;
  title: string;
  /** The quiet second line — size, page count, "no transcript". */
  subtitle?: string;
  state: ChipState;
  /** Used instead of the glyph when present (YouTube, image previews). */
  thumbnail?: string;
  /** Cancel while uploading, remove once ready. Omit for a read-only chip. */
  onDismiss?: () => void;
  className?: string;
}) {
  const motionOK = useMotionOK();
  const Glyph = GLYPH[kind] ?? FileGlyph;
  const uploading = state.phase === "uploading";
  const failed = state.phase === "error";
  // Undefined progress means the browser could not measure the body. An
  // indeterminate sweep is the honest rendering of that; a bar creeping to 90%
  // and waiting is not.
  const pct =
    uploading && typeof state.progress === "number"
      ? Math.max(0, Math.min(1, state.progress))
      : null;

  return (
    <motion.div
      layout={motionOK}
      initial={motionOK ? { opacity: 0, y: 6, scale: 0.96 } : { opacity: 0 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={motionOK ? { opacity: 0, scale: 0.94, y: 2 } : { opacity: 0 }}
      transition={motionOK ? SPRING : { duration: 0 }}
      className={cn(
        // A fixed row height, so a chip does not change size when it stops
        // uploading — the progress rail lives *inside* the frame rather than
        // adding a row beneath it, which is what made the old layout jump.
        "relative flex h-[42px] w-full max-w-[15rem] items-center gap-2.5 overflow-hidden",
        "rounded-[11px] border bg-raised pl-1.5 pr-1.5",
        failed ? "border-warn/40" : "border-line",
        className,
      )}
    >
      <span
        className={cn(
          "relative grid h-[30px] w-[30px] shrink-0 place-items-center overflow-hidden rounded-[7px]",
          "bg-inset",
        )}
      >
        {thumbnail ? (
          <img
            src={thumbnail}
            alt=""
            className="h-full w-full object-cover"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.visibility = "hidden";
            }}
          />
        ) : (
          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={uploading ? "busy" : "glyph"}
              initial={motionOK ? { opacity: 0, scale: 0.8 } : false}
              animate={{ opacity: 1, scale: 1 }}
              exit={motionOK ? { opacity: 0, scale: 0.8 } : { opacity: 0 }}
              transition={motionOK ? SPRING_SNAP : { duration: 0 }}
              className="grid place-items-center"
            >
              {uploading ? (
                <motion.span
                  aria-hidden
                  className="sigil h-2.5 w-2.5 bg-accent"
                  animate={motionOK ? { opacity: [0.35, 1, 0.35] } : { opacity: 1 }}
                  transition={
                    motionOK
                      ? { duration: 1.3, repeat: Infinity, ease: "easeInOut" }
                      : { duration: 0 }
                  }
                />
              ) : (
                <Glyph
                  size={15}
                  strokeWidth={1.7}
                  className={failed ? "text-warn" : "text-accent"}
                  aria-hidden
                />
              )}
            </motion.span>
          </AnimatePresence>
        )}
      </span>

      {/* min-w-0 on both the column and its children: without it a long
          filename refuses to shrink and pushes the dismiss button out of the
          chip instead of truncating. */}
      <span className="flex min-w-0 flex-1 flex-col justify-center leading-tight">
        <span className="truncate text-2xs font-medium text-ink">{title}</span>
        <span
          className={cn(
            // The chip's status line — "Uploading — 47%", "Attached", or the
            // reason it failed. `ink-faint` on the chip's own `bg-raised`
            // measures ~3.2:1, the lowest contrast anywhere in the composer,
            // and it is the only thing that says whether an upload is working.
            // `ink-muted` on the same ground clears 5:1.
            "truncate text-[11px]",
            failed ? "text-warn" : "text-ink-muted",
          )}
        >
          {state.phase === "error"
            ? state.message
            : uploading
              ? pct === null
                ? "Uploading…"
                : `Uploading — ${Math.round(pct * 100)}%`
              : (subtitle ?? "Attached")}
        </span>
      </span>

      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={uploading ? `Cancel upload of ${title}` : `Remove ${title}`}
          data-tip={uploading ? "Cancel" : "Remove"}
          // Cancel-an-upload / remove-a-file. A control, so it has to read as
          // one rather than as decoration on a chip.
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-ink-muted
                     transition-colors duration-200 hover:bg-white/[0.08] hover:text-ink"
        >
          <X size={13} strokeWidth={2.2} aria-hidden />
        </button>
      )}

      {/* The rail. Pinned to the chip's own bottom edge so the chip never
          changes height between states. */}
      <AnimatePresence>
        {uploading && (
          <motion.span
            key="rail"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: motionOK ? 0.18 : 0 }}
            className="absolute inset-x-0 bottom-0 h-[2px] overflow-hidden bg-white/[0.06]"
          >
            {pct === null ? (
              <motion.span
                className="block h-full w-1/3 rounded-full bg-gradient-to-r from-accent to-accent-alt"
                animate={motionOK ? { x: ["-100%", "300%"] } : { x: "0%" }}
                transition={
                  motionOK
                    ? { duration: 1.1, repeat: Infinity, ease: "easeInOut" }
                    : { duration: 0 }
                }
              />
            ) : (
              <motion.span
                className="block h-full origin-left rounded-full bg-gradient-to-r from-accent to-accent-alt"
                initial={{ scaleX: 0 }}
                animate={{ scaleX: pct }}
                style={{ width: "100%" }}
                transition={
                  motionOK
                    ? { type: "spring", stiffness: 260, damping: 34, mass: 0.7 }
                    : { duration: 0 }
                }
              />
            )}
          </motion.span>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/** The row the chips sit in. Wraps, and animates members in and out. */
export function FileChipRow({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-start gap-2", className)}>
      <AnimatePresence initial={false}>{children}</AnimatePresence>
    </div>
  );
}
