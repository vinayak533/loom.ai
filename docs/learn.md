# Learn: courses and notebooks

> The authored course platform and the source-grounded notebooks.
>
> [← Back to the README](../README.md)

Two surfaces under one section, switched at the top: **Courses** (authored
curriculum, the default) and **Notebooks** (your own sources).

## Courses

Ten structured tracks — RAG, Prompt Engineering, Python, TypeScript,
React/Next.js, FastAPI, PostgreSQL, Git, System Design, ML Basics — each with
eight or nine chapters and two assessments. The loop is:

```
catalogue ─▶ course ─▶ chapter ─▶ mark complete ─▶ assessment unlocks
                ▲                                        │
                │                                        ▼
          review chapter ◀── recommendations ◀── weak-area analysis
```

Course content is **static data**, not database rows: one Python module per
course under `backend/app/learn/courses/`, normalised by that package's
registry. Adding an eleventh course is a new module plus one line in `MODULES`
— no endpoint, no component, no migration. Only what a *person* did with a
course is stored (`course_progress`, `course_exam_attempts`), keyed by the
content's own string ids, so a course can be rewritten without a data
migration.

Grading does more than count: every question carries a topic and the chapter it
came from, so topics scoring under 70% come back as **weak areas**, each with
the chapter to revisit, that chapter's resources, and practice prompts drawn
from its own takeaways. Answer keys never leave the server — the exam endpoint
strips `answer` and `explanation` from every question until a submission is
graded.

Three endpoints, split by payload size rather than by resource, which is what
keeps the catalogue from shipping eighty chapters of prose to draw ten cards:

| Endpoint | Returns | Cached |
|---|---|---|
| `GET /api/learn/courses` | card metadata + progress | 30s, dropped on write |
| `GET /api/learn/courses/{id}` | chapter titles + exam state | 30s, dropped on write |
| `GET /api/learn/courses/{id}/chapters/{id}` | one chapter's body | for the session |

Chapter bodies carry no per-user state, so they are identical for everyone and
cached permanently client-side; the next chapter is prefetched when one is
opened. Videos are click-to-load facades — nothing is requested from YouTube
until someone presses play. Progress writes are optimistic and the route is
persisted, so closing the app mid-chapter and returning reopens that chapter.

## Notebooks

A **notebook** holds sources, a chat scoped to them, notes, and optionally a
course. Three ways in: a PDF (text layer only — there is no OCR), a URL the
backend fetches and reduces to readable text, or pasted text. A YouTube URL is
recognised and resolved to its transcript rather than scraped, reusing
`app/sources.py`.

```
source ──▶ extract ──▶ chunk (~1200 chars, paragraph-aligned, 180 overlap)
                          │
                          └──▶ embed (768d) ──▶ notebook_chunks.embedding
                                                        │
question ──▶ embed ──▶ match_notebook_chunks(...) ──┴──▶ top 8 passages
                                                             │
                                          grounded prompt ───┴──▶ llm_router
```

Answers cite passages as `[1]`, `[2]`; the UI resolves each marker back to the
source and shows the passage behind it, so an answer can be disbelieved
cheaply. When the retrieved passages do not cover the question, the prompt
requires the model to say so rather than fall back on what it knows.

**Embeddings** are 768-dimensional hashed bag-of-words vectors, computed
in-process with no key and no network call (`learn/embeddings.py`). Cosine over
these is weighted lexical overlap: a decent retriever for a personal notebook.
The dimension matches the `vector(768)` column, which is declared once, and
each chunk stores an `embedding_signature` so that a future change of
vectoriser is detectable rather than silently mismatched. `/api/learn/config`
reports the live mode.

**Structured mode** generates a curriculum from the notebook's sources: 4–7
sequential sections, each with explanatory content and a short multiple-choice
check-in, plus per-section progress. Regenerating replaces the course and its
progress, since progress against a differently-shaped curriculum is a number
about nothing.

Without Supabase, Learn keeps notebooks in the backend's memory and the library
says so — the section still runs end to end locally, it just does not survive a
restart.
