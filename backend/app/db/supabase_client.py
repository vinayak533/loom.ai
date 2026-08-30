"""Supabase access.

The service-role key is used here and only here. It is never sent to the
browser — the frontend gets the anon key and talks to Supabase directly only
for auth.

If Supabase is not configured the whole layer degrades to a no-op so the app
still runs end-to-end locally. `enabled` tells callers which mode they're in.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

#: One Supabase client per thread.
#:
#: `repository._run` dispatches every call through `asyncio.to_thread`, so the
#: *synchronous* supabase-py client is driven from several pool threads at once.
#: That client wraps a single `httpx.Client` with HTTP/2 enabled, and one HTTP/2
#: connection multiplexed from multiple threads interleaves frames: the server
#: answers with GOAWAY/PROTOCOL_ERROR and every request in flight on that
#: connection dies as `httpx.RemoteProtocolError: ConnectionTerminated`.
#:
#: `_run` treats a raised call as "no data", so the visible symptom was an
#: existing session intermittently 404ing whenever the UI issued a few requests
#: at once (React StrictMode's double mount was enough) — and a whole burst
#: failed together, because they shared the connection that was torn down.
#:
#: A client per thread gives each its own connection pool, which is what makes
#: the concurrent use safe. The pool is small and long-lived, so this is a
#: handful of clients, not one per call.
_local = threading.local()
_missing_logged = False
_log_lock = threading.Lock()


def get_client() -> Any | None:
    settings = get_settings()
    if not settings.supabase_enabled:
        global _missing_logged
        with _log_lock:
            if not _missing_logged:
                _missing_logged = True
                log.warning(
                    "Supabase is not configured — sessions and messages will "
                    "not persist."
                )
        return None

    client = getattr(_local, "client", None)
    if client is None:
        from supabase import create_client

        client = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
        _local.client = client
    return client


def enabled() -> bool:
    return get_client() is not None
