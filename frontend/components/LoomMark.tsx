import { cn } from "@/lib/cn";

/**
 * The brand mark.
 *
 * The source artwork was an app-icon tile: gold script on a #13131C rounded
 * square. That tile was drawn for an icon grid, and on Loom's true-black floor
 * it read as its own slightly-lit, slightly-blue square — a logo carrying a
 * background colour the product does not have. The assets in `public/` are
 * therefore re-cut (`scripts/remask-loom-mark.py`): the tile is gone, the
 * script is the brand gold (`--gold`) on transparency, and the page paints the
 * tile. Whatever the floor is, the mark sits *on* it rather than in front of
 * it. Two consequences drive the rest.
 *
 * First, **it is shipped pre-masked at four sizes** rather than as one asset
 * scaled by CSS. The script is a hairline at small sizes, and a browser
 * downscaling a 512px PNG into a 22px rail slot turns it to mush; picking the
 * nearest asset at or above the device-pixel size keeps the strokes crisp.
 *
 * Second, **the tile still needs an edge.** With no fill of its own the mark
 * would be a floating squiggle, so a hairline ring is drawn on the same
 * `line` token every other surface uses, at the 22.36% radius the original
 * mask was cut with. It is not a decorative border; it is the tile's outline,
 * and the only thing added to the artwork.
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
