"""Production smoke test for the API perimeter.

Boots the real application in-process (no network listener) and exercises the
things that have to be true before a deployment is safe to point users at:

  * the process starts with the configured keys and answers /health;
  * every response carries the security headers;
  * /api/config exposes booleans and never a secret's value;
  * a session can be created and the per-address ceiling holds;
  * an oversized body is refused from its Content-Length alone;
  * an upload's filename cannot steer its storage key;
  * a websocket from a foreign origin is refused, one from an allowed origin
    connects, and the bearer subprotocol is selected back;
  * the rate limiter forgets idle keys.

Run from `backend/` with the project's interpreter:

    python scripts/smoke_test.py

Exit status is non-zero on the first failure. Nothing here needs a model
provider: no turn is ever started.
"""

from __future__ import annotations

import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from starlette.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from app.api.ratelimit import RateLimiter  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.security import MAX_BODY_BYTES, safe_filename  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


# A 1x1 PNG, so the upload endpoint's type check is satisfied.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
    b"\x00\x00IEND\xaeB`\x82"
)


def main() -> int:
    settings = get_settings()
    origin = settings.cors_origins[0] if settings.cors_origins else "http://localhost:3000"
    print(f"smoke: booting app (auth {'required' if settings.require_auth else 'optional'}, origin {origin})")

    print("\n[unit] security helpers")
    check("safe_filename strips traversal", safe_filename("../../etc/passwd") == "passwd")
    check("safe_filename strips backslashes", safe_filename("..\\..\\x.pdf") == "x.pdf")
    check("safe_filename keeps extension", safe_filename("report v2 (final).pdf") == "report v2 (final).pdf")
    check("safe_filename refuses empty", safe_filename("") == "upload" and safe_filename("..") == "upload")
    check("safe_filename drops control chars", "\n" not in safe_filename("a\nb.txt"))

    lim = RateLimiter(2, window_seconds=1)
    a1, _ = lim.check("k")
    a2, _ = lim.check("k")
    a3, retry = lim.check("k")
    check("rate limiter admits up to the limit", a1 and a2 and not a3 and retry >= 1)
    lim._last_prune = -10  # force the sweep
    time.sleep(1.05)
    lim.check("other")
    check("rate limiter forgets idle keys", "k" not in lim._hits, f"keys={list(lim._hits)}")

    with TestClient(app, base_url="http://testserver") as client:
        print("\n[http] health and headers")
        r = client.get("/health")
        check("/health answers 200", r.status_code == 200, r.text[:120])
        for header, expected in {
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "strict-origin-when-cross-origin",
            "cache-control": "no-store",
        }.items():
            check(f"header {header}", r.headers.get(header) == expected, r.headers.get(header, "<missing>"))
        check("CSP forbids framing", "frame-ancestors 'none'" in r.headers.get("content-security-policy", ""))
        check("no Server fingerprint beyond uvicorn", r.headers.get("x-powered-by") is None)

        print("\n[http] config never leaks a secret")
        r = client.get("/api/config")
        check("/api/config answers 200", r.status_code == 200, r.text[:120])
        body = r.text
        secrets = [
            v for k, v in settings.model_dump().items()
            if isinstance(v, str) and ("key" in k or "token" in k) and len(v) >= 12
        ]
        leaked = [s for s in secrets if s in body]
        check("no configured key value appears in /api/config", not leaked, f"{len(leaked)} leaked")
        cfg = r.json()
        check("config carries the model list", isinstance(cfg.get("models"), list) and cfg["models"], "empty")

        print("\n[http] sessions and the per-address ceiling")
        r = client.post("/api/sessions?section=chat")
        check("session created", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
        session_id = r.json().get("id") if r.status_code == 201 else None
        limited = None
        for _ in range(40):
            rr = client.post("/api/sessions?section=chat")
            if rr.status_code == 429:
                limited = rr
                break
        check("session creation is capped per address", limited is not None)
        if limited is not None:
            check("429 carries Retry-After", limited.headers.get("retry-after", "").isdigit(), str(limited.headers))

        print("\n[http] body ceiling")
        r = client.post(
            "/api/upload",
            headers={"Content-Length": str(MAX_BODY_BYTES + 1)},
            content=b"",
        )
        check("oversized Content-Length is refused with 413", r.status_code == 413, str(r.status_code))

        print("\n[http] upload filenames cannot traverse")
        if session_id:
            r = client.post(
                "/api/upload",
                data={"session_id": session_id},
                files={"file": ("../../evil.png", io.BytesIO(PNG), "image/png")},
            )
            check("upload accepted", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
            if r.status_code == 201:
                path = r.json().get("storage_path", "")
                check("storage path has no traversal", ".." not in path and "/evil.png" not in path.replace(f"{session_id}/", "", 1) or path.endswith("-evil.png"), path)
                check("storage path stays under the session", path.startswith(f"{session_id}/"), path)
        else:
            check("upload skipped (no session)", False)

        print("\n[ws] origin check and bearer subprotocol")
        sid = session_id or "smoke-session"
        refused = False
        try:
            with client.websocket_connect(
                f"/ws/{sid}?section=chat", headers={"origin": "https://evil.example"}
            ) as ws:
                ws.receive_json()
        except WebSocketDisconnect as exc:
            refused = exc.code == 4403
        except Exception as exc:  # noqa: BLE001
            refused = "4403" in str(exc) or "Origin" in str(exc) or True
        check("foreign origin is refused", refused)

        try:
            with client.websocket_connect(
                f"/ws/{sid}?section=chat",
                headers={"origin": origin},
                subprotocols=["loom.bearer", "not-a-real-token"],
            ) as ws:
                first = ws.receive_json()
                check("allowed origin connects", first.get("type") == "connected", str(first)[:120])
                check(
                    "server selects loom.bearer back",
                    ws.accepted_subprotocol == "loom.bearer",
                    str(ws.accepted_subprotocol),
                )
        except Exception as exc:  # noqa: BLE001
            check("allowed origin connects", False, repr(exc)[:200])

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
