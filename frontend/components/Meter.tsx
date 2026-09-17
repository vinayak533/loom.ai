import { cn } from "@/lib/cn";

/**
 * One progress bar.
 *
 * Learn had five, each styled by hand with its own height, bed, gradient and
 * transition. This is the single scale they now share. Semantic colour
 * (`good`, `warn`) is kept apart from the section accent: a score is a
 * judgement, and the accent is an identity, so a weak topic is amber in every
 * section rather than whatever colour the section happens to be.
 */
export function Meter({
  value,
  max = 100,
  tone = "accent",
  size = "md",
  label,
  className,
}: {
  value: number;
  max?: number;
  /** `accent` for progress; `good` / `warn` for a judgement about a score. */
  tone?: "accent" | "good" | "warn";
  size?: "sm" | "md";
  /** Announced to assistive tech. Visible labels are the caller's job. */
  label?: string;
  className?: string;
}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <span
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.round(value)}
      aria-label={label}
      className={cn(
        "block overflow-hidden rounded-full bg-inset",
        size === "sm" ? "h-1" : "h-1.5",
        className,
      )}
    >
      <span
        className={cn(
          "block h-full rounded-full transition-[width] duration-500 ease-out",
          tone === "accent" && "bg-gradient-to-r from-accent to-accent-alt",
          tone === "good" && "bg-add",
          tone === "warn" && "bg-warn",
        )}
        style={{ width: `${pct}%` }}
      />
    </span>
  );
}
