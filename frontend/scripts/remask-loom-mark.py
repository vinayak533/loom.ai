"""Re-cut the brand mark so its tile is the app's own floor.

The source artwork is gold script on a #13131C tile — a bluish near-black that
was drawn for an app-icon grid, not for a page. On Loom's true-black floor that
tile read as a slightly different, slightly lit square: a logo with its own
background colour, sitting on a product whose whole premise is one flat floor.

This script keeps the script and throws the tile away. Alpha is derived from
luminance (the script is the only bright thing in the artwork) and the colour
is pinned to the brand gold (`--gold` in globals.css), so the result is the
gold stroke on transparency — the page paints the tile, and the hairline ring
in `LoomMark.tsx` gives it its edge. Run once per asset size; idempotent.

    python scripts/remask-loom-mark.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

GOLD = (244, 231, 173)
# Below this luminance a pixel is tile/vignette; above `HI` it is stroke.
# The vignette in the 512px source peaks around L≈50, the script sits at
# L≈220, so the ramp between them only ever touches anti-aliased edges.
LO, HI = 64.0, 200.0

PUBLIC = Path(__file__).resolve().parent.parent / "public"


def _lum(p: tuple[int, int, int, int]) -> float:
    return 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]


def remask(path: Path) -> None:
    im = Image.open(path).convert("RGBA")
    px = im.load()
    w, h = im.size

    # The tile's top-right corner carries a specular highlight that is as
    # bright as the script itself, so luminance alone cannot tell them apart.
    # Geometry can: the script is a band across the middle of the tile and
    # never reaches that corner. Find the band from bright pixels *outside*
    # the corner, then drop everything outside it.
    xs: list[int] = []
    ys: list[int] = []
    for y in range(h):
        for x in range(w):
            if x > w * 0.7 and y < h * 0.3:
                continue
            if px[x, y][3] > 0 and _lum(px[x, y]) > HI:
                xs.append(x)
                ys.append(y)
    pad = max(2, round(w * 0.02))
    x0, x1 = max(0, min(xs) - pad), min(w - 1, max(xs) + pad)
    y0, y1 = max(0, min(ys) - pad), min(h - 1, max(ys) + pad)

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                px[x, y] = (*GOLD, 0)
                continue
            k = (_lum((r, g, b, a)) - LO) / (HI - LO)
            k = 0.0 if k < 0 else 1.0 if k > 1 else k
            px[x, y] = (*GOLD, int(round(255 * k * (a / 255))))
    im.save(path, optimize=True)


if __name__ == "__main__":
    for size in (64, 128, 256, 512):
        target = PUBLIC / f"loom-mark-{size}.png"
        remask(target)
        print("remasked", target.name)
