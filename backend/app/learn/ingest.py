"""Turning a source into plain text.

Three inputs, one output: a title and a body of readable text that the chunker
can index. Nothing here raises on bad input — a source that cannot be read is
still stored, with ``status='failed'`` and an ``error``, so the notebook shows
what it could not open instead of silently dropping it.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.learn.chunking import normalise
from app.sources import ingest_youtube, is_youtube_url

log = logging.getLogger(__name__)

#: A source longer than this is truncated. Retrieval means we never send the
#: whole thing to a model, but the row still has to fit in a database column
#: and in the notebook's total budget.
MAX_SOURCE_CHARS = 400_000
#: Refuse to buffer a page larger than this.
MAX_FETCH_BYTES = 8 * 1024 * 1024
FETCH_TIMEOUT = 20.0

#: Some sites 403 anything without a browser-shaped UA. This is a plain,
#: honest identifier — not an attempt to look like a browser we are not.
_UA = "Mozilla/5.0 (compatible; AtlasLearn/1.0; +notebook source fetcher)"


@dataclass
class Extracted:
    title: str
    text: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text) and self.error is None


def from_text(raw: str, title: str | None = None) -> Extracted:
    text = normalise(raw)[:MAX_SOURCE_CHARS]
    if not text:
        return Extracted(title or "Pasted text", "", "Nothing to read — the text was empty.")
    return Extracted(title or _title_from_text(text), text)


# --- PDF -------------------------------------------------------------------


def from_pdf(data: bytes, filename: str) -> Extracted:
    """Extract a PDF's text layer.

    A scanned PDF has no text layer and there is no OCR step in this project,
    so it comes back as a failed source with an explanation rather than as an
    empty one the user has to diagnose themselves.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return Extracted(
            filename,
            "",
            "pypdf is not installed. Run `pip install -r requirements.txt` in backend/.",
        )

    import io

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - vendor lib raises many types
        log.info("PDF extraction failed for %s: %s", filename, exc)
        return Extracted(filename, "", f"Could not read this PDF ({type(exc).__name__}).")

    text = normalise("\n\n".join(pages))[:MAX_SOURCE_CHARS]
    if not text:
        return Extracted(
            filename,
            "",
            "This PDF has no text layer — it is probably a scan. "
            "Paste the text instead, or run it through OCR first.",
        )

    title = (reader.metadata.title if reader.metadata else None) or filename
    return Extracted(str(title).strip() or filename, text)


# --- URL -------------------------------------------------------------------


async def from_url(url: str) -> Extracted:
    url = url.strip()

    # A YouTube watch page is a shell around a player: reader-mode extraction
    # of one returns the sidebar and the cookie notice. The app already knows
    # how to resolve these to a transcript (`app.sources`, built for the old
    # Learning composer), so a link to a lecture becomes the words in it.
    if is_youtube_url(url):
        source = await ingest_youtube(url)
        if not source.text:
            return Extracted(source.title, "", source.error or "No transcript available.")
        return Extracted(source.title, source.text)

    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc
    # A host with a space in it, or with no dot, is a phrase someone typed —
    # not a link. Catch it here so it fails as "that isn't an address" rather
    # than as a DNS error twenty seconds later.
    if (
        parsed.scheme not in ("http", "https")
        or not host
        or " " in host
        or ("." not in host and not host.startswith("localhost"))
    ):
        return Extracted(url, "", "That does not look like a web address.")
    url = parsed.geturl()

    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept": "text/html,text/plain;q=0.9"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.content[:MAX_FETCH_BYTES]
            content_type = response.headers.get("content-type", "")
    except httpx.HTTPStatusError as exc:
        return Extracted(url, "", f"The site returned {exc.response.status_code}.")
    except Exception as exc:  # noqa: BLE001
        log.info("URL fetch failed for %s: %s", url, exc)
        return Extracted(url, "", f"Could not fetch that page ({type(exc).__name__}).")

    if "application/pdf" in content_type:
        extracted = await asyncio.to_thread(from_pdf, body, parsed.netloc)
        return Extracted(extracted.title, extracted.text, extracted.error)

    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
    html = body.decode(charset, errors="replace")

    title, text = readable(html)
    if not text:
        return Extracted(title or url, "", "No readable text on that page.")
    return Extracted(title or parsed.netloc, text[:MAX_SOURCE_CHARS])


# --- HTML → text -----------------------------------------------------------

_DROP_RE = re.compile(
    r"<(script|style|noscript|svg|nav|header|footer|form|aside|iframe)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_MAIN_RE = re.compile(
    r"<(article|main)\b[^>]*>(?P<body>.*?)</\1>", re.IGNORECASE | re.DOTALL
)
_BLOCK_RE = re.compile(
    r"</?(p|div|section|br|li|tr|h[1-6]|blockquote|pre)\b[^>]*>", re.IGNORECASE
)
_TAG_RE = re.compile(r"<[^>]+>")


def readable(html: str) -> tuple[str, str]:
    """A dependency-free reader view: (title, text).

    This is a deliberate ~40 lines of regex rather than a parser dependency.
    It is not trying to beat Readability — it strips the furniture, prefers
    `<article>`/`<main>` when the page marks one, and keeps block boundaries as
    newlines so the chunker still has paragraphs to split on.
    """
    title_match = _TITLE_RE.search(html)
    title = _clean(title_match.group(1)) if title_match else ""

    body = _DROP_RE.sub(" ", html)
    main = _MAIN_RE.search(body)
    if main and len(main.group("body")) > 400:
        body = main.group("body")

    body = _BLOCK_RE.sub("\n", body)
    body = _TAG_RE.sub(" ", body)
    text = _clean(body)
    # Extraction leaves behind runs of one- and two-word nav remnants; drop the
    # lines too short to be prose.
    lines = [line.strip() for line in text.split("\n")]
    keep = [line for line in lines if len(line) > 2]
    return title, normalise("\n\n".join(keep))


def _clean(fragment: str) -> str:
    return html_lib.unescape(re.sub(r"[ \t]+", " ", fragment)).strip()


def _title_from_text(text: str) -> str:
    first = next((line.strip() for line in text.split("\n") if line.strip()), "")
    return (first[:70] + "…") if len(first) > 70 else (first or "Pasted text")
