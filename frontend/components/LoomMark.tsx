import { cn } from "@/lib/cn";

/**
 * The brand mark.
 *
 * The source artwork is an app-icon tile: gold script on a near-black
 * (#10101B) rounded square. Two consequences drive everything here.
 *
 * First, **it is shipped pre-masked at four sizes** rather than as one asset
 * scaled by CSS. The script is a hairline at small sizes, and a browser
 * downscaling a 512px PNG into a 22px rail slot turns it to mush; picking the
 * nearest asset at or above the device-pixel size keeps the strokes crisp.
 *
 * Second, **the tile needs an edge on true black.** At #10101B on #000000 the
 * silhouette is a ~1.3:1 step — the gold reads fine, but the tile's own shape
 * dissolves and the mark looks like a floating squiggle rather than a logo. So
 * a hairline ring is drawn over it on the same `line` token every other surface
 * in the app uses, at the same 22.36% radius the alpha mask was cut with. That
 * is the "sits well on pure black" treatment; it is not a decorative border,
 * and it is the only thing added to the artwork.
 */

/** The sizes actually generated into `public/`. Keep in sync with the assets. */
const ASSETS = [64, 128, 256, 512] as const;

/** The squircle proportion the PNG alpha masks were cut at. */
const RADIUS_RATIO = 0.2236;

export function LoomMark({
  size = 36,
  className,
  ring = true,
  alt = "",
}: {
  /** Rendered CSS size in px. The asset is chosen for 2× this. */
  size?: number;
  className?: string;
  /** The hairline that gives the tile an edge on black. Off inside the intro,
   *  where the mark lands on its own lit stage and does not need one. */
  ring?: boolean;
  /** Empty by default: the mark is nearly always beside its own wordmark or
   *  inside a labelled control, where a second announcement is noise. */
  alt?: string;
}) {
  const asset = ASSETS.find((a) => a >= size * 2) ?? 512;
  const radius = Math.round(size * RADIUS_RATIO * 100) / 100;

  return (
    <span
      className={cn("relative inline-block shrink-0 align-middle", className)}
      style={{ width: size, height: size }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- a fixed-size
          transparent PNG under 8KB; the image optimizer has nothing to add and
          `next/image` would only impose a layout wrapper on it. */}
      <img
        src={`/loom-mark-${asset}.png`}
        alt={alt}
        aria-hidden={alt ? undefined : true}
        width={size}
        height={size}
        draggable={false}
        className="block h-full w-full select-none"
        style={{ borderRadius: radius }}
      />
      {ring && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 border border-line"
          style={{ borderRadius: radius }}
        />
      )}
    </span>
  );
}

/**
 * Mark plus wordmark, for the two places the product actually names itself —
 * the sign-in panel and the Code intro.
 *
 * The wordmark is set in the interface sans rather than traced from the logo's
 * script: the artwork's script is a display face at 1118px and would be
 * illegible set at 15px beside it, and two competing renderings of the same
 * word is exactly the "assembled" tell this pass exists to remove.
 */
export function LoomLockup({
  size = 34,
  className,
  sub,
}: {
  size?: number;
  className?: string;
  /** A quiet second line — the section, or the account state. */
  sub?: string;
}) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <LoomMark size={size} />
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="text-[0.9375rem] font-semibold tracking-[-0.01em] text-ink">
          Loom
        </span>
        {sub && <span className="truncate text-2xs text-ink-faint">{sub}</span>}
      </span>
    </span>
  );
}
