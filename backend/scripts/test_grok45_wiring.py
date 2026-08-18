"""Prove the Grok 4.5 path is wired correctly without spending a real call.

Everything here is verifiable with no XAI_API_KEY: the registry entry, the wire
id that reaches xAI, the request body the adapter builds (including the tool
schema translation and image handling), the streaming decode, and the credit
cost derived from xAI's published rates.

What it deliberately does NOT prove: that xAI accepts the request. That needs a
real key — see the note printed at the end.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The adapter refuses to build a client with no key, and this file never makes
# a network call, so a placeholder is enough to exercise the request path.
os.environ.setdefault("XAI_API_KEY", "test-key-not-real")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

import app.llm_router as R  # noqa: E402

OK, BAD = "  [PASS]", "  [FAIL]"
results: list[bool] = []


def check(label: str, cond, detail: str = "") -> None:
    results.append(bool(cond))
    print(f"{OK if cond else BAD} {label}" + (f" - {detail}" if detail else ""))


def obj(**kw):
    return types.SimpleNamespace(**kw)


print("1. Registry entry matches xAI's published model page")
meta = R.MODEL_REGISTRY["grok-4-5"]
check("provider is the existing xai integration", meta["provider"] == "xai", meta["provider"])
check("NOT routed via openrouter or opencode", meta["provider"] not in ("openrouter", "opencode"))
check("wire id is exactly `grok-4.5`", R._resolve(meta, "model_name") == "grok-4.5",
      R._resolve(meta, "model_name"))
check("tool calling declared", meta["supports_tools"] is True)
check("vision declared (text, image -> text)", meta["supports_vision"] is True)
check("dispatches to XAIAdapter", isinstance(R._adapters()["xai"], R.XAIAdapter))
check("base url is xAI direct", R._adapters()["xai"].base_url == "https://api.x.ai/v1",
      R._adapters()["xai"].base_url)
check("is the configured default model", get_settings().default_model_id == "grok-4-5",
      get_settings().default_model_id)

print("\n2. Pricing reflects xAI's real sub-200k rates")
p = meta["pricing"]
check("input $2.00/M", abs(p["input"] * 1_000_000 - 2.00) < 1e-9, f"{p['input'] * 1e6:.2f}")
check("output $6.00/M", abs(p["output"] * 1_000_000 - 6.00) < 1e-9, f"{p['output'] * 1e6:.2f}")
check("cached input $0.30/M", abs(p["cache_read"] * 1_000_000 - 0.30) < 1e-9)
cost = R.estimate_cost("grok-4-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
check("1M in + 1M out estimates $8.00", abs(cost - 8.00) < 1e-6, f"${cost:.4f}")
# charge_llm() converts a USD estimate into credits by this same division.
credits = round(cost / max(get_settings().credit_usd, 1e-9), 4)
check("that converts to a real credit debit", credits > 0,
      f"{credits:,.2f} credits at CREDIT_USD={get_settings().credit_usd}")

print("\n3. The request body the adapter actually builds")
captured: dict = {}


def text_chunks():
    def delta(**d):
        return obj(choices=[obj(delta=obj(**d), finish_reason=None)], usage=None)

    yield delta(content="Hel", tool_calls=None, reasoning_content=None)
    yield delta(content="lo", tool_calls=None, reasoning_content=None)
    yield obj(
        choices=[obj(delta=obj(content=None, tool_calls=None, reasoning_content=None),
                     finish_reason="stop")],
        usage=obj(prompt_tokens=11, completion_tokens=2),
    )


def tool_chunks():
    call = obj(index=0, id="call_1",
               function=obj(name="run_bash", arguments='{"cmd":"ls"}'))
    yield obj(choices=[obj(delta=obj(content=None, tool_calls=[call],
                                     reasoning_content=None), finish_reason=None)],
              usage=None)
    yield obj(
        choices=[obj(delta=obj(content=None, tool_calls=None, reasoning_content=None),
                     finish_reason="tool_calls")],
        usage=obj(prompt_tokens=9, completion_tokens=7),
    )


class FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kw):
        captured.clear()
        captured.update(kw)

        async def gen():
            for chunk in self._chunks():
                yield chunk

        return gen()


def install(chunks):
    R._adapters()["xai"]._cached_client = obj(chat=obj(completions=FakeCompletions(chunks)))


TOOLS = [{
    "name": "run_bash",
    "description": "Run a shell command",
    "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}},
                     "required": ["cmd"]},
}]
MSGS = [{"role": "user", "content": [
    {"type": "text", "text": "hi"},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
]}]


async def run_turn():
    out = []
    async for e in R.call_model("grok-4-5", MSGS, tools=TOOLS, system="be terse", stream=True):
        out.append(e)
    return out


install(text_chunks)
events = asyncio.run(run_turn())

check("model sent to xAI is `grok-4.5`", captured.get("model") == "grok-4.5", captured.get("model"))
check("max_tokens comes from XAI_MAX_TOKENS",
      captured.get("max_tokens") == get_settings().xai_max_tokens, str(captured.get("max_tokens")))
tools_sent = captured.get("tools") or []
check("tool schema translated to the OpenAI function shape",
      bool(tools_sent) and tools_sent[0].get("type") == "function"
      and tools_sent[0]["function"]["name"] == "run_bash",
      json.dumps(tools_sent)[:80])
check("function parameters carried over",
      tools_sent[0]["function"]["parameters"]["required"] == ["cmd"])
sent = captured.get("messages") or []
check("system prompt sent as a system message",
      bool(sent) and sent[0]["role"] == "system" and sent[0]["content"] == "be terse")
parts = sent[1]["content"]
check("image survives as an image_url part (vision model)",
      isinstance(parts, list) and any(pp.get("type") == "image_url" for pp in parts),
      str([pp.get("type") for pp in parts]))
check("image is sent as a data URI",
      any(pp.get("image_url", {}).get("url", "").startswith("data:image/png;base64,")
          for pp in parts if pp.get("type") == "image_url"))

print("\n4. Streaming decode")
kinds = [e.kind for e in events]
text = "".join(e.content or "" for e in events if e.kind == "text_delta")
check("emits text_start / text_delta / done",
      "text_start" in kinds and "text_delta" in kinds and kinds[-1] == "done", str(kinds))
check("text reassembles in order", text == "Hello", repr(text))
final = events[-1].message
check("usage normalised",
      final.usage["input_tokens"] == 11 and final.usage["output_tokens"] == 2, str(final.usage))
check("model_name reported is the wire id", final.model_name == "grok-4.5", final.model_name)
check("stop_reason normalised", final.stop_reason == "end_turn", final.stop_reason)

print("\n5. A tool-calling turn decodes into internal tool_use blocks")
install(tool_chunks)
msg = asyncio.run(run_turn())[-1].message
uses = [b for b in msg.content if b.get("type") == "tool_use"]
check("a tool_use block is produced", len(uses) == 1, str(uses))
check("tool name preserved", bool(uses) and uses[0]["name"] == "run_bash")
check("arguments parsed into input", bool(uses) and uses[0]["input"] == {"cmd": "ls"},
      str(uses[0]["input"]) if uses else "")
check("stop_reason is tool_use", msg.stop_reason == "tool_use", msg.stop_reason)

print("\n6. Removed models are gone and stale sessions recover")
ids = list(R.MODEL_REGISTRY)
check("no claude/anthropic id in the registry",
      not any("claude" in m or "anthropic" in m for m in ids), str(ids))
check("no openai/gpt id in the registry",
      not any("openai" in m or "gpt" in m for m in ids))
check("no anthropic adapter", "anthropic" not in R._adapters(), str(list(R._adapters())))
mid, notice = R.resolve_stored_model("claude-sonnet")
check("a session on a removed model is reassigned", mid == "grok-4-5", mid)
check("and is told why, in one line", bool(notice) and "Grok 4.5" in notice, notice or "")
check("`auto` still passes through untouched", R.resolve_stored_model("auto") == ("auto", None))
check("OpenCode auto pool is intact",
      list(R.HINT_MODEL.values()) == ["deepseek_v4_flash", "minimax_m2_7",
                                      "qwen3_7_plus", "mimo_v2_5"], str(R.HINT_MODEL))

print(f"\n{sum(results)}/{len(results)} checks passed.")
print("\nNOT COVERED (needs a real key): whether api.x.ai accepts this body.")
print("Set XAI_API_KEY in backend/.env, then run scripts/test_grok45_live.py.")
sys.exit(0 if all(results) else 1)
