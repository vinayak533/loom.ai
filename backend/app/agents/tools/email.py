"""Agent 2 — Email Copywriter.

    list_email_templates  REAL. A maintained library of structural skeletons,
                          one per audience. Structure only, never copy — the
                          model fills them, which is why the templates carry
                          `{placeholders}` and a rule list rather than prose.
    score_subject_line    REAL. Measured signals only: length against the
                          mobile truncation point, spam-word hits, ALL-CAPS and
                          punctuation ratios, personalisation and specificity
                          markers. The A/B *generation* is the model's; this is
                          what makes the choice between the two evidence-based.
    check_spam_words      REAL. A maintained list of trigger words and phrases,
                          matched on word boundaries so "free" is caught and
                          "freedom" is not.
    send_email            REAL — Resend HTTP API. Gated: it goes through Agent
                          9's approval mechanism before a single byte leaves,
                          and returns "not configured" when RESEND_API_KEY is
                          unset rather than pretending to have sent.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.agents.tools.base import ToolContext, ToolResult, artifact, as_json, not_configured
from app.config import get_settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# list_email_templates
# ---------------------------------------------------------------------------

# Structural scaffolding, not copy. Each template says what each beat of the
# email is *for*; the model writes the actual sentences. Shipping a filled-in
# template verbatim would produce the exact generic mail this agent exists to
# avoid, which is why nothing here is a usable sentence on its own.
TEMPLATES: dict[str, dict[str, Any]] = {
    "formal": {
        "label": "Formal / institutional",
        "when": "Notices, policy changes, anything with a compliance flavour.",
        "structure": [
            "Salutation with title and surname",
            "Purpose stated in the first sentence — no preamble",
            "The substance, one idea per paragraph",
            "What the reader must do, and by when",
            "Where to direct questions",
            "Formal sign-off with name, role and organisation",
        ],
        "rules": [
            "No contractions, no exclamation marks, no rhetorical questions.",
            "Passive voice is acceptable here and nowhere else in this agent.",
            "Dates in full (14 March 2026), never relative ('next Tuesday').",
        ],
        "length": "150-250 words",
    },
    "cold_outbound": {
        "label": "Cold outbound",
        "when": "First contact with someone who has never heard of you.",
        "structure": [
            "One line proving you know who they are — specific, not flattery",
            "The problem you believe they have, in their language",
            "One sentence on what you do about it",
            "One piece of evidence: a number, a named customer, a result",
            "A low-friction ask — a reply, not a 30-minute call",
        ],
        "rules": [
            "Every sentence must earn the next one. Under 120 words, hard.",
            "Never open with 'I hope this email finds you well' or 'My name is'.",
            "One call to action. A second one halves the first.",
            "No attachments, no images, no more than one link.",
        ],
        "length": "60-120 words",
    },
    "warm_follow_up": {
        "label": "Warm follow-up",
        "when": "You have already spoken, met, or been introduced.",
        "structure": [
            "Reference the prior contact in the first line — when and what",
            "The thing you said you would send or do",
            "One new piece of value since you last spoke",
            "A concrete next step with a proposed time",
        ],
        "rules": [
            "Name the specific thing discussed. 'Great chatting' is not a reference.",
            "If you promised something, it goes above everything else.",
            "Contractions are fine; this should sound like the conversation did.",
        ],
        "length": "80-150 words",
    },
    "re_engagement": {
        "label": "Re-engagement",
        "when": "The thread went quiet and you are reviving it.",
        "structure": [
            "Acknowledge the gap without apologising for existing",
            "What changed that makes this worth reopening",
            "An explicit easy out, which is what makes replies honest",
        ],
        "rules": [
            "Never say 'just bumping this' or 'circling back'.",
            "Offer the no as a real option — it raises the reply rate.",
        ],
        "length": "50-90 words",
    },
    "announcement": {
        "label": "Product / feature announcement",
        "when": "Telling existing users something shipped.",
        "structure": [
            "What shipped, in the subject and again in the first line",
            "The problem it solves, from the user's side",
            "How to use it — one concrete step",
            "What is not covered yet, stated plainly",
        ],
        "rules": [
            "No 'we're excited to announce'. Lead with the thing itself.",
            "Name the limitation. It is the paragraph that earns trust.",
        ],
        "length": "120-200 words",
    },
}


async def list_email_templates(ctx: ToolContext, args: dict) -> ToolResult:
    audience = (args.get("audience") or "").strip().lower().replace("-", "_")
    if audience and audience in TEMPLATES:
        payload = {audience: TEMPLATES[audience]}
    elif audience:
        return ToolResult(
            f"Unknown audience `{audience}`. Available: {', '.join(TEMPLATES)}.",
            success=False,
        )
    else:
        payload = TEMPLATES
    return ToolResult(
        output=(
            "Structural templates — beats and rules, not copy. Write every "
            "sentence yourself.\n\n" + as_json(payload)
        ),
        meta={"audiences": list(payload)},
    )


# ---------------------------------------------------------------------------
# check_spam_words
# ---------------------------------------------------------------------------

# Grouped by why they hurt, because "this word is spammy" is not actionable but
# "this is a false-urgency phrase" is. Matched on word boundaries, so `free`
# fires and `freedom` does not.
SPAM_WORDS: dict[str, list[str]] = {
    "financial_bait": [
        "free", "100% free", "risk free", "no cost", "no obligation",
        "cash bonus", "double your", "earn money", "extra income",
        "make money", "money back", "no credit check", "pure profit",
        "save big", "serious cash", "why pay more", "cheap", "discount",
        "lowest price", "best price", "bargain", "incredible deal",
    ],
    "false_urgency": [
        "act now", "apply now", "buy now", "call now", "click here",
        "don't delete", "don't hesitate", "expires", "instant", "limited time",
        "now only", "once in a lifetime", "order now", "urgent", "while supplies last",
        "final notice", "last chance", "hurry",
    ],
    "overclaiming": [
        "amazing", "guarantee", "guaranteed", "miracle", "no risk",
        "revolutionary", "satisfaction guaranteed", "the best", "unbelievable",
        "you have been selected", "winner", "congratulations", "exclusive deal",
        "breakthrough", "life changing",
    ],
    "compliance_smell": [
        "this is not spam", "unsubscribe", "opt in", "opt-in", "no strings attached",
        "bulk email", "direct email", "marketing solution", "mass email",
    ],
    "pressure_phrases": [
        "dear friend", "dear sir or madam", "to whom it may concern",
        "as seen on", "increase sales", "increase traffic", "search engine ranking",
    ],
}

_ALL_CAPS_RE = re.compile(r"\b[A-Z]{3,}\b")
_EXCLAIM_RE = re.compile(r"!")


def _find_spam(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    hits: list[dict[str, Any]] = []
    for category, phrases in SPAM_WORDS.items():
        for phrase in phrases:
            # Word boundaries on both ends. Multi-word phrases keep their
            # internal spacing, so "act now" matches only as a phrase.
            pattern = r"\b" + re.escape(phrase) + r"\b"
            found = list(re.finditer(pattern, lowered))
            if found:
                hits.append(
                    {"phrase": phrase, "category": category, "occurrences": len(found)}
                )
    return hits


async def check_spam_words(ctx: ToolContext, args: dict) -> ToolResult:
    subject = str(args.get("subject") or "")
    body = str(args.get("body") or args.get("text") or "")
    if not subject and not body:
        return ToolResult("Error: give `subject` and/or `body`.", success=False)

    combined = f"{subject}\n{body}"
    hits = _find_spam(combined)
    caps = _ALL_CAPS_RE.findall(combined)
    exclamations = len(_EXCLAIM_RE.findall(combined))

    # A rough triage rather than a spam-filter simulation. It is deliberately
    # not calibrated against any real filter's score, because no such score is
    # published and inventing one would be the fabrication this project avoids.
    severity = "clean"
    if len(hits) >= 5 or exclamations >= 4:
        severity = "high"
    elif len(hits) >= 2 or exclamations >= 2 or len(caps) >= 2:
        severity = "medium"
    elif hits or caps or exclamations:
        severity = "low"

    payload = {
        "severity": severity,
        "flagged": hits,
        "all_caps_words": caps[:10],
        "exclamation_marks": exclamations,
        "note": (
            "A maintained trigger-word list, not a simulation of any real spam "
            "filter. Deliverability depends far more on domain reputation and "
            "authentication than on wording."
        ),
    }
    headline = (
        "No trigger words found."
        if not hits
        else f"{len(hits)} trigger phrase(s) flagged — severity {severity}."
    )
    return ToolResult(output=f"{headline}\n\n{as_json(payload)}", meta=payload)


# ---------------------------------------------------------------------------
# score_subject_line
# ---------------------------------------------------------------------------

#: Where mobile clients truncate. Not a style preference — a subject longer
#: than this is literally not read on a phone.
MOBILE_TRUNCATION = 41
DESKTOP_TRUNCATION = 60

_PERSONALISATION_RE = re.compile(
    r"\{\{?\s*\w+\s*\}?\}|\b(you|your|you're)\b", re.IGNORECASE
)
_NUMBER_RE = re.compile(r"\d")


async def score_subject_line(ctx: ToolContext, args: dict) -> ToolResult:
    """Measure a subject line. Every signal below is counted, not judged."""
    subject = str(args.get("subject") or "").strip()
    if not subject:
        return ToolResult("Error: `subject` was empty.", success=False)

    length = len(subject)
    words = subject.split()
    spam_hits = _find_spam(subject)
    caps = _ALL_CAPS_RE.findall(subject)
    exclamations = len(_EXCLAIM_RE.findall(subject))
    personalised = bool(_PERSONALISATION_RE.search(subject))
    has_number = bool(_NUMBER_RE.search(subject))

    # A transparent additive score out of 100. The weights are editorial
    # judgement and are stated as such — what makes this tool worth calling is
    # that the *inputs* are measurements, not that the total is authoritative.
    score = 100
    notes: list[str] = []

    if length > DESKTOP_TRUNCATION:
        score -= 20
        notes.append(
            f"{length} chars — cut off on desktop (>{DESKTOP_TRUNCATION}) and phones."
        )
    elif length > MOBILE_TRUNCATION:
        score -= 8
        notes.append(f"{length} chars — truncated on mobile (>{MOBILE_TRUNCATION}).")
    elif length < 20:
        score -= 6
        notes.append(f"{length} chars — short enough to read as low-effort.")

    if spam_hits:
        score -= 12 * len(spam_hits)
        notes.append(
            "Trigger phrases: " + ", ".join(h["phrase"] for h in spam_hits) + "."
        )
    if caps:
        score -= 10 * len(caps)
        notes.append("ALL-CAPS words: " + ", ".join(caps) + ".")
    if exclamations:
        score -= 8 * exclamations
        notes.append(f"{exclamations} exclamation mark(s).")
    if personalised:
        score += 6
        notes.append("Addresses the reader directly.")
    if has_number:
        score += 4
        notes.append("Contains a figure, which reads as specific.")

    score = max(0, min(100, score))
    payload = {
        "subject": subject,
        "score": score,
        "length": length,
        "words": len(words),
        "truncates_on_mobile": length > MOBILE_TRUNCATION,
        "truncates_on_desktop": length > DESKTOP_TRUNCATION,
        "spam_hits": [h["phrase"] for h in spam_hits],
        "all_caps": caps,
        "exclamations": exclamations,
        "personalised": personalised,
        "has_number": has_number,
        "notes": notes,
        "note": (
            "Signals are measured; the weighting that turns them into one score "
            "is editorial. Compare two subjects with it — do not quote the "
            "number to the user as if it predicted an open rate."
        ),
    }
    return ToolResult(output=f"Score {score}/100.\n\n{as_json(payload)}", meta=payload)


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def send_email(ctx: ToolContext, args: dict) -> ToolResult:
    """Actually send, via Resend — behind the human approval gate.

    Two guards stand in front of the HTTP call, in this order:

    1. **Configuration.** No key, no send, and the model is told exactly that.
    2. **A human.** :func:`app.agents.approvals.request` pauses the run and
       waits for an explicit click. There is no auto-approve path, no timeout
       that defaults to yes, and a rejection ends the call. Sending mail on
       someone's behalf is irreversible and outward-facing, so the gate is not
       optional and not configurable away.
    """
    settings = get_settings()
    to = [a.strip() for a in _as_list(args.get("to")) if a and a.strip()]
    subject = str(args.get("subject") or "").strip()
    body = str(args.get("body") or "").strip()

    if not to or not subject or not body:
        return ToolResult(
            "Error: `to`, `subject` and `body` are all required.", success=False
        )
    invalid = [a for a in to if not _EMAIL_RE.match(a)]
    if invalid:
        return ToolResult(
            f"Error: not valid email addresses: {', '.join(invalid)}.", success=False
        )

    if not settings.resend_enabled:
        return not_configured(
            "send_email",
            "RESEND_API_KEY",
            "The draft is finished and correct — it simply cannot be sent from "
            "here. Offer the user the copy to send themselves.",
        )

    from app.agents import approvals

    decision = await approvals.request(
        ctx,
        action="send_email",
        summary=f"Send an email to {', '.join(to)}",
        parameters={
            "to": to,
            "subject": subject,
            "body": body,
            "from": settings.resend_from_email,
        },
        risk="high",
        editable=["to", "subject", "body"],
    )
    if not decision.approved:
        return ToolResult(
            f"The user did not approve the send ({decision.decision}). Nothing "
            "was sent. Do not ask again in this turn — wait for new instructions.",
            success=False,
            meta={"approval": decision.as_dict()},
        )

    # The user may have edited the parameters in the approval card. What was
    # approved is what gets sent — never the original proposal.
    final = decision.parameters
    payload = {
        "from": final.get("from") or settings.resend_from_email,
        "to": _as_list(final.get("to")) or to,
        "subject": final.get("subject") or subject,
        "text": final.get("body") or body,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("Resend call failed", exc_info=True)
        return ToolResult(f"Error: the send failed ({type(exc).__name__}: {exc}).", False)

    if response.status_code >= 400:
        return ToolResult(
            f"Error: Resend rejected the send ({response.status_code}): "
            f"{response.text[:400]}",
            success=False,
        )

    data = response.json() if response.content else {}
    return ToolResult(
        output=f"Sent to {', '.join(payload['to'])}. Resend id: {data.get('id', '(none)')}.",
        meta={"resend_id": data.get("id"), "to": payload["to"], "approved": True},
    )


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, dict] = {
    "list_email_templates": {
        "name": "list_email_templates",
        "description": (
            "Fetch the structural template for an audience: the beats the email "
            "must hit, the rules for that register, and a target length. Call it "
            "before drafting so the structure is right before the wording is. "
            "Returns skeletons only — you write every sentence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "audience": {
                    "type": "string",
                    "enum": list(TEMPLATES),
                    "description": "Which template. Omit to see all of them.",
                }
            },
            "required": [],
        },
    },
    "score_subject_line": {
        "name": "score_subject_line",
        "description": (
            "Measure a subject line: length against mobile and desktop "
            "truncation points, trigger phrases, ALL-CAPS, exclamation marks, "
            "personalisation and specificity. Call it on BOTH of your A/B "
            "candidates — the point of writing two is choosing between them on "
            "evidence, and this is the evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "One subject line."}
            },
            "required": ["subject"],
        },
    },
    "check_spam_words": {
        "name": "check_spam_words",
        "description": (
            "Scan a draft against a maintained list of spam-trigger words and "
            "phrases, plus ALL-CAPS and exclamation-mark counts. Call it on the "
            "finished draft and rewrite whatever it flags. Do not report a flag "
            "you then leave in the copy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "The subject line."},
                "body": {"type": "string", "description": "The email body."},
            },
            "required": [],
        },
    },
    "send_email": {
        "name": "send_email",
        "description": (
            "ACTUALLY SEND the email, via Resend. This is irreversible. It pauses "
            "and asks the user to approve, edit or reject before anything leaves, "
            "and a rejection is final. Only call it when the user has explicitly "
            "asked you to send this specific draft — never to test it, never on "
            "your own initiative, and never twice in one turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Recipient addresses.",
                },
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text body."},
            },
            "required": ["to", "subject", "body"],
        },
    },
}

HANDLERS = {
    "list_email_templates": list_email_templates,
    "score_subject_line": score_subject_line,
    "check_spam_words": check_spam_words,
    "send_email": send_email,
}
