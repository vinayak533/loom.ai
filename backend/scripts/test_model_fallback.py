"""Automatic model fallback, against mocked adapters. No live provider calls.

    python scripts/test_model_fallback.py

What this proves, and why each case is here rather than left to inspection:

1.  A rate-limited model (HTTP 429) is retried on another model and the request
    still completes. This is the headline behaviour.
2.  It happens for a *manually selected* model too. A user's pick is honoured
    right up to the point it stops being able to answer; then finishing the
    request wins.
3.  It happens for an *auto-routed* model as well, so neither mode is a
    special case.
4.  A malformed request (HTTP 400) does NOT fall back. This is the case that
    justifies the whole retryability split: falling back there would burn a
    second call and hide an application bug behind a model switch.
5.  A content-policy refusal does NOT fall back, for the same reason.
6.  The user is told. A `model_changed` event carrying `fallback_from` is
    emitted before the retry, which is what drives the toast.
7.  The failed attempt costs nothing. Only the model that actually answered is
    charged, and it is charged at *its own* rate, not the failed model's.
8.  A failure that arrives mid-stream, after the user is already reading text,
    is NOT retried — restarting would duplicate or truncate the answer.
9.  Attempts are capped, so a systemic outage cannot walk the whole registry.
10. And all of the above holds through the real `agent_node`, not just the
    router in isolation — including that a fallback does NOT overwrite the
    user's manual selection. That one is a regression guard: it did, once.

Exit code is 0 only if every check passes.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Keys for every provider, so availability never depends on the developer's
# .env. Set before `app.config` is imported, since Settings is lru_cached.
for _k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENCODE_API_KEY"):
    os.environ.setdefault(_k, "test-key-not-real")

import app.llm_router as R  # noqa: E402
from app.llm_router import (  # noqa: E402
    FallbackNotice,
    ModelCallError,
    NormalizedMessage,
    StreamEvent,
)

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {label}" + (f" {DIM}({detail}){RESET}" if detail else ""))


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def http_error(status: int, message: str = "") -> Exception:
    """An exception shaped like the provider SDK's, i.e. carrying a status.

    `classify_failure` reads `.status_code` first and only falls back to text
    matching, so this is the realistic shape — an `openai.APIStatusError` and
    an `httpx.HTTPStatusError` both expose it.
    """

    class _Resp:
        status_code = status

    class ProviderError(Exception):
        def __init__(self) -> None:
            super().__init__(message or f"HTTP {status}")
            self.status_code = status
            self.response = _Resp()

    return ProviderError()


class FakeAdapter:
    """Stands in for a provider adapter. Fails or succeeds on command.

    `behaviour` maps a wire model name to either an exception to raise or the
    text to answer with. Every call is recorded so a test can assert on which
    models were actually reached, and in what order.
    """

    def __init__(self, provider: str, behaviour: dict, calls: list) -> None:
        self.provider = provider
        self.behaviour = behaviour
        self.calls = calls

    def _act(self, model_name: str):
        return self.behaviour.get(model_name, f"answer from {model_name}")

    def _call_error(self, exc: BaseException) -> ModelCallError:
        retryable, status, kind = R.classify_failure(exc)
        return ModelCallError(
            f"{self.provider} call failed: {exc}",
            provider=self.provider,
            status_code=status,
            retryable=retryable,
            kind=kind,
        )

    async def stream(self, model_name, messages, tools, system, max_tokens, vision=False):
        self.calls.append(model_name)
        action = self._act(model_name)
        if isinstance(action, BaseException):
            raise self._call_error(action)
        if isinstance(action, dict) and action.get("mid_stream"):
            # Emit real content first, *then* fail. This is case 8.
            yield StreamEvent("text_start")
            yield StreamEvent("text_delta", action["text"])
            raise self._call_error(action["error"])
        yield StreamEvent("text_start")
        yield StreamEvent("text_delta", str(action))
        yield StreamEvent("text_end")
        yield StreamEvent(
            "done",
            message=NormalizedMessage(
                content=[{"type": "text", "text": str(action)}],
                usage={"input_tokens": 1000, "output_tokens": 1000},
                stop_reason="end_turn",
                model_name=model_name,
            ),
        )

    async def complete(self, model_name, messages, tools, system, max_tokens, vision=False):
        self.calls.append(model_name)
        action = self._act(model_name)
        if isinstance(action, BaseException):
            raise self._call_error(action)
        return NormalizedMessage(
            content=[{"type": "text", "text": str(action)}],
            usage={"input_tokens": 1000, "output_tokens": 1000},
            stop_reason="end_turn",
            model_name=model_name,
        )


#: The real `_adapters`, kept so repeated `install()` calls replace the stub
#: rather than stacking on top of one another.
_REAL_ADAPTERS = R._adapters


def install(behaviour: dict) -> list:
    """Point every provider at a FakeAdapter. Returns the shared call log."""
    calls: list = []
    fakes = {
        p: FakeAdapter(p, behaviour, calls)
        for p in ("openrouter", "groq", "opencode")
    }
    _REAL_ADAPTERS.cache_clear()
    R._adapters = lambda: fakes  # type: ignore[assignment]
    return calls


def wire(model_id: str) -> str:
    """The wire name a registry id resolves to, i.e. what FakeAdapter sees."""
    return R._resolve(R.MODEL_REGISTRY[model_id], "model_name")


async def drain(model_id: str, **kw):
    """Run a streamed call with fallback; return (text, final_message, notices)."""
    notices: list[FallbackNotice] = []
    text = ""
    final = None

    def on_fallback(n: FallbackNotice) -> None:
        notices.append(n)

    async for se in R.stream_with_fallback(
        model_id, messages=[{"role": "user", "content": "hi"}],
        tools=[], system=None, on_fallback=on_fallback, **kw
    ):
        if se.kind == "text_delta":
            text += se.content or ""
        if se.kind == "done":
            final = se.message
    return text, final, notices


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


async def case_rate_limit_manual() -> None:
    print("\n1. A manually selected model hits a rate limit (429)")
    primary = "qwen3_7_plus"
    expected_next = R.fallback_candidates(primary)[0]
    calls = install({wire(primary): http_error(429, "rate limit exceeded")})

    text, final, notices = await drain(primary)

    check("the request still completed", bool(text), text)
    check("the failing model was tried first", calls[0] == wire(primary), str(calls))
    check(
        "it fell back to the head of that model's chain",
        len(calls) == 2 and calls[1] == wire(expected_next),
        f"{calls} (expected second = {wire(expected_next)})",
    )
    check("exactly one fallback was reported", len(notices) == 1, str(len(notices)))
    if notices:
        n = notices[0]
        check("classified as a rate limit", n.kind == "rate_limit", n.kind)
        check("status code preserved", n.status_code == 429, str(n.status_code))
        check(
            "the notice names both models",
            n.failed_model_id == primary and n.next_model_id == expected_next,
            f"{n.failed_model_id} -> {n.next_model_id}",
        )
        check(
            "and reads as a sentence a user could be shown",
            "hit a rate limit" in n.message() and n.next_name in n.message(),
            n.message(),
        )
    check(
        "the answer came from the fallback model",
        final is not None and final.model_name == wire(expected_next),
        getattr(final, "model_name", "None"),
    )


async def case_auto_routed() -> None:
    print("\n2. An AUTO-routed model fails — same behaviour, no special case")
    routed = R.model_for_hint("fast_simple")
    expected_next = R.fallback_candidates(routed)[0]
    calls = install({wire(routed): http_error(503, "service unavailable")})

    text, final, notices = await drain(routed, section="chat")

    check("the request still completed", bool(text), text)
    check("the auto-routed model was tried first", calls[0] == wire(routed), str(calls))
    check("then the fallback", calls[1:] == [wire(expected_next)], str(calls))
    check(
        "classified as a provider error, not a rate limit",
        notices and notices[0].kind == "provider_error",
        notices[0].kind if notices else "no notice",
    )


async def case_no_fallback_on_bad_request() -> None:
    print("\n3. A malformed request (400) is NOT retried elsewhere")
    primary = "qwen3_7_plus"
    calls = install({wire(primary): http_error(400, "invalid 'messages[0].role'")})

    raised = None
    try:
        await drain(primary)
    except ModelCallError as exc:
        raised = exc

    check("the error was surfaced, not swallowed", raised is not None)
    check("only one model was called", len(calls) == 1, str(calls))
    check(
        "and it was marked non-retryable",
        raised is not None and raised.retryable is False,
        str(getattr(raised, "retryable", "?")),
    )


async def case_no_fallback_on_refusal() -> None:
    print("\n4. A content-policy refusal is NOT retried elsewhere")
    primary = "mimo_v2_5"
    err = Exception("content_policy_violation: the request was refused")
    calls = install({wire(primary): err})

    raised = None
    try:
        await drain(primary)
    except ModelCallError as exc:
        raised = exc

    check("the refusal was surfaced", raised is not None)
    check("only one model was called", len(calls) == 1, str(calls))


async def case_mid_stream_not_retried() -> None:
    print("\n5. A failure AFTER text has been shown is NOT retried")
    primary = "mimo_v2_5"
    calls = install({
        wire(primary): {
            "mid_stream": True,
            "text": "Here is the first half of the answ",
            "error": http_error(503, "connection dropped"),
        }
    })

    raised = None
    try:
        await drain(primary)
    except ModelCallError as exc:
        raised = exc

    check("the error was surfaced", raised is not None)
    check(
        "no second model was called despite a retryable status",
        len(calls) == 1,
        str(calls),
    )
    check(
        "which is the point: the user was already reading the first answer",
        raised is not None and raised.retryable is True,
        "503 is retryable in isolation; the mid-stream rule overrides it",
    )


async def case_attempt_cap() -> None:
    print("\n6. Attempts are capped — a systemic outage does not walk the registry")
    primary = "qwen3_7_plus"
    # Everything is down.
    behaviour = {wire(m): http_error(429) for m in R.MODEL_REGISTRY}
    calls = install(behaviour)

    raised = None
    try:
        await drain(primary)
    except ModelCallError as exc:
        raised = exc

    check("it eventually gave up", raised is not None)
    check(
        f"after at most {R.MAX_FALLBACK_ATTEMPTS + 1} models",
        len(calls) <= R.MAX_FALLBACK_ATTEMPTS + 1,
        f"{len(calls)} calls: {calls}",
    )
    check("and did not repeat a model", len(calls) == len(set(calls)), str(calls))


async def case_non_streaming() -> None:
    print("\n7. The non-streaming path falls back too, and reports who answered")
    primary = "gpt-oss-120b"
    expected_next = R.fallback_candidates(primary)[0]
    install({wire(primary): http_error(429, "TPM limit")})

    notices: list[FallbackNotice] = []
    message, answered_by = await R.complete_with_fallback(
        primary, messages=[{"role": "user", "content": "hi"}], tools=[],
        on_fallback=notices.append, section="title",
    )

    check("a message came back", message is not None)
    check(
        "and the caller is told which model produced it",
        answered_by == expected_next,
        f"{answered_by} (expected {expected_next})",
    )
    check("a fallback was reported", len(notices) == 1, str(len(notices)))


async def case_credit_accuracy() -> None:
    print("\n8. Credits: the failed attempt is free, the answer is priced correctly")
    primary = "gpt-oss-120b"
    expected_next = R.fallback_candidates(primary)[0]
    install({wire(primary): http_error(429)})

    message, answered_by = await R.complete_with_fallback(
        primary, messages=[{"role": "user", "content": "hi"}], tools=[],
    )
    usage = message.usage

    # This mirrors exactly what the graph does: it prices `usage` against the
    # model id the router says answered. There is no usage from the failed
    # attempt to price, because it never produced any.
    charged = R.estimate_cost(answered_by, usage)
    would_have_been = R.estimate_cost(primary, usage)

    check(
        "the failed model produced no usage to bill",
        message.model_name == wire(expected_next),
        message.model_name,
    )
    check(
        "cost is computed against the model that answered",
        abs(charged - R.estimate_cost(expected_next, usage)) < 1e-12,
        f"{charged:.8f}",
    )
    check(
        "which differs from pricing it at the failed model's rate",
        abs(charged - would_have_been) > 1e-12,
        f"{expected_next}={charged:.8f} vs {primary}={would_have_been:.8f}",
    )
    check(
        "and only one model's worth of tokens is billed at all",
        usage["output_tokens"] == 1000,
        str(usage),
    )


async def case_emitted_event() -> None:
    print("\n9. The user is told: a model_changed event carries `fallback_from`")
    from app import events as ev

    primary = "qwen3_7_plus"
    expected_next = R.fallback_candidates(primary)[0]
    install({wire(primary): http_error(429, "rate limit")})

    emitted: list[dict] = []

    def on_fallback(n: FallbackNotice) -> None:
        # The shape both graph nodes build. Kept in the test so a change to the
        # event signature breaks here rather than silently at runtime.
        emitted.append(
            ev.model_changed(
                model_id=n.next_model_id,
                name=n.next_name,
                supports_tools=R.model_meta(n.next_model_id)["supports_tools"],
                available=True,
                note=f"{n.failed_name} {n.reason}",
                reason=n.reason,
                fallback_from=n.failed_model_id,
            )
        )

    async for _ in R.stream_with_fallback(
        primary, messages=[{"role": "user", "content": "hi"}], tools=[],
        system=None, on_fallback=on_fallback,
    ):
        pass

    check("one event was emitted", len(emitted) == 1, str(len(emitted)))
    if emitted:
        e = emitted[0]
        check("it is a model_changed", e["type"] == "model_changed", e["type"])
        check(
            "`fallback_from` names the model that failed",
            e["fallback_from"] == primary,
            e["fallback_from"],
        )
        check("`model_id` names the one that answered", e["model_id"] == expected_next, e["model_id"])
        check(
            "and the note reads like the toast copy",
            "hit a rate limit" in e["note"],
            e["note"],
        )


async def case_classification_table() -> None:
    print("\n10. Retryability, status code by status code")
    table = [
        (429, True, "rate_limit"),
        (413, True, "rate_limit"),
        (500, True, "provider_error"),
        (502, True, "provider_error"),
        (503, True, "provider_error"),
        (504, True, "provider_error"),
        (404, True, "unavailable"),
        (400, False, "rejected"),
        (401, False, "rejected"),
        (403, False, "rejected"),
        (422, False, "rejected"),
    ]
    for status, want_retry, want_kind in table:
        got_retry, got_status, got_kind = R.classify_failure(http_error(status))
        check(
            f"HTTP {status} -> {'retry' if want_retry else 'surface'} ({want_kind})",
            got_retry == want_retry and got_kind == want_kind and got_status == status,
            f"got retryable={got_retry} kind={got_kind}",
        )

    # No status code at all — a transport failure. Text matching takes over.
    for text, want_retry in [
        ("Connection error.", True),
        ("Request timed out", True),
        ("quota exceeded for this project", True),
        ("content_policy: refused", False),
        ("KeyError: 'messages'", False),
    ]:
        got_retry, _, _ = R.classify_failure(Exception(text))
        check(
            f"{text!r} -> {'retry' if want_retry else 'surface'}",
            got_retry == want_retry,
            f"got retryable={got_retry}",
        )


async def case_through_the_graph() -> None:
    """The agent node, not just the router.

    Everything above is the router in isolation. This drives the real
    `agent_node` with only the provider boundary stubbed, because three things
    that matter are decided in the node rather than the router: which model the
    session is left on, which model gets charged, and whether the failure
    reaches the user as an error.

    The first of those is here because it regressed once. `model_id` was being
    rebound to the stand-in and returned in the graph state, which quietly
    moved a user off their own manual pick for good after a single 429.
    """
    print("\n11. Through the real agent node: selection, billing, error surface")
    from langgraph.checkpoint.memory import MemorySaver  # noqa: F401  (import cost)

    from app.agent import graph as g
    from app.emitter import Emitter
    import app.emitter as em

    charged: list[dict] = []

    async def fake_charge(user_id, **kw):
        charged.append(kw)
        return kw.get("cost_usd", 0.0)

    real_charge, real_fire = g.charge_llm, g.repository.fire
    g.charge_llm = fake_charge
    g.repository.fire = lambda *a, **k: None

    emitted: list[dict] = []
    real_emit = Emitter.emit

    def spy(self, event):
        emitted.append(event)
        return real_emit(self, event)

    Emitter.emit = spy

    try:
        for label, mode, picked, status in (
            ("manual pick, 429", "manual", "qwen3_7_plus", 429),
            ("auto-routed, 429", "auto", None, 429),
            ("manual pick, 400", "manual", "qwen3_7_plus", 400),
        ):
            charged.clear()
            emitted.clear()
            primary = picked or R.model_for_hint(R.DEFAULT_HINT)
            expected = R.fallback_candidates(primary)[0]
            calls = install({wire(primary): http_error(status, "boom")})

            emitter = Emitter()
            em.registry.register(emitter, f"fb-{status}-{mode}")
            out = await g.agent_node(
                {
                    "session_id": f"fb-{status}-{mode}",
                    "user_id": "",
                    "model_id": picked or "auto",
                    "routing_mode": mode,
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
                    ],
                    "pending": [], "tool_results": [], "iterations": 0,
                    "stop_reason": "", "usage": {}, "credits_spent": 0.0,
                },
                {"configurable": {"emitter_id": emitter.id}},
            )
            toasts = [
                e for e in emitted
                if e.get("type") == "model_changed" and e.get("fallback_from")
            ]
            errors = [e for e in emitted if e.get("type") == "error"]

            if status == 429:
                check(f"[{label}] the turn completed",
                      out.get("stop_reason") == "end_turn", out.get("stop_reason") or "")
                check(f"[{label}] it fell back", calls[1:] == [wire(expected)], str(calls))
                check(f"[{label}] the user was told once", len(toasts) == 1, str(len(toasts)))
                check(f"[{label}] no error was surfaced", not errors,
                      str([e.get("message") for e in errors]))
                check(f"[{label}] exactly one charge", len(charged) == 1, str(len(charged)))
                check(f"[{label}] billed to the model that answered",
                      charged and charged[0]["model_id"] == expected,
                      charged[0]["model_id"] if charged else "-")
                if mode == "manual":
                    check(f"[{label}] the session KEEPS the user's pick",
                          out.get("model_id") == primary,
                          f"{out.get('model_id')} (picked {primary})")
            else:
                check(f"[{label}] no fallback was attempted", len(calls) == 1, str(calls))
                check(f"[{label}] no toast", not toasts, str(len(toasts)))
                check(f"[{label}] the error reached the user", len(errors) == 1,
                      str(len(errors)))
                check(f"[{label}] nothing was charged", not charged, str(charged))
                check(f"[{label}] the turn is marked failed",
                      out.get("stop_reason") == "error", out.get("stop_reason") or "")
    finally:
        Emitter.emit = real_emit
        g.charge_llm, g.repository.fire = real_charge, real_fire


async def main() -> int:
    print("Automatic model fallback — mocked adapters, no live calls")
    print(f"{DIM}registry: {', '.join(R.MODEL_REGISTRY)}{RESET}")

    for case in (
        case_rate_limit_manual,
        case_auto_routed,
        case_no_fallback_on_bad_request,
        case_no_fallback_on_refusal,
        case_mid_stream_not_retried,
        case_attempt_cap,
        case_non_streaming,
        case_credit_accuracy,
        case_emitted_event,
        case_classification_table,
        case_through_the_graph,
    ):
        await case()

    print()
    if _failures:
        print(f"{RED}{_failures} check(s) failed.{RESET}")
        return 1
    print(f"{GREEN}All checks passed.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
