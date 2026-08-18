"""Retrieval-Augmented Generation."""

from __future__ import annotations

COURSE = {
    "id": "rag",
    "title": "Retrieval-Augmented Generation",
    "short_title": "RAG",
    "subtitle": "Ground a language model in your own documents",
    "difficulty": "intermediate",
    "tags": ["AI", "LLM", "Search"],
    "description": (
        "A language model knows what was in its training data and nothing else. "
        "RAG closes that gap: retrieve the passages that actually answer the "
        "question, put them in the prompt, and make the model cite them. This "
        "course walks the whole pipeline — chunking, embeddings, vector search, "
        "reranking, generation, evaluation — and finishes on what breaks in "
        "production."
    ),
    "objectives": [
        "Explain why retrieval beats fine-tuning for fast-moving, private knowledge",
        "Chunk documents so that a retrieved passage is actually self-contained",
        "Choose an embedding model and a vector store for a given workload",
        "Build a retriever with hybrid search and a reranking stage",
        "Write a generation prompt that refuses rather than hallucinates",
        "Measure a RAG system with faithfulness, relevance and recall metrics",
    ],
    "resources": [
        {
            "kind": "paper",
            "title": "Lewis et al. — Retrieval-Augmented Generation (arXiv 2005.11401)",
            "url": "https://arxiv.org/abs/2005.11401",
            "note": "The original paper. Section 2 is the part worth reading twice.",
        },
        {
            "kind": "doc",
            "title": "LangChain — RAG tutorial",
            "url": "https://python.langchain.com/docs/tutorials/rag/",
            "note": "End-to-end code you can run today.",
        },
        {
            "kind": "doc",
            "title": "Pinecone — RAG learning series",
            "url": "https://www.pinecone.io/learn/series/rag/",
            "note": "The best free explanation of the retrieval half.",
        },
    ],
    "chapters": [
        {
            "id": "intro",
            "title": "Introduction to RAG",
            "topic": "Fundamentals",
            "summary": "What retrieval adds to a language model, and when it is the wrong tool.",
            "minutes": 14,
            "body": """
## The problem RAG solves

A language model is a compression of its training data. That gives it fluency
and general reasoning, but it leaves three holes that no amount of scale fixes:

- **Staleness.** Anything after the training cutoff does not exist to the model.
- **Privacy.** Your company's contracts were never in the training set, and you
  do not want them to be.
- **Attribution.** A model that answers from its weights cannot tell you where
  the answer came from, so nobody can check it.

Retrieval-Augmented Generation fixes all three the same way: before answering,
*fetch* the relevant text and put it in the prompt. The model stops being the
knowledge store and becomes the reasoning layer over a knowledge store you
control.

## The shape of the pipeline

```
            ┌──────────── indexing (offline) ────────────┐
documents → chunk → embed → vector store
            └────────────────────────────────────────────┘

            ┌──────────── querying (per request) ────────┐
question  → embed → search → rerank → prompt → LLM → answer + citations
            └────────────────────────────────────────────┘
```

Two halves that run at completely different times. Indexing is a batch job you
run when documents change. Querying happens on every request and its latency
budget is measured in hundreds of milliseconds.

## RAG or fine-tuning?

They answer different questions, and the confusion between them is the single
most common mistake in this space.

- **Fine-tuning changes behaviour.** Tone, format, a domain's way of phrasing
  things, a task the base model performs badly.
- **RAG changes knowledge.** Facts, documents, anything that was true this
  morning and false this afternoon.

If your complaint is "it doesn't know about our product", that is retrieval. If
it is "it knows but it answers like a chatbot", that is fine-tuning — or, far
more often, a better prompt.

## When RAG is the wrong tool

Retrieval adds a failure mode: the retriever can miss. For a small, fixed
corpus that fits comfortably in the context window, just put the whole thing in
the prompt. Long-context models made "stuff it all in" viable for far more
cases than it used to be, and a pipeline you did not build cannot break.
""",
            "concepts": [
                ("Grounding", "Constraining a model's answer to text supplied in the prompt rather than to its weights."),
                ("Context window", "The maximum number of tokens a model can attend to in one call — the hard ceiling on how much retrieved text you can supply."),
                ("Hallucination", "A fluent, confident answer that is not supported by any source. RAG reduces it; nothing eliminates it."),
            ],
            "takeaways": [
                "RAG supplies knowledge; fine-tuning supplies behaviour",
                "Indexing is offline and batched, querying is online and latency-bound",
                "Retrieval buys you freshness, privacy and citations",
                "A corpus that fits in the context window does not need a retriever",
            ],
            "resources": [
                {
                    "kind": "paper",
                    "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                    "url": "https://arxiv.org/abs/2005.11401",
                    "note": "Lewis et al., 2020 — the paper that named the pattern.",
                },
                {
                    "kind": "doc",
                    "title": "Pinecone — What is RAG?",
                    "url": "https://www.pinecone.io/learn/retrieval-augmented-generation/",
                },
            ],
            "video": {
                "query": "what is retrieval augmented generation RAG explained",
                "title": "RAG explained end to end",
                "channel": "YouTube search",
            },
            "notes": """
Rule of thumb from practice: if a stakeholder can describe the answer's source
as "a document we have", it is RAG. If they describe it as "the way we talk",
it is prompting or fine-tuning.
""",
        },
        {
            "id": "chunking",
            "title": "Chunking & document preparation",
            "topic": "Chunking",
            "summary": "Why the chunk is the unit of retrieval, and how to cut one that stands alone.",
            "minutes": 18,
            "body": """
## The chunk is what you retrieve

You do not retrieve documents. You retrieve *chunks*, and the model only ever
sees the chunks. So a chunk has to be simultaneously:

- **small enough** that its embedding means one thing, and
- **large enough** that it answers a question without its neighbours.

Those pull in opposite directions, which is why chunking is where most RAG
quality is won or lost.

## Strategies, worst to best

**Fixed-size character splitting** cuts every N characters. Fast, and it will
happily slice a sentence in half.

**Recursive character splitting** tries a list of separators in order —
paragraphs, then lines, then sentences, then characters — and only falls to the
next when a piece is still too big. This is the sane default.

**Structure-aware splitting** uses the document's own boundaries: Markdown
headings, HTML sections, PDF headers. Best quality, needs a parser per format.

**Semantic splitting** embeds sentences and cuts where the meaning shifts.
Expensive to index and rarely worth it before you have exhausted the above.

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,          # characters, not tokens — roughly 200 tokens
    chunk_overlap=120,       # ~15%: enough to carry a sentence across a cut
    separators=["\\n\\n", "\\n", ". ", " ", ""],
)
chunks = splitter.split_documents(docs)
```

## Overlap

Overlap repeats the tail of one chunk at the head of the next, so a fact that
straddles a boundary survives in at least one piece. Ten to twenty percent of
chunk size is the usual range. More than that inflates the index and returns
near-duplicate results.

## Metadata is not optional

Every chunk should carry where it came from: document id, title, page or
heading, and a URL. That metadata is what powers citations, what lets you
filter a search to one customer's documents, and what makes a retrieved passage
debuggable at 2am.

```python
chunk.metadata = {
    "source_id": doc.id,
    "title": doc.title,
    "url": doc.url,
    "heading": "3.2 Termination",
    "chunk_index": 7,
}
```
""",
            "concepts": [
                ("Chunk", "The unit that is embedded, stored, retrieved and shown to the model."),
                ("Overlap", "Repeated text shared between adjacent chunks so facts spanning a boundary survive."),
                ("Recursive splitting", "Splitting on a priority list of separators, descending only when a piece is still too large."),
            ],
            "takeaways": [
                "A chunk must be self-contained — it is read without its neighbours",
                "Recursive character splitting is the right default; structure-aware is better where you can afford it",
                "10–20% overlap protects facts that straddle a boundary",
                "Metadata on every chunk is what makes citations and filtering possible",
            ],
            "resources": [
                {
                    "kind": "doc",
                    "title": "Pinecone — Chunking strategies",
                    "url": "https://www.pinecone.io/learn/chunking-strategies/",
                },
                {
                    "kind": "doc",
                    "title": "LangChain — Text splitters",
                    "url": "https://python.langchain.com/docs/concepts/text_splitters/",
                },
            ],
            "video": {
                "query": "chunking strategies for RAG explained",
                "title": "Chunking strategies compared",
            },
        },
        {
            "id": "embeddings",
            "title": "Embeddings",
            "topic": "Embeddings",
            "summary": "Turning text into vectors whose geometry means something.",
            "minutes": 18,
            "body": """
## What an embedding is

An embedding model maps a piece of text to a fixed-length vector of floats —
384, 768, 1536 dimensions are all common. The model is trained so that texts
with similar meaning land near each other, which turns "find related text" into
"find nearby points".

That is the whole trick. Everything downstream — vector databases, similarity
search, reranking — exists to make that nearest-neighbour lookup fast.

## Similarity

**Cosine similarity** measures the angle between two vectors and ignores their
length. It is the standard choice for text.

```python
import numpy as np

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
```

If you **normalise** vectors to unit length at index time, cosine similarity
and the dot product become the same operation — which is why most vector stores
tell you to normalise. Euclidean distance also becomes a monotone function of
cosine, so all three rank identically.

## Choosing a model

Three axes, in order of how much they matter:

1. **Domain fit.** A general model on legal text underperforms a legal model.
2. **Dimensionality.** More dimensions cost storage and query time for a
   usually-small quality gain. 768 is a good middle.
3. **Symmetry.** Some models are trained asymmetrically for search: the
   *query* and the *document* go through different prefixes or even different
   towers. Use them the way they were trained.

The one rule with no exceptions: **index and query with the same model**. A
vector from model A is meaningless in a space built by model B.

## Cost and batching

Embedding a corpus is the expensive part of indexing. Batch aggressively —
most APIs accept many texts per call — and cache by a hash of the chunk text so
that re-indexing an unchanged document costs nothing.

```python
import hashlib

def cache_key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}:{text}".encode()).hexdigest()
```
""",
            "concepts": [
                ("Embedding", "A dense vector representation of text whose distances encode semantic similarity."),
                ("Cosine similarity", "Similarity as the cosine of the angle between two vectors; magnitude-independent."),
                ("Normalisation", "Scaling a vector to unit length, which makes dot product and cosine similarity equivalent."),
                ("Asymmetric search", "Embedding queries and documents differently because a short question and a long passage are not the same kind of text."),
            ],
            "takeaways": [
                "Embeddings turn semantic search into nearest-neighbour search",
                "Cosine similarity is the default; normalise once and use dot product",
                "You must query with the same model you indexed with",
                "Batch and cache embeddings — indexing cost is dominated by them",
            ],
            "resources": [
                {
                    "kind": "doc",
                    "title": "Pinecone — Vector embeddings for developers",
                    "url": "https://www.pinecone.io/learn/vector-embeddings/",
                },
                {
                    "kind": "doc",
                    "title": "MTEB — the embedding benchmark leaderboard",
                    "url": "https://huggingface.co/spaces/mteb/leaderboard",
                    "note": "Sort by your task, not by the overall average.",
                },
            ],
            "video": {
                "query": "text embeddings explained vector similarity search",
                "title": "How text embeddings work",
            },
        },
        {
            "id": "vector-databases",
            "title": "Vector databases",
            "topic": "Vector Databases",
            "summary": "Where the vectors live, and how approximate search stays fast.",
            "minutes": 17,
            "body": """
## What the store is for

A vector database does one thing well: given a query vector, return the *k*
nearest stored vectors, fast, over millions of rows. It also has to store the
chunk text and metadata alongside, and filter on that metadata during the
search rather than after it.

Exact nearest-neighbour search is O(n) per query. At a million vectors that is
too slow, so real systems use **approximate** nearest neighbour (ANN) indexes,
which trade a small amount of recall for orders of magnitude of speed.

## The two index families you will meet

**IVFFlat** partitions the space into clusters, then searches only the closest
few. Tuned by `lists` (how many partitions) and `probes` (how many to search).
Cheap to build, needs data present before it can be built meaningfully.

**HNSW** builds a navigable small-world graph and walks it greedily. Better
recall at the same latency, more memory, slower to build. Tuned by `m` and
`ef_construction` at build time and `ef_search` at query time.

## pgvector, concretely

You do not need a separate database to start. Postgres with `pgvector` keeps
your chunks, metadata and vectors in one place, which means one transaction,
one backup and ordinary SQL filters.

```sql
create extension if not exists vector;

create table chunks (
  id          uuid primary key default gen_random_uuid(),
  document_id uuid not null,
  content     text not null,
  metadata    jsonb not null default '{}',
  embedding   vector(768)
);

create index on chunks
  using hnsw (embedding vector_cosine_ops);

-- `<=>` is cosine distance; smaller is closer.
select id, content, 1 - (embedding <=> $1) as similarity
from   chunks
where  metadata->>'tenant' = $2
order  by embedding <=> $1
limit  8;
```

## Filtering

Metadata filtering is where naive setups fall over. **Pre-filtering** narrows
the candidate set before the ANN search — correct, but it can defeat the index.
**Post-filtering** searches first and drops non-matching results — fast, but
you may end up with two results out of the ten you asked for. Good engines do
filtered search natively; know which one yours does.
""",
            "concepts": [
                ("ANN", "Approximate nearest neighbour search — trades exactness for speed."),
                ("Recall@k", "The fraction of the true top-k neighbours an approximate index actually returns."),
                ("HNSW", "A graph-based ANN index with strong recall/latency characteristics and high memory use."),
                ("IVFFlat", "A cluster-based ANN index: partition the space, search the nearest partitions."),
            ],
            "takeaways": [
                "A vector database stores embeddings and answers top-k nearest queries fast",
                "ANN indexes trade a little recall for a lot of speed",
                "HNSW gives better recall per millisecond; IVFFlat is cheaper to build",
                "Postgres with pgvector is a legitimate production choice, not just a prototype",
            ],
            "resources": [
                {
                    "kind": "doc",
                    "title": "pgvector — README",
                    "url": "https://github.com/pgvector/pgvector",
                },
                {
                    "kind": "doc",
                    "title": "Pinecone — Vector indexes explained",
                    "url": "https://www.pinecone.io/learn/series/faiss/vector-indexes/",
                },
            ],
            "video": {
                "query": "vector database HNSW index explained",
                "title": "Vector databases and ANN indexes",
            },
        },
        {
            "id": "retrieval",
            "title": "Retrieval",
            "topic": "Retrieval",
            "summary": "Getting the right passages back — dense, sparse, and both at once.",
            "minutes": 20,
            "body": """
## Dense retrieval is not enough

Embedding search is strong on meaning and weak on *exactness*. Ask for error
code `E4021` or the surname of one customer and a dense retriever will happily
return semantically similar passages that contain neither.

Keyword search — BM25, Postgres full-text, Elasticsearch — has the opposite
profile: exact on rare tokens, blind to paraphrase.

**Hybrid search** runs both and merges. It is not a nicety; for most real
corpora it is the single biggest quality win after chunking.

## Reciprocal Rank Fusion

The cleanest way to merge two ranked lists without tuning score scales:

```python
def rrf(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + position + 1)
    return sorted(scores, key=scores.get, reverse=True)

merged = rrf([dense_ids, bm25_ids])
```

RRF only reads *positions*, so a cosine similarity of 0.83 and a BM25 score of
14.2 never have to be made comparable.

## Query transformation

The user's question is often a bad search query.

- **Multi-query**: ask the LLM for three rephrasings, search all of them, fuse.
- **HyDE**: have the LLM write a *hypothetical answer*, embed that, and search
  with it — answers look more like documents than questions do.
- **Decomposition**: split a compound question into parts and retrieve for each.

Every one of these costs an extra model call. Measure before you adopt.

## How many to fetch

Retrieve wide, rerank narrow. Pulling the top 20–50 candidates and cutting them
down (next chapter) beats pulling the top 5 and hoping. The generator only ever
sees the survivors, so a wide first stage costs retrieval time, not tokens.
""",
            "concepts": [
                ("BM25", "A sparse, term-frequency ranking function — the strong classical keyword baseline."),
                ("Hybrid search", "Combining dense (embedding) and sparse (keyword) retrieval, usually via rank fusion."),
                ("RRF", "Reciprocal Rank Fusion — merging ranked lists by position, so score scales never need aligning."),
                ("HyDE", "Hypothetical Document Embeddings: search with an LLM-written draft answer instead of the question."),
            ],
            "takeaways": [
                "Dense retrieval misses exact tokens; keyword search misses paraphrase; use both",
                "RRF merges rankings using positions, avoiding score normalisation entirely",
                "The user's question is often a poor query — rewriting it helps",
                "Retrieve wide and rerank narrow rather than retrieving narrow",
            ],
            "resources": [
                {
                    "kind": "doc",
                    "title": "Pinecone — Hybrid search",
                    "url": "https://www.pinecone.io/learn/hybrid-search-intro/",
                },
                {
                    "kind": "doc",
                    "title": "LangChain — Retrievers",
                    "url": "https://python.langchain.com/docs/concepts/retrievers/",
                },
            ],
            "video": {
                "query": "hybrid search BM25 vector reciprocal rank fusion RAG",
                "title": "Hybrid retrieval in practice",
            },
        },
        {
            "id": "reranking",
            "title": "Reranking",
            "topic": "Reranking",
            "summary": "A second, slower, much more accurate pass over a short candidate list.",
            "minutes": 15,
            "body": """
## Why a second stage exists

The retriever embedded your query and every document **separately**. That is
what makes it fast — document vectors are computed once, offline — and it is
also its ceiling: the model never sees the query and the document together, so
it cannot reason about how they relate.

A **cross-encoder** does exactly that. It takes `(query, passage)` as one input
and outputs a single relevance score.

```
Bi-encoder  (retrieval):  embed(query) · embed(passage)      → fast, approximate
Cross-encoder (rerank):   score(query ++ passage)            → slow, accurate
```

A cross-encoder cannot be pre-computed, because the score depends on the query.
So it is unusable over a million documents and ideal over the fifty the
retriever just returned.

## The two-stage pattern

```python
candidates = retriever.search(query, k=50)      # cheap, wide
scored = reranker.rank(query, [c.text for c in candidates])
top = [candidates[s.index] for s in scored[:6]]  # expensive, narrow
```

Typical numbers: retrieve 25–100, keep 3–8. The reranker adds 50–300ms and is
usually the largest single quality jump per unit of effort in the whole
pipeline.

## What else reranking fixes

- **Deduplication.** Overlapping chunks from the same document often all match;
  a reranker plus a per-document cap keeps the context diverse.
- **Lost in the middle.** Models attend most strongly to the start and end of a
  long context. With fewer, better passages the problem mostly disappears — and
  ordering the best passage first (or last) helps.
- **Token budget.** Six good passages cost a fraction of fifty mediocre ones.

## Cheaper variants

If a hosted reranker is out of budget, an LLM can rerank by scoring passages in
one batched call, and even a simple heuristic — boost passages whose metadata
matches the query's entities — beats no second stage at all.
""",
            "concepts": [
                ("Bi-encoder", "Encodes query and document independently, enabling precomputed vectors and fast search."),
                ("Cross-encoder", "Encodes query and document jointly for a far more accurate relevance score, at much higher cost."),
                ("Two-stage retrieval", "A wide cheap first pass followed by a narrow expensive rerank."),
                ("Lost in the middle", "The tendency of long-context models to under-use passages placed in the middle of the prompt."),
            ],
            "takeaways": [
                "A cross-encoder sees query and passage together, which is why it is more accurate",
                "Rerankers are too slow for the corpus and perfect for the candidate list",
                "Retrieve 25–100, rerank down to 3–8",
                "Fewer, better passages beat more passages — in both quality and cost",
            ],
            "resources": [
                {
                    "kind": "doc",
                    "title": "Pinecone — Rerankers and two-stage retrieval",
                    "url": "https://www.pinecone.io/learn/series/rag/rerankers/",
                },
                {
                    "kind": "paper",
                    "title": "Lost in the Middle: How Language Models Use Long Contexts",
                    "url": "https://arxiv.org/abs/2307.03172",
                },
            ],
            "video": {
                "query": "cross encoder reranker RAG two stage retrieval",
                "title": "Rerankers explained",
            },
        },
        {
            "id": "generation",
            "title": "Generation & citations",
            "topic": "Generation",
            "summary": "Prompting the model to answer only from the passages — and to say so when it cannot.",
            "minutes": 16,
            "body": """
## The prompt is a contract

Retrieval delivered passages. The generation step has one job: answer *from
them*, and refuse when they do not contain the answer. That behaviour comes
almost entirely from how the prompt is written.

```python
SYSTEM = \"\"\"You answer strictly from the numbered passages provided.

Rules:
- Cite the passage number in square brackets after each claim, e.g. [2].
- If the passages do not contain the answer, say exactly:
  "The provided sources do not cover this."
- Never use knowledge that is not in the passages.
- Quote figures and identifiers exactly as they appear.\"\"\"

def build_prompt(question: str, passages: list[str]) -> list[dict]:
    context = "\\n\\n".join(f"[{i + 1}] {p}" for i, p in enumerate(passages))
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Passages:\\n{context}\\n\\nQuestion: {question}"},
    ]
```

Three details in that prompt do most of the work: passages are **numbered** so
citation is mechanical, the refusal string is **exact** so you can detect it in
code, and "quote figures exactly" stops the most damaging class of paraphrase.

## Citations that can be verified

A citation is only worth something if the UI can resolve it. Keep the mapping
from passage number to chunk id, and return it alongside the answer:

```python
citations = [
    {"marker": i + 1, "source_id": c.source_id, "title": c.title,
     "url": c.url, "snippet": c.text[:280]}
    for i, c in enumerate(passages)
]
```

Then render the marker as a link. The learner-facing rule: **if a claim has no
marker, treat it as unsupported.**

## Context assembly

- Order passages best-first, and consider repeating the single best one at the
  end of the prompt.
- Include each passage's title/heading — it helps the model tell sources apart.
- Leave headroom. If passages fill the whole window, the answer gets truncated.

## Streaming and latency

Retrieval and reranking happen before the first token, so RAG feels slower than
a bare model. Stream the answer, and show which sources were retrieved while
the model is still writing — perceived latency drops even though total latency
does not.
""",
            "concepts": [
                ("Grounded generation", "Answering only from supplied passages, with an explicit refusal path."),
                ("Citation marker", "A number in the answer that resolves to a specific retrieved chunk."),
                ("Context assembly", "Choosing, ordering and formatting the passages that go into the prompt."),
            ],
            "takeaways": [
                "Number the passages so citing them is mechanical",
                "Give the model an exact refusal string your code can detect",
                "A citation must resolve to a chunk the user can open",
                "Stream the answer and show sources early — perceived latency is what users feel",
            ],
            "resources": [
                {
                    "kind": "doc",
                    "title": "Anthropic — Reduce hallucinations",
                    "url": "https://docs.anthropic.com/en/docs/test-and-evaluate/strengthen-guardrails/reduce-hallucinations",
                },
                {
                    "kind": "doc",
                    "title": "LangChain — RAG tutorial (generation step)",
                    "url": "https://python.langchain.com/docs/tutorials/rag/",
                },
            ],
            "video": {
                "query": "RAG prompt engineering citations grounded answers",
                "title": "Prompting for grounded answers",
            },
        },
        {
            "id": "evaluation",
            "title": "Evaluation",
            "topic": "Evaluation",
            "summary": "Measuring retrieval and generation separately, because they fail separately.",
            "minutes": 17,
            "body": """
## Evaluate the halves separately

"The answer was wrong" is not a diagnosis. There are two independent failure
modes and they need different fixes:

- The retriever did not return the passage that contains the answer.
- The retriever did, and the generator ignored or misread it.

So measure retrieval and generation as separate stages.

## Retrieval metrics

Build a small set of questions paired with the chunk ids that should be
retrieved — 50 well-chosen examples beat 5,000 scraped ones.

- **Recall@k** — was the right chunk in the top *k*? The metric that matters
  most: if it is not retrieved, no prompt can save you.
- **Precision@k** — what fraction of the returned chunks were relevant.
- **MRR** — mean reciprocal rank; rewards putting the right chunk first.

```python
def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    hit = len(set(retrieved[:k]) & relevant)
    return hit / len(relevant) if relevant else 0.0
```

## Generation metrics

Usually LLM-judged, with a rubric:

- **Faithfulness** — is every claim supported by the passages? This is the
  hallucination metric.
- **Answer relevance** — does it actually answer the question asked?
- **Context relevance** — how much of the supplied context was needed? Low
  values mean you are paying for tokens that do nothing.

Frameworks such as RAGAS package these; the important part is that you run
*something* on every change, not which library it comes from.

## Build the golden set first

The most common failure in RAG projects is shipping without an evaluation set,
then tuning by vibes. Write 30–50 real questions with known answers *before*
tuning anything. Every subsequent decision — chunk size, k, reranker on or off
— becomes a measurement instead of an argument.

## Watch it in production

Log the query, the retrieved chunk ids, their scores, the answer and whether it
refused. Refusal rate and mean top-1 similarity are two cheap dashboards that
catch a broken index long before a user reports it.
""",
            "concepts": [
                ("Recall@k", "Whether the answer-bearing chunk appears in the top k results."),
                ("Faithfulness", "Whether every claim in the answer is supported by the retrieved context."),
                ("Golden set", "A curated set of questions with known correct answers and source chunks."),
                ("LLM-as-judge", "Using a language model with a rubric to score answers at scale."),
            ],
            "takeaways": [
                "Retrieval and generation fail independently — measure them separately",
                "Recall@k is the ceiling on everything downstream",
                "Faithfulness is the hallucination metric",
                "Write the golden set before you start tuning",
            ],
            "resources": [
                {
                    "kind": "doc",
                    "title": "RAGAS — evaluation metrics for RAG",
                    "url": "https://docs.ragas.io/",
                },
                {
                    "kind": "article",
                    "title": "Pinecone — RAG evaluation",
                    "url": "https://www.pinecone.io/learn/series/vector-databases-in-production-for-busy-engineers/rag-evaluation/",
                },
            ],
            "video": {
                "query": "RAG evaluation RAGAS faithfulness recall",
                "title": "How to evaluate a RAG system",
            },
        },
        {
            "id": "production",
            "title": "Production RAG",
            "topic": "Production",
            "summary": "Freshness, multi-tenancy, cost, caching and the failure modes that only appear at scale.",
            "minutes": 18,
            "body": """
## Keeping the index fresh

A stale index is a confidently wrong system. Three mechanisms, usually all
three at once:

- **Incremental upserts** keyed by a content hash, so unchanged documents cost
  nothing to re-ingest.
- **Tombstones** — deleting the source row must delete its chunks, or the model
  will cite a document that no longer exists.
- **A periodic full rebuild**, because incremental pipelines drift.

## Multi-tenancy

Never rely on the LLM to respect a tenant boundary. Filter at the query, in the
database:

```sql
select id, content
from   chunks
where  tenant_id = $1              -- enforced by the query, not the prompt
order  by embedding <=> $2
limit  20;
```

Row-level security is stronger still: if the filter is a policy rather than a
`where` clause, forgetting it in one code path is no longer a data leak.

## Cost and latency

Per-request costs, largest first: generation tokens, reranking, embedding the
query, the vector search itself. Levers that actually move the number:

- **Cache** answers for repeated questions, keyed by a normalised query.
- **Cache prompt prefixes** — a static system prompt plus stable context can be
  reused across calls by providers that support it.
- **Shrink k** after measuring; most systems retrieve more than they need.
- **Route by difficulty** — a small model handles most questions.

## The failure modes worth alerting on

| Symptom | Usual cause |
|---|---|
| Confident answers with no citations | Prompt not enforcing grounding |
| Refuses everything | Empty or mis-embedded index; wrong embedding model |
| Right document, wrong passage | Chunks too large, or no reranker |
| Good on demos, bad on real queries | Golden set is not representative |
| Slowly degrading quality | Index drift — nothing re-indexed the updates |

## A minimal production checklist

1. Golden set in CI, with a quality gate on recall@k.
2. Structured logs: query, chunk ids, scores, latency per stage, refusal flag.
3. Tenant filtering enforced in the database.
4. Re-index pipeline with hash-based skip and hard deletes.
5. A dashboard showing refusal rate, p95 latency per stage, and cost per query.
""",
            "concepts": [
                ("Incremental indexing", "Re-embedding only what changed, detected by content hash."),
                ("Tombstone", "A deletion record ensuring removed documents disappear from the index."),
                ("Prompt caching", "Reusing a stable prompt prefix across calls to cut cost and latency."),
                ("Index drift", "Divergence between the source of truth and the index over time."),
            ],
            "takeaways": [
                "Freshness is a pipeline concern: upsert by hash, delete hard, rebuild periodically",
                "Tenant isolation belongs in the query or in RLS, never in the prompt",
                "Generation tokens dominate cost; caching and smaller k are the real levers",
                "Log per-stage latency and refusal rate — they catch a broken index first",
            ],
            "resources": [
                {
                    "kind": "doc",
                    "title": "Pinecone — RAG series",
                    "url": "https://www.pinecone.io/learn/series/rag/",
                },
                {
                    "kind": "doc",
                    "title": "LangChain — RAG tutorial",
                    "url": "https://python.langchain.com/docs/tutorials/rag/",
                },
            ],
            "video": {
                "query": "production RAG systems lessons learned scaling",
                "title": "RAG in production",
            },
            "notes": """
The single highest-leverage habit: log the retrieved chunk ids with every
answer. Almost every "the model hallucinated" report turns out, on inspection,
to be "the retriever returned nothing useful and the model filled the gap".
""",
        },
    ],
    "exams": [
        {
            "id": "rag-exam-1",
            "title": "RAG — Foundations Assessment",
            "description": "Covers chapters 1–4: fundamentals, chunking, embeddings and vector databases.",
            "chapter_ids": ["intro", "chunking", "embeddings", "vector-databases"],
            "questions": [
                {
                    "type": "mcq",
                    "topic": "Fundamentals",
                    "chapter_id": "intro",
                    "prompt": "What problem does RAG primarily solve that fine-tuning does not?",
                    "options": [
                        "Making the model's tone match a brand voice",
                        "Giving the model access to fresh, private, citable knowledge",
                        "Reducing the model's parameter count",
                        "Speeding up token generation",
                    ],
                    "answer": 1,
                    "explanation": "RAG supplies knowledge at query time. Fine-tuning changes behaviour — tone, format, task style — not what the model knows about your documents.",
                },
                {
                    "type": "truefalse",
                    "topic": "Fundamentals",
                    "chapter_id": "intro",
                    "prompt": "If a corpus comfortably fits inside the model's context window, a retrieval pipeline is still required for correctness.",
                    "options": ["True", "False"],
                    "answer": 1,
                    "explanation": "False. If everything fits, put it all in the prompt. Retrieval adds a failure mode (the retriever can miss) with no benefit in that case.",
                },
                {
                    "type": "mcq",
                    "topic": "Chunking",
                    "chapter_id": "chunking",
                    "prompt": "Why does chunk overlap exist?",
                    "options": [
                        "To make the vector index smaller",
                        "So a fact spanning a chunk boundary survives in at least one chunk",
                        "To make embeddings deterministic",
                        "Because vector databases require duplicate rows",
                    ],
                    "answer": 1,
                    "explanation": "Overlap repeats the tail of one chunk at the head of the next so boundary-straddling facts are not lost. 10–20% of chunk size is typical.",
                },
                {
                    "type": "scenario",
                    "topic": "Chunking",
                    "chapter_id": "chunking",
                    "prompt": "Users ask questions about a 300-page contract. Retrieved passages are relevant but the model keeps answering without knowing which clause number it is reading from. What is the fix?",
                    "options": [
                        "Increase the number of retrieved chunks",
                        "Switch to a larger LLM",
                        "Attach heading/clause metadata to every chunk and include it in the prompt",
                        "Lower the chunk overlap to zero",
                    ],
                    "answer": 2,
                    "explanation": "The chunks lack structural metadata. Carrying the heading, page or clause on each chunk — and rendering it in the context — is what makes the answer locatable.",
                },
                {
                    "type": "mcq",
                    "topic": "Embeddings",
                    "chapter_id": "embeddings",
                    "prompt": "You indexed a corpus with embedding model A and query with model B. What happens?",
                    "options": [
                        "Results are slightly worse but usable",
                        "Results are effectively random — the vectors live in different spaces",
                        "The vector database converts between them automatically",
                        "Only recall drops; precision is unaffected",
                    ],
                    "answer": 1,
                    "explanation": "Embedding spaces are not comparable across models. Distances between vectors from different models carry no meaning.",
                },
                {
                    "type": "code",
                    "topic": "Embeddings",
                    "chapter_id": "embeddings",
                    "language": "python",
                    "prompt": "Given unit-normalised vectors, what does this function compute?",
                    "code": "import numpy as np\n\ndef score(a, b):\n    return float(a @ b)",
                    "options": [
                        "Euclidean distance",
                        "Cosine similarity",
                        "Manhattan distance",
                        "Jaccard similarity",
                    ],
                    "answer": 1,
                    "explanation": "For unit-length vectors the dot product equals the cosine of the angle between them — which is why stores ask you to normalise at index time.",
                },
                {
                    "type": "mcq",
                    "topic": "Vector Databases",
                    "chapter_id": "vector-databases",
                    "prompt": "What is the primary purpose of a vector database in a RAG system?",
                    "options": [
                        "Generate the LLM's responses",
                        "Store embeddings and return the nearest ones to a query vector",
                        "Train the language model",
                        "Replace the LLM entirely",
                    ],
                    "answer": 1,
                    "explanation": "It is a nearest-neighbour store: embeddings plus text plus metadata, with a fast top-k query and metadata filtering.",
                },
                {
                    "type": "mcq",
                    "topic": "Vector Databases",
                    "chapter_id": "vector-databases",
                    "prompt": "What does an approximate nearest neighbour (ANN) index trade away for speed?",
                    "options": [
                        "Storage space",
                        "Some recall — it may miss a few true nearest neighbours",
                        "The ability to store metadata",
                        "Support for cosine similarity",
                    ],
                    "answer": 1,
                    "explanation": "ANN indexes such as HNSW and IVFFlat give up exactness. Recall@k measures how much.",
                },
                {
                    "type": "truefalse",
                    "topic": "Vector Databases",
                    "chapter_id": "vector-databases",
                    "prompt": "In pgvector, the `<=>` operator returns cosine distance, so smaller values mean more similar.",
                    "options": ["True", "False"],
                    "answer": 0,
                    "explanation": "True. That is why queries `order by embedding <=> $1` ascending, and similarity is often reported as `1 - distance`.",
                },
                {
                    "type": "scenario",
                    "topic": "Chunking",
                    "chapter_id": "chunking",
                    "prompt": "Retrieved chunks are frequently cut mid-sentence and the model's answers read as fragments. Which change addresses the cause?",
                    "options": [
                        "Move from fixed-size character splitting to recursive splitting on paragraph and sentence separators",
                        "Increase the number of vector index probes",
                        "Use a bigger embedding model",
                        "Lower the temperature of the generation call",
                    ],
                    "answer": 0,
                    "explanation": "Fixed-size splitting cuts blindly. Recursive splitting tries paragraph, then line, then sentence boundaries first and only falls back to characters.",
                },
            ],
        },
        {
            "id": "rag-exam-2",
            "title": "RAG — Pipeline & Production Assessment",
            "description": "Covers chapters 5–8: retrieval, reranking, generation, evaluation and production.",
            "chapter_ids": ["retrieval", "reranking", "generation", "evaluation", "production"],
            "questions": [
                {
                    "type": "mcq",
                    "topic": "Retrieval",
                    "chapter_id": "retrieval",
                    "prompt": "Why does hybrid search usually beat dense-only retrieval?",
                    "options": [
                        "It halves the storage requirement",
                        "Keyword search catches exact rare tokens that embeddings blur away",
                        "It removes the need for chunking",
                        "It eliminates the need to rerank",
                    ],
                    "answer": 1,
                    "explanation": "Dense retrieval is strong on meaning and weak on exact identifiers, codes and rare names. BM25 covers precisely that gap.",
                },
                {
                    "type": "code",
                    "topic": "Retrieval",
                    "chapter_id": "retrieval",
                    "language": "python",
                    "prompt": "What property of this fusion function makes it useful for combining BM25 and vector results?",
                    "code": "def rrf(rankings, k=60):\n    scores = {}\n    for ranking in rankings:\n        for pos, doc in enumerate(ranking):\n            scores[doc] = scores.get(doc, 0) + 1 / (k + pos + 1)\n    return sorted(scores, key=scores.get, reverse=True)",
                    "options": [
                        "It normalises the two score scales onto [0, 1]",
                        "It uses only rank positions, so incompatible score scales never need aligning",
                        "It guarantees the dense result always wins ties",
                        "It removes duplicate documents from both lists",
                    ],
                    "answer": 1,
                    "explanation": "Reciprocal Rank Fusion reads positions, not scores. A cosine similarity of 0.83 and a BM25 score of 14.2 never have to be made comparable.",
                },
                {
                    "type": "mcq",
                    "topic": "Reranking",
                    "chapter_id": "reranking",
                    "prompt": "What makes a cross-encoder more accurate than the bi-encoder used for retrieval?",
                    "options": [
                        "It has more parameters",
                        "It sees the query and the passage together in a single forward pass",
                        "It uses cosine similarity instead of dot product",
                        "It is trained on more data",
                    ],
                    "answer": 1,
                    "explanation": "The bi-encoder embeds each side independently so document vectors can be precomputed. The cross-encoder attends over both jointly — which is also why it cannot be precomputed.",
                },
                {
                    "type": "scenario",
                    "topic": "Reranking",
                    "chapter_id": "reranking",
                    "prompt": "Recall@50 is 0.95 but answers are still poor, and the model is given all 50 passages. What is the most effective next step?",
                    "options": [
                        "Retrieve 200 passages instead of 50",
                        "Add a reranking stage and pass only the top 5 to the model",
                        "Switch to a different embedding model",
                        "Reduce chunk overlap",
                    ],
                    "answer": 1,
                    "explanation": "The answer is being retrieved — the generator is drowning in noise. Rerank to a handful of passages; this is the classic two-stage fix.",
                },
                {
                    "type": "mcq",
                    "topic": "Generation",
                    "chapter_id": "generation",
                    "prompt": "Why should the refusal string in a RAG system prompt be an exact, fixed phrase?",
                    "options": [
                        "It makes the answer shorter",
                        "So application code can detect refusals reliably and react to them",
                        "Because models cannot generate varied refusals",
                        "To reduce token cost",
                    ],
                    "answer": 1,
                    "explanation": "A fixed refusal string is machine-detectable: you can log refusal rate, trigger a fallback search, or show a different UI state.",
                },
                {
                    "type": "truefalse",
                    "topic": "Generation",
                    "chapter_id": "generation",
                    "prompt": "Numbering the passages in the prompt makes citation a mechanical operation for the model.",
                    "options": ["True", "False"],
                    "answer": 0,
                    "explanation": "True. `[1] … [2] …` gives the model a stable label to emit, and gives your UI a key to resolve back to a chunk.",
                },
                {
                    "type": "mcq",
                    "topic": "Evaluation",
                    "chapter_id": "evaluation",
                    "prompt": "Which metric tells you whether every claim in an answer is supported by the retrieved context?",
                    "options": ["Recall@k", "Faithfulness", "MRR", "Precision@k"],
                    "answer": 1,
                    "explanation": "Faithfulness is the hallucination metric. Recall@k, MRR and precision@k all measure retrieval, not the answer.",
                },
                {
                    "type": "scenario",
                    "topic": "Evaluation",
                    "chapter_id": "evaluation",
                    "prompt": "An answer is wrong. Logs show the chunk containing the correct answer was never in the top 20 results. Where is the defect?",
                    "options": [
                        "The generation prompt",
                        "The retrieval stage",
                        "The citation renderer",
                        "The model's temperature setting",
                    ],
                    "answer": 1,
                    "explanation": "If the passage never arrived, no prompt can fix it. Recall@k is the ceiling on everything downstream.",
                },
                {
                    "type": "mcq",
                    "topic": "Production",
                    "chapter_id": "production",
                    "prompt": "Where should multi-tenant isolation be enforced in a RAG system?",
                    "options": [
                        "In the system prompt, by telling the model which tenant to use",
                        "In the database query or row-level security policy",
                        "In the embedding model",
                        "In the frontend, by hiding other tenants' answers",
                    ],
                    "answer": 1,
                    "explanation": "The prompt is not a security boundary. Filter in the query, or better, enforce it as a policy so no code path can forget it.",
                },
                {
                    "type": "scenario",
                    "topic": "Production",
                    "chapter_id": "production",
                    "prompt": "The system suddenly refuses almost every question after a deploy. Which cause fits best?",
                    "options": [
                        "The reranker latency increased",
                        "The embedding model changed, so query vectors no longer match the indexed ones",
                        "The context window was increased",
                        "Chunk overlap was raised from 10% to 15%",
                    ],
                    "answer": 1,
                    "explanation": "A blanket refusal means nothing relevant is coming back. A changed embedding model is the classic cause — the index must be rebuilt with it.",
                },
            ],
        },
    ],
}
