"""The parts of the perimeter that are not authentication.

Auth (`api/auth.py`) says who a caller is and ownership (`api/ownership.py`)
says what they may touch. Everything here is the layer around those two:

* **Response headers** every browser should see, so a page from this API can
  neither be framed, sniffed, nor talk back to a downgraded origin.
* **A request body ceiling**, enforced from `Content-Length` before a byte is
  read. The upload endpoints already checked size, but only after
  `await file.read()` had buffered the whole body in memory, so the limit
  was a statement rather than a defence.
* **A websocket origin check.** CORS does not apply to websockets, which
  means any page on the internet could open `/ws/<session-id>` in a visitor's
  browser and, with `REQUIRE_AUTH=0`, drive a session. The socket now has to
  come from an allowed origin, judged by the same list the CORS middleware
  uses.
* **A token transport that stays out of logs.** The access token used to ride
  in `?token=` on the websocket URL, where every proxy and access log between
  the browser and this process records it. Browsers cannot set headers on a
  websocket, but they can name a subprotocol, so the client sends
  `Sec-WebSocket-Protocol: loom.bearer, <token>` and the server picks the
  token off the header. The query parameter is still honoured for older
  clients; it is simply no longer the preferred path.
* **A filename sanitiser.** An upload's name is used in its storage key, and
  `../` in a storage key is a path traversal whichever store is behind it.
* **The client address**, honouring one hop of `X-Forwarded-For` so a rate
  limit keyed by address survives being deployed behind a proxy.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.config import get_settings

# --- headers ----------------------------------------------------------------

_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    # This API serves JSON and files to a script, never a document to a tab, so
    # the CSP can be the strictest possible one. `frame-ancestors` is what
    # actually stops framing in browsers that ignore X-Frame-Options.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    # Everything under /api is per-user and per-session. A shared cache in
    # front of it would be a data leak, not a speed-up.
    "Cache-Control": "no-store",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for key, value in _HEADERS.items():
            response.headers.setdefault(key, value)
        # Only meaningful once the connection is already TLS, and only true
        # then: telling a plain-http local dev server to insist on https would
        # lock the developer out of localhost for a year.
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


# --- body size --------------------------------------------------------------

#: The largest request body any route accepts. Uploads are capped at 20 MB by
#: `files.MAX_UPLOAD_BYTES`; multipart framing and a form field or two are
#: the headroom above it.
MAX_BODY_BYTES = 21 * 1024 * 1024


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Refuse an oversized body from its `Content-Length` alone.

    A client that lies about the length is caught later by the per-route
    checks; this is the cheap first gate that keeps a 2 GB POST from being
    buffered before anything looks at it.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse(
                {"detail": f"Request body exceeds {MAX_BODY_BYTES // (1024 * 1024)} MB."},
                # The literal: Starlette renamed this constant between the
                # versions this project has run on, and the number is stable.
                status_code=413,
            )
        return await call_next(request)


# --- websocket origin -------------------------------------------------------


def _origin_allowed(origin: str | None) -> bool:
    """Whether a websocket handshake from `origin` may proceed.

    A missing Origin header is allowed: browsers always send one, so its
    absence means a non-browser client (a script, a health probe), which has
    no ambient credentials to abuse. A *present* origin has to match the
    allowlist exactly, including scheme and port.
    """
    if not origin:
        return True
    allowed = get_settings().cors_origins
    if "*" in allowed:
        return True
    parts = urlsplit(origin)
    candidate = f"{parts.scheme}://{parts.netloc}".lower()
    return any(candidate == a.rstrip("/").lower() for a in allowed)


async def reject_foreign_origin(websocket: WebSocket) -> bool:
    """Close the handshake if it comes from a page this deployment does not
    serve. Returns True when the socket was closed and the caller must return.
    """
    if _origin_allowed(websocket.headers.get("origin")):
        return False
    await websocket.close(code=4403, reason="Origin not allowed")
    return True


# --- websocket token transport ---------------------------------------------

#: The subprotocol name the browser offers alongside the token. Chosen to be
#: unmistakably ours: a proxy that strips unknown subprotocols will strip
#: both, and the connect then fails loudly instead of silently anonymously.
BEARER_SUBPROTOCOL = "loom.bearer"


def websocket_token(websocket: WebSocket, query_token: str | None) -> tuple[str | None, str | None]:
    """The access token for a websocket, and the subprotocol to accept with.

    Prefers `Sec-WebSocket-Protocol: loom.bearer, <token>`. Falls back to the
    `?token=` query parameter for clients that have not been updated. The
    second value is what `websocket.accept(subprotocol=...)` must be given:
    a browser that offered a subprotocol drops the connection unless the
    server selects one of them.
    """
    raw = websocket.headers.get("sec-websocket-protocol", "")
    offered = [p.strip() for p in raw.split(",") if p.strip()]
    if BEARER_SUBPROTOCOL in offered:
        others = [p for p in offered if p != BEARER_SUBPROTOCOL]
        token = others[0] if others else None
        return (token or query_token), BEARER_SUBPROTOCOL
    return query_token, None


# --- filenames --------------------------------------------------------------

_UNSAFE = re.compile(r"[^A-Za-z0-9._\- ()\[\]]+")


def safe_filename(name: str | None, fallback: str = "upload") -> str:
    """A filename that is only a filename.

    Strips any directory component (either separator), normalises unicode,
    drops control characters and everything outside a conservative set, and
    refuses the two names that mean "not this directory". The extension is
    kept, because the type checks downstream read it.
    """
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFKC", base)
    base = "".join(ch for ch in base if ch.isprintable())
    base = _UNSAFE.sub("_", base).strip(" .")
    if base in ("", ".", ".."):
        return fallback
    return base[:180]


# --- client address ---------------------------------------------------------


def client_key(request: Request) -> str:
    """A rate-limit key for the caller's network address.

    Honours a single `X-Forwarded-For` hop, which is what a reverse proxy in
    front of this service sets; anything further left in that header was
    written by the client and is not trusted.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


def websocket_client_key(websocket: WebSocket) -> str:
    forwarded = websocket.headers.get("x-forwarded-for")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return websocket.client.host if websocket.client else "unknown"


def too_many(retry_after: int, what: str = "requests") -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Too many {what}. Try again in {retry_after}s.",
        headers={"Retry-After": str(retry_after)},
    )
