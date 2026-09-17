"use client";

import type { LearningSource } from "@/lib/api";
import { cn } from "@/lib/cn";
import { chipKindFor, FileChip, FileChipRow, type ChipKind } from "./FileChip";

/** The second line's first word, per chip kind. */
const LABEL: Record<ChipKind, string> = {
  pdf: "PDF",
  csv: "Spreadsheet",
  image: "Image",
  audio: "Audio",
  video: "Video",
  folder: "Folder",
  youtube: "YouTube",
  file: "File",
};

/**
 * Attached Learning sources, shown above the composer.
 *
 * This used to draw its own chip — its own frame, its own icon, its own
 * dismiss button, its own truncation rules — beside a composer that drew a
 * different one for file attachments. Two chips a centimetre apart, agreeing
 * on nothing. It now renders `FileChip`, so a YouTube source and an uploaded
 * PDF are the same object in the same row, and there is one place to change
 * how an attachment looks.
 *
 * A source whose text could not be extracted still shows — degraded, with its
 * reason — rather than vanishing.
 *
 * `kind` on a source is only ever "youtube" or "pdf", because that is the
 * vocabulary Learn's backend speaks. The *chip* is not limited to those: an
 * upload is filed as a `pdf` source whatever it actually is, so the glyph and
 * the second line are derived from the filename instead. That is what stops an
 * attached screenshot in Agents from being drawn as a PDF.
 */
export function SourceChips({
  sources,
  onRemove,
  readOnly = false,
  className,
}: {
  sources: LearningSource[];
  onRemove?: (id: string) => void;
  readOnly?: boolean;
  className?: string;
}) {
  if (sources.length === 0) return null;

  return (
    <FileChipRow className={cn(className)}>
      {sources.map((s) => {
        const kind: ChipKind =
          s.kind === "youtube" ? "youtube" : chipKindFor(s.title);
        return (
          <FileChip
            key={s.id}
            kind={kind}
            title={s.title}
            // Every source's, not only YouTube's. A YouTube thumbnail is a URL
            // the API returned; an upload's is a data URI drawn from the file
            // in the browser before it was sent. The chip does not care which.
            thumbnail={s.thumbnail}
            subtitle={
              s.kind === "youtube"
                ? `YouTube · ${fmtChars(s.chars)}`
                : `${LABEL[kind]} · ${fmtChars(s.chars)}`
            }
            state={
              s.error
                ? { phase: "error", message: "no transcript" }
                : { phase: "ready" }
            }
            onDismiss={!readOnly && onRemove ? () => onRemove(s.id) : undefined}
          />
        );
      })}
    </FileChipRow>
  );
}

function fmtChars(chars?: number): string {
  if (!chars) return "attached";
  if (chars < 1000) return `${chars} chars`;
  return `${(chars / 1000).toFixed(1)}k chars`;
}
