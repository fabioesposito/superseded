from __future__ import annotations

import json

from superseded.models import ReviewResult


def format_json(result: ReviewResult) -> str:
    data = {
        "findings": [f.model_dump() for f in result.findings],
        "warnings": list(result.warnings),
    }
    return json.dumps(data, indent=2)
