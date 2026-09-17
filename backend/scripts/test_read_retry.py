"""Reads survive a transport blip; writes are never repeated. Offline.

    python scripts/test_read_retry.py

Nothing is called for real — the thing under test is `repository._read`'s
retry policy, and a policy is best asserted against a function you can make
fail on demand.

The bug this pins down
----------------------
`_run` answers every failure with `None`. For a *read* that is the same value
as "there is no such row", a collision `get_session` already documents: a
caller that cannot tell the two apart turns an unreachable database into a 404
for a session that exists.

That stayed theoretical until a burst of six concurrent reads failed together
with `httpx.ReadError: [WinError 10035]` during an ordinary page reload. It
did not reproduce under a 12-way cold burst, nor after 75 or 150 seconds of
idling, which is the signature of a transient socket error rather than a
broken connection pool — the kind a second attempt clears.

What is asserted
----------------
1. A transport error on the first attempt is retried, and the read succeeds.
2. Exactly one retry — a failing database is not hammered.
3. A persistent transport failure still degrades to `None`.
4. A non-transport error is *not* retried: an API error is a real answer from
   the server, and asking again would only ask again.
5. `_run` is unchanged, so writes keep their single attempt. A retried insert
   whose first try landed would duplicate the row; what went missing there is
   the response, not the effect.
6. The happy path runs exactly once.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging  # noqa: E402

import httpx  # noqa: E402

from app.db import repository  # noqa: E402

# The failures below are all deliberate; their tracebacks are not findings.
logging.disable(logging.WARNING)

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def counting(exc: BaseException | None, succeed_on: int | None = None):
    """A callable that records its attempts and fails until ``succeed_on``."""
    calls: list[int] = []

    def fn():
        calls.append(1)
        if succeed_on is not None and len(calls) >= succeed_on:
            return "rows"
        raise exc

    return fn, calls


async def main() -> int:
    print("\n1. A transport blip is retried")
    fn, calls = counting(httpx.ReadError("[WinError 10035] would block"), succeed_on=2)
    got = await repository._read(fn)
    check("the read succeeds", got == "rows", f"got {got!r}")
    check("after exactly one retry", len(calls) == 2, f"{len(calls)} attempts")

    print("\n2. A database that is really down still degrades")
    fn2, calls2 = counting(httpx.ConnectError("down"))
    check("the read returns None", await repository._read(fn2) is None)
    check("and stops after two attempts", len(calls2) == 2, f"{len(calls2)} attempts")

    print("\n3. An API error is an answer, not a blip")
    fn3, calls3 = counting(ValueError("PGRST116"))
    check("the read returns None", await repository._read(fn3) is None)
    check("and is not retried", len(calls3) == 1, f"{len(calls3)} attempts")

    print("\n4. Writes keep their single attempt")
    fn4, calls4 = counting(httpx.ReadError("blip"))
    await repository._run(fn4)
    check(
        "`_run` does not retry",
        len(calls4) == 1,
        f"{len(calls4)} attempt — a repeated insert would duplicate the row",
    )

    print("\n5. The happy path is untouched")
    fn5, calls5 = counting(None, succeed_on=1)
    check("one call, one attempt", await repository._read(fn5) == "rows")
    check("no speculative second read", len(calls5) == 1, f"{len(calls5)} attempts")

    failures = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed.")
    for _, name, detail in failures:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
