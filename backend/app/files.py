"""Upload handling: PDFs and images the user drops into the chat.

Storage goes to Supabase Storage with metadata in the `files` table. When
Supabase is not configured we fall back to an in-process cache so the feature
still works locally — the cache is intentionally bounded and non-durable.

Images become `image` blocks, which a vision-capable model reads directly.

A PDF is **read here, not forwarded**. Every provider in this project speaks the
OpenAI chat wire format, and that format has no portable document part — so a
`document` block never reached a model at all: `_internal_to_openai` replaced it
with "[A document was attached... its contents are not readable]", for vision
models and text models alike. Attaching a PDF in Chat therefore did nothing
except tell the model it was missing something, which is why asking for a
summary produced a refusal instead of a summary.

So the text layer is extracted at this boundary and sent as text, exactly as a
CSV already is. There is still no OCR: a scanned PDF has no text layer, and it
comes back saying so rather than as silence.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from collections import OrderedDict

from app.db import repository
from app.db.supabase_client import enabled as supabase_enabled
from app.security import safe_filename

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
PDF_TYPE = "application/pdf"
#: Browsers are wildly inconsistent about what they label a .csv — Excel being
#: installed is enough to make Chrome send `application/vnd.ms-excel`. All of
#: these are read as delimited text.
CSV_TYPES = {
    "text/csv",
    "application/csv",
    "text/plain",
    "text/tab-separated-values",
    "application/vnd.ms-excel",
}
ACCEPTED = IMAGE_TYPES | {PDF_TYPE} | CSV_TYPES

# session_id -> {file_id: (filename, content_type, bytes)}
_LOCAL: OrderedDict[str, dict[str, tuple[str, str, bytes]]] = OrderedDict()
_LOCAL_LIMIT = 64


class UploadRejected(ValueError):
    pass


async def save_upload(
    session_id: str, filename: str, content_type: str, data: bytes
) -> dict:
    if content_type not in ACCEPTED:
        raise UploadRejected(
            f"Unsupported type `{content_type}`. Upload a PDF, a CSV, or a "
            "PNG/JPEG/GIF/WebP image."
        )
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected("File is larger than the 20 MB limit.")

    # The name goes into the storage key, and a storage key is a path. A
    # browser-supplied `../../x.pdf` must not be allowed to decide where in
    # the bucket the object lands.
    filename = safe_filename(filename)
    file_id = str(uuid.uuid4())
    storage_path = f"{session_id}/{file_id}-{filename}"

    if supabase_enabled():
        await repository.upload_file(storage_path, data, content_type)
        row = await repository.add_file(session_id, filename, storage_path, content_type)
        file_id = row["id"]
    else:
        bucket = _LOCAL.setdefault(session_id, {})
        bucket[file_id] = (filename, content_type, data)
        _LOCAL.move_to_end(session_id)
        while len(_LOCAL) > _LOCAL_LIMIT:
            _LOCAL.popitem(last=False)

    return {
        "id": file_id,
        "filename": filename,
        "file_type": content_type,
        "size": len(data),
        "storage_path": storage_path,
        "persisted": supabase_enabled(),
    }


async def load_content_blocks(session_id: str, file_ids: list[str]) -> list[dict]:
    """Turn uploaded file ids into internal content blocks.

    `_to_block` runs on a worker thread because two of its branches are real
    CPU work on a file that can be 20 MB: pypdf parsing a PDF's page tree, and
    the CSV sniffer decoding and profiling a spreadsheet. Both used to run
    inline on the event loop, which stalls every other socket on the process
    for as long as they take — and this is called on the path between the user
    pressing Enter and the first token, so the stall is exactly where it is
    most visible.
    """
    if not file_ids:
        return []

    blocks: list[dict] = []

    if supabase_enabled():
        rows = await repository.get_files(session_id, file_ids)
        for row in rows:
            data = await repository.download_file(row["storage_path"])
            if not data:
                continue
            blocks.append(
                await asyncio.to_thread(
                    _to_block, row["file_type"], data, row["filename"]
                )
            )
    else:
        bucket = _LOCAL.get(session_id, {})
        for fid in file_ids:
            entry = bucket.get(fid)
            if not entry:
                continue
            filename, content_type, data = entry
            blocks.append(
                await asyncio.to_thread(_to_block, content_type, data, filename)
            )

    return [b for b in blocks if b]


#: How much of a spreadsheet is handed to the model verbatim after the profile.
#: A profile without rows cannot be reasoned over; the whole file cannot be
#: afforded. This is the compromise, and it is stated in the output so the model
#: knows it is looking at a head rather than the entire table.
CSV_INLINE_ROWS = 200
CSV_SAMPLE_ROWS = 8

#: How much of a PDF's text goes into the prompt. The same compromise as
#: `CSV_INLINE_ROWS` and made for the same reason: `ingest.MAX_SOURCE_CHARS` is
#: 400,000, which is right for Learn — where a source is chunked and retrieved
#: against — and about 100k tokens of prompt here, where it is not. 40,000
#: characters is roughly 10k tokens and covers a resume, a paper, a contract or
#: a chapter whole. Past it the text is cut and the cut is stated in the block,
#: so the model knows it is holding a head rather than the document.
PDF_INLINE_CHARS = 40_000


def _sniff_rows(data: bytes) -> tuple[list[str], list[list[str]], str | None]:
    """Decode and parse delimited text into (header, rows, error)."""
    import csv as csv_mod
    import io

    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return [], [], "Could not decode the file as text."

    try:
        dialect = csv_mod.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv_mod.Error:
        # A single-column file has no delimiter to find, which is not an error.
        dialect = csv_mod.excel

    reader = csv_mod.reader(io.StringIO(text), dialect)
    try:
        all_rows = [r for r in reader if any(c.strip() for c in r)]
    except csv_mod.Error as exc:
        return [], [], f"Malformed delimited text: {exc}"

    if not all_rows:
        return [], [], "The file is empty."
    return all_rows[0], all_rows[1:], None


def _profile_csv(data: bytes, filename: str) -> str:
    """Turn a spreadsheet into something a model can actually reason over.

    A CSV pasted in as raw text is close to useless past a few dozen rows: the
    model spends its attention re-deriving the shape of the table instead of
    answering the question. So this leads with the shape — columns, inferred
    types, ranges, missingness, cardinality — and *then* gives rows. That is
    the difference between "here are 4000 commas" and a table someone can be
    asked questions about.
    """
    header, rows, error = _sniff_rows(data)
    if error:
        return f"# {filename}\n\n[This CSV could not be read: {error}]"

    header = [h.strip() or f"column_{i + 1}" for i, h in enumerate(header)]
    ncols = len(header)
    out: list[str] = [
        f"# {filename}",
        "",
        f"A tabular data file: **{len(rows):,} data rows × {ncols} columns**.",
        "",
        "## Columns",
        "",
        "| # | Column | Type | Missing | Distinct | Summary |",
        "|---|--------|------|---------|----------|---------|",
    ]

    for i, name in enumerate(header):
        values = [(r[i].strip() if i < len(r) else "") for r in rows]
        present = [v for v in values if v != ""]
        missing = len(values) - len(present)
        distinct = len(set(present))

        numbers: list[float] = []
        for v in present:
            try:
                numbers.append(float(v.replace(",", "")))
            except ValueError:
                break

        if present and len(numbers) == len(present):
            kind = "number"
            lo, hi = min(numbers), max(numbers)
            mean = sum(numbers) / len(numbers)
            summary = f"min {lo:g}, max {hi:g}, mean {mean:.4g}"
        elif present and distinct <= max(12, len(present) // 20):
            kind = "category"
            counts: dict[str, int] = {}
            for v in present:
                counts[v] = counts.get(v, 0) + 1
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:4]
            summary = ", ".join(f"{k} ({n})" for k, n in top)
        else:
            kind = "text"
            widths = [len(v) for v in present] or [0]
            summary = f"length {min(widths)}–{max(widths)} chars"

        out.append(
            f"| {i + 1} | `{name}` | {kind} | {missing} | {distinct} | "
            f"{summary.replace('|', '/')} |"
        )

    out += ["", f"## First {min(CSV_SAMPLE_ROWS, len(rows))} rows", ""]
    out.append("| " + " | ".join(header) + " |")
    out.append("|" + "---|" * ncols)
    for r in rows[:CSV_SAMPLE_ROWS]:
        cells = [(r[i] if i < len(r) else "").replace("|", "/") for i in range(ncols)]
        out.append("| " + " | ".join(cells) + " |")

    # The data itself, so questions can be answered by computation rather than
    # by inference from a summary.
    inline = rows[:CSV_INLINE_ROWS]
    out += [
        "",
        f"## Data ({len(inline):,} of {len(rows):,} rows, CSV)",
        "",
        "```csv",
        ",".join(header),
    ]
    for r in inline:
        out.append(",".join((r[i] if i < len(r) else "") for i in range(ncols)))
    out.append("```")
    if len(rows) > CSV_INLINE_ROWS:
        out += [
            "",
            f"_Only the first {CSV_INLINE_ROWS:,} of {len(rows):,} rows are "
            "included above. Say so if a question needs the rest — do not "
            "present a figure computed from this head as if it covered the "
            "whole file._",
        ]
    return "\n".join(out)


def _to_block(content_type: str, data: bytes, filename: str) -> dict | None:
    if content_type in CSV_TYPES:
        # Delivered as text rather than as a `document` block: it is a table,
        # and the model reasons over it far better as a profile plus rows than
        # as an opaque attachment.
        return {"type": "text", "text": _profile_csv(data, filename)}

    if content_type == PDF_TYPE:
        # Text, not a `document` block. See the module docstring: a document
        # block is placeholdered out by `_internal_to_openai` for every model
        # in the roster, so the old block was a promise the pipeline could not
        # keep. The extractor is the one Learn and Agent 1 already use.
        return {"type": "text", "text": _read_pdf(data, filename)}

    b64 = base64.standard_b64encode(data).decode("utf-8")
    if content_type in IMAGE_TYPES:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": content_type, "data": b64},
        }
    log.warning("Skipping unsupported upload type %s", content_type)
    return None


def _read_pdf(data: bytes, filename: str) -> str:
    """A PDF's text layer, framed so the model knows what it is holding.

    Never raises and never returns nothing. A PDF that cannot be read comes
    back as a sentence saying which kind of unreadable it is — no text layer,
    encrypted, corrupt — because the failure the user actually hits is asking
    for a summary and getting a shrug with no reason attached.
    """
    # Imported here rather than at module scope: `app.learn.ingest` pulls in
    # pypdf, and `app.files` is imported on every worker boot whether or not
    # anyone uploads anything.
    from app.learn import ingest

    extracted = ingest.from_pdf(data, filename)
    if not extracted.ok:
        return (
            f"# {filename}\n\n"
            f"[This PDF could not be read: {extracted.error} "
            "Tell the user this plainly — do not guess at what the document "
            "says.]"
        )

    text = extracted.text
    truncated = len(text) > PDF_INLINE_CHARS
    if truncated:
        text = text[:PDF_INLINE_CHARS]

    note = (
        f"Truncated at {PDF_INLINE_CHARS:,} of {len(extracted.text):,} "
        "characters — say so if the answer depends on the part that was cut."
        if truncated
        else f"{len(text):,} characters, complete."
    )
    return (
        f"# {extracted.title}\n\n"
        f"[PDF text layer, extracted. {note}]\n\n"
        f"{text}"
    )
