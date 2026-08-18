"""Learning-mode source ingestion.

PDFs already flow through :mod:`app.files` (upload → extracted text). This
module adds the other source type the Learning section accepts: a YouTube URL,
resolved to its transcript so the model reads words rather than a link.

Transcript fetching is best-effort and never fatal. A video with captions
disabled still produces a usable source row — the caller gets ``text=None`` and
an ``error`` string it can surface as a chip state, rather than an exception
that kills the turn.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# youtu.be/ID · youtube.com/watch?v=ID · /shorts/ID · /embed/ID · /live/ID
_YT_PATTERNS = [
    re.compile(r"(?:youtu\.be/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/watch\?(?:[^&\s]*&)*v=)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/(?:shorts|embed|live)/)([A-Za-z0-9_-]{11})"),
]

# Transcripts can be enormous; a 2h talk is ~120k characters. Cap what we hand
# the model so one source can't blow the context window on its own.
MAX_TRANSCRIPT_CHARS = 40_000


def extract_video_id(url: str) -> str | None:
    """Return the 11-char video id in ``url``, or None if it isn't a YouTube link."""
    for pattern in _YT_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def is_youtube_url(url: str) -> bool:
    return extract_video_id(url) is not None


@dataclass
class YouTubeSource:
    video_id: str
    url: str
    title: str
    thumbnail: str
    text: str | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "youtube",
            "id": f"yt:{self.video_id}",
            "video_id": self.video_id,
            "url": self.url,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "text": self.text,
            "chars": len(self.text or ""),
            "error": self.error,
            **self.meta,
        }


def _fetch_transcript(video_id: str) -> tuple[str | None, str | None]:
    """(text, error). Import is local so a missing package degrades gracefully."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None, (
            "youtube-transcript-api is not installed. "
            "Run `pip install youtube-transcript-api` in backend/."
        )

    try:
        # The 1.x API is instance-based; 0.6.x exposed a classmethod. Support both
        # so a pinned older install keeps working.
        if hasattr(YouTubeTranscriptApi, "list"):
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id)
            chunks = [snippet.text for snippet in fetched]
        else:  # pragma: no cover - legacy path
            raw = YouTubeTranscriptApi.get_transcript(video_id)
            chunks = [c["text"] for c in raw]
    except Exception as exc:  # noqa: BLE001 - vendor lib raises many types
        log.info("Transcript unavailable for %s: %s", video_id, exc)
        return None, f"No transcript available for this video ({type(exc).__name__})."

    text = " ".join(c.strip() for c in chunks if c and c.strip())
    if not text:
        return None, "Transcript was empty."
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS] + "\n\n[transcript truncated]"
    return text, None


async def _fetch_title(video_id: str) -> tuple[str | None, str | None]:
    """(title, author) from YouTube's oEmbed endpoint.

    Public, keyless and cheap. Worth the round trip because the alternative is
    listing a source as `youtu.be/dQw4w9WgXcQ` in the sources panel and in every
    citation, which tells the reader nothing about what they added. Best-effort:
    a failure here must never cost us a transcript we did fetch.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                "https://www.youtube.com/oembed",
                params={
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "format": "json",
                },
            )
            res.raise_for_status()
            body = res.json()
        return body.get("title"), body.get("author_name")
    except Exception as exc:  # noqa: BLE001 - cosmetic, never fatal
        log.debug("oEmbed lookup failed for %s: %s", video_id, exc)
        return None, None


async def ingest_youtube(url: str) -> YouTubeSource:
    """Resolve a YouTube URL to a transcript-backed source row."""
    import asyncio

    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("Not a recognisable YouTube URL.")

    source = YouTubeSource(
        video_id=video_id,
        url=url,
        title=f"youtu.be/{video_id}",
        thumbnail=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    )

    # The transcript is the point, so it is what we wait on; the title is a
    # nicety fetched alongside it rather than before it.
    # The vendor lib is blocking; keep it off the event loop.
    (text, error), (title, author) = await asyncio.gather(
        asyncio.to_thread(_fetch_transcript, video_id),
        _fetch_title(video_id),
    )
    source.text = text
    source.error = error
    if title:
        source.title = title
        if author:
            source.meta["author"] = author
    return source


def build_source_prompt(sources: list[dict[str, Any]]) -> str:
    """Fold ingested sources into a prompt preamble.

    Sources that failed to resolve are still listed, so the model can say what it
    couldn't read instead of silently answering from nothing.
    """
    if not sources:
        return ""

    parts: list[str] = ["<sources>"]
    for i, src in enumerate(sources, 1):
        label = src.get("title") or src.get("filename") or f"source {i}"
        kind = src.get("kind", "file")
        parts.append(f'<source index="{i}" kind="{kind}" title="{label}">')
        if src.get("text"):
            parts.append(str(src["text"]))
        else:
            reason = src.get("error") or "content could not be extracted"
            parts.append(f"[unavailable: {reason}]")
        parts.append("</source>")
    parts.append("</sources>")
    return "\n".join(parts)
