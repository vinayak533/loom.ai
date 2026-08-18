"""Embeddings for notebook retrieval.

One mode, one output shape: a unit-length vector of :data:`EMBED_DIM` floats,
because the ``notebook_chunks.embedding`` column is declared once as
``vector(768)`` and changing it later is a migration.

Vectors are hashed bag-of-words, computed in-process, with no key and no
network call. Cosine similarity over these is essentially weighted lexical
overlap: it finds the chunk that shares the question's terms, which for a
personal notebook of a handful of documents is a reasonable retriever.

This used to have a second mode that called an external ``/embeddings``
endpoint when a provider key was set. That key has been removed from the
project along with the rest of the provider's surface, so the mode is gone and
the local vectoriser is the only path. :func:`embedding_signature` still names
the vector space and is still stored alongside each chunk, so if a second mode
is ever reintroduced a switch remains detectable rather than silently returning
nonsense against chunks embedded the old way.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Iterable

log = logging.getLogger(__name__)

#: Must match the `vector(768)` column in schema.sql.
EMBED_DIM = 768

_WORD_RE = re.compile(r"[a-z0-9]+")


#: Names the vector space these embeddings live in. Stored per chunk so a
#: future change of vectoriser is detectable instead of silently mismatching.
def embedding_signature() -> str:
    return f"local-hash:v1:{EMBED_DIM}"


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch. Never raises and never touches the network."""
    if not texts:
        return []
    return [embed_local(t) for t in texts]


async def embed_one(text: str) -> list[float]:
    return (await embed([text]))[0]


# --- local mode ------------------------------------------------------------


def embed_local(text: str) -> list[float]:
    """A signed hashing vectoriser over unigrams and bigrams.

    Sublinear term frequency (``1 + log tf``) keeps a word repeated forty times
    in one chunk from dominating the vector, and the sign bit drawn from a
    second hash makes unrelated terms cancel instead of accumulating, which is
    what keeps 768 buckets usable for a vocabulary far larger than that.
    """
    counts: dict[str, int] = {}
    for token in _tokens(text):
        counts[token] = counts.get(token, 0) + 1

    vector = [0.0] * EMBED_DIM
    for token, count in counts.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBED_DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    return _normalise(vector)


def _tokens(text: str) -> Iterable[str]:
    words = [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP]
    yield from words
    # Bigrams give the vector a little word order, which is the difference
    # between matching "binary search" and matching any chunk with "search".
    for a, b in zip(words, words[1:]):
        yield f"{a}_{b}"


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


def cosine(a: list[float], b: list[float]) -> float:
    """Both sides are unit vectors, so this is just the dot product."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


#: Function words carry no retrieval signal and, hashed, are pure collision
#: pressure on a 768-bucket vector.
_STOP = {
    "a", "about", "an", "and", "are", "as", "at", "be", "but", "by", "can", "did",
    "do", "does", "for", "from", "had", "has", "have", "how", "i", "if", "in",
    "into", "is", "it", "its", "of", "on", "or", "our", "so", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "to",
    "was", "we", "were", "what", "when", "where", "which", "who", "why",
    "will", "with", "you", "your",
}
