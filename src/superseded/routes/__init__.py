from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"


@lru_cache
def get_templates() -> Jinja2Templates:
    return Jinja2Templates(directory=str(_TEMPLATES_DIR))
