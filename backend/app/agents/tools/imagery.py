"""Agents 4 and 5 — Creative Prompt Engineer and Graphic/Poster Creator.

Agent 4:
    normalize_aspect_ratio  REAL. Parses whatever the user implied ("a poster",
                            "for stories", "16x9", "1.91:1") and snaps it to a
                            ratio the image endpoints actually accept, with the
                            pixel dimensions that follow. A ratio the provider
                            rejects is a failed render, so this is validation,
                            not formatting.
    list_style_modifiers    REAL. A maintained vocabulary of lighting, lens,
                            film stock, composition and medium terms, grouped
                            so the agent draws what it needs rather than
                            stuffing every modifier into one prompt.

Agent 5:
    generate_image          REAL external API — Stability AI v2beta. Costs real
                            money, so it goes through the human approval gate
                            first, and carries a credit surcharge. With no
                            STABILITY_API_KEY it returns a clear "not
                            configured" result; it never fabricates an image.
    resize_image            REAL. Pillow, in-process, on an asset already
                            generated in this session — so a different size
                            costs nothing rather than being another render.

The negative-prompt generator from the brief is deliberately absent: it is
written by the model from the subject in front of it, and is declared as a
reasoning tool in the registry.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import re
import time
import uuid
from typing import Any

import httpx

from app.agents.tools.base import (
    ToolContext,
    ToolResult,
    artifact,
    as_json,
    not_configured,
)
from app.config import get_settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# normalize_aspect_ratio
# ---------------------------------------------------------------------------

#: The ratios Stability's v2beta endpoints accept, with a representative pixel
#: size for each. These are not suggestions — passing anything else is a 400,
#: which is why this tool snaps rather than reformats.
SUPPORTED_RATIOS: dict[str, dict[str, Any]] = {
    "1:1": {"width": 1024, "height": 1024, "use": "Avatars, product shots, album art, Instagram feed."},
    "16:9": {"width": 1344, "height": 768, "use": "Hero banners, video thumbnails, desktop wallpaper."},
    "9:16": {"width": 768, "height": 1344, "use": "Stories, Reels, TikTok, phone wallpaper."},
    "4:3": {"width": 1152, "height": 896, "use": "Classic photography, presentation slides."},
    "3:4": {"width": 896, "height": 1152, "use": "Portrait photography, book covers."},
    "3:2": {"width": 1216, "height": 832, "use": "35mm photography, landscape prints."},
    "2:3": {"width": 832, "height": 1216, "use": "Film portrait, movie posters, flyers."},
    "5:4": {"width": 1088, "height": 896, "use": "Large-format photography, framed prints."},
    "4:5": {"width": 896, "height": 1088, "use": "Instagram portrait — the tallest the feed allows."},
    "21:9": {"width": 1536, "height": 640, "use": "Ultrawide cinematic, website headers."},
    "9:21": {"width": 640, "height": 1536, "use": "Tall vertical banners, sidebars."},
}

#: Phrases people use instead of a ratio. Worth mapping because "make me a
#: poster" is far more common in this agent's input than "2:3".
_RATIO_ALIASES: dict[str, str] = {
    "square": "1:1", "avatar": "1:1", "profile": "1:1", "instagram": "1:1",
    "instagram post": "1:1", "album": "1:1", "thumbnail": "16:9",
    "widescreen": "16:9", "youtube": "16:9", "banner": "16:9", "hero": "16:9",
    "desktop": "16:9", "wallpaper": "16:9", "landscape": "16:9",
    "story": "9:16", "stories": "9:16", "reel": "9:16", "reels": "9:16",
    "tiktok": "9:16", "shorts": "9:16", "phone": "9:16", "vertical": "9:16",
    "portrait": "3:4", "headshot": "3:4", "poster": "2:3", "flyer": "2:3",
    "movie poster": "2:3", "book cover": "3:4", "print": "3:2",
    "photo": "3:2", "photograph": "3:2", "35mm": "3:2",
    "instagram portrait": "4:5", "slide": "4:3", "presentation": "4:3",
    "ultrawide": "21:9", "cinematic": "21:9", "cinemascope": "21:9",
}

_RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[:xX×/]\s*(\d+(?:\.\d+)?)")
_DIMS_RE = re.compile(r"(\d{3,5})\s*[x×*]\s*(\d{3,5})")


async def normalize_aspect_ratio(ctx: ToolContext, args: dict) -> ToolResult:
    raw = str(args.get("value") or args.get("aspect_ratio") or "").strip()
    if not raw:
        return ToolResult(
            "Error: `value` was empty. Pass whatever the user said — "
            "'a poster', '16x9', '1080x1920' all work.",
            success=False,
        )

    lowered = raw.lower().strip()
    matched_by = ""
    ratio: str | None = None

    if lowered in SUPPORTED_RATIOS:
        ratio, matched_by = lowered, "exact"
    elif lowered in _RATIO_ALIASES:
        ratio, matched_by = _RATIO_ALIASES[lowered], f"alias '{lowered}'"
    else:
        # Longest alias first, so "instagram portrait" beats "instagram".
        for alias in sorted(_RATIO_ALIASES, key=len, reverse=True):
            if alias in lowered:
                ratio, matched_by = _RATIO_ALIASES[alias], f"alias '{alias}'"
                break

    numeric: float | None = None
    if ratio is None:
        dims = _DIMS_RE.search(raw)
        pair = _RATIO_RE.search(raw)
        if dims:
            numeric = int(dims.group(1)) / max(int(dims.group(2)), 1)
            matched_by = f"pixel dimensions {dims.group(1)}x{dims.group(2)}"
        elif pair:
            numeric = float(pair.group(1)) / max(float(pair.group(2)), 1e-9)
            matched_by = f"ratio {pair.group(1)}:{pair.group(2)}"
        elif _is_number(lowered):
            numeric = float(lowered)
            matched_by = f"decimal {lowered}"

        if numeric is not None and numeric > 0:
            ratio = _nearest(numeric)

    if ratio is None:
        return ToolResult(
            f"Could not read `{raw}` as an aspect ratio. Supported: "
            f"{', '.join(SUPPORTED_RATIOS)}. Ask the user which one they want, "
            "or pick from the intended use.",
            success=False,
            meta={"supported": list(SUPPORTED_RATIOS)},
        )

    entry = SUPPORTED_RATIOS[ratio]
    exact = numeric is None or abs(numeric - _value(ratio)) < 0.005
    payload = {
        "input": raw,
        "aspect_ratio": ratio,
        "width": entry["width"],
        "height": entry["height"],
        "matched_by": matched_by,
        "exact": exact,
        "typical_use": entry["use"],
        "supported": list(SUPPORTED_RATIOS),
    }
    note = "" if exact else (
        f"\n\nNote: {raw} is not a supported ratio; {ratio} is the closest the "
        "image models accept. Say so if the difference would matter."
    )
    return ToolResult(
        output=f"{raw} → {ratio} ({entry['width']}×{entry['height']}).{note}\n\n{as_json(payload)}",
        meta=payload,
    )


def _value(ratio: str) -> float:
    w, h = ratio.split(":")
    return float(w) / float(h)


def _nearest(target: float) -> str:
    # Compared in log space so 21:9 and 9:21 are equally far from square — a
    # linear comparison biases every ambiguous input towards the wide options.
    return min(
        SUPPORTED_RATIOS,
        key=lambda r: abs(math.log(_value(r)) - math.log(max(target, 1e-9))),
    )


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# list_style_modifiers
# ---------------------------------------------------------------------------

# A working vocabulary, grouped so the agent can draw one or two terms per
# category. Concrete terms only — a real focal length, a real film stock, a
# named lighting setup. "Beautiful" and "high quality" are absent on purpose:
# they consume prompt budget without steering the model anywhere.
STYLE_MODIFIERS: dict[str, dict[str, list[str]]] = {
    "lighting": {
        "natural": ["golden hour", "blue hour", "overcast diffused light", "dappled sunlight", "backlit at sunset", "moonlight", "window light"],
        "studio": ["Rembrandt lighting", "butterfly lighting", "split lighting", "rim lighting", "high key", "low key", "three-point lighting", "softbox key light"],
        "dramatic": ["chiaroscuro", "hard directional light", "single practical light source", "silhouette", "god rays", "caustics"],
        "artificial": ["neon signage glow", "sodium vapour street light", "candlelight", "firelight", "screen glow", "bioluminescence"],
    },
    "camera_lens": {
        "focal_length": ["14mm ultra-wide", "24mm wide", "35mm reportage", "50mm standard", "85mm portrait", "135mm telephoto", "200mm compression", "100mm macro"],
        "aperture": ["f/1.2 razor-thin depth of field", "f/1.8 shallow", "f/2.8", "f/5.6", "f/11 deep focus", "f/16 hyperfocal"],
        "angle": ["eye level", "low angle looking up", "high angle looking down", "dutch angle", "overhead flat lay", "worm's eye view"],
        "shot_size": ["extreme close-up", "close-up", "medium shot", "cowboy shot", "full body", "wide establishing shot"],
        "movement": ["shallow rack focus", "long exposure motion blur", "panning blur", "freeze frame"],
    },
    "film_medium": {
        "stock": ["Kodak Portra 400", "Kodak Ektar 100", "Fujifilm Velvia 50", "Ilford HP5 black and white", "CineStill 800T", "Polaroid SX-70"],
        "process": ["cross-processed", "push-processed one stop", "bleach bypass", "tintype", "cyanotype", "daguerreotype"],
        "digital": ["shot on ARRI Alexa", "shot on RED Komodo", "medium format digital", "drone photography"],
    },
    "art_style": {
        "illustration": ["flat vector illustration", "isometric illustration", "line art", "risograph print", "woodblock print", "children's book watercolour", "technical blueprint"],
        "painting": ["oil on canvas", "gouache", "watercolour wash", "impasto", "acrylic pour", "ink wash"],
        "movement": ["art nouveau", "bauhaus", "brutalist", "swiss international style", "art deco", "ukiyo-e", "constructivist", "memphis design"],
        "digital_art": ["low poly 3D render", "octane render", "clay render", "pixel art", "vaporwave", "cel shaded", "matte painting"],
    },
    "composition": {
        "framing": ["rule of thirds", "centred symmetrical composition", "leading lines", "framed through a doorway", "negative space", "golden ratio spiral"],
        "depth": ["strong foreground element", "layered depth", "atmospheric perspective", "bokeh background", "flat two-dimensional"],
    },
    "colour_mood": {
        "palette": ["monochromatic", "complementary orange and teal", "analogous earth tones", "triadic primaries", "desaturated muted", "high saturation"],
        "temperature": ["warm amber tones", "cool blue tones", "neutral daylight balance", "split toned"],
        "mood": ["melancholic", "serene", "foreboding", "nostalgic", "clinical", "opulent", "austere"],
    },
    "texture_finish": {
        "surface": ["film grain", "halftone dots", "paper texture", "glossy lacquer", "matte finish", "brushed metal", "frosted glass"],
        "atmosphere": ["volumetric fog", "dust motes in the air", "rain on glass", "heat haze", "smoke", "falling snow"],
    },
    "typography_poster": {
        "layout": ["bold sans-serif headline", "condensed grotesque type", "centred serif titling", "stacked type block", "type as image"],
        "note": [
            "Image models render text unreliably. Keep it to a few words, and "
            "put 'misspelled text, garbled letters' in the negative prompt."
        ],
    },
}


async def list_style_modifiers(ctx: ToolContext, args: dict) -> ToolResult:
    category = (args.get("category") or "").strip().lower().replace(" ", "_")
    if category:
        matched = {k: v for k, v in STYLE_MODIFIERS.items() if category in k}
        if not matched:
            return ToolResult(
                f"Unknown category `{category}`. Available: "
                f"{', '.join(STYLE_MODIFIERS)}.",
                success=False,
            )
        payload: dict[str, Any] = matched
    else:
        payload = STYLE_MODIFIERS

    return ToolResult(
        output=(
            "Draw one or two terms from the categories you actually need. A "
            "prompt carrying every modifier steers less than one carrying "
            "four.\n\n" + as_json(payload, limit=16_000)
        ),
        meta={"categories": list(payload)},
    )


# ---------------------------------------------------------------------------
# generate_image
# ---------------------------------------------------------------------------

#: Generated assets, per session. Held in process rather than persisted: the
#: bytes are handed to the browser as a data URI immediately, and `resize_image`
#: is the only thing that needs them afterwards. Bounded so a long session
#: cannot grow without limit.
_ASSETS: dict[str, dict[str, Any]] = {}
_ASSET_LIMIT = 24

#: Stability's v2beta model paths. `core` is the cheapest and the default; the
#: others cost materially more per image, which is why switching is a config
#: change rather than something the model can choose per call.
_STABILITY_ENDPOINTS = {
    "core": "https://api.stability.ai/v2beta/stable-image/generate/core",
    "sd3.5-large": "https://api.stability.ai/v2beta/stable-image/generate/sd3",
    "ultra": "https://api.stability.ai/v2beta/stable-image/generate/ultra",
}

#: Style presets Stability's `core` endpoint accepts. Anything else is a 400.
STYLE_PRESETS = (
    "3d-model", "analog-film", "anime", "cinematic", "comic-book",
    "digital-art", "enhance", "fantasy-art", "isometric", "line-art",
    "low-poly", "modeling-compound", "neon-punk", "origami",
    "photographic", "pixel-art", "tile-texture",
)


async def generate_image(ctx: ToolContext, args: dict) -> ToolResult:
    """Render a real image. Gated by human approval, and charged for."""
    settings = get_settings()

    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("Error: `prompt` was empty.", success=False)

    ratio = str(args.get("aspect_ratio") or "1:1").strip()
    if ratio not in SUPPORTED_RATIOS:
        return ToolResult(
            f"Error: `{ratio}` is not a supported aspect ratio. Call "
            f"`normalize_aspect_ratio` first. Supported: {', '.join(SUPPORTED_RATIOS)}.",
            success=False,
        )
    negative = str(args.get("negative_prompt") or "").strip()
    preset = str(args.get("style_preset") or "").strip()
    if preset and preset not in STYLE_PRESETS:
        return ToolResult(
            f"Error: `{preset}` is not a valid style preset. Choose from: "
            f"{', '.join(STYLE_PRESETS)}, or omit it.",
            success=False,
        )

    if not settings.image_gen_enabled:
        env_key = (
            "STABILITY_API_KEY"
            if settings.image_gen_provider == "stability"
            else "IMAGE_GEN_API_KEY"
        )
        return not_configured(
            "generate_image",
            env_key,
            "No image was generated and none can be. Tell the user image "
            "generation is not configured — do not describe an image, do not "
            "produce a placeholder URL, and do not draw ASCII art instead.",
        )

    # The spend gate. An image costs real money at the provider AND real
    # credits here, so a person confirms the parameters before either is spent.
    from app.agents import approvals
    from app.credits import UnknownImagePricing, surcharge_for

    # Refuse before spending anything if the configured model has no published
    # price. Rendering first and charging a guess afterwards is how a pricier
    # tier gets billed at the cheap tier's rate indefinitely; the whole point of
    # deriving the surcharge from STABILITY_MODEL is that this cannot happen
    # quietly. The message names the fix.
    try:
        cost = surcharge_for("generate_image")
    except UnknownImagePricing as exc:
        return ToolResult(
            f"Error: {exc}\n\nNothing was generated and nothing was charged — "
            "the render is refused rather than billed at a rate that may be "
            "wrong. Tell the user this is a server misconfiguration.",
            success=False,
            meta={"pricing_error": True, "model": settings.stability_model},
        )

    decision = await approvals.request(
        ctx,
        action="generate_image",
        summary=f"Generate a {ratio} image ({settings.stability_model})",
        parameters={
            "prompt": prompt,
            "negative_prompt": negative,
            "aspect_ratio": ratio,
            "style_preset": preset,
        },
        risk="medium",
        editable=["prompt", "negative_prompt", "aspect_ratio", "style_preset"],
        cost_note=(
            f"Costs {cost:.0f} credits — the published price of one "
            f"`{settings.stability_model}` image at "
            f"{settings.image_gen_provider}."
        ),
    )
    if not decision.approved:
        return ToolResult(
            f"The user did not approve the render ({decision.decision}). Nothing "
            "was generated and nothing was charged. Ask what to change rather "
            "than re-requesting.",
            success=False,
            meta={"approval": decision.as_dict()},
        )

    final = decision.parameters
    prompt = str(final.get("prompt") or prompt).strip()
    negative = str(final.get("negative_prompt") or negative).strip()
    ratio = str(final.get("aspect_ratio") or ratio).strip()
    preset = str(final.get("style_preset") or preset).strip()
    if ratio not in SUPPORTED_RATIOS:
        ratio = "1:1"

    started = time.monotonic()
    try:
        data, meta = await _stability_generate(prompt, negative, ratio, preset)
    except _ProviderError as exc:
        return ToolResult(f"Error: {exc}", success=False)

    elapsed = time.monotonic() - started
    asset = _store_asset(ctx.session_id, data, meta | {"prompt": prompt, "aspect_ratio": ratio})

    payload = {
        "asset_id": asset["id"],
        "provider": settings.image_gen_provider,
        "model": meta.get("model"),
        "aspect_ratio": ratio,
        "width": asset["width"],
        "height": asset["height"],
        "bytes": len(data),
        "seed": meta.get("seed"),
        "style_preset": preset or None,
        "prompt": prompt,
        "negative_prompt": negative or None,
        "elapsed_seconds": round(elapsed, 2),
    }
    return ToolResult(
        output=(
            f"Generated a {asset['width']}×{asset['height']} image in "
            f"{elapsed:.1f}s. asset_id `{asset['id']}` — pass it to "
            "`resize_image` for a different size rather than regenerating.\n\n"
            + as_json(payload)
        ),
        meta={
            **payload,
            # The bytes travel to the browser here rather than through a
            # storage round trip: the image is already in memory, and the chat
            # needs it now. `data_url` is what the UI renders inline.
            **artifact(
                "image",
                {
                    "data_url": asset["data_url"],
                    "asset_id": asset["id"],
                    "width": asset["width"],
                    "height": asset["height"],
                    "prompt": prompt,
                    "negative_prompt": negative or None,
                    "aspect_ratio": ratio,
                    "model": meta.get("model"),
                    "seed": meta.get("seed"),
                    "provider": settings.image_gen_provider,
                },
            ),
        },
    )


class _ProviderError(RuntimeError):
    pass


async def _stability_generate(
    prompt: str, negative: str, ratio: str, preset: str
) -> tuple[bytes, dict[str, Any]]:
    settings = get_settings()
    model = settings.stability_model
    endpoint = _STABILITY_ENDPOINTS.get(model)
    if endpoint is None:
        raise _ProviderError(
            f"STABILITY_MODEL=`{model}` is not one this tool knows how to call. "
            f"Use one of: {', '.join(_STABILITY_ENDPOINTS)}."
        )

    # multipart/form-data with an explicitly empty file part: the v2beta API
    # requires a multipart body even when there is no image to send, and a
    # plain JSON POST is rejected with a 400 that does not say so.
    form: dict[str, Any] = {
        "prompt": (None, prompt),
        "aspect_ratio": (None, ratio),
        "output_format": (None, "png"),
        "mode": (None, "text-to-image"),
    }
    if negative:
        form["negative_prompt"] = (None, negative)
    if preset:
        form["style_preset"] = (None, preset)
    if model == "sd3.5-large":
        form["model"] = (None, "sd3.5-large")

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {settings.stability_api_key}",
                    # `image/*` returns the bytes directly; the default JSON
                    # envelope would base64 them and double the transfer.
                    "Accept": "image/*",
                },
                files=form,
            )
    except Exception as exc:  # noqa: BLE001
        raise _ProviderError(
            f"the image provider could not be reached ({type(exc).__name__}: {exc})"
        ) from exc

    if response.status_code == 402:
        raise _ProviderError(
            "the Stability account is out of credits. Nothing was generated. "
            "Tell the user their image-generation balance needs topping up."
        )
    if response.status_code == 403:
        raise _ProviderError(
            "the provider refused this prompt (content moderation). Rephrase "
            "the subject rather than retrying the same words."
        )
    if response.status_code >= 400:
        raise _ProviderError(
            f"the image provider returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    return response.content, {
        "model": model,
        "seed": response.headers.get("seed"),
        "finish_reason": response.headers.get("finish-reason"),
    }


def _store_asset(session_id: str, data: bytes, meta: dict[str, Any]) -> dict[str, Any]:
    width, height = _dimensions(data)
    asset_id = f"img_{uuid.uuid4().hex[:10]}"
    entry = {
        "id": asset_id,
        "session_id": session_id,
        "bytes": data,
        "width": width,
        "height": height,
        "mime": "image/png",
        "data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
        "meta": meta,
        "created": time.time(),
    }
    _ASSETS[asset_id] = entry
    # Oldest out first. These are megabytes each, so the cap is small.
    while len(_ASSETS) > _ASSET_LIMIT:
        oldest = min(_ASSETS, key=lambda k: _ASSETS[k]["created"])
        _ASSETS.pop(oldest, None)
    return entry


def _dimensions(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except Exception:  # noqa: BLE001
        return 0, 0


# ---------------------------------------------------------------------------
# resize_image
# ---------------------------------------------------------------------------


async def resize_image(ctx: ToolContext, args: dict) -> ToolResult:
    """Resize an asset generated earlier in this session, locally.

    Deliberately not a re-render: regenerating at a new size costs another
    provider charge *and* produces a different picture. Resizing costs neither.
    """
    asset_id = str(args.get("asset_id") or "").strip()
    asset = _ASSETS.get(asset_id)
    if asset is None:
        available = [a for a, v in _ASSETS.items() if v["session_id"] == ctx.session_id]
        return ToolResult(
            f"Error: no asset `{asset_id}` in this session. Generate an image "
            f"first. Available: {', '.join(available) or '(none)'}.",
            success=False,
        )
    if asset["session_id"] != ctx.session_id:
        return ToolResult("Error: that asset belongs to a different session.", False)

    width = _int_or_none(args.get("width"))
    height = _int_or_none(args.get("height"))
    if not width and not height:
        return ToolResult("Error: give `width`, `height`, or both.", success=False)

    fit = str(args.get("fit") or "contain").lower()
    if fit not in {"contain", "cover", "stretch"}:
        return ToolResult("Error: `fit` must be contain, cover or stretch.", False)

    try:
        data, out_w, out_h = await asyncio.to_thread(
            _resize_bytes, asset["bytes"], width, height, fit
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Resize failed", exc_info=True)
        return ToolResult(f"Error: could not resize ({type(exc).__name__}: {exc}).", False)

    new_asset = _store_asset(
        ctx.session_id,
        data,
        asset["meta"] | {"derived_from": asset_id, "fit": fit},
    )
    payload = {
        "asset_id": new_asset["id"],
        "derived_from": asset_id,
        "width": out_w,
        "height": out_h,
        "fit": fit,
        "bytes": len(data),
        "original": {"width": asset["width"], "height": asset["height"]},
    }
    return ToolResult(
        output=(
            f"Resized {asset['width']}×{asset['height']} → {out_w}×{out_h} "
            f"({fit}). New asset_id `{new_asset['id']}`.\n\n{as_json(payload)}"
        ),
        meta={
            **payload,
            **artifact(
                "image",
                {
                    "data_url": new_asset["data_url"],
                    "asset_id": new_asset["id"],
                    "width": out_w,
                    "height": out_h,
                    "prompt": asset["meta"].get("prompt"),
                    "aspect_ratio": asset["meta"].get("aspect_ratio"),
                    "derived_from": asset_id,
                },
            ),
        },
    )


def _resize_bytes(
    data: bytes, width: int | None, height: int | None, fit: str
) -> tuple[bytes, int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        src_w, src_h = img.size
        target_w = width or (int(src_w * (height / src_h)) if height else src_w)
        target_h = height or (int(src_h * (width / src_w)) if width else src_h)
        target_w = max(1, min(target_w, 8192))
        target_h = max(1, min(target_h, 8192))

        if fit == "stretch":
            out = img.resize((target_w, target_h), Image.LANCZOS)
        else:
            scale_fn = min if fit == "contain" else max
            scale = scale_fn(target_w / src_w, target_h / src_h)
            scaled = img.resize(
                (max(1, round(src_w * scale)), max(1, round(src_h * scale))),
                Image.LANCZOS,
            )
            if fit == "contain":
                out = scaled
            else:
                # Cover fills the box, so the overflow is centre-cropped —
                # cropping from a corner would cut the subject off one side.
                left = (scaled.width - target_w) // 2
                top = (scaled.height - target_h) // 2
                out = scaled.crop((left, top, left + target_w, top + target_h))

        buffer = io.BytesIO()
        out.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), out.width, out.height


def _int_or_none(raw: Any) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, dict] = {
    "normalize_aspect_ratio": {
        "name": "normalize_aspect_ratio",
        "description": (
            "Snap any expression of an aspect ratio onto one the image models "
            "actually accept, and get the pixel dimensions that follow. Handles "
            "plain ratios ('16:9'), pixel sizes ('1080x1920') and intent ('a "
            "poster', 'for stories'). Call it before naming a ratio in a prompt "
            "— an unsupported ratio is a failed render, not a rounding issue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                    "description": "Whatever the user said, verbatim.",
                }
            },
            "required": ["value"],
        },
    },
    "list_style_modifiers": {
        "name": "list_style_modifiers",
        "description": (
            "Fetch the vocabulary of lighting, lens, film stock, art style, "
            "composition, colour and texture terms to build a prompt from. Pass "
            "`category` to narrow it. Draw one or two terms from the categories "
            "you actually need — a prompt carrying every modifier steers less "
            "than one carrying four."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": list(STYLE_MODIFIERS),
                    "description": "Narrow to one category. Omit for all of them.",
                }
            },
            "required": [],
        },
    },
    "generate_image": {
        "name": "generate_image",
        "description": (
            "Generate a real image through the configured provider. This costs "
            "real money, so it PAUSES and asks the user to approve, edit or "
            "reject the parameters first. Call `normalize_aspect_ratio` before "
            "this so the ratio is a supported one. Returns an `asset_id` — use "
            "`resize_image` on it for a different size instead of generating "
            "again. If the provider is not configured this returns a clear "
            "not-configured result: report that and stop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The full image prompt, comma-separated.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": list(SUPPORTED_RATIOS),
                    "description": "A supported ratio. Default 1:1.",
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "What to exclude, drawn from this subject's likely failure modes.",
                },
                "style_preset": {
                    "type": "string",
                    "enum": list(STYLE_PRESETS),
                    "description": "Optional provider style preset.",
                },
            },
            "required": ["prompt"],
        },
    },
    "resize_image": {
        "name": "resize_image",
        "description": (
            "Resize an image already generated in this session. Free and "
            "instant — always prefer it over regenerating at a different size, "
            "which costs another charge and produces a different picture. Give "
            "`width`, `height`, or both; with one, the other is derived from "
            "the aspect ratio."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {
                    "type": "string",
                    "description": "The `asset_id` from a previous generate_image.",
                },
                "width": {"type": "integer", "description": "Target width in pixels."},
                "height": {"type": "integer", "description": "Target height in pixels."},
                "fit": {
                    "type": "string",
                    "enum": ["contain", "cover", "stretch"],
                    "description": (
                        "contain preserves the whole image (default); cover "
                        "fills the box and centre-crops; stretch distorts."
                    ),
                },
            },
            "required": ["asset_id"],
        },
    },
}

HANDLERS = {
    "normalize_aspect_ratio": normalize_aspect_ratio,
    "list_style_modifiers": list_style_modifiers,
    "generate_image": generate_image,
    "resize_image": resize_image,
}
