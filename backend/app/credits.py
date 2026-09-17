"""The per-user credit meter.

Every agent turn is charged. There is no free agent and no free tool — see
:func:`ensure_can_start`, which is called before the first model call of a
turn, and :func:`charge_llm` / :func:`charge_tool`, which are called after the
work is actually done.

Why "after"
-----------
A pre-authorisation hold would be more correct in a payments sense, but the
router does not know a call's cost until the response comes back with its token
counts. Charging the real number after the fact, and refusing to *start* a turn
below :attr:`Settings.credit_minimum_to_start`, means the worst case is one
overdrawn turn rather than a systematic estimate error on every turn. The
balance is allowed to go negative for exactly that reason — hiding an overdraw
by clamping at zero would make the ledger stop adding up.

Two backends
------------
With Supabase configured the balance lives in ``user_credits`` and every
movement is appended to ``credit_ledger``. Without it, an in-process dict
stands in so the meter still *works* locally rather than silently letting
everything through. Both paths run through the same public functions, so no
caller knows which is in use.

When the durable store breaks
-----------------------------
It is treated as an outage, not as a new permanent mode. The meter keeps
charging into the in-process ledger, buffers every movement in memory *and* on
disk (``credit_spill_path``), retries the store on a backoff, and flushes the
buffer into ``credit_ledger`` the moment it answers. A restart mid-outage
replays the spill file rather than losing it.

This is the correction of a real incident: the previous version latched a
boolean on the first error and never cleared it, so one transient failure
demoted the meter for the life of the process and ~910 credits of real usage
died at the next restart. The invariant now is simply that **a movement is
never discarded** — it is durable, buffered, or on disk, and the credits
endpoint says which.

Anonymous users
---------------
``REQUIRE_AUTH=0`` is the default, so ``user_id`` is frequently ``None``. That
is metered too, under the :data:`ANONYMOUS` sentinel, rather than treated as an
exemption.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db.supabase_client import enabled as supabase_enabled, get_client

log = logging.getLogger(__name__)

#: Where usage lands when there is no signed-in user. A real bucket, not a
#: bypass: anonymous turns are charged exactly like everyone else's.
ANONYMOUS = "anonymous"


# ---------------------------------------------------------------------------
# Tool surcharges
# ---------------------------------------------------------------------------

# Some agent tools call a *separately billed* third-party API. The LLM cost of
# the turn does not cover those, so they carry their own charge on top.
#
# One credit is CREDIT_USD, $0.001 by default, so a surcharge of N credits is
# N/1000 dollars and can be compared directly against a vendor's price list.
#
#   generate_image   NOT a constant — see `image_surcharge()`. It is derived
#                    from STABILITY_MODEL, because the configured tier changes
#                    the real price by nearly 3x.
#   resize_image     local Pillow work, no vendor call                ->  0
#   search_web       Tavily/Exa                                       ->  5
#   read_url         Jina Reader, and only when Jina actually served   ->  2
#   send_email       Resend ≈ $0.0004, priced up because it is an
#                    irreversible outward-facing action worth metering->  5
#   run_code         an E2B sandbox-second is not free                ->  5
#
# STILL OPEN, AWAITING THE PRODUCT OWNER'S DECISION — checked against the
# vendors' own pricing pages, deliberately NOT adjusted:
#
#   search_web  charges $0.005. Tavily basic is $0.008 pay-as-you-go (1 credit)
#               and advanced $0.016 (2 credits); the Exa fallback with contents
#               is ~$0.013. So it undercharges by 1.6x-3.2x on every search.
#   read_url    charges $0.002. Jina Reader is $0.05 per 1M output tokens
#               ($50/1B), and a typical page is 8-15k tokens — $0.0004-$0.0008.
#               So it overcharges by roughly 3-5x on an ordinary page, and only
#               breaks even on a very large one.
#
# Leave these alone until that decision comes back.
TOOL_SURCHARGE: dict[str, float] = {
    "resize_image": 0.0,
    "search_web": 5.0,
    "read_url": 2.0,
    "send_email": 5.0,
    "run_code": 5.0,
}


# --- image generation, priced from the configured model --------------------
#
# Stability bills per generated image at a published rate of 1 Stability credit
# = $0.01 (platform.stability.ai/pricing), and the per-image credit cost
# differs sharply by model. A flat surcharge was correct only for `core`: with
# STABILITY_MODEL=ultra the same 30 credits would cover $0.03 of a $0.08 call,
# undercharging by 2.7x for as long as nobody noticed.
#
# Keys here must match `app.agents.tools.imagery._STABILITY_ENDPOINTS` exactly.
# `validate_pricing()` asserts that at startup, so adding an endpoint without a
# price is caught on boot rather than by an unbilled render months later.
STABILITY_CREDIT_USD = 0.01
STABILITY_CREDITS_PER_IMAGE: dict[str, float] = {
    "core": 3.0,
    "sd3.5-large": 6.5,
    "ultra": 8.0,
}


class UnknownImagePricing(RuntimeError):
    """No published price is known for the configured image model.

    Raised rather than defaulted. A default here is a silent undercharge that
    persists until someone audits the ledger — which is exactly how this class
    of bug survives.
    """


def image_surcharge(model: str | None = None) -> float:
    """Credits to charge for one image from the configured Stability model."""
    settings = get_settings()
    name = (model or settings.stability_model or "").strip()
    price = STABILITY_CREDITS_PER_IMAGE.get(name)
    if price is None:
        raise UnknownImagePricing(
            f"STABILITY_MODEL=`{name}` has no entry in "
            "`credits.STABILITY_CREDITS_PER_IMAGE`, so the correct charge for "
            "an image is unknown. Add its published per-image credit cost from "
            "platform.stability.ai/pricing, or set STABILITY_MODEL to one of: "
            + ", ".join(sorted(STABILITY_CREDITS_PER_IMAGE))
        )
    # Stability credits -> dollars -> our credits.
    return round(price * STABILITY_CREDIT_USD / max(settings.credit_usd, 1e-9), 4)


def validate_pricing() -> list[str]:
    """Check the pricing tables at startup. Returns the problems found.

    Two things can drift apart: the endpoint table in `imagery.py` and the
    price table here. Either an image model that can be called has no price
    (silent undercharge) or the configured model has no price (the render is
    refused). Both are reported loudly at boot, where they are cheap to fix.
    """
    problems: list[str] = []
    settings = get_settings()
    try:
        from app.agents.tools.imagery import _STABILITY_ENDPOINTS
    except Exception:  # noqa: BLE001 - never block startup on the check itself
        return problems

    for model in _STABILITY_ENDPOINTS:
        if model not in STABILITY_CREDITS_PER_IMAGE:
            problems.append(
                f"image model `{model}` is callable but has no entry in "
                "credits.STABILITY_CREDITS_PER_IMAGE — a render on it would be "
                "charged nothing"
            )
    configured = (settings.stability_model or "").strip()
    if configured not in STABILITY_CREDITS_PER_IMAGE:
        problems.append(
            f"STABILITY_MODEL=`{configured}` has no published price here, so "
            "`generate_image` will refuse to run rather than guess a charge"
        )
    return problems


class InsufficientCredits(RuntimeError):
    """Raised when a turn cannot start because the balance is too low."""

    def __init__(self, balance: float, required: float) -> None:
        self.balance = balance
        self.required = required
        super().__init__(
            f"Not enough credits to start this turn — balance {balance:.2f}, "
            f"need at least {required:.2f}."
        )


@dataclass
class Balance:
    user_id: str
    balance: float
    granted: float
    spent: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "balance": round(self.balance, 4),
            "granted": round(self.granted, 4),
            "spent": round(self.spent, 4),
        }


# --- in-process fallback ----------------------------------------------------


@dataclass
class _LocalAccount:
    balance: float
    granted: float
    spent: float
    ledger: list[dict] = field(default_factory=list)


_LOCAL: dict[str, _LocalAccount] = {}
#: Serialises read-modify-write on a balance. Two tool charges landing in the
#: same turn are genuinely concurrent (the graph runs read-only tools in
#: parallel), and without this the later write would clobber the earlier one.
_LOCK = asyncio.Lock()

#: The last balance each account was seen to have in the durable store.
#:
#: Read while degraded to seed the in-process account. Without it, a
#: degradation mid-session hands an account with 12 credits left a fresh
#: opening grant of 1000 — the original fail-open bug in miniature, reached by
#: a different route.
class _Snapshots(dict):
    """``_LAST_KNOWN``, with the time each entry was written.

    A plain dict would need the timestamp bolted on at all five assignment
    sites, and the one that got forgotten would be the one that mattered.
    Recording it here makes "how old is this number?" answerable wherever the
    snapshot is, which is what :func:`ensure_can_start` needs to decide whether
    it may skip its round trip.
    """

    def __init__(self) -> None:
        super().__init__()
        self.at: dict[str, float] = {}

    def __setitem__(self, key: str, value: tuple[float, float, float]) -> None:
        super().__setitem__(key, value)
        self.at[key] = time.monotonic()

    def age(self, key: str) -> float:
        """Seconds since this account's snapshot was written; ``inf`` if never."""
        stamped = self.at.get(key)
        return float("inf") if stamped is None else time.monotonic() - stamped


_LAST_KNOWN: _Snapshots = _Snapshots()


# ---------------------------------------------------------------------------
# Degradation, recovery, and the buffer in between
# ---------------------------------------------------------------------------
#
# The durable store can go away for two very different reasons: `schema.sql`
# was never applied (it will be unusable until someone acts), or the network
# hiccuped (it will be back in seconds). The meter cannot tell them apart at
# the point of failure, so it treats both the same way — keep charging into an
# in-process ledger, keep every movement, and retry.
#
# The rule this module now holds to: **a movement is never discarded.** While
# degraded, every debit and grant is appended to `_PENDING` *and* to a spill
# file on disk, so neither a long outage nor a restart in the middle of one
# loses the usage. When a probe finally succeeds, the buffer is flushed into
# `credit_ledger` and the balances are moved by exactly the buffered amount.
#
# The earlier version of this file latched a boolean and never cleared it. One
# transient error therefore demoted the meter for the entire life of the
# process, and everything charged afterwards died at the next restart — an
# 11m42s window cost ~910 credits of real usage that way.

#: When the store was first seen to be unusable, or ``None`` when healthy.
_DEGRADED_SINCE: datetime | None = None
_DEGRADED_REASON: str = ""
#: `time.monotonic()` after which another probe is allowed.
_NEXT_PROBE: float = 0.0
_PROBE_FAILURES: int = 0
#: Serialises recovery so a burst of concurrent charges probes once, not once
#: each — and so a flush cannot run twice over the same rows.
_RECOVERY_LOCK = asyncio.Lock()

#: Movements that have not reached the durable store yet. Each carries two
#: private flags so a flush that dies halfway can be retried without either
#: dropping a row or applying its amount to a balance twice.
_PENDING: list[dict[str, Any]] = []

#: Past this many buffered movements something is badly wrong and the log
#: should say so. Buffering continues regardless: dropping the record is the
#: one behaviour this rewrite exists to prevent.
_PENDING_ALARM = 5_000


def _degrade(reason: str) -> None:
    """Move the meter onto the in-process ledger and start retrying."""
    global _DEGRADED_SINCE, _DEGRADED_REASON, _NEXT_PROBE, _PROBE_FAILURES
    if _DEGRADED_SINCE is not None:
        return
    settings = get_settings()
    _DEGRADED_SINCE = datetime.now(timezone.utc)
    _DEGRADED_REASON = reason
    _PROBE_FAILURES = 0
    _NEXT_PROBE = time.monotonic() + settings.credit_store_retry_seconds
    log.error(
        "CREDIT METER DEGRADED (%s). Charging continues into an in-process "
        "ledger and every movement is being buffered to %s. The store will be "
        "retried in %.0fs and the buffer flushed when it answers. If this is a "
        "missing schema, run `python -m scripts.apply_schema` in backend/.",
        reason,
        settings.credit_spill_path,
        settings.credit_store_retry_seconds,
    )


def _recovered(flushed: int) -> None:
    global _DEGRADED_SINCE, _DEGRADED_REASON, _PROBE_FAILURES
    since = _DEGRADED_SINCE
    outage = (datetime.now(timezone.utc) - since) if since else None
    _DEGRADED_SINCE = None
    _DEGRADED_REASON = ""
    _PROBE_FAILURES = 0
    log.warning(
        "CREDIT METER RECOVERED after %s. %d buffered movement(s) flushed to "
        "the durable ledger; balances are consistent again.",
        outage,
        flushed,
    )


def degraded() -> bool:
    return _DEGRADED_SINCE is not None


async def _durable() -> bool:
    """True when this call should read from and write to the database.

    Also the recovery clock: while degraded, the first caller past the retry
    deadline probes the store and, if it answers, flushes the buffer before
    returning ``True``. Recovery therefore rides on ordinary traffic and needs
    no background task — a meter with nothing to charge has nothing to recover.
    """
    if not supabase_enabled():
        return False
    if _DEGRADED_SINCE is None:
        return True
    if time.monotonic() < _NEXT_PROBE:
        return False
    return await _try_recover()


async def _try_recover() -> bool:
    """Probe the store; on success flush the buffer and clear the latch."""
    global _NEXT_PROBE, _PROBE_FAILURES

    async with _RECOVERY_LOCK:
        # Another caller may have recovered while this one waited for the lock.
        if _DEGRADED_SINCE is None:
            return True
        if time.monotonic() < _NEXT_PROBE:
            return False

        settings = get_settings()
        client = get_client()

        def _probe():
            # Deliberately the cheapest round trip that proves both tables are
            # readable — a probe that only checks the connection would recover
            # into the same missing-table failure it just backed off from.
            client.table("user_credits").select("user_id").limit(1).execute()
            client.table("credit_ledger").select("id").limit(1).execute()
            return True

        try:
            await asyncio.to_thread(_probe)
        except Exception as exc:  # noqa: BLE001 - the store is still down
            _PROBE_FAILURES += 1
            backoff = min(
                settings.credit_store_retry_seconds * (2 ** (_PROBE_FAILURES - 1)),
                settings.credit_store_retry_max_seconds,
            )
            _NEXT_PROBE = time.monotonic() + backoff
            log.warning(
                "Credit store still unusable (%s: %s) after %s degraded. "
                "%d movement(s) worth %.2f credits are buffered. Next retry in "
                "%.0fs.",
                type(exc).__name__,
                exc,
                datetime.now(timezone.utc) - _DEGRADED_SINCE,
                len(_PENDING),
                sum(abs(float(r.get("amount") or 0)) for r in _PENDING),
                backoff,
            )
            return False

        flushed = await _flush_pending(client)
        if flushed is None:
            # The store answered the probe but refused the flush. Stay degraded
            # rather than declaring victory over a buffer that is still stuck.
            _PROBE_FAILURES += 1
            _NEXT_PROBE = time.monotonic() + min(
                settings.credit_store_retry_seconds * (2 ** (_PROBE_FAILURES - 1)),
                settings.credit_store_retry_max_seconds,
            )
            return False

        _recovered(flushed)
        return True


async def _flush_pending(client) -> int | None:
    """Write every buffered movement to the durable store.

    Returns the number flushed, or ``None`` if the store refused partway — in
    which case whatever did not land stays in the buffer.

    Both halves are idempotent, which is what makes a retry safe:

    * ledger rows go in by ``upsert`` on their own uuid, so a row written just
      before a crash is not duplicated by the retry;
    * the balance delta is applied per row and each row is marked once it has
      been counted, so a flush that dies between the two writes resumes rather
      than charging the same credits twice.

    The delta is applied to the existing balance rather than the balance being
    recomputed from the ledger. Recomputing would be simpler and would also
    silently erase any pre-existing drift between the two — including drift
    that has already been reviewed and accepted.
    """
    global _PENDING

    if not _PENDING:
        return 0

    settings = get_settings()
    outstanding = [r for r in _PENDING if not r.get("_written")]
    if outstanding:
        rows = [_strip(r) for r in outstanding]
        try:
            for start in range(0, len(rows), 100):
                chunk = rows[start : start + 100]
                await asyncio.to_thread(
                    lambda c=chunk: client.table("credit_ledger").upsert(c).execute()
                )
                for row in outstanding[start : start + 100]:
                    row["_written"] = True
        except Exception:  # noqa: BLE001
            log.warning("Flushing the credit ledger failed", exc_info=True)
            _spill_save()
            return None
        _spill_save()

    # --- balances ---------------------------------------------------------
    deltas: dict[str, list[float]] = {}
    for row in _PENDING:
        if row.get("_applied"):
            continue
        amount = float(row.get("amount") or 0)
        entry = deltas.setdefault(row["user_id"], [0.0, 0.0, 0.0])
        entry[0] += amount                       # balance
        if amount > 0:
            entry[1] += amount                   # granted
        else:
            entry[2] += -amount                  # spent

    for account, (d_balance, d_granted, d_spent) in deltas.items():
        # Under the same lock every debit and grant uses. A flush is a
        # read-modify-write on the same row they are writing, and the balance
        # phase is the one place recovery can silently undo a live charge.
        # Nothing here may call `_durable()`, which is why its callers resolve
        # durability before taking the lock.
        try:
            async with _LOCK:
                current = await asyncio.to_thread(
                    lambda a=account: client.table("user_credits")
                    .select("balance,granted,spent")
                    .eq("user_id", a)
                    .limit(1)
                    .execute()
                )
                existing = (getattr(current, "data", None) or [None])[0]
                if existing is None:
                    # The account row never made it either. Recreate it at zero
                    # and let the buffered movements — which include the
                    # opening grant — put it where it belongs.
                    base = {"balance": 0.0, "granted": 0.0, "spent": 0.0}
                    await asyncio.to_thread(
                        lambda a=account, b=base: client.table("user_credits")
                        .upsert(
                            {"user_id": a, **b, "created_at": _now(), "updated_at": _now()}
                        )
                        .execute()
                    )
                    existing = base
                patch = {
                    "balance": round(float(existing.get("balance") or 0) + d_balance, 4),
                    "granted": round(float(existing.get("granted") or 0) + d_granted, 4),
                    "spent": round(float(existing.get("spent") or 0) + d_spent, 4),
                    "updated_at": _now(),
                }
                await asyncio.to_thread(
                    lambda a=account, p=patch: client.table("user_credits")
                    .update(p)
                    .eq("user_id", a)
                    .execute()
                )
                _LAST_KNOWN[account] = (
                    patch["balance"], patch["granted"], patch["spent"]
                )
        except Exception:  # noqa: BLE001
            log.warning(
                "Applying buffered credits to `%s` failed; %d movement(s) stay "
                "buffered.", account, len(_PENDING), exc_info=True,
            )
            _spill_save()
            return None
        # The balance now reflects these rows, so the rows must say so. They
        # were upserted a moment ago carrying `balance_applied = false` —
        # correctly, since at that point it was false — and leaving them that
        # way would have reconciliation report every recovered outage as a
        # discrepancy for ever. One update per account, and a flush is rare.
        settled = [
            row["id"]
            for row in _PENDING
            if row["user_id"] == account and not row.get("_applied") and row.get("id")
        ]
        for row in _PENDING:
            if row["user_id"] == account:
                row["_applied"] = True
                row["balance_applied"] = True
        if settled and _LEDGER_HAS_APPLIED is not False:
            try:
                await asyncio.to_thread(
                    lambda ids=settled: client.table("credit_ledger")
                    .update({"balance_applied": True})
                    .in_("id", ids)
                    .execute()
                )
            except Exception:  # noqa: BLE001 - cosmetic for reconciliation only
                log.warning(
                    "Could not mark %d flushed ledger row(s) for `%s` as "
                    "applied; reconciliation will report them.",
                    len(settled), account, exc_info=True,
                )
        _spill_save()

    flushed = len(_PENDING)
    _PENDING = []
    _LOCAL.clear()  # balances come from the durable store again
    _spill_clear()
    log.info(
        "Flushed %d buffered credit movement(s) into %s.",
        flushed, settings.credit_spill_path,
    )
    return flushed


def _buffer(row: dict[str, Any]) -> dict[str, Any]:
    """Hold a movement that could not be written durably.

    The defaults come first so a caller that already knows one half landed —
    `balance_written` in :func:`_append_ledger` — can say so and not have it
    overwritten.

    Returns the entry that was appended, so a debit whose balance write lands
    *after* its ledger row can come back and flip `_applied` on this exact row
    (see :func:`_mark_applied`). Without the reference the flush would move the
    balance a second time and turn a recovered outage into a double charge.
    """
    held = {"_written": False, "_applied": False, **row}
    _PENDING.append(held)
    if len(_PENDING) == _PENDING_ALARM:
        log.error(
            "The credit buffer has reached %d unflushed movements. The durable "
            "store has been unreachable since %s. Nothing is being dropped, but "
            "this needs attention.",
            _PENDING_ALARM, _DEGRADED_SINCE,
        )
    _spill_save()
    return held


# --- the spill file --------------------------------------------------------
# The buffer alone only survives as long as the process. A degraded window that
# ends in a restart — which is exactly how the observed 910 credits were lost —
# needs the movements on disk, so they can be replayed or, failing everything,
# reconciled by hand. The file existing at rest means there is unreconciled
# usage; it is removed only once every row it holds is durably written.


def _spill_path() -> str:
    return get_settings().credit_spill_path


def _spill_save() -> None:
    """Rewrite the spill file from the buffer. Never raises."""
    path = _spill_path()
    try:
        if not _PENDING:
            _spill_clear()
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for row in _PENDING:
                fh.write(json.dumps(row, default=str) + "\n")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - a failed spill must not fail a turn
        log.warning("Could not write the credit spill file at %s", path, exc_info=True)


def _spill_clear() -> None:
    try:
        os.remove(_spill_path())
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        log.warning("Could not remove the credit spill file", exc_info=True)


def load_spill() -> int:
    """Replay a previous process's degraded window. Called at startup.

    Rows come back with their `_written` / `_applied` flags intact, so a
    process that died mid-flush resumes from where it stopped instead of
    re-charging what already landed.
    """
    path = _spill_path()
    if not os.path.exists(path):
        return 0
    recovered: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    recovered.append(json.loads(line))
    except Exception:  # noqa: BLE001
        log.error(
            "The credit spill file at %s could not be read. It is left in place "
            "for manual reconciliation — do not delete it.", path, exc_info=True,
        )
        return 0

    if not recovered:
        _spill_clear()
        return 0

    _PENDING.extend(recovered)
    _spill_dirty = True
    total = sum(abs(float(r.get("amount") or 0)) for r in recovered)
    log.error(
        "Recovered %d unflushed credit movement(s) worth %.2f credits from a "
        "previous degraded window (%s). They will be written to the durable "
        "ledger as soon as the store answers.",
        len(recovered), total, path,
    )
    # Nothing is durable until proven otherwise: entering degraded mode is what
    # schedules the probe that will flush these.
    _degrade("unflushed movements recovered from the spill file")
    return len(recovered)


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    """The row as the table wants it, without our private bookkeeping."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def store_status() -> dict[str, Any]:
    """Where balances are actually being kept. Surfaced on the credits endpoint."""
    if not supabase_enabled():
        return {"durable": False, "reason": "Supabase is not configured."}
    if _DEGRADED_SINCE is not None:
        buffered = len(_PENDING)
        return {
            "durable": False,
            "degraded_since": _DEGRADED_SINCE.isoformat(),
            "reason": _DEGRADED_REASON,
            "buffered_movements": buffered,
            "buffered_credits": round(
                sum(abs(float(r.get("amount") or 0)) for r in _PENDING), 4
            ),
            "retry_in_seconds": max(0, round(_NEXT_PROBE - time.monotonic(), 1)),
            "detail": (
                f"The credit store is unreachable ({_DEGRADED_REASON}). Charging "
                f"continues in memory and {buffered} movement(s) are buffered to "
                "disk; they will be written to the ledger when it recovers. "
                "Nothing is being lost."
            ),
        }
    return {"durable": True, "reason": ""}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _account_id(user_id: str | None) -> str:
    return user_id or ANONYMOUS


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _local(account: str) -> _LocalAccount:
    """The in-process stand-in, seeded from the last durable balance seen.

    Seeding matters: falling back to the *opening grant* would hand an account
    that is nearly empty a full balance again the moment the store hiccups,
    which is the fail-open behaviour this module exists to prevent — reached by
    a different route. The opening grant is only used for an account this
    process has genuinely never seen.
    """
    entry = _LOCAL.get(account)
    if entry is None:
        known = _LAST_KNOWN.get(account)
        if known is not None:
            balance, granted, spent = known
        else:
            balance = granted = get_settings().credit_starting_balance
            spent = 0.0
        entry = _LocalAccount(balance=balance, granted=granted, spent=spent)
        _LOCAL[account] = entry
    return entry


async def get_balance(user_id: str | None) -> Balance:
    """The account, creating it with the starting grant on first sight."""
    settings = get_settings()
    account = _account_id(user_id)

    if not await _durable():
        entry = _local(account)
        return Balance(account, entry.balance, entry.granted, entry.spent)

    client = get_client()

    def _read():
        return (
            client.table("user_credits")
            .select("*")
            .eq("user_id", account)
            .limit(1)
            .execute()
        )

    res = await _safe(_read, "reading the balance")
    if res is None:
        # The read did not merely find nothing — it failed. Returning the
        # opening grant here is what made the meter free; degrade instead.
        entry = _local(account)
        return Balance(account, entry.balance, entry.granted, entry.spent)

    rows = getattr(res, "data", None) or []
    if rows:
        row = rows[0]
        balance = Balance(
            account,
            float(row.get("balance") or 0),
            float(row.get("granted") or 0),
            float(row.get("spent") or 0),
        )
        # Remembered so a later degradation starts from the real number rather
        # than from the opening grant. See `_local`.
        _LAST_KNOWN[account] = (balance.balance, balance.granted, balance.spent)
        return balance

    # First sight of this user. The opening grant is a ledger movement like any
    # other, so a balance is always the sum of its history.
    #
    # Creation has to be idempotent, and it was not. A new user's first moments
    # fire three of these at once — `GET /api/credits` for the header, the
    # socket connect, and `ensure_can_start` for the first turn — and all three
    # read no row and all three created one. The old `upsert(row)` is an
    # `ON CONFLICT DO UPDATE`: whichever call landed last wrote `balance` and
    # `spent` back to their opening values, erasing any charge that had already
    # been debited in between, and each call appended its own "Opening balance"
    # ledger row, so the history then claimed three grants for one account.
    #
    # Two guards, because there are two races and one guard cannot close both:
    #
    #   * `_LOCK` serialises the in-process case — which is the one that
    #     actually happens, since those three callers share a process — and the
    #     re-read inside it means the second and third callers find the row the
    #     first one wrote and take the ordinary path;
    #   * `ignore_duplicates=True` closes the cross-process case. `user_id` is
    #     already this table's primary key, so the constraint needed no
    #     migration; what was missing was asking PostgREST for
    #     `ON CONFLICT DO NOTHING` rather than `DO UPDATE`. A losing insert now
    #     returns no rows and changes nothing, instead of overwriting a live
    #     balance with an opening one.
    #
    # `_durable()` was resolved above and `_append_ledger` resolves it again,
    # so the ledger append stays outside the lock: a recovery flush takes
    # `_LOCK` itself, and `asyncio.Lock` is not reentrant.
    opening = settings.credit_starting_balance
    created_here = False

    async with _LOCK:
        # Re-read under the lock. Whoever held it before this call may have
        # created the account already, in which case there is nothing to do and
        # — this is the part that matters — nothing to overwrite.
        res = await _safe(_read, "re-reading the balance before creating it")
        rows = getattr(res, "data", None) or [] if res is not None else []
        if res is None:
            entry = _local(account)
            return Balance(account, entry.balance, entry.granted, entry.spent)

        if not rows:
            row = {
                "user_id": account,
                "balance": opening,
                "granted": opening,
                "spent": 0,
                "created_at": _now(),
                "updated_at": _now(),
            }
            created = await _safe(
                lambda: client.table("user_credits")
                .upsert(row, ignore_duplicates=True)
                .execute(),
                "creating the account",
            )
            if created is None:
                entry = _local(account)
                return Balance(account, entry.balance, entry.granted, entry.spent)
            # PostgREST returns the rows it actually inserted. Empty means
            # another process got there first, so this call created nothing and
            # must not claim the grant.
            created_here = bool(getattr(created, "data", None))

            if not created_here:
                res = await _safe(_read, "reading the account another writer created")
                rows = getattr(res, "data", None) or [] if res is not None else []
                if res is None:
                    entry = _local(account)
                    return Balance(account, entry.balance, entry.granted, entry.spent)

    if not created_here:
        # Someone else created it — either before this call took the lock or
        # underneath it. Whatever the row says now is the truth, including any
        # charge that has already landed against it.
        if rows:
            row = rows[0]
            balance = Balance(
                account,
                float(row.get("balance") or 0),
                float(row.get("granted") or 0),
                float(row.get("spent") or 0),
            )
            _LAST_KNOWN[account] = (balance.balance, balance.granted, balance.spent)
            return balance
        # The insert reported no rows and the re-read found none either. Not a
        # state that should be reachable, but guessing an opening balance here
        # is how an account gets granted twice; degrade instead.
        log.warning(
            "Credit account %s neither created nor found; serving locally.",
            account,
        )
        entry = _local(account)
        return Balance(account, entry.balance, entry.granted, entry.spent)

    # This call created the row, so this call — and only this call — records
    # the grant. The insert above already wrote the opening balance, so if the
    # ledger row has to be buffered it must not move the balance a second time
    # on flush.
    await _append_ledger(
        account, None, None, "grant", opening, "Opening balance",
        balance_written=True,
    )
    _LAST_KNOWN[account] = (opening, opening, 0.0)
    return Balance(account, opening, opening, 0.0)


async def ensure_can_start(user_id: str | None, agent_id: str) -> Balance:
    """Gate the start of a turn. Raises :class:`InsufficientCredits`.

    Deliberately a hard stop rather than a warning: the alternative is letting
    a turn run up a bill against an account that has already been told it is
    empty, which is the failure the meter exists to prevent.

    **Why this is allowed to answer from memory.** This await sat between the
    user pressing Enter and the first byte reaching a provider, and it is one
    Supabase round trip — measured at 151 ms warm, 1.3 s cold — paid on every
    single turn. So a *recent* snapshot is used instead, on two conditions
    that between them keep the gate honest:

      · every debit this process makes updates the snapshot as it writes, so
        the number is not merely cached, it is maintained. Spending is what
        moves a balance, and spending goes through :func:`_debit`.
      · the shortcut only applies while the balance is clear of the floor by
        more than one turn's ceiling. An account anywhere near empty — the
        only case where being wrong costs anything — always takes the real
        read.

    What remains is an account topped up in another process not being seen for
    up to ``credit_gate_cache_seconds``, which errs towards refusing a turn the
    user could afford rather than granting one they could not, and resolves
    itself on the next tick.
    """
    settings = get_settings()
    if settings.credits_enabled:
        cached = _gate_shortcut(user_id)
        if cached is not None:
            return cached
    balance = await get_balance(user_id)
    if not settings.credits_enabled:
        return balance
    if balance.balance < settings.credit_minimum_to_start:
        raise InsufficientCredits(balance.balance, settings.credit_minimum_to_start)
    return balance


def _gate_shortcut(user_id: str | None) -> Balance | None:
    """A snapshot fresh and comfortable enough to open a turn on. Else None."""
    settings = get_settings()
    ttl = settings.credit_gate_cache_seconds
    if ttl <= 0:
        return None
    account = _account_id(user_id)
    if _DEGRADED_SINCE is not None or account not in _LAST_KNOWN:
        return None
    if _LAST_KNOWN.age(account) > ttl:
        return None
    balance, granted, spent = _LAST_KNOWN[account]
    # One whole turn's worth of headroom above the floor. Below that, the
    # difference between the snapshot and the truth could be the difference
    # between a turn that is affordable and one that is not.
    floor = settings.credit_minimum_to_start + max(settings.credit_turn_ceiling, 0.0)
    if balance < floor:
        return None
    return Balance(account, balance, granted, spent)


async def prime_balance(user_id: str | None) -> None:
    """Warm the snapshot off the critical path. Never raises.

    Called when a socket connects, which is dead time the user is not waiting
    on, so the first turn of the session finds :func:`_gate_shortcut` already
    populated instead of paying the round trip at the worst possible moment.
    """
    try:
        await get_balance(user_id)
    except Exception:  # noqa: BLE001 - a cold cache is not a failure
        log.debug("Balance prime skipped", exc_info=True)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def charge_llm(
    user_id: str | None,
    *,
    session_id: str | None,
    agent_id: str,
    model_id: str,
    cost_usd: float,
) -> float:
    """Debit one model call. Returns the credits taken.

    The router already estimates the USD cost of every call (including cache
    reads and writes); this converts that into the product's own unit rather
    than inventing a second pricing model that could drift from it.
    """
    settings = get_settings()
    if not settings.credits_enabled or cost_usd <= 0:
        return 0.0
    amount = round(cost_usd / max(settings.credit_usd, 1e-9), 4)
    if amount <= 0:
        return 0.0
    await _debit(
        _account_id(user_id),
        session_id,
        agent_id,
        "llm",
        amount,
        f"{model_id} call",
        model_id=model_id,
    )
    return amount


async def charge_tool(
    user_id: str | None,
    *,
    session_id: str | None,
    agent_id: str,
    tool: str,
    multiplier: float = 1.0,
) -> float:
    """Debit a tool that costs real money beyond its LLM call.

    Only tools in :data:`TOOL_SURCHARGE` are charged; everything else (a
    tokenizer, a readability formula, a regex matcher) runs on our own CPU and
    is already paid for by the turn's model cost.
    """
    settings = get_settings()
    if not settings.credits_enabled:
        return 0.0
    # `surcharge_for` raises for an image model with no published price. That
    # propagates deliberately: the render already happened, so the alternative
    # is charging a number nobody can justify. `generate_image` refuses before
    # spending anything, so reaching here with an unknown model means the two
    # tables drifted after boot.
    base = surcharge_for(tool)
    amount = round(base * max(multiplier, 0.0), 4)
    if amount <= 0:
        return 0.0
    await _debit(
        _account_id(user_id),
        session_id,
        agent_id,
        "tool",
        amount,
        f"{tool} (external API)",
    )
    return amount


async def grant(user_id: str | None, amount: float, reason: str = "Manual grant") -> Balance:
    """Credit an account. Used by the top-up endpoint and by refunds."""
    account = _account_id(user_id)
    if amount <= 0:
        return await get_balance(user_id)
    await get_balance(user_id)  # ensure the account exists first

    # Resolved *before* the lock, deliberately. `_durable()` can trigger a
    # recovery flush, and that flush has to take `_LOCK` itself to avoid
    # clobbering a concurrent debit — calling it from inside the lock would
    # deadlock on the first outage that ever recovered mid-turn.
    durable = await _durable()

    balance_written = False
    async with _LOCK:
        if not durable:
            entry = _local(account)
            entry.balance += amount
            entry.granted += amount
        else:
            client = get_client()

            def _read():
                return (
                    client.table("user_credits")
                    .select("balance,granted,spent")
                    .eq("user_id", account)
                    .limit(1)
                    .execute()
                )

            # Read inside the lock rather than via `get_balance`, which would
            # re-enter `_durable()`. Same read-modify-write discipline as
            # `_debit`, and the same reason.
            res = await _safe(_read, "reading the balance to credit it")
            rows = getattr(res, "data", None) or [] if res is not None else []
            if res is None or not rows:
                entry = _local(account)
                entry.balance += amount
                entry.granted += amount
            else:
                patch = {
                    "balance": round(float(rows[0].get("balance") or 0) + amount, 4),
                    "granted": round(float(rows[0].get("granted") or 0) + amount, 4),
                    "updated_at": _now(),
                }
                written = await _safe(
                    lambda: client.table("user_credits")
                    .update(patch)
                    .eq("user_id", account)
                    .execute(),
                    "crediting the account",
                )
                if written is None:
                    entry = _local(account)
                    entry.balance += amount
                    entry.granted += amount
                else:
                    balance_written = True
                    _LAST_KNOWN[account] = (
                        patch["balance"],
                        patch["granted"],
                        float(rows[0].get("spent") or 0),
                    )
    await _append_ledger(
        account, None, None, "grant", amount, reason,
        balance_written=balance_written,
    )
    return await get_balance(user_id)


async def _debit(
    account: str,
    session_id: str | None,
    agent_id: str | None,
    kind: str,
    amount: float,
    reason: str,
    model_id: str | None = None,
) -> None:
    # The balance is allowed to go negative. See the module docstring: an
    # overdraw is a fact to record, not one to hide by clamping.
    #
    # Outside the lock: see the note in `grant`. A recovery flush needs `_LOCK`,
    # so nothing may resolve durability while already holding it.
    durable = await _durable()

    # --- the ledger row goes first ----------------------------------------
    # These two writes are two round trips and cannot be made one. What is in
    # our gift is which of them a crash lands between, and the orders are not
    # equivalent:
    #
    #   balance then ledger  — a crash leaves credits deducted with no row
    #                          explaining them. Nothing can recover that: the
    #                          spill file only holds movements whose durable
    #                          write *returned* a failure, and a process that
    #                          died returned nothing. The charge is
    #                          unattributable and the account's own history
    #                          disagrees with its balance, permanently.
    #   ledger then balance  — a crash leaves a movement recorded and marked
    #                          `balance_applied = false`. The ledger is the
    #                          authority (see the module docstring: a balance is
    #                          the sum of its history), so the row is enough to
    #                          finish the job. `scripts/reconcile_credits.py`
    #                          finds exactly these.
    #
    # So: append, move, then mark the row applied. A crash after the move but
    # before the mark leaves a row that merely *looks* unfinished, which is why
    # reconciliation recomputes each account from the ledger sum rather than
    # replaying individual rows — recomputing is idempotent, replaying is not.
    handle = await _append_ledger(
        account, session_id, agent_id, kind, -amount, reason,
        model_id=model_id, balance_written=False,
    )

    balance_written = False
    async with _LOCK:
        if not durable:
            entry = _local(account)
            entry.balance -= amount
            entry.spent += amount
        else:
            client = get_client()

            def _read():
                # `granted` is selected although this path never changes it, so
                # `_LAST_KNOWN` below stays complete. Without it a debit leaves
                # a stale snapshot behind, and the next degradation seeds the
                # in-process account from a balance several charges out of date.
                return (
                    client.table("user_credits")
                    .select("balance,granted,spent")
                    .eq("user_id", account)
                    .limit(1)
                    .execute()
                )

            res = await _safe(_read, "reading the balance to debit it")
            rows = getattr(res, "data", None) or [] if res is not None else []
            if res is None or not rows:
                # Two different failures, one correct response: charge locally
                # rather than not at all.
                #
                # The empty-rows case is the subtle one. Defaulting the balance
                # to 0 and issuing the UPDATE anyway looks harmless, but the
                # `.eq(user_id)` matches nothing, so the write silently does
                # nothing while `_append_ledger` below still records the debit
                # — the balance and its own history then disagree, which is
                # exactly the drift a ledger exists to make impossible.
                entry = _local(account)
                entry.balance -= amount
                entry.spent += amount
            else:
                balance = float(rows[0].get("balance") or 0)
                spent = float(rows[0].get("spent") or 0)
                patch = {
                    "balance": round(balance - amount, 4),
                    "spent": round(spent + amount, 4),
                    "updated_at": _now(),
                }
                written = await _safe(
                    lambda: client.table("user_credits")
                    .update(patch)
                    .eq("user_id", account)
                    .execute(),
                    "writing the debit",
                )
                if written is None:
                    entry = _local(account)
                    entry.balance -= amount
                    entry.spent += amount
                else:
                    balance_written = True
                    _LAST_KNOWN[account] = (
                        patch["balance"],
                        float(rows[0].get("granted") or 0),
                        patch["spent"],
                    )

    # Outside the lock for the same reason the append was: this can reach the
    # store, and reaching the store can trigger a recovery flush that needs
    # `_LOCK` itself.
    if balance_written:
        await _mark_applied(handle)


#: Whether `credit_ledger.balance_applied` exists in the live database.
#: `None` until the first insert proves it either way.
#:
#: The column arrives with `schema.sql`, and a deploy that has not run
#: `scripts/apply_schema.py` yet does not have it. Without this probe the very
#: first debit after such a deploy would fail its insert, `_safe` would read
#: that as the store being unreachable, and the whole meter would degrade to
#: in-process accounting over a missing boolean — turning a cosmetic gap in
#: reconciliation into every balance in the product going stale. So the column
#: is treated as an enhancement: used when present, dropped when not, and the
#: absence is logged once rather than every call.
_LEDGER_HAS_APPLIED: bool | None = None


def _is_missing_applied_column(exc: Exception) -> bool:
    """True when this failure is the column being absent, not the store being
    down. Postgres reports `column x does not exist` under SQLSTATE 42703 and
    PostgREST passes both through, so either is accepted — the client wraps the
    error differently depending on which layer rejected it. The column name is
    required in the text as well, so an unrelated 42703 is not mistaken for
    this one.
    """
    text = str(exc).lower()
    return "balance_applied" in text and (
        "does not exist" in text or "42703" in text or "column" in text
    )


async def _insert_ledger_row(client, row: dict[str, Any]):
    """Insert one ledger row, tolerating a database without `balance_applied`.

    Returns the result, or ``None`` if the write genuinely failed — the same
    contract as :func:`_safe`, and it calls :func:`_degrade` on a real failure
    for the same reason. A missing column is not a real failure and must not
    degrade the meter.
    """
    global _LEDGER_HAS_APPLIED

    payload = dict(row)
    if _LEDGER_HAS_APPLIED is False:
        payload.pop("balance_applied", None)

    try:
        result = await asyncio.to_thread(
            lambda: client.table("credit_ledger").insert(payload).execute()
        )
    except Exception as exc:  # noqa: BLE001
        if _LEDGER_HAS_APPLIED is None and _is_missing_applied_column(exc):
            _LEDGER_HAS_APPLIED = False
            log.warning(
                "`credit_ledger.balance_applied` is missing from the database, "
                "so the half-written-movement marker is unavailable and "
                "`scripts/reconcile_credits.py` cannot tell a crashed debit "
                "from a finished one. Run `python -m scripts.apply_schema` to "
                "add it. Charging continues without it."
            )
            payload.pop("balance_applied", None)
            return await _safe(
                lambda: client.table("credit_ledger").insert(payload).execute(),
                "appending to the ledger",
            )
        log.warning("Credit store failed while appending to the ledger", exc_info=True)
        _degrade(f"{type(exc).__name__} while appending to the ledger")
        return None

    if _LEDGER_HAS_APPLIED is None:
        _LEDGER_HAS_APPLIED = True
    return result


@dataclass
class _LedgerHandle:
    """Where one appended movement ended up, so a caller can finish it.

    A debit writes its ledger row before it moves the balance, and then has to
    come back and say the balance moved. That second step lands in a different
    place depending on where the first one did — the durable table, or the
    in-memory buffer — so :func:`_append_ledger` hands back this rather than
    leaving the caller to work it out.
    """

    row_id: str
    #: The row reached `credit_ledger`.
    durable: bool
    #: The `_PENDING` entry holding it, when it did not.
    buffered: dict[str, Any] | None = None


async def _append_ledger(
    account: str,
    session_id: str | None,
    agent_id: str | None,
    kind: str,
    amount: float,
    reason: str,
    model_id: str | None = None,
    balance_written: bool = False,
) -> _LedgerHandle:
    """Record one movement — durably if possible, into the buffer if not.

    Every movement in the system passes through here, which is what makes
    "nothing is discarded" enforceable in one place. When the durable write
    cannot happen the row goes to :func:`_buffer`, which holds it in memory
    *and* on disk until a flush succeeds.

    ``balance_written`` says whether the balance columns already reflect this
    movement. A grant passes True, having moved the balance first. A debit
    passes False, because it has not moved it yet, and calls
    :func:`_mark_applied` with the returned handle once it has.
    """
    row = {
        "id": str(uuid.uuid4()),
        "user_id": account,
        "session_id": session_id,
        "agent_id": agent_id,
        "kind": kind,
        "amount": round(amount, 4),
        "reason": reason,
        "model_id": model_id,
        "balance_applied": balance_written,
        "created_at": _now(),
    }
    def _hold() -> _LedgerHandle:
        # `_PENDING` is the record that gets flushed; this is the local view
        # `recent_ledger` serves while the store is away. Deliberately only
        # touched on the degraded path: creating the in-process account during
        # healthy operation leaves a balance behind that no durable write ever
        # updates, and the next outage would then resume from a stale number.
        entry = _local(account)
        entry.ledger.append(row)
        del entry.ledger[:-200]
        held = _buffer({**row, "_applied": balance_written})
        return _LedgerHandle(row["id"], durable=False, buffered=held)

    if not await _durable():
        return _hold()
    client = get_client()
    written = await _insert_ledger_row(client, row)
    if written is None:
        return _hold()
    return _LedgerHandle(row["id"], durable=True)


async def _mark_applied(handle: _LedgerHandle) -> None:
    """Say that the balance now reflects an already-appended movement.

    Best-effort on purpose. Failing here leaves a durable row reading
    `balance_applied = false` when the balance did in fact move — which
    reconciliation reports as a discrepancy to *look at*, and resolves by
    recomputing from the ledger sum rather than by replaying the row. An
    over-report is a nuisance; the under-report the old ordering produced was a
    charge with no history at all.
    """
    if handle.buffered is not None:
        # Both flags: `_applied` stops the flush moving the balance a second
        # time, and `balance_applied` is the column the row carries into the
        # table when it is finally written.
        handle.buffered["_applied"] = True
        handle.buffered["balance_applied"] = True
        _spill_save()
        return
    if not handle.durable:
        return
    if _LEDGER_HAS_APPLIED is False:
        # No column to mark. The row is still in the ledger, which is the part
        # that matters; reconciliation falls back to comparing sums.
        return
    if not await _durable():
        return
    client = get_client()
    await _safe(
        lambda: client.table("credit_ledger")
        .update({"balance_applied": True})
        .eq("id", handle.row_id)
        .execute(),
        "marking a ledger row applied",
    )


async def recent_ledger(user_id: str | None, limit: int = 25) -> list[dict]:
    account = _account_id(user_id)
    if not await _durable():
        entry = _LOCAL.get(account)
        return list(reversed(entry.ledger[-limit:])) if entry else []
    client = get_client()

    def _read():
        return (
            client.table("credit_ledger")
            .select("*")
            .eq("user_id", account)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

    res = await _safe(_read, "reading the ledger")
    return getattr(res, "data", None) or []


async def _safe(fn, what: str = "call"):
    """Run a synchronous supabase-py call off the loop, never raising.

    Same rule as `app.db.repository`: the meter must not be able to take down a
    live agent run. Returning ``None`` on failure is a *signal*, not a
    swallowed error — every caller checks it and falls back to the in-process
    ledger, and the first failure calls :func:`_degrade`, which stops the rest
    of the process paying for round trips that cannot succeed *and* starts the
    retry clock that will bring it back.
    """
    try:
        return await asyncio.to_thread(fn)
    except Exception as exc:  # noqa: BLE001
        log.warning("Credit store failed while %s", what, exc_info=True)
        _degrade(f"{type(exc).__name__} while {what}")
        return None


def estimate_credits(cost_usd: float) -> float:
    """USD -> credits, for display. Mirrors :func:`charge_llm`'s conversion."""
    return round(cost_usd / max(get_settings().credit_usd, 1e-9), 4)


def surcharge_for(tool: str) -> float:
    """What one call of ``tool`` costs on top of its LLM cost.

    Image generation is resolved from the configured model rather than read
    from the table — see :func:`image_surcharge`. It raises
    :class:`UnknownImagePricing` for a model with no published price.
    """
    if tool == "generate_image":
        return image_surcharge()
    return TOOL_SURCHARGE.get(tool, 0.0)


def ceil_credits(value: float) -> int:
    return int(math.ceil(value))
