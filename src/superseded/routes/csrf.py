from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
EXEMPT_PATHS = {"/health", "/static"}


def _generate_csrf_token() -> str:
    return secrets.token_hex(32)


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip CSRF for exempt paths and static files
        if path in EXEMPT_PATHS or path.startswith("/static/"):
            return await call_next(request)

        # Skip CSRF if API key auth is used
        config = getattr(request.app.state, "config", None)
        if config and config.api_key and request.headers.get("X-API-Key"):
            return await call_next(request)

        # Safe methods don't need CSRF
        if request.method in SAFE_METHODS:
            response = await call_next(request)
            # Set CSRF cookie on GET requests if not present
            if request.method == "GET" and "csrf_token" not in request.cookies:
                token = _generate_csrf_token()
                response.set_cookie(
                    "csrf_token",
                    token,
                    httponly=False,
                    samesite="lax",
                    secure=request.url.scheme == "https",
                )
            return response

        # Validate CSRF token on unsafe methods
        csrf_cookie = request.cookies.get("csrf_token", "")
        csrf_header = request.headers.get("X-CSRF-Token", "")

        if not csrf_cookie or csrf_header != csrf_cookie:
            return JSONResponse(status_code=403, content={"error": "CSRF validation failed"})

        return await call_next(request)
