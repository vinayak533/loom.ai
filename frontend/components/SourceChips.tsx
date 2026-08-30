"use client";

import type { LearningSource } from "@/lib/api";
import { cn } from "@/lib/cn";
import { FileChip, FileChipRow, type ChipKind } from "./FileChip";

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
        const kind: ChipKind = s.kind === "youtube" ? "youtube" : "pdf";
        return (
          <FileChip
            key={s.id}
            kind={kind}
            title={s.title}
            thumbnail={s.kind === "youtube" ? s.thumbnail : undefined}
            subtitle={
              s.kind === "youtube"
                ? `YouTube · ${fmtChars(s.chars)}`
                : `PDF · ${fmtChars(s.chars)}`
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
