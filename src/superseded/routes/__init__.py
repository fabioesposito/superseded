from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from superseded.routes.csrf import _generate_csrf_token

_TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"


def _csrf_token_for_request(request) -> str:
    """Get CSRF token from request cookies, generating one if missing.

    Caches in request.state to ensure the same token is used across
    middleware, route handlers, and Jinja2 templates.
    """
    cached = getattr(request.state, "csrf_token", None)
    if cached:
        return cached
    token = request.cookies.get("csrf_token") or _generate_csrf_token()
    request.state.csrf_token = token
    return token


@lru_cache
def get_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.globals["csrf_token_for_request"] = _csrf_token_for_request
    return templates
