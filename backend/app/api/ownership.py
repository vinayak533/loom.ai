"""Resource ownership checks.

This service holds the Supabase **service-role** key (see
`app/db/supabase_client.py`), so row-level security does not apply to anything
it does: every query runs as the table owner and will happily return, update or
delete another account's row. `schema.sql` having RLS policies is therefore not
evidence that the HTTP surface is safe — those policies only guard the
browser's own direct connection.

Ownership is consequently an application-level obligation, and this module is
where it is discharged. The rule is simple and has no exceptions:

    every route keyed by a resource id must pass through one of these
    `require_*` helpers before it reads, writes or deletes anything.

`list_*` endpoints do not need them because they filter by owner inside the
query itself — see `repository.list_sessions`, whose docstring explains why an
unfiltered listing would hand one caller every other account's history. The
single-resource routes are exactly the ones that had no equivalent guard.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.db import learn_repository as learn_repo
from app.db import repository


def _same_owner(owner: str | None, user_id: str | None) -> bool:
    """Does `user_id` own a row whose `user_id` column holds `owner`?

    Both sides are normalised to None so that "" and null cannot diverge. An
    anonymous caller — no token at all, or `REQUIRE_AUTH=0` — owns the
    anonymous shelf, the rows whose `user_id` is null. That is precisely the
    set `list_sessions` already shows them, so the guard and the listing agree
    on what "mine" means rather than each deciding separately.
    """
    return (owner or None) == (user_id or None)


def _deny(kind: str) -> HTTPException:
    # 403 rather than 404. It admits the id exists, which for an unguessable
    # v4 UUID is a negligible disclosure next to the clarity of the failure —
    # and the caller genuinely is authenticated-but-not-entitled, which is what
    # 403 means. Missing rows still get a 404 below.
    return HTTPException(
        status.HTTP_403_FORBIDDEN, f"That {kind} belongs to another account."
    )


async def require_session(user_id: str | None, session_id: str) -> dict:
    """Fetch a session, or refuse. Returns the row so callers need not re-read it."""
    row = await repository.get_session(session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such session.")
    if not _same_owner(row.get("user_id"), user_id):
        raise _deny("session")
    return row


async def require_session_write(user_id: str | None, session_id: str) -> dict | None:
    """As :func:`require_session`, but tolerates a session that does not exist yet.

    The sandbox and preview routes are reachable before the row has landed —
    the browser mints the session id locally and the websocket inserts it — so
    a hard 404 here would break a legitimate first call. A row that *is* there
    is still checked, which is the case that matters: an attacker's target
    always exists.
    """
    row = await repository.get_session(session_id)
    if row is None:
        return None
    if not _same_owner(row.get("user_id"), user_id):
        raise _deny("session")
    return row


async def require_notebook(user_id: str | None, notebook_id: str) -> dict:
    """Fetch a notebook, or refuse.

    This replaces `learn.py`'s `_require`, which checked only that the notebook
    existed — enough to return a 404 for a typo, and no help at all against a
    caller holding somebody else's id.
    """
    notebook = await learn_repo.find("notebooks", notebook_id)
    if not notebook:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such notebook.")
    if not _same_owner(notebook.get("user_id"), user_id):
        raise _deny("notebook")
    return notebook


async def require_note(user_id: str | None, notebook_id: str, note_id: str) -> dict:
    """Ownership of the notebook, *and* the note actually being in it.

    Both halves are load-bearing. `update_note`/`delete_note` key on the note id
    alone, so checking only the notebook would let a caller pass their own
    notebook_id alongside someone else's note_id and edit it.
    """
    await require_notebook(user_id, notebook_id)
    note = await learn_repo.find("notebook_notes", note_id)
    if not note:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such note.")
    if note.get("notebook_id") != notebook_id:
        raise _deny("note")
    return note


async def require_source(user_id: str | None, notebook_id: str, source_id: str) -> dict:
    """Same shape as :func:`require_note`, for sources."""
    await require_notebook(user_id, notebook_id)
    source = await learn_repo.find("notebook_sources", source_id)
    if not source:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such source.")
    if source.get("notebook_id") != notebook_id:
        raise _deny("source")
    return source


async def owns_session(user_id: str | None, session_id: str) -> bool:
    """Non-raising variant for the websockets, which report failure by closing.

    A session that does not exist yet is *not* a refusal: the Chat/Code socket
    is where a new session row is created, so the connect necessarily precedes
    the row.
    """
    row = await repository.get_session(session_id)
    if row is None:
        return True
    return _same_owner(row.get("user_id"), user_id)
