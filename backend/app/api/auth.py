"""Supabase Auth verification.

Off by default (`REQUIRE_AUTH=0`) so the app runs locally with no auth setup.
Turn it on and the frontend must send the Supabase access token as
`Authorization: Bearer <token>` on REST calls, or `?token=` on the websocket.
"""

from __future__ import annotations

import logging

from fastapi import Header, HTTPException, status

from app.config import get_settings
from app.db.supabase_client import get_client

log = logging.getLogger(__name__)


async def resolve_user(token: str | None) -> str | None:
    """Return a user id, or None for anonymous. Raises if auth is required
    and the token is missing or invalid."""
    settings = get_settings()

    if not settings.require_auth:
        if not token:
            return None
        user_id = await _verify(token)
        return user_id  # best-effort attribution when auth is optional

    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing access token")
    user_id = await _verify(token)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid access token")
    return user_id


async def _verify(token: str) -> str | None:
    import asyncio

    client = get_client()
    if client is None:
        return None

    def _call():
        return client.auth.get_user(token)

    try:
        res = await asyncio.to_thread(_call)
        user = getattr(res, "user", None)
        return getattr(user, "id", None)
    except Exception:  # noqa: BLE001
        log.debug("Token verification failed", exc_info=True)
        return None


async def bearer_user(authorization: str | None = Header(default=None)) -> str | None:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    return await resolve_user(token)
