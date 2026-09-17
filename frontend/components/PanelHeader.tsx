import { cn } from "@/lib/cn";

/**
 * One header for every panel.
 *
 * Panel headers were shipping at eight different heights and six left insets
 * because fifteen components each invented their own. This owns the three
 * things they were disagreeing about (height, inset and hairline) and leaves
 * the title and actions to the caller.
 *
 * `level="page"` is for any bar that meets the page header (the context
 * column, a section's tab bar) and matches it exactly, so stacked chrome
 * lines up. `level="panel"` is for headers inside a panel.
 */
export function PanelHeader({
  level = "panel",
  title,
  eyebrow,
  children,
  className,
  as: Tag = "header",
}: {
  level?: "page" | "panel";
  /** Rendered in the label voice when a string; anything else as given. */
  title?: React.ReactNode;
  /** A rhombus in the section accent before the title, like the page header. */
  eyebrow?: boolean;
  /** Actions, right-aligned. */
  children?: React.ReactNode;
  className?: string;
  as?: "header" | "div";
}) {
  return (
    <Tag
      className={cn(
        "flex shrink-0 items-center gap-2.5 border-b border-line",
        level === "page" ? "h-bar px-gutter" : "h-bar-sub px-3.5",
        className,
      )}
    >
      {eyebrow && <span className="sigil h-2 w-2 shrink-0 bg-accent" aria-hidden />}
      {typeof title === "string" ? (
        <span className="voice-label min-w-0 truncate text-ink-muted">{title}</span>
      ) : (
        title
      )}
      {children && (
        <div className="ml-auto flex shrink-0 items-center gap-1.5">{children}</div>
      )}
    </Tag>
  );
}
