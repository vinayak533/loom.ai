"""Live checks for the OpenCode provider, through the real router.

Hits the OpenCode API — costs a few tokens per model. Run with:

    python scripts/test_opencode_models.py

Covers: basic chat, streaming, tool-calling, and that image blocks reach the
models that can see and are replaced by a text note for the ones that cannot.
"""

from __future__ import annotations

import asyncio
import base64
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.llm_router import (  # noqa: E402
    HINT_MODEL,
    call_model,
    display_name,
    is_available,
    model_meta,
    supports_vision,
)

OPENCODE_MODELS = [
    mid
    for mid, meta in __import__("app.llm_router", fromlist=["x"]).MODEL_REGISTRY.items()
    if meta["provider"] == "opencode"
]

WEATHER_TOOL = [
    {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }
]


def red_png(w: int = 128, h: int = 128) -> bytes:
    raw = b"".join(b"\x00" + bytes((220, 30, 30)) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


IMAGE_BLOCK = {
    "type": "image",
    "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(red_png()).decode(),
    },
}


async def collect(stream) -> tuple[str, str, object]:
    """Drain a router stream into (text, thinking, final message)."""
    text, thinking, final = "", "", None
    async for e in stream:
        if e.kind == "text_delta":
            text += e.content or ""
        elif e.kind == "thinking_delta":
            thinking += e.content or ""
        elif e.kind == "done":
            final = e.message
    return text, thinking, final


async def check(model_id: str) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    # --- basic chat, streamed -------------------------------------------
    try:
        text, thinking, msg = await collect(
            call_model(
                model_id,
                messages=[{"role": "user", "content": "Reply with exactly: PONG"}],
                tools=[],
                stream=True,
            )
        )
        ok = "PONG" in text.upper()
        detail = f"{text.strip()[:40]!r}"
        if thinking:
            detail += f" (+{len(thinking)} chars reasoning)"
        if msg:
            detail += f" in={msg.usage.get('input_tokens')} out={msg.usage.get('output_tokens')}"
        out.append(("chat+stream", ok, detail))
    except Exception as exc:  # noqa: BLE001
        out.append(("chat+stream", False, f"{type(exc).__name__}: {exc}"))

    # --- tool calling ----------------------------------------------------
    try:
        msg = await call_model(
            model_id,
            messages=[
                {"role": "user", "content": "What's the weather in Paris? Use the tool."}
            ],
            tools=WEATHER_TOOL,
            stream=False,
        )
        calls = [b for b in msg.content if b.get("type") == "tool_use"]
        expected = model_meta(model_id)["supports_tools"]
        if expected:
            ok = bool(calls)
            detail = (
                f"{calls[0]['name']}({calls[0]['input']})" if calls else "no tool_use block"
            )
        else:
            # Degradation path: tools are stripped, prose is still fine.
            ok = not calls
            detail = "tools stripped as expected"
        out.append(("tools", ok, detail))
    except Exception as exc:  # noqa: BLE001
        out.append(("tools", False, f"{type(exc).__name__}: {exc}"))

    # --- image handling --------------------------------------------------
    try:
        text, _, _ = await collect(
            call_model(
                model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            IMAGE_BLOCK,
                            {
                                "type": "text",
                                "text": "What colour is this image? One word.",
                            },
                        ],
                    }
                ],
                tools=[],
                stream=True,
            )
        )
        if supports_vision(model_id):
            ok = "red" in text.lower()
            detail = f"saw: {text.strip()[:60]!r}"
        else:
            # Must not crash, and must not claim to have seen anything.
            ok = "red" not in text.lower()
            detail = f"declined cleanly: {text.strip()[:60]!r}"
        out.append(("image", ok, detail))
    except Exception as exc:  # noqa: BLE001
        out.append(("image", False, f"{type(exc).__name__}: {exc}"))

    return out


async def main() -> int:
    print("hint routing:")
    for hint, mid in HINT_MODEL.items():
        print(f"  {hint:20} -> {mid} ({display_name(mid)})")
    print()

    failures = 0
    for model_id in OPENCODE_MODELS:
        if not is_available(model_id):
            print(f"SKIP {model_id} — OPENCODE_API_KEY not set")
            continue
        vis = "vision" if supports_vision(model_id) else "no-vision"
        print(f"--- {display_name(model_id)} ({model_id}, {vis})")
        for name, ok, detail in await check(model_id):
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'} {name:12} {detail}")
    print("\nall checks passed" if not failures else f"\n{failures} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
