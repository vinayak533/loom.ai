import { cn } from "@/lib/cn";

/**
 * The loading placeholder, shaped like the thing it stands in for.
 *
 * Four surfaces had their own shimmer and six popped in from nothing, and
 * perceived performance is set by the worst surface. This is the one shimmer,
 * shared. A skeleton should mirror the real layout's shape (a row of the
 * right height, a heading-width line then two body-width lines) rather than
 * be a grey block; the helpers below build the common shapes.
 *
 * The shimmer runs on the `BREATH` period like every other working cue, and
 * reduced motion turns it into a still tint via the global rule.
 */
export function Skeleton({
  className,
  rounded = "ctl",
}: {
  className?: string;
  rounded?: "ctl" | "card" | "inner" | "full";
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "block animate-shimmer bg-[length:200%_100%]",
        "bg-[linear-gradient(90deg,rgba(255,255,255,0.04)_25%,rgba(255,255,255,0.09)_50%,rgba(255,255,255,0.04)_75%)]",
        rounded === "ctl" && "rounded-ctl",
        rounded === "card" && "rounded-card",
        rounded === "inner" && "rounded-inner",
        rounded === "full" && "rounded-full",
        className,
      )}
    />
  );
}

/** A heading-width line followed by body-width lines. */
export function SkeletonText({
  lines = 3,
  className,
}: {
  lines?: number;
  className?: string;
}) {
  const widths = ["w-2/5", "w-full", "w-11/12", "w-4/5", "w-3/5"];
  return (
    <span className={cn("flex flex-col gap-2", className)} aria-hidden>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} rounded="inner" className={cn("h-3", widths[i % widths.length])} />
      ))}
    </span>
  );
}

/** A list of rows, each a leading glyph and a line: sessions, files, notes. */
export function SkeletonRows({
  rows = 5,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <span className={cn("flex flex-col gap-1", className)} aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <span key={i} className="flex h-9 items-center gap-2.5 px-2">
          <Skeleton rounded="inner" className="h-4 w-4 shrink-0" />
          <Skeleton rounded="inner" className={cn("h-3", i % 3 === 0 ? "w-3/5" : i % 3 === 1 ? "w-4/5" : "w-1/2")} />
        </span>
      ))}
    </span>
  );
}

/** A card-shaped placeholder: a header row, then text. */
export function SkeletonCard({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex flex-col gap-3 rounded-card border border-line bg-surface p-4",
        className,
      )}
    >
      <span className="flex items-center gap-3">
        <Skeleton className="h-9 w-9 shrink-0" />
        <span className="flex flex-1 flex-col gap-2">
          <Skeleton rounded="inner" className="h-3 w-1/2" />
          <Skeleton rounded="inner" className="h-2.5 w-1/3" />
        </span>
      </span>
      <SkeletonText lines={2} />
    </span>
  );
}
