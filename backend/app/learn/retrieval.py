"""Indexing and retrieval over a notebook's sources.

Indexing runs once per source, at ingest. Retrieval runs once per question and
returns passages, never documents — the point of this module is that a chat
turn's prompt is bounded by the number of chunks retrieved, not by the size of
the notebook.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.db import learn_repository as repo
from app.learn import chunking, embeddings

log = logging.getLogger(__name__)

#: Passages given to the model per question. Eight ~1200-character chunks is
#: roughly 2.5k tokens of grounding — enough for a real answer, small enough
#: that the prompt stays the same size whether the notebook holds one PDF or
#: forty.
TOP_K = 8
#: Below this cosine score a chunk is treated as unrelated. It matters most in
#: local-hash mode, where every chunk has *some* lexical overlap with every
#: question and an unfiltered top-8 would always look confident.
MIN_SIMILARITY = 0.045


@dataclass
class Passage:
    source_id: str
    chunk_index: int
    content: str
    similarity: float
    source_title: str = ""


async def index_source(notebook_id: str, source_id: str, text: str) -> int:
    """Chunk, embed and store one source. Returns the chunk count."""
    pieces = chunking.chunk(text)
    if not pieces:
        return 0

    vectors = await embeddings.embed(pieces)
    await repo.add_chunks(
        [
            {
                "notebook_id": notebook_id,
                "source_id": source_id,
                "chunk_index": i,
                "content": piece,
                "embedding": repo.format_vector(vector),
            }
            for i, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
        ]
    )
    return len(pieces)


async def retrieve(notebook_id: str, question: str, limit: int = TOP_K) -> list[Passage]:
    """The most relevant passages in this notebook for ``question``."""
    if not question.strip():
        return []

    query = await embeddings.embed_one(question)

    rows = await repo.match_chunks(notebook_id, query, limit)
    if not rows:
        # `None` means no Supabase or no `match_notebook_chunks` function.
        # An empty *list* is the more interesting case: the RPC ran and found
        # nothing. That is a legitimate answer only if the notebook is empty —
        # and when it is not, it means the vector index missed, which is
        # precisely the failure the old IVFFlat index produced (see the index
        # comment in schema.sql). Either way the recovery is the same and it is
        # cheap at this scale, so rather than trusting an approximate index to
        # be exhaustive, fall back to scoring in-process: correct by
        # construction, just linear in the size of the notebook.
        #
        # Without this, one under-probed index turns a full notebook into "I
        # have no sources", which is the worst possible failure mode here —
        # the model then answers from general knowledge, or denies the
        # document exists, and the user has no way to tell why.
        if rows is not None:
            log.warning(
                "Vector search returned no rows for notebook %s; falling back "
                "to an exact in-process scan.",
                notebook_id,
            )
        rows = await _local_match(notebook_id, query, limit)

    titles = {s["id"]: s.get("title") or "Untitled source" for s in await repo.list_sources(notebook_id)}
    passages = [
        Passage(
            source_id=row.get("source_id", ""),
            chunk_index=int(row.get("chunk_index") or 0),
            content=row.get("content") or "",
            similarity=float(row.get("similarity") or 0.0),
            source_title=titles.get(row.get("source_id", ""), "Untitled source"),
        )
        for row in rows
    ]
    kept = [p for p in passages if p.similarity >= MIN_SIMILARITY]
    # If everything scored below the floor, keep the single best one anyway:
    # "here is the closest thing I have, and it may not answer you" is more
    # useful than an empty context that reads as an empty notebook.
    return kept or passages[:1]


async def _local_match(notebook_id: str, query: list[float], limit: int) -> list[dict]:
    scored: list[tuple[float, dict]] = []
    for row in await repo.all_chunks(notebook_id):
        vector = repo.parse_vector(row.get("embedding"))
        if not vector:
            continue
        scored.append((embeddings.cosine(query, vector), row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{**row, "similarity": score} for score, row in scored[:limit]]


def as_prompt(passages: list[Passage]) -> str:
    """Fold retrieved passages into a numbered context block.

    Numbered because the answer prompt asks the model to cite by number; the
    numbers are what the UI turns back into source chips.
    """
    if not passages:
        return "<context>\n(No sources in this notebook yet.)\n</context>"
    parts = ["<context>"]
    for i, passage in enumerate(passages, 1):
        parts.append(
            f'<passage id="{i}" source="{passage.source_title}">\n'
            f"{passage.content}\n</passage>"
        )
    parts.append("</context>")
    return "\n".join(parts)
