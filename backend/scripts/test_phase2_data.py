"""Search, feedback and per-account preferences, against the live database.

These three are all storage features, so they are tested where they live rather
than through the UI: the questions worth asking are "does search find a phrase
buried in a transcript", "is one person's thumb invisible to another", and
"does a preference survive a new client with no browser state" — and none of
those are answered by clicking a button once.

The preference test is the interesting one. The claim being checked is
"persists across a login on a different browser or device", and the honest way
to check it without two browsers is to notice what that claim actually reduces
to: the value is keyed by account and by nothing else, so a *fresh client with
no local state* reading it back is the same test. That is what `read_back_cold`
does.

    .venv/Scripts/python -m scripts.test_phase2_data
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.db import repository  # noqa: E402
from app.db.supabase_client import enabled, get_client  # noqa: E402

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


async def test_search() -> None:
    print("\n[1] History search finds titles and message bodies")
    sid = str(uuid.uuid4())
    needle = f"zqx{uuid.uuid4().hex[:8]}"

    await repository.create_session(sid, None, title="A search fixture", section="chat")
    await repository.add_message(sid, "user", f"Please explain {needle} to me.")
    # A tool-result row: role 'user' with null content. It must never be
    # returned as a body match, and must not break the query.
    await repository.add_message(sid, "user", None, {"call_id": "c1", "tool": "x"})
    await asyncio.sleep(1.0)

    try:
        hits = await repository.search_sessions(needle, user_id=None, section="chat")
        check("a phrase inside a message is found", any(h["id"] == sid for h in hits),
              f"{len(hits)} hit(s)")
        hit = next((h for h in hits if h["id"] == sid), None)
        check("the hit is labelled as a body match", bool(hit) and hit["match"] == "message")
        check("the hit carries the line that matched",
              bool(hit) and needle in (hit.get("snippet") or ""),
              repr((hit or {}).get("snippet"))[:70])

        by_title = await repository.search_sessions(
            "search fixture", user_id=None, section="chat"
        )
        found = next((h for h in by_title if h["id"] == sid), None)
        check("a title is found", bool(found))
        check("a title match is labelled as one", bool(found) and found["match"] == "title")

        check("an empty query returns nothing",
              await repository.search_sessions("", user_id=None) == [])
        check("a wildcard-only query returns nothing, not everything",
              await repository.search_sessions("%", user_id=None) == [])
        check("an underscore-only query returns nothing",
              await repository.search_sessions("_", user_id=None) == [])

        # Ownership. The message table has no user_id, so the ids a body search
        # produces have to be checked back against `sessions` — this is the
        # check that they are.
        stranger = await repository.search_sessions(
            needle, user_id="00000000-0000-0000-0000-0000000000ff", section="chat"
        )
        check("another account cannot see this session via search",
              not any(h["id"] == sid for h in stranger),
              f"{len(stranger)} hit(s) for the stranger")
    finally:
        await repository.delete_session(sid)


async def test_feedback() -> None:
    print("\n[2] Feedback is per message, per person, and revisable")
    sid = str(uuid.uuid4())
    await repository.create_session(sid, None, title="Feedback fixture", section="chat")
    alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"

    try:
        await repository.set_feedback(sid, 0, "up", user_id=alice, model_id="m", section="chat")
        await repository.set_feedback(sid, 1, "down", user_id=alice, reason="too long")
        rows = await repository.list_feedback(sid, alice)
        by_turn = {r["turn_index"]: r for r in rows}
        check("both verdicts stored against the right turns",
              by_turn.get(0, {}).get("rating") == "up"
              and by_turn.get(1, {}).get("rating") == "down",
              str(sorted((r["turn_index"], r["rating"]) for r in rows)))
        check("the optional reason is kept",
              by_turn.get(1, {}).get("reason") == "too long")

        # Changing your mind replaces the verdict; it does not add a second.
        await repository.set_feedback(sid, 0, "down", user_id=alice)
        rows = await repository.list_feedback(sid, alice)
        turn0 = [r for r in rows if r["turn_index"] == 0]
        check("changing a verdict replaces it rather than duplicating",
              len(turn0) == 1 and turn0[0]["rating"] == "down",
              f"{len(turn0)} row(s) for turn 0")

        # Withdrawing.
        await repository.set_feedback(sid, 0, None, user_id=alice)
        rows = await repository.list_feedback(sid, alice)
        check("a verdict can be withdrawn",
              not any(r["turn_index"] == 0 for r in rows))

        # Isolation between people looking at the same transcript.
        await repository.set_feedback(sid, 1, "up", user_id=bob)
        check("two people can disagree about the same reply",
              (await repository.list_feedback(sid, bob))[0]["rating"] == "up"
              and next(
                  r["rating"] for r in await repository.list_feedback(sid, alice)
                  if r["turn_index"] == 1
              ) == "down")
        check("one person's verdicts are not returned to another",
              len(await repository.list_feedback(sid, bob)) == 1,
              f"{len(await repository.list_feedback(sid, bob))} row(s) for bob")
    finally:
        client = get_client()
        client.table("message_feedback").delete().eq("session_id", sid).execute()
        await repository.delete_session(sid)


async def test_preferences() -> None:
    print("\n[3] The default model follows the account, not the browser")
    user = f"pref-{uuid.uuid4()}"
    other = f"pref-{uuid.uuid4()}"

    try:
        check("an account with no preference reports none",
              (await repository.get_preferences(user)).get("default_model_id") is None)

        saved = await repository.set_preferences(user, default_model_id="mimo_v2_5")
        check("a preference is saved", saved.get("default_model_id") == "mimo_v2_5",
              str(saved.get("default_model_id")))

        # The cross-device claim, reduced to what it actually rests on: the
        # value is keyed by account and by nothing else, so a client holding no
        # local state at all reads back the same answer.
        read_back_cold = await repository.get_preferences(user)
        check("a client with no local state reads the same value",
              read_back_cold.get("default_model_id") == "mimo_v2_5")

        check("another account is unaffected",
              (await repository.get_preferences(other)).get("default_model_id") is None)

        # 'auto' is a mode, and has to round-trip through the same column.
        await repository.set_preferences(user, default_model_id="auto")
        check("the Auto sentinel round-trips",
              (await repository.get_preferences(user)).get("default_model_id") == "auto")

        # Clearing is distinct from choosing the current default.
        await repository.set_preferences(user, default_model_id="")
        check("a preference can be cleared back to 'no preference'",
              (await repository.get_preferences(user)).get("default_model_id") is None)

        # A partial write must not blank the fields it does not mention.
        await repository.set_preferences(user, default_model_id="mimo_v2_5")
        await repository.set_preferences(user, theme="dark")
        check("writing one field leaves the others alone",
              (await repository.get_preferences(user)).get("default_model_id")
              == "mimo_v2_5")
    finally:
        client = get_client()
        for uid in (user, other):
            client.table("user_preferences").delete().eq("user_id", uid).execute()


async def main() -> None:
    print("=" * 68)
    print("Phase 2 data: search, feedback, preferences")
    print("=" * 68)
    if not enabled():
        print("Supabase is not configured; nothing to test.")
        return
    await test_search()
    await test_feedback()
    await test_preferences()
    print("\n" + "=" * 68)
    if _failures:
        print(f"{len(_failures)} check(s) failed:")
        for name in _failures:
            print(f"  - {name}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
