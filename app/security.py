"""CSRF, rate limiting and admin basic-auth helpers.

Single instance, so the rate limiter is a plain in-memory sliding window keyed by
client IP. CSRF uses a signed stateless token (itsdangerous) embedded in the form
and mirrored in a cookie (double submit): an attacker's site cannot forge a token
signed with our secret, and cannot read our cookie.
"""
from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer

CSRF_COOKIE = "csrf_token"
CSRF_FIELD = "_csrf"
_CSRF_MAX_AGE = 3600


class Security:
    def __init__(self, secret_key: str, admin_user: str, admin_password: str,
                 rate_limit_per_min: int):
        self._serializer = URLSafeTimedSerializer(secret_key, salt="csrf")
        self._admin_user = admin_user
        self._admin_password = admin_password
        self._limit = rate_limit_per_min
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    # -- CSRF --------------------------------------------------------------- #
    def issue_csrf(self) -> str:
        return self._serializer.dumps(secrets.token_urlsafe(16))

    def verify_csrf(self, cookie_token: str | None, form_token: str | None) -> None:
        if not cookie_token or not form_token:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF token. Reload the page.")
        if not secrets.compare_digest(cookie_token, form_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token mismatch. Reload the page.")
        try:
            self._serializer.loads(form_token, max_age=_CSRF_MAX_AGE)
        except BadSignature as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token. Reload the page.") from exc

    # -- rate limit --------------------------------------------------------- #
    def client_ip(self, request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def check_rate(self, request: Request) -> None:
        ip = self.client_ip(request)
        now = time.monotonic()
        window = self._hits[ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self._limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many signups from your network. Wait a minute and try again.",
            )
        window.append(now)

    # -- admin basic auth --------------------------------------------------- #
    def check_admin(self, request: Request) -> None:
        import base64
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
                user, _, pw = decoded.partition(":")
                ok = (
                    secrets.compare_digest(user, self._admin_user)
                    and secrets.compare_digest(pw, self._admin_password)
                )
            except Exception:  # noqa: BLE001 - any decode error => unauthorized
                ok = False
        if not ok:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Authentication required",
                headers={"WWW-Authenticate": 'Basic realm="admin"'},
            )
