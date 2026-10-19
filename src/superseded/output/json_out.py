from __future__ import annotations

import json

from superseded.models import ReviewResult


def format_json(result: ReviewResult) -> str:
    data = {
        "findings": [f.model_dump() for f in result.findings],
        "warnings": list(result.warnings),
        "carried_findings": [f.model_dump() for f in result.carried_findings],
        "round_number": result.round_number,
        "at_round_cap": result.at_round_cap,
        "suppressed_count": result.suppressed_count,
        "convergence_note": result.convergence_note,
    }
    return json.dumps(data, indent=2)
