from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

EXEMPT_PATH_PREFIXES = ("/health", "/static")


def _is_exempt(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in EXEMPT_PATH_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        config = getattr(request.app.state, "config", None)
        if config is None:
            return await call_next(request)

        api_key = config.api_key
        if not api_key:
            return await call_next(request)

        path = request.url.path
        if _is_exempt(path):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if provided != api_key:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

        return await call_next(request)
