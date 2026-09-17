"""Agent 1 — Document Summarizer.

    parse_source     REAL. Reuses the Learn section's extractor
                     (`app.learn.ingest`) and the existing upload pipeline
                     (`app.files`) — PDF text layer, URL reader, YouTube
                     transcript, pasted text. No new ingestion path was built.
    chunk_document   REAL. Reuses `app.learn.chunking.chunk`, the same
                     paragraph-boundary splitter the notebook retrieval index
                     uses, so a chunk here reads the way a chunk there does.
    count_tokens     REAL. tiktoken, with a documented character heuristic as
                     the last resort. Exact only where tiktoken actually ships
                     the running model's vocabulary; for every other family it
                     counts with cl100k_base and says in the result that the
                     number is an approximation. A count labelled as matched
                     when it was not is worse than an honest estimate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agents.tools.base import ToolContext, ToolResult, as_json
from app.learn.chunking import chunk as chunk_text, normalise

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

#: Model family -> tiktoken vocabulary, for the families where tiktoken really
#: has the matching one. Matched as a prefix on the model id.
#:
#: This used to be keyed on *provider*, and every provider in the registry
#: mapped to `cl100k_base` — as did the fallback. So the tool returned
#: `cl100k_base` for every model in the roster while its description promised a
#: tokenizer matched to the running model. The number was fine as an estimate
#: and the claim around it was not.
#:
#: The list is short because it is honest. tiktoken ships OpenAI's vocabularies;
#: Llama, Qwen, DeepSeek, MiniMax and Nemotron each use their own SentencePiece
#: or BPE vocabulary that is not among them, and mapping them to a cl100k they
#: do not use would be the same false claim with more entries.
_ENCODING_FOR_FAMILY: dict[str, str] = {
    # OpenAI's open-weight models ship with the harmony vocabulary.
    "gpt-oss": "o200k_harmony",
}

#: Used for every model with no exact vocabulary of its own. It is a real BPE
#: tokenizer over English, so it is a much better estimate than counting
#: characters — but it is an estimate, and `_describe` says so in the result.
_DEFAULT_ENCODING = "cl100k_base"

#: The last resort, when tiktoken itself is unavailable. Four characters per
#: token is the long-standing rule of thumb for English prose and is wrong by
#: roughly ±15% — stated in the output so the number is never mistaken for a
#: measurement.
_CHARS_PER_TOKEN = 4.0


def _encoding_for(model_id: str) -> tuple[str, bool]:
    """The vocabulary to count ``model_id`` with, and whether it is really its.

    The boolean is the whole point of returning a tuple: it is what stops the
    result claiming a model-matched count when what it did was approximate one
    family's tokens with another family's vocabulary.
    """
    key = (model_id or "").strip().lower()
    for family, encoding in _ENCODING_FOR_FAMILY.items():
        if key.startswith(family):
            return encoding, True
    return _DEFAULT_ENCODING, False


async def count_tokens(ctx: ToolContext, args: dict) -> ToolResult:
    text = str(args.get("text") or "")
    if not text:
        return ToolResult("Error: `text` was empty.", success=False)

    model_id = (args.get("model_id") or ctx.model_id or "").strip()
    encoding_name, exact = _encoding_for(model_id)

    count, method = await asyncio.to_thread(_tiktoken_count, text, encoding_name)

    if count is None:
        exact = False
        count = int(len(text) / _CHARS_PER_TOKEN)
        method = (
            f"character heuristic ({_CHARS_PER_TOKEN:g} chars/token, ±15% — an "
            "estimate, not a measurement)"
        )
    elif not exact:
        # Say it here, once, rather than leaving the caller to infer it from a
        # vocabulary name it has no reason to recognise.
        method = (
            f"{method} — an approximation. {model_id or 'This model'} uses its "
            "own vocabulary, which tiktoken does not ship; expect the real "
            "count to differ by roughly 10-20%."
        )

    payload = {
        "tokens": count,
        "characters": len(text),
        "words": len(text.split()),
        "model_id": model_id or "(unknown)",
        "tokenizer": method,
        # A machine-readable version of the same fact, so a caller can branch
        # on it without parsing the sentence above.
        "exact_for_model": exact,
    }
    headline = (
        f"{count:,} tokens for {len(text):,} characters."
        if exact
        else f"~{count:,} tokens for {len(text):,} characters (approximate)."
    )
    return ToolResult(
        output=f"{headline}\n\n{as_json(payload)}",
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
            "Count the tokens in some text with a real BPE tokenizer. Call it "
            "on any source before summarising it — the count is what goes in "
            "your metadata block, and it tells you whether the material fits "
            "in one pass. It is an approximate count unless the result says "
            "otherwise: tiktoken ships OpenAI's vocabularies, so for any other "
            "model family the number is a close estimate rather than that "
            "model's own count. The result names the tokenizer it used and "
            "sets `exact_for_model`; report whichever it says, and never "
            "describe an approximate count as the model's exact one."
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
