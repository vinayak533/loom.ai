"""Auto-router checks: does classify_task pick the intended model?

Pure state inspection — no network, no API keys needed. Run with:

    python scripts/test_task_routing.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.agent.task_classifier import classify_task, explain  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm_router import model_for_hint  # noqa: E402


def user(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def user_with_image(text: str) -> dict:
    return {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "iVBORw0KGgo=",
                },
            },
            {"type": "text", "text": text},
        ],
    }


def assistant(text: str = "", tool: str | None = None, args: dict | None = None) -> dict:
    content: list[dict] = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append(
            {"type": "tool_use", "id": "toolu_1", "name": tool, "input": args or {}}
        )
    return {"role": "assistant", "content": content}


PLAN = """Here's my plan:

1. Read the existing router module
2. Add the provider adapter
3. Register the four models
4. Write the classifier
5. Wire it into the graph
6. Update the frontend selector
7. Run the tests
"""

CASES: list[tuple[str, dict, str, str]] = [
    (
        "simple question",
        {"messages": [user("What's the capital of France?")], "iterations": 1},
        "fast_simple",
        "deepseek_v4_flash",
    ),
    (
        "edit_file call in progress (pending)",
        {
            "messages": [user("Rename the helper in utils.py")],
            "pending": [
                {
                    "type": "tool_use",
                    "id": "toolu_9",
                    "name": "edit_file",
                    "input": {"path": "utils.py"},
                }
            ],
            "iterations": 2,
        },
        "code_editing",
        "minimax_m2_7",
    ),
    (
        "iterative edit thread (recent edit in history)",
        {
            "messages": [
                user("Fix the failing test"),
                assistant("I'll patch it.", tool="edit_file", args={"path": "a.py"}),
                user("Now also update the docstring"),
            ],
            "iterations": 3,
        },
        "code_editing",
        "minimax_m2_7",
    ),
    (
        "image attached",
        {
            "messages": [user_with_image("Build this layout in React")],
            "iterations": 1,
        },
        "visual_structural",
        "qwen3_7_plus",
    ),
    (
        "image outranks an in-flight edit",
        {
            "messages": [user_with_image("Match the button styling to this")],
            "pending": [
                {"type": "tool_use", "id": "t", "name": "edit_file", "input": {}}
            ],
            "iterations": 2,
        },
        "visual_structural",
        "qwen3_7_plus",
    ),
    (
        "long multi-step plan",
        {"messages": [user("Port the app to Postgres"), assistant(PLAN)], "iterations": 1},
        "complex_longhorizon",
        "mimo_v2_5",
    ),
    (
        "large context",
        {
            "messages": [user("x" * (24_000 * 4 + 400))],
            "iterations": 1,
        },
        "complex_longhorizon",
        "mimo_v2_5",
    ),
    (
        "task has run long",
        {"messages": [user("Keep going")], "iterations": 9},
        "complex_longhorizon",
        "mimo_v2_5",
    ),
    (
        "read_file alone is not editing",
        {
            "messages": [
                user("What does config.py do?"),
                assistant("Let me look.", tool="read_file", args={"path": "config.py"}),
            ],
            "iterations": 2,
        },
        "fast_simple",
        "deepseek_v4_flash",
    ),
    (
        "empty state falls back to the cheap default",
        {},
        "fast_simple",
        "deepseek_v4_flash",
    ),
]


async def check_usage_rows() -> int:
    """Assert what `record_usage` writes, without needing a live Supabase.

    Stubs the client and captures the row, so the `routing_mode` /
    `routing_hint` columns are verified even when the schema has not been
    applied to the project yet.
    """
    from app.db import repository

    captured: list[dict] = []

    class _Table:
        def insert(self, row):
            captured.append(row)
            return self

        def execute(self):
            return None

    class _Client:
        def table(self, _name):
            return _Table()

    real_enabled, real_client = repository.enabled, repository.get_client
    repository.enabled = lambda: True
    repository.get_client = _Client
    try:
        await repository.record_usage(
            "sess-1", "qwen3.7-plus", 120, 45, 0.0012,
            model_id="qwen3_7_plus",
            routing_mode="auto",
            routing_hint="visual_structural",
        )
        await repository.record_usage(
            "sess-1", "grok-4.5", 80, 20, 0.0009, model_id="grok-4-5"
        )
    finally:
        repository.enabled, repository.get_client = real_enabled, real_client

    failures = 0
    expectations = [
        ("auto row records the mode", captured[0].get("routing_mode") == "auto"),
        (
            "auto row records the hint",
            captured[0].get("routing_hint") == "visual_structural",
        ),
        ("auto row records the model", captured[0].get("model_id") == "qwen3_7_plus"),
        ("manual row defaults to manual", captured[1].get("routing_mode") == "manual"),
        ("manual row has no hint", captured[1].get("routing_hint") is None),
    ]
    for label, ok in expectations:
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    print(f"      row: {captured[0]}")
    return failures


def main() -> int:
    s = get_settings()
    print(
        f"thresholds: context>={s.auto_complex_context_tokens} tok, "
        f"iterations>={s.auto_complex_iteration_count}, "
        f"plan_steps>={s.auto_complex_plan_steps}, "
        f"lookback={s.auto_lookback_messages}\n"
    )
    failures = 0
    for name, state, want_hint, want_model in CASES:
        hint = classify_task(state)
        model = model_for_hint(hint)
        ok = hint == want_hint and model == want_model
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        print(f"      -> {hint} / {model}")
        if not ok:
            print(f"      expected {want_hint} / {want_model}")
            print(f"      signals: {explain(state)}")

    print("\ntoken_usage row shape:")
    failures += asyncio.run(check_usage_rows())

    total = len(CASES) + 5
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
