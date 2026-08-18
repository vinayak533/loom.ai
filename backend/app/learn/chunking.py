"""Splitting a source into retrievable chunks.

The unit of retrieval is a passage, not a document: stuffing whole PDFs into
every question is what this pipeline exists to avoid. Chunks are built by
accumulating paragraphs up to a character budget rather than by slicing at a
fixed offset, so a chunk almost always begins at a paragraph boundary and reads
as prose when it is quoted back to the user as a citation.
"""

from __future__ import annotations

import re

#: Target chunk size in characters. ~1200 chars is roughly 300 tokens — small
#: enough that eight of them fit comfortably in a grounded prompt, large enough
#: to hold a whole argument rather than one sentence of it.
CHUNK_CHARS = 1200
#: Carried from the end of one chunk into the start of the next, so a fact that
#: straddles a boundary is retrievable from either side.
OVERLAP_CHARS = 180
#: Below this a trailing chunk is folded into its predecessor instead of
#: standing alone as a fragment.
MIN_CHUNK_CHARS = 120

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_WS_RE = re.compile(r"[ \t ]+")


def normalise(text: str) -> str:
    """Collapse the whitespace damage that PDF and HTML extraction leave behind."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    # Three or more newlines is always extraction noise, never meaning.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Split ``text`` into overlapping passages on paragraph boundaries."""
    text = normalise(text)
    if not text:
        return []

    pieces: list[str] = []
    for para in _PARAGRAPH_RE.split(text):
        para = para.strip()
        if not para:
            continue
        # A single paragraph longer than the budget (common in extracted PDFs,
        # where the whole page can arrive as one block) is cut on sentence
        # boundaries before it goes into the accumulator.
        pieces.extend(_split_long(para, size) if len(para) > size else [para])

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            current = _tail(current, overlap) + piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)

    if len(chunks) > 1 and len(chunks[-1]) < MIN_CHUNK_CHARS:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks.pop()}"
    return chunks


def _split_long(para: str, size: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", para)
    out: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > size:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
        # A "sentence" with no terminator at all — minified text, a table — is
        # still bounded by a hard cut so one piece cannot exceed the budget.
        while len(current) > size:
            out.append(current[:size])
            current = current[size:]
    if current:
        out.append(current)
    return out


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    # Start the carried-over text at a word boundary so the next chunk does not
    # open mid-word.
    space = tail.find(" ")
    return (tail[space + 1 :] if space != -1 else tail) + "\n\n"
