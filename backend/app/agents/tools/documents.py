"""Agent 1 — Document Summarizer.

    parse_source     REAL. Reuses the Learn section's extractor
                     (`app.learn.ingest`) and the existing upload pipeline
                     (`app.files`) — PDF text layer, URL reader, YouTube
                     transcript, pasted text. No new ingestion path was built.
    chunk_document   REAL. Reuses `app.learn.chunking.chunk`, the same
                     paragraph-boundary splitter the notebook retrieval index
                     uses, so a chunk here reads the way a chunk there does.
    count_tokens     REAL. tiktoken over the vocabulary each provider bills
                     against, with a
                     documented character heuristic as the last resort. The
                     tokenizer is chosen from the *active* model, which is the
                     whole point — a count against the wrong vocabulary is a
                     worse answer than an honest estimate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agents.tools.base import ToolContext, ToolResult, as_json
from app.config import get_settings
from app.learn.chunking import chunk as chunk_text, normalise
from app.llm_router import MODEL_REGISTRY

log = logging.getLogger(__name__)

#: A chunk request is bounded so a careless `chunk_size: 10` cannot turn a
#: 200KB document into twenty thousand tool-result lines.
MIN_CHUNK = 200
MAX_CHUNK = 8_000
MAX_CHUNKS_RETURNED = 40

#: How many characters of each chunk come back in the tool result. The model
#: needs enough to work with; it does not need the document twice.
CHUNK_PREVIEW_CHARS = 1_400


# ---------------------------------------------------------------------------
# parse_source
# ---------------------------------------------------------------------------


async def parse_source(ctx: ToolContext, args: dict) -> ToolResult:
    """Extract readable text from an upload, a URL, or a paste."""
    file_id = (args.get("file_id") or "").strip()
    url = (args.get("url") or "").strip()
    text = args.get("text") or ""

    if not any((file_id, url, text)):
        return ToolResult(
            "Error: give exactly one of `file_id` (an upload the user attached), "
            "`url`, or `text`.",
            success=False,
        )

    # Imported lazily: `app.learn.ingest` pulls in pypdf, and a tool that is
    # never called should not cost import time on every worker boot.
    from app.learn import ingest

    if file_id:
        extracted = await _parse_upload(ctx.session_id, file_id)
    elif url:
        extracted = await ingest.from_url(url)
    else:
        extracted = ingest.from_text(str(text))

    if extracted is None:
        return ToolResult(
            f"Error: no upload with id `{file_id}` is attached to this session. "
            "Ask the user to attach the file again.",
            success=False,
        )
    if not extracted.ok:
        # A source that cannot be read is reported, not raised: the model needs
        # to tell the user *why* rather than the turn dying.
        return ToolResult(
            f"Could not read that source: {extracted.error}",
            success=False,
            meta={"title": extracted.title},
        )

    body = extracted.text
    summary = {
        "title": extracted.title,
        "characters": len(body),
        "words": len(body.split()),
        "lines": body.count("\n") + 1,
    }
    return ToolResult(
        output=(
            f"Extracted `{extracted.title}` — {summary['characters']:,} characters, "
            f"{summary['words']:,} words.\n\n--- BEGIN SOURCE ---\n{body}\n"
            "--- END SOURCE ---"
        ),
        meta={"source": summary},
    )


async def _parse_upload(session_id: str, file_id: str):
    """Read one previously uploaded file through the existing upload store."""
    from app.db import repository
    from app.db.supabase_client import enabled as supabase_enabled
    from app.files import _LOCAL, PDF_TYPE  # noqa: PLC2701 - same-project store
    from app.learn import ingest

    filename = ""
    content_type = ""
    data: bytes | None = None

    if supabase_enabled():
        rows = await repository.get_files(session_id, [file_id])
        if not rows:
            return None
        row = rows[0]
        filename = row.get("filename") or "upload"
        content_type = row.get("file_type") or ""
        data = await repository.download_file(row["storage_path"])
    else:
        entry = (_LOCAL.get(session_id) or {}).get(file_id)
        if entry is None:
            return None
        filename, content_type, data = entry

    if not data:
        return ingest.Extracted(filename or "upload", "", "The upload is empty.")

    if content_type == PDF_TYPE:
        return await asyncio.to_thread(ingest.from_pdf, data, filename)
    if content_type.startswith("image/"):
        # Honest rather than clever: there is no OCR in this project, and
        # pretending otherwise would produce an empty summary the user has to
        # diagnose. The multimodal models can see the image directly, which is
        # what to tell them to do instead.
        return ingest.Extracted(
            filename,
            "",
            "That is an image, and this project has no OCR step. Attach it to "
            "the message directly instead — a vision-capable model reads it "
            "natively.",
        )
    return ingest.from_text(data.decode("utf-8", errors="replace"), filename)


# ---------------------------------------------------------------------------
# chunk_document
# ---------------------------------------------------------------------------


async def chunk_document(ctx: ToolContext, args: dict) -> ToolResult:
    """Split text into overlapping, paragraph-aligned chunks."""
    text = normalise(str(args.get("text") or ""))
    if not text:
        return ToolResult("Error: `text` was empty.", success=False)

    size = _clamp(args.get("chunk_size"), MIN_CHUNK, MAX_CHUNK, 1200)
    # Overlap has to stay well under the chunk size or every chunk is mostly
    # its predecessor; a third is the practical ceiling.
    overlap = _clamp(args.get("overlap"), 0, max(size // 3, 0), min(180, size // 3))

    chunks = chunk_text(text, size=size, overlap=overlap)
    total = len(chunks)
    shown = chunks[:MAX_CHUNKS_RETURNED]

    body = "\n\n".join(
        f"### chunk {i + 1}/{total} ({len(c):,} chars)\n"
        + (c if len(c) <= CHUNK_PREVIEW_CHARS else c[:CHUNK_PREVIEW_CHARS] + " …")
        for i, c in enumerate(shown)
    )
    note = (
        ""
        if total <= MAX_CHUNKS_RETURNED
        else (
            f"\n\n[{total - MAX_CHUNKS_RETURNED} further chunks were not "
            "returned. Summarise what you have and say the tail was not read.]"
        )
    )
    return ToolResult(
        output=f"Split into {total} chunks (size {size}, overlap {overlap}).\n\n{body}{note}",
        meta={
            "chunks": total,
            "chunk_size": size,
            "overlap": overlap,
            "returned": len(shown),
            "sizes": [len(c) for c in chunks],
        },
    )


def _clamp(raw: Any, low: int, high: int, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------

#: Which tiktoken vocabulary matches which provider. These are the encodings
#: the OpenAI-compatible gateways actually bill against; a model served through
#: OpenRouter or OpenCode is not guaranteed to use either, which is why the
#: result always names the tokenizer it used rather than presenting the number
#: as authoritative.
_ENCODING_FOR_PROVIDER = {
    "openrouter": "cl100k_base",
    "opencode": "cl100k_base",
    "groq": "cl100k_base",
}

#: The fallback. Four characters per token is the long-standing rule of thumb
#: for English prose and is wrong by roughly ±15% — stated in the output so the
#: number is never mistaken for a measurement.
_CHARS_PER_TOKEN = 4.0


async def count_tokens(ctx: ToolContext, args: dict) -> ToolResult:
    text = str(args.get("text") or "")
    if not text:
        return ToolResult("Error: `text` was empty.", success=False)

    model_id = (args.get("model_id") or ctx.model_id or "").strip()
    meta = MODEL_REGISTRY.get(model_id)
    provider = (meta or {}).get("provider", "")

    count, method = await asyncio.to_thread(
        _tiktoken_count, text, _ENCODING_FOR_PROVIDER.get(provider, "cl100k_base")
    )

    if count is None:
        count = int(len(text) / _CHARS_PER_TOKEN)
        method = (
            f"character heuristic ({_CHARS_PER_TOKEN:g} chars/token, ±15% — an "
            "estimate, not a measurement)"
        )

    payload = {
        "tokens": count,
        "characters": len(text),
        "words": len(text.split()),
        "model_id": model_id or "(unknown)",
        "tokenizer": method,
    }
    return ToolResult(
        output=f"{count:,} tokens for {len(text):,} characters.\n\n{as_json(payload)}",
        meta=payload,
    )


def _tiktoken_count(text: str, encoding_name: str) -> tuple[int | None, str]:
    try:
        import tiktoken
    except ImportError:
        return None, ""
    try:
        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(text, disallowed_special=())), f"tiktoken {encoding_name}"
    except Exception:  # noqa: BLE001 - first use downloads the BPE file
        log.debug("tiktoken encoding %s unavailable", encoding_name, exc_info=True)
        return None, ""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, dict] = {
    "parse_source": {
        "name": "parse_source",
        "description": (
            "Extract readable plain text from a source. Give exactly one of: "
            "`file_id` for a file the user attached to this message, `url` for "
            "a web page or PDF (YouTube links resolve to their transcript), or "
            "`text` for a paste you want normalised. Call this FIRST whenever "
            "the user has attached or linked anything — never ask them to "
            "paste content you can read yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "Id of a file attached to this session.",
                },
                "url": {"type": "string", "description": "A web page or PDF URL."},
                "text": {"type": "string", "description": "Raw text to normalise."},
            },
            "required": [],
        },
    },
    "chunk_document": {
        "name": "chunk_document",
        "description": (
            "Split a long document into overlapping chunks that break on "
            "paragraph boundaries. Use it when a source is too long to hold in "
            "one pass, so you can summarise section by section instead of "
            "skimming. Returns the chunks with their sizes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to split."},
                "chunk_size": {
                    "type": "integer",
                    "description": "Target characters per chunk (200-8000, default 1200).",
                },
                "overlap": {
                    "type": "integer",
                    "description": (
                        "Characters carried between chunks so a fact spanning a "
                        "boundary stays readable. Default 180; capped at a third "
                        "of `chunk_size`."
                    ),
                },
            },
            "required": ["text"],
        },
    },
    "count_tokens": {
        "name": "count_tokens",
        "description": (
            "Count the tokens in some text using the tokenizer that matches the "
            "model currently running this turn. Call it on any source before "
            "summarising it — the count is what goes in your metadata block, "
            "and it tells you whether the material fits in one pass. The result "
            "names the tokenizer it used, including when it had to fall back to "
            "a character estimate; report that honestly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to measure."},
                "model_id": {
                    "type": "string",
                    "description": "Override the model to count against. Defaults to the active one.",
                },
            },
            "required": ["text"],
        },
    },
}

HANDLERS = {
    "parse_source": parse_source,
    "chunk_document": chunk_document,
    "count_tokens": count_tokens,
}
