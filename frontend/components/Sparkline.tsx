import { useId } from "react";
import { cn } from "@/lib/cn";

/**
 * The product's one data-visualisation primitive.
 *
 * Project Pulse reported tokens and steps as bare numerals, and Agents
 * reported a credit balance the same way; for a tool whose premise is
 * watching an agent work, a read-out is less than an instrument. This is a
 * 40px area chart: a fill at accent/0.14, a 1.5px stroke, an emphasised
 * final point and a faint baseline. It draws nothing until it has two points,
 * because one point is not a trend.
 *
 * Semantic colour stays the caller's decision through `tone`; the default is
 * the section accent.
 */
export function Sparkline({
  values,
  width = 120,
  height = 40,
  tone = "accent",
  label,
  className,
}: {
  values: number[];
  width?: number;
  height?: number;
  tone?: "accent" | "warn" | "good";
  /** Announced to assistive tech; the SVG itself is decorative. */
  label?: string;
  className?: string;
}) {
  const id = useId();
  if (values.length < 2) return null;

  const pad = 2;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const step = (width - pad * 2) / (values.length - 1);
  const y = (v: number) => height - pad - ((v - min) / span) * (height - pad * 2);

  const points = values.map((v, i) => [pad + i * step, y(v)] as const);
  const line = points.map(([x, py], i) => `${i ? "L" : "M"}${x.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const area = `${line} L${points[points.length - 1][0].toFixed(1)},${height - pad} L${pad},${height - pad} Z`;
  const [lx, ly] = points[points.length - 1];

  // Colour comes from the text tokens via `currentColor`, so the semantic
  // tones stay the same tokens the rest of the app uses.
  const stroke = "currentColor";

  return (
    <svg
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn(
        "block overflow-visible",
        tone === "accent" && "text-accent",
        tone === "warn" && "text-warn",
        tone === "good" && "text-add",
        className,
      )}
    >
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <line
        x1={pad}
        x2={width - pad}
        y1={height - pad}
        y2={height - pad}
        stroke="rgba(255,255,255,0.10)"
        strokeWidth="1"
      />
      <path d={area} fill={`url(#${id})`} />
      <path
        d={line}
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={lx} cy={ly} r="2.25" fill={stroke} />
    </svg>
  );
}
