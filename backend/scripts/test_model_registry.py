"""Registry invariants. No network, no keys, no spend.

    python scripts/test_model_registry.py

Replaces a per-model wiring test that was pinned to a model since removed. The
assertions that outlived it are the ones about the registry *as a whole* rather
than about any one entry: removed vendors stay removed and cannot be
reintroduced through configuration, every entry is internally consistent, and a
session stored against a removed model recovers instead of dying at dispatch.

Live capability checks live in `scripts/test_live_models.py`; fallback logic in
`scripts/test_model_fallback.py`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "OPENCODE_API_KEY"):
    os.environ.setdefault(_k, "test-key-not-real")

import app.llm_router as R  # noqa: E402
from app.config import get_settings  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {label}" + (f" {DIM}({detail}){RESET}" if detail else ""))


settings = get_settings()
ids = list(R.MODEL_REGISTRY)


def _dispatch_refusal(model_id: str) -> str | None:
    """The message `_adapter_for` refuses with, or None if it allowed it."""
    try:
        R._adapter_for(model_id)
        return None
    except R.ModelUnavailableError as exc:
        return str(exc)


#: Vendors that have been removed from this app, and the artefacts each one
#: would leave behind. Kept as data so the removed names appear in exactly one
#: place instead of being sprinkled through a dozen assertions — and so adding
#: the next removal is a row, not a new block of copy-paste.
#:
#: The names below are here *to assert their absence*. That is the opposite of
#: a reference that could route somewhere: if one of these ever matches, the
#: test fails.
REMOVED_VENDORS = [
    # (label, id/adapter substrings, Settings field prefix or None)
    ("Anthropic", ("claude", "anthropic"), None),
    ("OpenAI-as-vendor", ("gpt-4", "o1-"), None),
    ("xAI / Grok", ("grok", "xai"), "xai_"),
]

print("1. Removed vendors stay removed")
blob = " ".join(ids).lower()
adapters = list(R._adapters())
for label, needles, field_prefix in REMOVED_VENDORS:
    hit = [n for n in needles if n in blob]
    check(f"no {label} model id", not hit, f"{hit} in {blob}")
    hit = [n for n in needles if any(n in a.lower() for a in adapters)]
    check(f"no {label} adapter", not hit, f"{hit} in {adapters}")
    if field_prefix:
        leftover = [f for f in type(settings).model_fields if f.startswith(field_prefix)]
        check(f"no {label} Settings fields", not leftover, str(leftover))
        check(f"public_summary does not advertise {label}",
              not any(k.startswith(field_prefix.rstrip("_")) for k in settings.public_summary()),
              str(sorted(settings.public_summary())))
check("no llama-70b id (it named a model it had stopped serving)", "llama-70b" not in ids)
check("no XAIAdapter class is left behind", not hasattr(R, "XAIAdapter"))

print("\n1b. Grok is refused, not merely absent")
# Removing the registry entry is not the whole job: every entry's wire id comes
# from settings, so an .env line could repoint an innocently-named entry at a
# banned model. These cover the guard that closes that.
check("a denylist exists and covers grok",
      any("grok" in b.lower() for b in R.BANNED_MODEL_SUBSTRINGS),
      str(R.BANNED_MODEL_SUBSTRINGS))
check("is_banned matches case-insensitively",
      R.is_banned("grok-4.5") and R.is_banned("GROK-5") and R.is_banned("x/Grok-9"),
      "")
check("and does not over-match a legitimate slug",
      not any(R.is_banned(R._resolve(m, "model_name")) for m in R.MODEL_REGISTRY.values()),
      "")
check("this deployment has no banned entry configured", not R.banned_entries(),
      str(R.banned_entries()))
# The real test: repoint a live slot at grok and confirm every route closes.
_real = R.MODEL_REGISTRY["deepseek_v4_flash"]["model_name"]
R.MODEL_REGISTRY["deepseek_v4_flash"]["model_name"] = "grok-4.5"
try:
    check("a repointed slot is reported by banned_entries()",
          ("deepseek_v4_flash", "grok-4.5") in R.banned_entries(), str(R.banned_entries()))
    check("... is not available", not R.is_available("deepseek_v4_flash"))
    check("... is hidden from the model selector",
          "deepseek_v4_flash" not in [m["id"] for m in R.available_models()],
          str([m["id"] for m in R.available_models()]))
    check("... is refused at dispatch, naming the real reason",
          _dispatch_refusal("deepseek_v4_flash") is not None
          and "API key" not in (_dispatch_refusal("deepseek_v4_flash") or ""),
          _dispatch_refusal("deepseek_v4_flash") or "")
    reachable = {t for m in R.MODEL_REGISTRY for t in R.fallback_candidates(m)}
    check("... and cannot be reached as a fallback target",
          "deepseek_v4_flash" not in reachable, str(sorted(reachable)))
finally:
    R.MODEL_REGISTRY["deepseek_v4_flash"]["model_name"] = _real

print("\n2. Every entry is internally consistent")
adapters = R._adapters()
for mid, meta in R.MODEL_REGISTRY.items():
    problems = []
    for field in ("provider", "group", "model_name", "display_name",
                  "supports_tools", "supports_vision", "max_tokens",
                  "required_key", "pricing", "fallback_chain"):
        if field not in meta:
            problems.append(f"missing `{field}`")
    if meta.get("provider") not in adapters:
        problems.append(f"provider `{meta.get('provider')}` has no adapter")
    if not hasattr(settings, meta.get("required_key", "")):
        problems.append(f"required_key `{meta.get('required_key')}` is not a Settings field")
    for p in meta.get("pricing", {}):
        if p not in ("input", "output", "cache_read", "cache_write"):
            problems.append(f"unknown pricing key `{p}`")
    for target in meta.get("fallback_chain", []):
        if target not in R.MODEL_REGISTRY:
            problems.append(f"fallback_chain names unknown model `{target}`")
        if target == mid:
            problems.append("fallback_chain contains itself")
    check(f"`{mid}` is well-formed", not problems, "; ".join(problems))

print("\n3. The Groq slot is gpt-oss-120b, at gpt-oss-120b's real price")
meta = R.MODEL_REGISTRY["gpt-oss-120b"]
check("provider is groq", meta["provider"] == "groq", meta["provider"])
check("wire id is `openai/gpt-oss-120b`", R._resolve(meta, "model_name") == "openai/gpt-oss-120b",
      R._resolve(meta, "model_name"))
check("labelled GPT-OSS 120B in the selector", meta["display_name"] == "GPT-OSS 120B",
      meta["display_name"])
check("tool calling declared (verified live in test_live_models.py)", meta["supports_tools"] is True)
check("vision declared False — the model is text-in/text-out", meta["supports_vision"] is False)
# Groq publishes $0.15 in / $0.60 out / $0.075 cached-in per 1M.
cost = R.estimate_cost("gpt-oss-120b", {"input_tokens": 1_000_000, "output_tokens": 0})
check("input priced at $0.15/1M", abs(cost - 0.15) < 1e-9, f"${cost:.4f}")
cost = R.estimate_cost("gpt-oss-120b", {"input_tokens": 0, "output_tokens": 1_000_000})
check("output priced at $0.60/1M, not the old llama rate of $0.75",
      abs(cost - 0.60) < 1e-9, f"${cost:.4f}")
cost = R.estimate_cost("gpt-oss-120b", {"cache_read_input_tokens": 1_000_000})
check("cached input priced at $0.075/1M", abs(cost - 0.075) < 1e-9, f"${cost:.4f}")
check("max_tokens stays under Groq's 8000 TPM cap",
      R._resolve(meta, "max_tokens") < 8000, str(R._resolve(meta, "max_tokens")))

print("\n4. MiMo V2.5 and MiMo V2.5 Pro are two models, not one relabelled")
base, pro = R.MODEL_REGISTRY["mimo_v2_5"], R.MODEL_REGISTRY["mimo_v2_5_pro"]
check("distinct wire ids", R._resolve(base, "model_name") != R._resolve(pro, "model_name"),
      f"{R._resolve(base, 'model_name')} vs {R._resolve(pro, 'model_name')}")
check("pro's wire id is `mimo-v2.5-pro`", R._resolve(pro, "model_name") == "mimo-v2.5-pro",
      R._resolve(pro, "model_name"))
check("both served by opencode", base["provider"] == pro["provider"] == "opencode")
check("both declare tool calling", base["supports_tools"] and pro["supports_tools"])
check("and they differ on vision — the reason they are separate entries",
      base["supports_vision"] is True and pro["supports_vision"] is False,
      f"base={base['supports_vision']} pro={pro['supports_vision']}")
check("pro is manual-only, so Auto's four slots are untouched",
      "routing_hint" not in pro)
check("Auto's long-horizon slot is still the model that can see",
      R.model_for_hint("complex_longhorizon") == "mimo_v2_5",
      R.model_for_hint("complex_longhorizon"))

print("\n5. The default model")
default = settings.default_model_id
check("is a real registry key", default in R.MODEL_REGISTRY, default)
check("is qwen3_7_plus", default == "qwen3_7_plus", default)
check("supports tools", R.MODEL_REGISTRY[default]["supports_tools"] is True)
check("and supports vision — a default has to handle an attachment",
      R.MODEL_REGISTRY[default]["supports_vision"] is True)
for name, table in (("auto_routes", settings.auto_routes),):
    bad = [f"{k}={v}" for k, v in table.items() if v not in R.MODEL_REGISTRY]
    check(f"every {name} entry names a live model", not bad, "; ".join(bad))

print("\n5b. Per-section defaults")
# `auto_route_chat == default_model_id` used to be checked here. It is no
# longer the contract: the section table mirrors each section's *own* default,
# not the roster-wide one, which is the whole point of the per-section table.
# What has to hold is that the two agree with each other.
resolved = R.section_default_models()
for section, mid in resolved.items():
    check(f"`{section}` resolves to a live model", mid in R.MODEL_REGISTRY, mid)
    check(f"`{section}`'s model is available", R.is_available(mid), mid)
    check(f"`{section}`'s model can call tools",
          R.MODEL_REGISTRY[mid]["supports_tools"] is True, mid)
check("Code opens on MiMo V2.5", resolved["code"] == "mimo_v2_5", resolved["code"])
check("Chat opens on DeepSeek V4 Flash",
      resolved["chat"] == "deepseek_v4_flash", resolved["chat"])
check("Code's model reads images — a screenshot pasted into Code must land",
      R.MODEL_REGISTRY[resolved["code"]]["supports_vision"] is True)
for section in ("chat", "code", "learning"):
    check(f"Auto's `{section}` route matches that section's default",
          settings.auto_routes[section] == settings.section_defaults[section],
          f"{settings.auto_routes[section]} vs {settings.section_defaults[section]}")
check("a section naming no default falls back to the roster default",
      R.section_default_model("agents") == R.effective_default_model(),
      R.section_default_model("agents"))
check("an unknown section is not an error, it is the roster default",
      R.section_default_model("nonesuch") == R.effective_default_model(),
      R.section_default_model("nonesuch"))

print("\n5c. A 402 is a provider fault, not a refusal")
# OpenRouter answers 402 when the account cannot afford the request's
# `max_tokens` ceiling. Another model on another provider genuinely fixes it,
# so it must fall back rather than ending the turn.
class _Payment(Exception):
    status_code = 402
retryable, status, kind = R.classify_failure(
    _Payment("This request requires more credits, or fewer max_tokens")
)
check("402 is retryable", retryable is True, str(retryable))
check("402 carries its own kind", kind == "out_of_credit", kind)
check("402 keeps its status code", status == 402, str(status))
check("the toast reads as English",
      "credit" in R.FallbackNotice("nemotron-3", "mimo_v2_5", kind, 402, "", 1).reason)
check("the transcript never gets the raw provider payload",
      "{" not in R.ModelCallError(
          "openrouter call failed: {'error': {...}, 'user_id': 'user_abc'}",
          provider="openrouter", status_code=402, retryable=True,
          kind="out_of_credit",
      ).user_message())

print("\n6. Sessions stored against a removed model recover")
for stale, label in (("llama-70b", "Llama 3.3 70B"), ("claude-sonnet", "Claude Sonnet")):
    mid, notice = R.resolve_stored_model(stale)
    check(f"`{stale}` is reassigned to the default", mid == default, mid)
    check(f"and the notice names it properly, not as a raw slug",
          bool(notice) and label in (notice or "") and stale not in (notice or ""),
          notice or "")
# A removed *vendor* is deliberately NOT in RETIRED_DISPLAY_NAMES: naming it in
# a toast would put it back in front of the user. The session still recovers,
# the notice just goes generic — and must not echo the stored id either.
mid, notice = R.resolve_stored_model("grok-4-5")
check("a removed vendor's session still recovers", mid == default, mid)
check("but the notice names neither the vendor nor the raw id",
      bool(notice) and "grok" not in (notice or "").lower(), notice or "")
mid, notice = R.resolve_stored_model(default)
check("a current model is left alone", mid == default and notice is None, f"{mid} / {notice}")
mid, notice = R.resolve_stored_model("auto")
check("the `auto` sentinel passes through untouched", mid == "auto" and notice is None)

print("\n7. Fallback chains resolve")
for mid in ids:
    cands = R.fallback_candidates(mid)
    check(f"`{mid}` has somewhere to go", bool(cands), str(cands))
    check(f"`{mid}` never falls back to itself", mid not in cands, str(cands))
vision_first = R.fallback_candidates("qwen3_7_plus")[0]
check("a sighted model falls back to a sighted model first",
      R.MODEL_REGISTRY[vision_first]["supports_vision"] is True, vision_first)

print("\n8. Helpers used elsewhere still point at live models")
from app.agent.llm import TITLE_MODELS  # noqa: E402
from app.learn.tutor import LEARN_MODELS  # noqa: E402

for name, tup in (("TITLE_MODELS", TITLE_MODELS), ("LEARN_MODELS", LEARN_MODELS)):
    bad = [m for m in tup if m not in R.MODEL_REGISTRY]
    check(f"{name} names only live models", not bad, "; ".join(bad))

print()
if _failures:
    print(f"{RED}{_failures} check(s) failed.{RESET}")
    raise SystemExit(1)
print(f"{GREEN}All checks passed.{RESET}")
