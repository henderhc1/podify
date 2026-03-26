from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    path_prefix: str
    methods: frozenset[str]
    max_requests: int
    window_seconds: int


RATE_LIMIT_RULES = (
    RateLimitRule("search", "/search", frozenset({"GET"}), 30, 60),
    RateLimitRule("playback", "/playback", frozenset({"GET"}), 45, 60),
    RateLimitRule("register", "/register", frozenset({"POST"}), 8, 900),
    RateLimitRule("verify", "/register/verify", frozenset({"GET"}), 20, 900),
    RateLimitRule("dmca", "/dmca/notices", frozenset({"POST"}), 5, 3600),
    RateLimitRule("admin", "/admin", frozenset({"GET", "POST", "DELETE"}), 120, 60),
)
RATE_LIMIT_HISTORY: dict[tuple[str, str], deque[float]] = defaultdict(deque)
RATE_LIMIT_LOCK = Lock()


def get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded_for:
        return forwarded_for
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rule = next(
            (
                candidate
                for candidate in RATE_LIMIT_RULES
                if request.url.path.startswith(candidate.path_prefix)
                and request.method in candidate.methods
            ),
            None,
        )
        if rule:
            now = time.monotonic()
            identifier = get_client_identifier(request)
            key = (rule.name, identifier)
            with RATE_LIMIT_LOCK:
                history = RATE_LIMIT_HISTORY[key]
                while history and now - history[0] >= rule.window_seconds:
                    history.popleft()
                if len(history) >= rule.max_requests:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": (
                                f"Rate limit exceeded for {rule.name}. Please wait and try again."
                            )
                        },
                        headers={
                            "Retry-After": str(rule.window_seconds),
                            "Cache-Control": "no-store",
                            "Pragma": "no-cache",
                        },
                    )
                history.append(now)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https://i.ytimg.com; "
            "connect-src 'self'; "
            "media-src https: blob:; "
            "frame-src 'none'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "microphone=(), payment=(), usb=()"
        )
        # Keep a narrow referrer policy while still allowing origin-level context on cross-site media loads.
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"

        return response
