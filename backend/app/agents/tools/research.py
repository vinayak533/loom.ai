"""Agents 6 and 7 — SEO Content Creator and Research & Fact-Checker.

    search_web        REAL. Tavily first (TEVILY_API_KEY), falling back to the
                      Exa client this project already uses for the Code
                      section. Both are real search APIs; neither is simulated.
                      With neither key set it reports "not configured".
    read_url          REAL. Jina Reader (JINA_AI_READER_API_KEY) for
                      HTML→Markdown, falling back to the Learn section's own
                      extractor (`app.learn.ingest.from_url`) so the tool
                      degrades instead of disappearing when the key is absent.
    rank_domain_trust REAL, and deliberately simple: TLD rules plus a
                      maintained list of well-known publishers and known
                      content-farm patterns. Its output states its own
                      limitations in every response, because a three-tier
                      heuristic dressed up as an authority score would be worse
                      than no score at all.
    keyword_density   REAL. Counts occurrences of each phrase against the total
                      word count, n-gram aware, and reports the standard
                      over/under-optimisation bands.
    readability_score REAL. Flesch Reading Ease, Flesch-Kincaid Grade and
                      Gunning Fog, computed from an actual syllable counter —
                      not a number the model made up.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.agents.tools.base import (
    ToolContext,
    ToolResult,
    artifact,
    as_json,
    not_configured,
)
from app.config import get_settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# search_web
# ---------------------------------------------------------------------------


async def search_web(ctx: ToolContext, args: dict) -> ToolResult:
    """Search the live web. Tavily preferred, Exa as the fallback."""
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult("Error: `query` was empty.", success=False)

    count = max(1, min(int(args.get("num_results") or 6), 12))
    depth = str(args.get("depth") or "basic").lower()
    settings = get_settings()

    results: list[dict[str, Any]] = []
    provider = ""
    errors: list[str] = []

    if settings.tavily_enabled:
        try:
            results = await _tavily(query, count, depth)
            provider = "tavily"
        except Exception as exc:  # noqa: BLE001
            log.info("Tavily search failed: %s", exc)
            errors.append(f"Tavily: {exc}")

    if not results and settings.exa_api_key:
        try:
            results = await _exa(query, count)
            provider = "exa"
        except Exception as exc:  # noqa: BLE001
            log.info("Exa search failed: %s", exc)
            errors.append(f"Exa: {exc}")

    if not provider:
        return not_configured(
            "search_web",
            "TEVILY_API_KEY",
            "Neither TEVILY_API_KEY nor EXA_API_KEY is set, so nothing can be "
            "looked up. Answer from what you already know and say plainly that "
            "you could not verify it against the live web.",
        )
    if not results:
        detail = f" ({'; '.join(errors)})" if errors else ""
        return ToolResult(f"No results for `{query}`{detail}.", meta={"provider": provider})

    lines = [
        f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet'][:600]}"
        for i, r in enumerate(results, start=1)
    ]
    return ToolResult(
        output=f"{len(results)} results from {provider}:\n\n" + "\n\n".join(lines),
        # `results` matches the shape the existing search tool already emits,
        # so the chat's tool card renders these citations with no new code.
        meta={"provider": provider, "results": results},
    )


async def _tavily(query: str, count: int, depth: str) -> list[dict[str, Any]]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {settings.tevily_api_key}"},
            json={
                "query": query,
                "max_results": count,
                "search_depth": "advanced" if depth == "advanced" else "basic",
                "include_answer": False,
            },
        )
    response.raise_for_status()
    payload = response.json()
    return [
        {
            "title": r.get("title") or "(untitled)",
            "url": r.get("url") or "",
            "snippet": (r.get("content") or "").strip(),
            "score": r.get("score"),
        }
        for r in (payload.get("results") or [])
    ]


async def _exa(query: str, count: int) -> list[dict[str, Any]]:
    settings = get_settings()

    def _call():
        from exa_py import Exa

        return Exa(api_key=settings.exa_api_key).search_and_contents(
            query, num_results=count, text={"max_characters": 1200}, type="auto"
        )

    response = await asyncio.to_thread(_call)
    return [
        {
            "title": getattr(r, "title", None) or "(untitled)",
            "url": getattr(r, "url", "") or "",
            "snippet": (getattr(r, "text", None) or "").strip(),
            "score": getattr(r, "score", None),
        }
        for r in (getattr(response, "results", []) or [])
    ]


# ---------------------------------------------------------------------------
# read_url
# ---------------------------------------------------------------------------

MAX_PAGE_CHARS = 30_000


async def read_url(ctx: ToolContext, args: dict) -> ToolResult:
    """Fetch a page and return it as Markdown."""
    url = str(args.get("url") or "").strip()
    if not url:
        return ToolResult("Error: `url` was empty.", success=False)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    settings = get_settings()
    limit = max(1_000, min(int(args.get("max_chars") or MAX_PAGE_CHARS), MAX_PAGE_CHARS))

    text = ""
    title = ""
    via = ""

    if settings.jina_enabled:
        try:
            title, text = await _jina(url)
            via = "jina-reader"
        except Exception as exc:  # noqa: BLE001
            log.info("Jina reader failed for %s: %s", url, exc)

    if not text:
        # The Learn section's extractor. Not a lesser answer — it is the same
        # code that ingests notebook sources, and it handles PDFs and YouTube
        # transcripts, which the reader endpoint does not.
        from app.learn import ingest

        extracted = await ingest.from_url(url)
        if not extracted.ok:
            return ToolResult(
                f"Could not read {url}: {extracted.error}",
                success=False,
                meta={"url": url},
            )
        title, text, via = extracted.title, extracted.text, "builtin-extractor"

    truncated = len(text) > limit
    body = text[:limit]
    payload = {
        "url": url,
        "title": title,
        "characters": len(text),
        "returned": len(body),
        "truncated": truncated,
        "via": via,
        # The surcharge on this tool is Jina's per-call price. When the
        # built-in extractor served the page — because Jina has no key, or
        # failed and we fell through — no vendor was billed, so neither is the
        # user. The graph reads this flag; see `_execute_call`.
        "billable": via == "jina-reader",
    }
    tail = (
        f"\n\n[Truncated at {limit:,} of {len(text):,} characters.]"
        if truncated
        else ""
    )
    return ToolResult(
        output=f"# {title or url}\n\nSource: {url} (via {via})\n\n{body}{tail}",
        meta=payload,
    )


async def _jina(url: str) -> tuple[str, str]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(
            f"https://r.jina.ai/{url}",
            headers={
                "Authorization": f"Bearer {settings.jina_ai_reader_api_key}",
                "X-Return-Format": "markdown",
            },
        )
    response.raise_for_status()
    body = response.text
    # The reader prefixes a small header block; lift the title out of it and
    # drop the rest so the body starts at the actual content.
    title = ""
    match = re.match(r"^Title:\s*(.+)$", body.split("\n", 1)[0])
    if match:
        title = match.group(1).strip()
        parts = body.split("Markdown Content:", 1)
        body = parts[1].strip() if len(parts) == 2 else body
    return title, body.strip()


# ---------------------------------------------------------------------------
# rank_domain_trust
# ---------------------------------------------------------------------------

#: Well-known outlets with published corrections policies and editorial
#: standards. Being on this list means "has an accountable newsroom", NOT
#: "is correct" — a distinction the tool's own output repeats every time.
_ESTABLISHED_PUBLISHERS = {
    "reuters.com", "apnews.com", "bbc.co.uk", "bbc.com", "ft.com",
    "nytimes.com", "washingtonpost.com", "theguardian.com", "economist.com",
    "wsj.com", "bloomberg.com", "npr.org", "aljazeera.com", "nature.com",
    "science.org", "sciencedirect.com", "thelancet.com", "nejm.org",
    "bmj.com", "cell.com", "pnas.org", "ieee.org", "acm.org", "arxiv.org",
    "propublica.org", "afp.com", "dw.com", "cbc.ca", "abc.net.au",
}

#: Primary or near-primary sources for technical claims: the thing itself
#: rather than an article about the thing.
_PRIMARY_TECHNICAL = {
    "github.com", "gitlab.com", "developer.mozilla.org", "w3.org",
    "ietf.org", "rfc-editor.org", "python.org", "docs.python.org",
    "rust-lang.org", "golang.org", "go.dev", "nodejs.org", "kernel.org",
    "postgresql.org", "sqlite.org", "kubernetes.io", "openjdk.org",
    "docs.oracle.com", "learn.microsoft.com", "developer.apple.com",
    "developer.android.com", "cloud.google.com", "docs.aws.amazon.com",
}

#: User-generated or aggregated. Frequently *useful* and frequently right —
#: but not citable as the source of a fact. Follow them to their own citations.
_AGGREGATOR_OR_UGC = {
    "wikipedia.org", "reddit.com", "quora.com", "medium.com", "substack.com",
    "stackoverflow.com", "stackexchange.com", "news.ycombinator.com",
    "x.com", "twitter.com", "facebook.com", "linkedin.com", "tiktok.com",
    "youtube.com", "pinterest.com", "tumblr.com", "blogspot.com",
    "wordpress.com", "wixsite.com", "answers.com", "ehow.com",
}

#: Patterns that correlate with SEO content farms and scraped aggregators.
#: A pattern match is a prompt to look harder, not a verdict.
_LOW_TRUST_PATTERNS = (
    (re.compile(r"(^|\.)(top|best)\d*[a-z]*\.(com|net|org)$"), "listicle-farm naming"),
    (re.compile(r"-?(review|deals|coupon|discount)s?\.(com|net)$"), "affiliate-driven"),
    (re.compile(r"^(www\.)?[a-z]+\d{2,}\.(com|net|info|biz|xyz)$"), "generated-looking domain"),
    (re.compile(r"\.(tk|ml|ga|cf|gq|xyz|top|click|link)$"), "free or bulk-registered TLD"),
    (re.compile(r"(news|daily|times|post|herald)\d+\."), "generated news-style domain"),
)


def _classify(url: str) -> dict[str, Any]:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]

    if not host:
        return {
            "url": url, "domain": "", "tier": "unknown",
            "reason": "Not a parseable URL.", "citable": False,
        }

    def _matches(collection: set[str]) -> bool:
        # Suffix match so `docs.python.org` inherits `python.org`, without
        # `notpython.org` matching by accident.
        return any(host == d or host.endswith("." + d) for d in collection)

    if host.endswith(".gov") or ".gov." in host or host.endswith(".mil"):
        return {
            "url": url, "domain": host, "tier": "high",
            "reason": "Government domain — a primary source for its own policy, statistics and law.",
            "citable": True,
        }
    if host.endswith(".edu") or host.endswith(".ac.uk") or ".edu." in host:
        return {
            "url": url, "domain": host, "tier": "high",
            "reason": "Academic institution. Note that a personal page hosted on one is not peer reviewed.",
            "citable": True,
        }
    if _matches(_PRIMARY_TECHNICAL):
        return {
            "url": url, "domain": host, "tier": "high",
            "reason": "Primary technical source — the specification, documentation or code itself.",
            "citable": True,
        }
    if _matches(_ESTABLISHED_PUBLISHERS):
        return {
            "url": url, "domain": host, "tier": "high",
            "reason": "Established publisher with an accountable newsroom or peer review. Reports on facts; is not itself the primary record.",
            "citable": True,
        }
    if _matches(_AGGREGATOR_OR_UGC):
        return {
            "url": url, "domain": host, "tier": "medium",
            "reason": "User-generated or aggregated. Often correct and often useful — follow it to its own citations and cite those instead.",
            "citable": False,
        }
    for pattern, why in _LOW_TRUST_PATTERNS:
        if pattern.search(host):
            return {
                "url": url, "domain": host, "tier": "low",
                "reason": f"Matches a low-quality pattern ({why}). Corroborate before citing.",
                "citable": False,
            }
    if host.endswith(".org"):
        return {
            "url": url, "domain": host, "tier": "medium",
            "reason": "A .org registration implies nothing about accuracy — anyone may register one. Judge it on the organisation.",
            "citable": True,
        }
    return {
        "url": url, "domain": host, "tier": "medium",
        "reason": "Not on any list here. Unrecognised is not the same as untrustworthy — judge it on the page.",
        "citable": True,
    }


#: Repeated on every response. The single most important thing about this tool
#: is what it cannot do, and a caveat that appears only in the docs is a caveat
#: the model will not pass on to the user.
_TRUST_CAVEAT = (
    "This is a transparent domain heuristic — TLD rules plus maintained lists "
    "of well-known publishers, primary technical sources, aggregators and "
    "content-farm patterns. It knows nothing about the page itself: not its "
    "author, its date, its corrections history, or whether this particular "
    "article is any good. A `high` domain publishes wrong things and a `low` "
    "one publishes right ones. Use it to decide what to read more carefully, "
    "never as the reason a claim is true. Say this to the user when you present "
    "the rankings."
)


async def rank_domain_trust(ctx: ToolContext, args: dict) -> ToolResult:
    urls = args.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    if not urls:
        return ToolResult("Error: `urls` was empty.", success=False)

    ranked = [_classify(str(u)) for u in urls[:40]]
    tiers = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for entry in ranked:
        tiers[entry["tier"]] = tiers.get(entry["tier"], 0) + 1

    payload = {"ranked": ranked, "counts": tiers, "limitations": _TRUST_CAVEAT}
    return ToolResult(
        output=(
            f"{tiers['high']} high · {tiers['medium']} medium · {tiers['low']} low.\n\n"
            + as_json(payload)
        ),
        meta={**payload, **artifact("citations", {"sources": ranked})},
    )


# ---------------------------------------------------------------------------
# keyword_density
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


async def keyword_density(ctx: ToolContext, args: dict) -> ToolResult:
    text = str(args.get("text") or "")
    keywords = args.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    if not text:
        return ToolResult("Error: `text` was empty.", success=False)
    if not keywords:
        return ToolResult("Error: `keywords` was empty.", success=False)

    words = _WORD_RE.findall(text.lower())
    total = len(words)
    if total == 0:
        return ToolResult("Error: no words found in `text`.", success=False)

    rows: list[dict[str, Any]] = []
    for raw in keywords[:25]:
        phrase = str(raw).strip().lower()
        if not phrase:
            continue
        terms = _WORD_RE.findall(phrase)
        if not terms:
            continue
        # Count the phrase as an n-gram over the tokenised body, so "content
        # marketing" is one hit rather than one hit each for its two words.
        n = len(terms)
        occurrences = sum(
            1 for i in range(total - n + 1) if words[i : i + n] == terms
        )
        # Density is against the count of n-gram *positions*, which is the
        # convention SEO tools use — dividing an n-gram count by the raw word
        # count understates a phrase by a factor of n.
        density = (occurrences * n / total) * 100 if total else 0.0
        rows.append(
            {
                "keyword": phrase,
                "occurrences": occurrences,
                "words_in_phrase": n,
                "density_percent": round(density, 3),
                "verdict": _density_verdict(density, occurrences),
            }
        )

    payload = {
        "total_words": total,
        "keywords": rows,
        "bands": {
            "absent": "0 occurrences — the phrase is not in the piece at all.",
            "thin": "below 0.5% — present, but not what the piece is about.",
            "healthy": "0.5%-2.5% — natural integration.",
            "stuffed": "above 2.5% — reads as keyword stuffing; rewrite rather than deleting at random.",
        },
    }
    return ToolResult(
        output=f"{total:,} words analysed.\n\n{as_json(payload)}", meta=payload
    )


def _density_verdict(density: float, occurrences: int) -> str:
    if occurrences == 0:
        return "absent"
    if density < 0.5:
        return "thin"
    if density <= 2.5:
        return "healthy"
    return "stuffed"


# ---------------------------------------------------------------------------
# readability_score
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"[.!?]+(?:\s|$)")
_VOWEL_GROUPS = re.compile(r"[aeiouy]+")


def _syllables(word: str) -> int:
    """Count syllables in an English word.

    The standard vowel-group heuristic with the two corrections that matter
    most in practice: a silent trailing `e`, and `le` after a consonant, which
    is a syllable ("table") that the plain rule drops. It is approximate — all
    three formulas below are defined on approximate syllable counts, so this is
    the accepted method rather than a shortcut.
    """
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    if len(word) <= 3:
        return 1
    groups = _VOWEL_GROUPS.findall(word)
    count = len(groups)
    if word.endswith("e") and not word.endswith(("le", "ee", "ye")):
        count -= 1
    if word.endswith("le") and len(word) > 2 and word[-3] not in "aeiouy":
        count += 1
    if word.endswith("ed") and not word.endswith(("ted", "ded")):
        count -= 1
    return max(1, count)


async def readability_score(ctx: ToolContext, args: dict) -> ToolResult:
    text = str(args.get("text") or "").strip()
    if not text:
        return ToolResult("Error: `text` was empty.", success=False)

    # Strip Markdown furniture first: code fences, link targets and heading
    # markers are not prose, and counting them makes the score meaningless.
    body = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    body = re.sub(r"`[^`]*`", " ", body)
    body = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", body)
    body = re.sub(r"^#{1,6}\s*", "", body, flags=re.MULTILINE)
    body = re.sub(r"[*_>|-]+", " ", body)

    words = _WORD_RE.findall(body)
    word_count = len(words)
    if word_count < 10:
        return ToolResult(
            "Error: too little prose to score (fewer than 10 words after "
            "stripping code and markup). These formulas are meaningless on "
            "short text.",
            success=False,
        )

    sentences = max(1, len([s for s in _SENTENCE_RE.split(body) if s.strip()]))
    syllable_total = sum(_syllables(w) for w in words)
    complex_words = sum(1 for w in words if _syllables(w) >= 3)

    words_per_sentence = word_count / sentences
    syllables_per_word = syllable_total / word_count

    flesch = 206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word
    fk_grade = 0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59
    fog = 0.4 * (words_per_sentence + 100 * (complex_words / word_count))

    payload = {
        "flesch_reading_ease": round(flesch, 1),
        "flesch_reading_ease_band": _flesch_band(flesch),
        "flesch_kincaid_grade": round(fk_grade, 1),
        "gunning_fog_index": round(fog, 1),
        "words": word_count,
        "sentences": sentences,
        "words_per_sentence": round(words_per_sentence, 1),
        "syllables_per_word": round(syllables_per_word, 2),
        "complex_words": complex_words,
        "complex_word_percent": round(100 * complex_words / word_count, 1),
        "target": (
            "Flesch Reading Ease 55-70 suits a general audience. Below 50 reads "
            "as academic; above 80 as simplistic for most B2B copy."
        ),
        "how_to_move_it": (
            "Sentence length moves the score far more than vocabulary does. "
            "Split long sentences before reaching for shorter words."
        ),
        "note": (
            "Real formulas over an approximate syllable count, which is how "
            "all three are defined. They measure sentence and word length — "
            "not clarity, structure or whether the argument holds."
        ),
    }
    return ToolResult(
        output=(
            f"Flesch Reading Ease {payload['flesch_reading_ease']} "
            f"({payload['flesch_reading_ease_band']}) · "
            f"Flesch-Kincaid grade {payload['flesch_kincaid_grade']} · "
            f"Gunning Fog {payload['gunning_fog_index']}\n\n{as_json(payload)}"
        ),
        meta=payload,
    )


def _flesch_band(score: float) -> str:
    if score >= 90:
        return "very easy — 5th grade"
    if score >= 80:
        return "easy — 6th grade"
    if score >= 70:
        return "fairly easy — 7th grade"
    if score >= 60:
        return "plain English — 8th to 9th grade"
    if score >= 50:
        return "fairly difficult — 10th to 12th grade"
    if score >= 30:
        return "difficult — university"
    return "very difficult — postgraduate"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, dict] = {
    "search_web": {
        "name": "search_web",
        "description": (
            "Search the live web. Use it for anything time-sensitive, anything "
            "you would otherwise be recalling, and every claim you intend to "
            "verify — one search per claim, not one for the whole topic. "
            "Returns titles, URLs and snippets. A snippet is an advert for a "
            "page, not evidence from it: follow up with `read_url`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "num_results": {
                    "type": "integer",
                    "description": "How many results (1-12, default 6).",
                },
                "depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "description": "`advanced` is slower and searches harder.",
                },
            },
            "required": ["query"],
        },
    },
    "read_url": {
        "name": "read_url",
        "description": (
            "Fetch a page and return its content as Markdown. Call it on any "
            "source you intend to cite — quoting a search snippet is citing an "
            "advert for a page rather than the page. Handles HTML, PDFs and "
            "YouTube transcripts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The page to read."},
                "max_chars": {
                    "type": "integer",
                    "description": "Cap the returned text (default 30000).",
                },
            },
            "required": ["url"],
        },
    },
    "rank_domain_trust": {
        "name": "rank_domain_trust",
        "description": (
            "Classify each URL's domain as high, medium or low trust, with the "
            "reason. Call it on every source you plan to cite. It is a "
            "transparent domain heuristic that knows nothing about the page "
            "itself — its own output says so, and you must pass that caveat on "
            "to the user rather than presenting the tier as a verdict."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The URLs to classify.",
                }
            },
            "required": ["urls"],
        },
    },
    "keyword_density": {
        "name": "keyword_density",
        "description": (
            "Measure how often each target phrase appears, as a percentage of "
            "the piece. Phrase-aware, so a two-word keyword counts as one hit. "
            "Call it on your finished draft: below 0.5% the phrase is not "
            "really in the article, above 2.5% it reads as stuffing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The draft."},
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Target phrases.",
                },
            },
            "required": ["text", "keywords"],
        },
    },
    "readability_score": {
        "name": "readability_score",
        "description": (
            "Compute Flesch Reading Ease, Flesch-Kincaid grade level and "
            "Gunning Fog for a draft. Real formulas over a real syllable count "
            "— never state a readability score you did not get from this tool. "
            "Markdown, code blocks and link targets are stripped before "
            "scoring. Aim for 55-70 Reading Ease for a general audience."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The prose to score."}
            },
            "required": ["text"],
        },
    },
}

HANDLERS = {
    "search_web": search_web,
    "read_url": read_url,
    "rank_domain_trust": rank_domain_trust,
    "keyword_density": keyword_density,
    "readability_score": readability_score,
}
