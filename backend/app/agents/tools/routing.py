"""Agents 8 and 9 — System Logic Router and Human Approval Gatekeeper.

Agent 8:
    validate_json_schema  REAL. A self-contained JSON Schema validator covering
                          the draft-07 keywords that matter for shape checking
                          (type, required, properties, items, enum, ranges,
                          patterns, nested objects). Deliberately not a new
                          dependency for ~150 lines of well-specified logic.
    match_patterns        REAL. Regex matching with named patterns, plus a
                          library of built-in detectors (email, URL, code
                          fence, JSON payload, stack trace) so the router can
                          classify content without an LLM call.
    evaluate_conditions   REAL. A rules engine over the input: ordered
                          condition/outcome pairs evaluated by actual boolean
                          logic. No `eval`, no expression language — a fixed
                          set of operators over a dotted path into the data.
    route_to_agent        REAL. Validates the target against the ten agent ids
                          and emits the handoff frame the UI turns into a
                          button. The handoff itself needs a human click.

Agent 9:
    request_approval      REAL. Genuinely suspends the graph run on an
                          asyncio.Future until a person answers. See
                          `app.agents.approvals`.
    list_pending_approvals REAL. Reads that same in-process registry.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agents.tools.base import ToolContext, ToolResult, artifact, as_json

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# validate_json_schema
# ---------------------------------------------------------------------------


def _validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Validate ``value`` against ``schema``. Returns a list of error strings.

    Covers the draft-07 keywords that matter for deciding whether a payload has
    the shape a downstream worker expects. Not a complete implementation —
    `$ref`, `allOf`/`anyOf`/`oneOf` and format assertions are out of scope, and
    the tool's own output says so rather than silently passing them.
    """
    errors: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: schema is not an object"]

    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(value, t) for t in types):
            errors.append(
                f"{path}: expected {' or '.join(types)}, got {_type_name(value)}"
            )
            # Every later keyword assumes the type matched; reporting them as
            # well would bury the one error that actually explains the failure.
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected the constant {schema['const']!r}")

    if isinstance(value, dict):
        for key in schema.get("required", []) or []:
            if key not in value:
                errors.append(f"{path}.{key}: required property is missing")
        properties = schema.get("properties") or {}
        for key, subschema in properties.items():
            if key in value:
                errors.extend(_validate(value[key], subschema, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            for key in sorted(extra):
                errors.append(f"{path}.{key}: additional property is not allowed")

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                errors.extend(_validate(item, items, f"{path}[{i}]"))
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} items, has {len(value)}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} items, has {len(value)}")
        if schema.get("uniqueItems") and len(value) != len({json.dumps(v, sort_keys=True, default=str) for v in value}):
            errors.append(f"{path}: items must be unique")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        pattern = schema.get("pattern")
        if pattern:
            try:
                if not re.search(pattern, value):
                    errors.append(f"{path}: does not match pattern {pattern!r}")
            except re.error as exc:
                errors.append(f"{path}: the schema's own pattern is invalid ({exc})")

    # `bool` is a subclass of int in Python; a boolean is not a number here.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: {value} must exceed {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: {value} must be under {schema['exclusiveMaximum']}")
        if "multipleOf" in schema and schema["multipleOf"]:
            if abs(value % schema["multipleOf"]) > 1e-9:
                errors.append(f"{path}: {value} is not a multiple of {schema['multipleOf']}")

    return errors


_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def _is_type(value: Any, name: str) -> bool:
    check = _TYPE_CHECKS.get(name)
    return check(value) if check else True


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


_UNSUPPORTED_KEYWORDS = {"$ref", "allOf", "anyOf", "oneOf", "not", "if", "format"}


async def validate_json_schema(ctx: ToolContext, args: dict) -> ToolResult:
    data = args.get("data")
    schema = args.get("schema")

    data = _maybe_parse(data)
    schema = _maybe_parse(schema)

    if isinstance(data, _ParseError):
        return ToolResult(f"Error: `data` is not valid JSON — {data.detail}", False)
    if isinstance(schema, _ParseError):
        return ToolResult(f"Error: `schema` is not valid JSON — {schema.detail}", False)
    if not isinstance(schema, dict):
        return ToolResult("Error: `schema` must be a JSON Schema object.", False)

    errors = _validate(data, schema)
    unsupported = sorted(_UNSUPPORTED_KEYWORDS & set(_walk_keys(schema)))

    payload = {
        "valid": not errors,
        "errors": errors,
        "error_count": len(errors),
        "data_type": _type_name(data),
        "keys": sorted(data)[:40] if isinstance(data, dict) else None,
    }
    if unsupported:
        payload["unsupported_keywords"] = unsupported
        payload["unsupported_note"] = (
            "These keywords were ignored — this validator covers the shape "
            "keywords, not composition or $ref. Say so rather than reporting a "
            "pass that was never checked."
        )

    headline = "Valid." if not errors else f"Invalid — {len(errors)} error(s)."
    return ToolResult(output=f"{headline}\n\n{as_json(payload)}", meta=payload)


def _walk_keys(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


class _ParseError:
    def __init__(self, detail: str) -> None:
        self.detail = detail


def _maybe_parse(value: Any) -> Any:
    """Accept either a real object or a JSON string.

    The models routinely hand a stringified object to a parameter typed as an
    object, and rejecting that would fail the call over a formatting detail
    rather than over anything the router cares about.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        return _ParseError(str(exc))


# ---------------------------------------------------------------------------
# match_patterns
# ---------------------------------------------------------------------------

#: Detectors for the content shapes a router actually has to tell apart. Having
#: these built in is what lets Agent 8 classify an input deterministically
#: instead of asking the model what it thinks the text is.
BUILTIN_PATTERNS: dict[str, str] = {
    "email_address": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "url": r"https?://[^\s<>\"')]+",
    "code_fence": r"```[\w+-]*\n[\s\S]*?```",
    "json_object": r"\{[\s\S]*\}",
    "stack_trace": r"(Traceback \(most recent call last\)|\bat [\w.$]+\([\w.]+:\d+\)|^\s+File \".+\", line \d+)",
    "sql_query": r"\b(SELECT|INSERT INTO|UPDATE|DELETE FROM|CREATE TABLE)\b",
    "html_markup": r"<(div|span|section|button|input|form|header|nav|main|article)\b",
    "markdown_heading": r"^#{1,6}\s+\S",
    "iso_date": r"\b\d{4}-\d{2}-\d{2}\b",
    "currency": r"[$£€]\s?\d[\d,]*(?:\.\d{2})?",
    "percentage": r"\b\d+(?:\.\d+)?\s?%",
    "phone_number": r"\+?\d[\d\s().-]{7,}\d",
    "file_path": r"(?:[A-Za-z]:)?[\\/](?:[\w.-]+[\\/])*[\w.-]+\.\w{1,5}",
    "python_code": r"\b(def |class |import |from \w+ import|async def )",
    "javascript_code": r"\b(const |let |function |=>|export default|require\()",
    "aspect_ratio": r"\b\d{1,2}\s*[:x]\s*\d{1,2}\b",
    "question": r"\?\s*$",
}


async def match_patterns(ctx: ToolContext, args: dict) -> ToolResult:
    text = str(args.get("text") or "")
    if not text:
        return ToolResult("Error: `text` was empty.", success=False)

    requested = args.get("patterns")
    custom: dict[str, str] = {}
    if isinstance(requested, dict):
        custom = {str(k): str(v) for k, v in requested.items()}
    elif isinstance(requested, list):
        # A list of built-in names narrows the scan; an unknown name is
        # reported rather than silently dropped.
        for name in requested:
            key = str(name)
            if key in BUILTIN_PATTERNS:
                custom[key] = BUILTIN_PATTERNS[key]
            else:
                custom[key] = ""

    patterns = custom or BUILTIN_PATTERNS
    results: list[dict[str, Any]] = []
    for name, expression in patterns.items():
        if not expression:
            results.append({"pattern": name, "error": "unknown built-in pattern"})
            continue
        try:
            found = list(
                re.finditer(expression, text, re.MULTILINE | re.IGNORECASE)
            )
        except re.error as exc:
            results.append({"pattern": name, "error": f"invalid regex: {exc}"})
            continue
        if found:
            results.append(
                {
                    "pattern": name,
                    "matches": len(found),
                    # A sample, not every hit: a match list over a long document
                    # would be longer than the document.
                    "samples": [m.group(0)[:120] for m in found[:5]],
                    "first_offset": found[0].start(),
                }
            )

    payload = {
        "matched": [r for r in results if r.get("matches")],
        "errors": [r for r in results if r.get("error")],
        "scanned_patterns": len(patterns),
        "text_length": len(text),
    }
    names = ", ".join(r["pattern"] for r in payload["matched"]) or "(none)"
    return ToolResult(output=f"Matched: {names}\n\n{as_json(payload)}", meta=payload)


# ---------------------------------------------------------------------------
# evaluate_conditions
# ---------------------------------------------------------------------------

#: The operator set. Fixed and total — there is no expression parser and no
#: `eval` anywhere in this tool, because the input is model-authored and an
#: expression language over model output is a code-execution hole.
_OPERATORS = {
    "equals": lambda a, b: a == b,
    "not_equals": lambda a, b: a != b,
    "greater_than": lambda a, b: _num(a) > _num(b),
    "greater_or_equal": lambda a, b: _num(a) >= _num(b),
    "less_than": lambda a, b: _num(a) < _num(b),
    "less_or_equal": lambda a, b: _num(a) <= _num(b),
    "contains": lambda a, b: _contains(a, b),
    "not_contains": lambda a, b: not _contains(a, b),
    "starts_with": lambda a, b: str(a).startswith(str(b)),
    "ends_with": lambda a, b: str(a).endswith(str(b)),
    "in": lambda a, b: a in b if isinstance(b, (list, tuple, set, str)) else False,
    "not_in": lambda a, b: a not in b if isinstance(b, (list, tuple, set, str)) else True,
    "matches": lambda a, b: bool(re.search(str(b), str(a), re.IGNORECASE)),
    "exists": lambda a, b: a is not _MISSING,
    "not_exists": lambda a, b: a is _MISSING,
    "is_empty": lambda a, b: a is _MISSING or a in (None, "", [], {}),
    "is_not_empty": lambda a, b: a is not _MISSING and a not in (None, "", [], {}),
    "length_greater_than": lambda a, b: _length(a) > _num(b),
    "length_less_than": lambda a, b: _length(a) < _num(b),
    "type_is": lambda a, b: _type_name(a) == str(b),
}


class _Missing:
    """Distinct from ``None``: a key that is absent is not a key set to null."""

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<missing>"


_MISSING = _Missing()


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _length(value: Any) -> float:
    try:
        return float(len(value))
    except TypeError:
        return float("nan")


def _contains(haystack: Any, needle: Any) -> bool:
    if isinstance(haystack, str):
        return str(needle).lower() in haystack.lower()
    if isinstance(haystack, (list, tuple, set, dict)):
        return needle in haystack
    return False


def _dotted(data: Any, path: str) -> Any:
    """Read `a.b[0].c` out of ``data``, returning `_MISSING` if any hop fails."""
    current = data
    for part in re.split(r"\.(?![^\[]*\])", str(path)):
        if not part:
            continue
        name, _, indexes = part.partition("[")
        if name:
            if isinstance(current, dict) and name in current:
                current = current[name]
            else:
                return _MISSING
        for index in re.findall(r"\[(\d+)\]", "[" + indexes if indexes else ""):
            if isinstance(current, (list, tuple)) and int(index) < len(current):
                current = current[int(index)]
            else:
                return _MISSING
    return current


async def evaluate_conditions(ctx: ToolContext, args: dict) -> ToolResult:
    data = _maybe_parse(args.get("data"))
    if isinstance(data, _ParseError):
        return ToolResult(f"Error: `data` is not valid JSON — {data.detail}", False)

    rules = _maybe_parse(args.get("rules"))
    if isinstance(rules, _ParseError):
        return ToolResult(f"Error: `rules` is not valid JSON — {rules.detail}", False)
    if not isinstance(rules, list) or not rules:
        return ToolResult(
            "Error: `rules` must be a non-empty array of "
            "{field, operator, value, outcome} objects.",
            success=False,
        )

    trace: list[dict[str, Any]] = []
    outcome: Any = None
    matched_index: int | None = None

    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            trace.append({"rule": index, "error": "rule is not an object"})
            continue
        field = str(rule.get("field") or "")
        operator = str(rule.get("operator") or "equals")
        expected = rule.get("value")

        fn = _OPERATORS.get(operator)
        if fn is None:
            trace.append(
                {
                    "rule": index,
                    "error": f"unknown operator `{operator}`",
                    "available": sorted(_OPERATORS),
                }
            )
            continue

        actual = _dotted(data, field) if field else data
        try:
            result = bool(fn(actual, expected))
        except Exception as exc:  # noqa: BLE001 - a bad comparison is a false, not a crash
            trace.append({"rule": index, "error": f"{type(exc).__name__}: {exc}"})
            continue

        trace.append(
            {
                "rule": index,
                "field": field,
                "operator": operator,
                "expected": expected,
                "actual": None if actual is _MISSING else actual,
                "field_present": actual is not _MISSING,
                "result": result,
            }
        )
        # First match wins, so the rules are an ordered decision list. Order is
        # the author's, not ours — evaluating all of them and picking would
        # make the outcome depend on something the caller cannot see.
        if result and matched_index is None:
            matched_index = index
            outcome = rule.get("outcome")

    if matched_index is None:
        default = next(
            (r.get("outcome") for r in rules if isinstance(r, dict) and r.get("default")),
            None,
        )
        outcome = default

    payload = {
        "outcome": outcome,
        "matched_rule": matched_index,
        "matched": matched_index is not None,
        "trace": trace,
        "note": "First matching rule wins; rules are evaluated in order.",
    }
    headline = (
        f"Rule {matched_index} matched → {outcome!r}"
        if matched_index is not None
        else f"No rule matched → {outcome!r}"
    )
    return ToolResult(output=f"{headline}\n\n{as_json(payload)}", meta=payload)


# ---------------------------------------------------------------------------
# route_to_agent
# ---------------------------------------------------------------------------


async def route_to_agent(ctx: ToolContext, args: dict) -> ToolResult:
    """Emit a routing directive the user can accept with one click.

    The handoff is deliberately *not* executed here. A directive becomes a
    button; a person presses it. Autonomous chaining is out of scope for this
    pass, and this is the seam where it would otherwise creep in.
    """
    # Imported here rather than at module scope: `registry` imports the tool
    # registry, which imports this module.
    from app.agents.registry import AGENTS, get_agent

    target = str(args.get("next_agent") or "").strip()
    reason = str(args.get("reason") or "").strip()

    if target not in AGENTS:
        return ToolResult(
            f"Error: `{target}` is not one of the ten agents. Valid ids: "
            f"{', '.join(AGENTS)}.",
            success=False,
        )
    if target == ctx.agent_id:
        return ToolResult(
            "Error: that routes to yourself. Either handle it here or pick a "
            "different specialist.",
            success=False,
        )
    if not reason:
        return ToolResult(
            "Error: `reason` is required — the user is deciding whether to "
            "accept this handoff and needs to know why.",
            success=False,
        )

    agent = get_agent(target)
    context = str(args.get("context") or "").strip()
    directive = {"next_agent": target, "reason": reason}

    if ctx.emitter and not ctx.emitter.closed:
        ctx.emitter.emit(
            ev_handoff(
                from_agent=ctx.agent_id,
                next_agent=target,
                next_agent_name=agent.name,
                reason=reason,
                context=context,
            )
        )

    return ToolResult(
        output=(
            f"Routing directive emitted: {target} ({agent.name} — {agent.role}). "
            "The user has been shown a handoff button; they decide whether to "
            "take it. Do not describe that agent's work as if it has happened.\n\n"
            + as_json(directive)
        ),
        meta={
            **directive,
            **artifact(
                "handoff",
                {
                    "next_agent": target,
                    "next_agent_name": agent.name,
                    "next_agent_role": agent.role,
                    "next_agent_icon": agent.icon,
                    "next_agent_accent": agent.accent,
                    "reason": reason,
                    "context": context,
                },
            ),
        },
    )


def ev_handoff(**kwargs) -> dict:
    from app import events as ev

    return ev.agent_handoff(**kwargs)


# ---------------------------------------------------------------------------
# Agent 9's tools
# ---------------------------------------------------------------------------


async def request_approval(ctx: ToolContext, args: dict) -> ToolResult:
    """Halt the run and put a decision in front of a person."""
    from app.agents import approvals

    action = str(args.get("action") or "").strip()
    summary = str(args.get("summary") or "").strip()
    if not action or not summary:
        return ToolResult(
            "Error: `action` and `summary` are both required — a person cannot "
            "approve what they cannot see.",
            success=False,
        )

    parameters = _maybe_parse(args.get("parameters")) or {}
    if isinstance(parameters, _ParseError):
        return ToolResult(f"Error: `parameters` is not valid JSON — {parameters.detail}", False)
    if not isinstance(parameters, dict):
        parameters = {"value": parameters}

    risk = str(args.get("risk") or "medium").lower()
    if risk not in {"low", "medium", "high"}:
        risk = "medium"

    editable = args.get("editable") or []
    if isinstance(editable, str):
        editable = [editable]
    editable = [str(e) for e in editable if str(e) in parameters]

    decision = await approvals.request(
        ctx,
        action=action,
        summary=summary,
        parameters=parameters,
        risk=risk,
        editable=editable,
    )

    changed = decision.decision == "edited"
    body = (
        f"The user chose: **{decision.decision}**."
        + (" They edited the parameters — act on these, not the ones you proposed."
           if changed else "")
        + (" Do not re-request this action in this turn."
           if not decision.approved else "")
    )
    return ToolResult(
        output=f"{body}\n\n{as_json(decision.as_dict())}",
        success=True,
        meta={"approval": decision.as_dict()},
    )


async def list_pending_approvals(ctx: ToolContext, args: dict) -> ToolResult:
    from app.agents import approvals

    pending = approvals.pending_for_session(ctx.session_id)
    if not pending:
        return ToolResult("Nothing is pending approval in this session.", meta={"pending": []})
    return ToolResult(
        output=f"{len(pending)} pending.\n\n{as_json(pending)}",
        meta={"pending": pending},
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, dict] = {
    "validate_json_schema": {
        "name": "validate_json_schema",
        "description": (
            "Check a payload against a JSON Schema and get the exact list of "
            "shape errors. Use it whenever the upstream data is supposed to "
            "have a defined structure — deciding a route from a real validation "
            "result beats deciding it from a glance at the JSON. Covers type, "
            "required, properties, items, enum, ranges and patterns; it reports "
            "any keyword it could not check rather than passing it silently."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {"description": "The payload. An object, or a JSON string."},
                "schema": {"description": "A JSON Schema object, or a JSON string."},
            },
            "required": ["data", "schema"],
        },
    },
    "match_patterns": {
        "name": "match_patterns",
        "description": (
            "Scan text for content signals: email addresses, URLs, code "
            "fences, JSON, stack traces, SQL, HTML, dates, currency and more. "
            "Call it with no `patterns` to run every built-in detector — that "
            "is usually the fastest way to classify an unfamiliar input. Pass "
            "an object of {name: regex} for your own."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to scan."},
                "patterns": {
                    "description": (
                        "Either an array of built-in pattern names to narrow "
                        f"the scan ({', '.join(list(BUILTIN_PATTERNS)[:6])}, …), "
                        "or an object of {name: regex}. Omit to run them all."
                    )
                },
            },
            "required": ["text"],
        },
    },
    "evaluate_conditions": {
        "name": "evaluate_conditions",
        "description": (
            "Run an ordered rule list against a payload and get back the "
            "outcome of the first rule that matches, with a full trace of every "
            "comparison. This is real boolean logic, not reasoning — use it "
            "whenever the branch depends on values you can actually read. "
            "`field` is a dotted path (`user.plan`, `items[0].type`); mark one "
            "rule `\"default\": true` to supply a fallback outcome."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {"description": "The payload to test."},
                "rules": {
                    "type": "array",
                    "description": (
                        "Ordered rules. Each: {field, operator, value, outcome}. "
                        f"Operators: {', '.join(sorted(_OPERATORS))}."
                    ),
                    "items": {"type": "object"},
                },
            },
            "required": ["data", "rules"],
        },
    },
    "route_to_agent": {
        "name": "route_to_agent",
        "description": (
            "Emit the routing directive. This is what actually surfaces the "
            "handoff button to the user — a route described only in prose is "
            "not a route. Exactly one target. Include `context` so the "
            "receiving agent starts with what it needs. The user must click to "
            "accept; you never execute the handoff yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "next_agent": {
                    "type": "string",
                    "description": "The target agent id.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this specialist, in one sentence. Shown to the user.",
                },
                "context": {
                    "type": "string",
                    "description": "What to carry over — the receiving chat is pre-seeded with it.",
                },
            },
            "required": ["next_agent", "reason"],
        },
    },
    "request_approval": {
        "name": "request_approval",
        "description": (
            "HALT the run and ask a person to approve, edit or reject a pending "
            "action. This genuinely suspends execution until they answer — it "
            "is not a rhetorical device, and there is no path where silence "
            "means yes. Put the real parameters in `parameters`; list the ones "
            "the user may change in `editable`. If they edit, act on what comes "
            "back rather than what you proposed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Machine name of the action, e.g. `send_email`.",
                },
                "summary": {
                    "type": "string",
                    "description": "One line a person can decide from.",
                },
                "parameters": {
                    "type": "object",
                    "description": "The exact parameters being approved.",
                },
                "risk": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "high = irreversible, outward-facing, or spends money.",
                },
                "editable": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Which parameter names the user may rewrite first.",
                },
            },
            "required": ["action", "summary", "parameters"],
        },
    },
    "list_pending_approvals": {
        "name": "list_pending_approvals",
        "description": "List approvals still waiting on a decision in this session.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
}

HANDLERS = {
    "validate_json_schema": validate_json_schema,
    "match_patterns": match_patterns,
    "evaluate_conditions": evaluate_conditions,
    "route_to_agent": route_to_agent,
    "request_approval": request_approval,
    "list_pending_approvals": list_pending_approvals,
}
