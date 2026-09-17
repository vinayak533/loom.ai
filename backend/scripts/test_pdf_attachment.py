"""A PDF attached in Chat actually reaches the model.

    .venv/Scripts/python scripts/test_pdf_attachment.py

The bug
-------
Attaching a PDF and asking for a summary produced a refusal, not a summary —
and the refusal was correct, because the document really had not arrived.

`files._to_block` turned a PDF into a `document` block, and every provider in
this project speaks the OpenAI chat wire format, which has no portable document
part. So `_internal_to_openai` replaced the block with

    "[A document (X) was attached to this message. Its contents are not
     readable by the current model.]"

for *every* model — vision-capable or not, since nothing here sends PDFs as
images either. The model was told a document existed and that it could not read
it, and answered accordingly. Meanwhile the Chat empty state advertises
"Summarise a document — attach a PDF with the + button", and the attach menu
says "for the model to read".

The fix is the one already used for CSV two branches above: extract at the
upload boundary and send text. The extractor is `learn.ingest.from_pdf`, which
Learn and Agent 1 already use, so no new dependency and no second code path.

What is asserted
----------------
1. A readable PDF becomes a `text` block carrying its actual words.
2. Those words survive translation to the wire format — including for a model
   with `supports_vision=False`, which is the Chat default and the case the
   bug was reported against.
3. The old placeholder is gone: nothing tells the model the contents are
   unreadable when they are right there.
4. A PDF with no text layer, and a corrupt one, each say which kind of
   unreadable they are — no OCR is not the same failure as a broken file.
5. A long PDF is cut at `PDF_INLINE_CHARS` and *says* it was cut.
6. Images still go as `image` blocks and CSVs still go as profiled text —
   this changed the PDF branch and nothing else.

No network and no database: `_to_block` and the router's translation are both
pure functions over bytes.
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.files import (  # noqa: E402
    PDF_INLINE_CHARS,
    PDF_TYPE,
    _to_block,
)
from app.llm_router import _internal_to_openai  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def make_pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF with a real text layer.

    Hand-built rather than pulled from a fixture file so the test is
    self-contained and the expected words are visible in the source.
    """
    content = "BT /F1 12 Tf 50 750 Td 14 TL\n"
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content += f"({escaped}) Tj T*\n"
    content += "ET"
    stream = content.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def blank_pdf() -> bytes:
    """A valid PDF with no text layer — what a scan looks like."""
    return make_pdf([])


def wire_text(block: dict, *, vision: bool) -> str:
    """What the model actually receives for a message carrying `block`."""
    msgs, _ = _internal_to_openai(
        [{"role": "user", "content": [block, {"type": "text", "text": "Summarise this."}]}],
        None,
        [],
        vision=vision,
    )
    body = msgs[-1]["content"]
    if isinstance(body, list):
        return "\n".join(p.get("text", "") for p in body if p.get("type") == "text")
    return body or ""


NEEDLE = "Ashitha builds retrieval-augmented pipelines in Kochi"
UNREADABLE = "not readable by the current model"


def main() -> int:
    ok = True

    print("\n1. A readable PDF arrives as its own words")
    pdf = make_pdf([NEEDLE, "Second line of the document."])
    block = _to_block(PDF_TYPE, pdf, "resume.pdf")
    ok &= check("it is a text block, not a document block", block["type"] == "text",
                f"type={block['type']}")
    ok &= check("the PDF's words are in it", NEEDLE in block["text"])
    ok &= check("and it is labelled as an extracted text layer",
                "PDF text layer, extracted" in block["text"])

    print("\n2. They survive translation — including without vision")
    for vision in (False, True):
        body = wire_text(block, vision=vision)
        label = "vision=False (the Chat default)" if not vision else "vision=True"
        ok &= check(f"{label}: the model receives the text", NEEDLE in body)
        ok &= check(
            f"{label}: nothing claims the contents are unreadable",
            UNREADABLE not in body,
            "this is the exact string the old path sent instead of the document",
        )

    print("\n3. An unreadable PDF says which kind of unreadable it is")
    scan = _to_block(PDF_TYPE, blank_pdf(), "scan.pdf")
    ok &= check("a scan is still a text block", scan["type"] == "text")
    ok &= check("it names the missing text layer", "no text layer" in scan["text"])
    ok &= check("and suggests OCR or pasting", "OCR" in scan["text"])
    ok &= check(
        "and tells the model not to invent the contents",
        "do not guess" in scan["text"].lower(),
    )

    broken = _to_block(PDF_TYPE, b"%PDF-1.4 truncated garbage", "broken.pdf")
    ok &= check("a corrupt PDF is reported as unreadable",
                "could not be read" in broken["text"].lower())
    ok &= check(
        "and is not confused with a scan",
        "no text layer" not in broken["text"],
        "a broken file and a scanned one need different advice",
    )

    print("\n4. A long PDF is cut, and says so")
    filler = "Retrieval augmented generation over a vector index. " * 1200
    long_pdf = make_pdf([filler[i : i + 90] for i in range(0, len(filler), 90)])
    long_block = _to_block(PDF_TYPE, long_pdf, "long.pdf")
    body = long_block["text"]
    ok &= check(
        "the block stays within the inline budget",
        len(body) <= PDF_INLINE_CHARS + 2000,
        f"{len(body):,} chars against a {PDF_INLINE_CHARS:,} budget",
    )
    if len(body) > PDF_INLINE_CHARS:
        ok &= check("and it says it was truncated", "Truncated at" in body)

    print("\n5. The other upload types are untouched")
    png = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    img = _to_block("image/png", png, "shot.png")
    ok &= check("a PNG is still an image block", img["type"] == "image",
                f"type={img['type']}")
    csv = _to_block("text/csv", b"name,role\nAshitha,Engineer\n", "people.csv")
    ok &= check("a CSV is still profiled text", csv["type"] == "text"
                and "people.csv" in csv["text"])
    ok &= check("an unknown type is still dropped",
                _to_block("application/zip", b"PK\x03\x04", "a.zip") is None)

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
