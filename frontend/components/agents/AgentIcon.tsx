"use client";

import * as Lucide from "lucide-react";

/**
 * One specialist's icon, by name.
 *
 * The name comes from the backend registry, which validates it against the
 * generated list of icons in the *installed* `lucide-react` build — the same
 * list Agent 3's `lookup_lucide_icons` consults. So a name reaching this
 * component is one that exists; the fallback below is for the case where the
 * registry and the installed package have drifted (a package upgrade that
 * renamed a glyph), where a visible placeholder beats an empty box.
 *
 * Lucide is used here and inside this section only. The rail's four mode
 * glyphs stay hand-drawn: they are a matched set at one stroke weight, and a
 * library icon among them reads as borrowed.
 */
export function AgentIcon({
  name,
  size = 20,
  strokeWidth = 1.65,
  className,
}: {
  name: string;
  size?: number;
  strokeWidth?: number;
  className?: string;
}) {
  const icons = Lucide as unknown as Record<string, unknown>;
  const candidate = icons[name];

  // Every Lucide icon is built with `forwardRef`, so `typeof` reports
  // "object", not "function" — testing for a function here rejected all 1,767
  // of them and rendered the fallback for every agent. Accept both shapes: a
  // plain function component, or any object carrying React's element-type
  // marker (`$$typeof`), which covers forwardRef and memo alike.
  const renderable =
    typeof candidate === "function" ||
    (typeof candidate === "object" &&
      candidate !== null &&
      "$$typeof" in (candidate as Record<string, unknown>));

  const Component = (renderable ? candidate : Lucide.Bot) as React.ComponentType<{
    size?: number;
    strokeWidth?: number;
    className?: string;
    "aria-hidden"?: boolean;
  }>;

  return (
    <Component
      size={size}
      strokeWidth={strokeWidth}
      className={className}
      aria-hidden
    />
  );
}
