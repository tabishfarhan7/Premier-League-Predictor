"""PitchIQ API – security helpers.

Covers:
- Request-ID middleware (accept X-Request-ID or generate one, echo it back).
- API key enforcement on /api/v1/* when PITCHIQ_API_KEY is set.
- In-memory sliding-window rate limiter (per client IP, configurable requests/min).
"""
from __future__ import annotations

import logging
import secrets
import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

log = logging.getLogger("pitchiq.api.security")


# --------------------------------------------------------------------------- #
# Request-ID middleware
# --------------------------------------------------------------------------- #
class RequestIdMiddleware(BaseHTTPMiddleware):
    """Accept X-Request-ID from the client (useful for distributed tracing) or
    generate a compact uuid4 hex.  The ID is stored in request.state and echoed
    in the response header X-Request-ID."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


# --------------------------------------------------------------------------- #
# API key dependency (FastAPI dependency, not middleware)
# --------------------------------------------------------------------------- #
def make_api_key_dependency(api_key: str | None):
    """Return a FastAPI dependency that checks X-Api-Key when api_key is set.
    When api_key is None the API is open (local dev mode).
    Uses secrets.compare_digest to prevent timing attacks."""

    from fastapi import HTTPException, Security
    from fastapi.security import APIKeyHeader

    scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def check_key(provided: str | None = Security(scheme)) -> None:
        if api_key is None:
            return  # open API
        if not provided or not secrets.compare_digest(provided, api_key):
            raise HTTPException(
                status_code=401,
                detail={
                    "error": {
                        "code": "unauthorized",
                        "message": "Missing or invalid X-API-Key header.",
                        "request_id": "",
                    }
                },
            )

    return check_key


# --------------------------------------------------------------------------- #
# In-memory sliding-window rate limiter
# --------------------------------------------------------------------------- #
class RateLimiter:
    """Per-IP sliding-window rate limiter.

    Thread-safe only via the asyncio event loop (single-threaded by design).
    Stores a deque of timestamps per IP; drops any older than 60 s on each check.
    """

    def __init__(self, requests_per_minute: int) -> None:
        self.rpm = requests_per_minute
        self._windows: dict[str, deque[float]] = defaultdict(deque)

    def is_allowed(self, ip: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds).  retry_after is 0 when allowed."""
        now = time.time()
        window = self._windows[ip]
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.rpm:
            # Oldest request in window + 60 s = when the slot opens
            retry_after = max(1, int(window[0] + 60.0 - now) + 1)
            return False, retry_after
        window.append(now)
        return True, 0


# --------------------------------------------------------------------------- #
# Helper: build a uniform error JSON response
# --------------------------------------------------------------------------- #
def error_response(
    status: int,
    code: str,
    message: str,
    request_id: str = "",
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id} if request_id else {},
    )


def get_client_ip(request: Request) -> str:
    """Best-effort client IP extraction (handles common reverse-proxy headers)."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"
