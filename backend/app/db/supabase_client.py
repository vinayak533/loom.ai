"""Supabase access.

The service-role key is used here and only here. It is never sent to the
browser — the frontend gets the anon key and talks to Supabase directly only
for auth.

If Supabase is not configured the whole layer degrades to a no-op so the app
still runs end-to-end locally. `enabled` tells callers which mode they're in.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)


@lru_cache
def get_client() -> Any | None:
    settings = get_settings()
    if not settings.supabase_enabled:
        log.warning(
            "Supabase is not configured — sessions and messages will not persist."
        )
        return None
    from supabase import create_client

    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def enabled() -> bool:
    return get_client() is not None
