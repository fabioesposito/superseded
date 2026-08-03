from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = frozenset({"critical", "important", "suggestion", "nit"})
_VALID_CONFIDENCES = frozenset({"high", "medium", "low"})


@dataclass
class Verdict:
    action: Literal["keep", "drop"]
    severity: str | None = None
    confidence: str | None = None
    reason: str = ""


def _parse_verdicts(
    raw: str, *, collect_errors: bool = False
) -> dict[str, Verdict] | tuple[list[str], dict[str, Verdict]]:
    """Parse the verifier's JSON output into a dict of ``finding_id -> Verdict``.

    ``collect_errors`` returns a tuple ``(errors, verdicts)`` for error-tolerant
    callers (e.g. logging partial parse failures). When ``False``, returns only
    the dict and logs warnings silently.

    Invalid items (missing id, unknown action, unparseable JSON) are skipped.
    Items with invalid severity strings keep the verdict but drop the severity.
    """
    verdicts: dict[str, Verdict] = {}
    errors: list[str] = []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as err:
        msg = f"Verifier output is not valid JSON: {err}"
        logger.warning(msg)
        if collect_errors:
            return [msg], {}
        return {}
    if not isinstance(items, list):
        msg = "Verifier output is not a JSON array"
        logger.warning(msg)
        if collect_errors:
            return [msg], {}
        return {}
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"verifier item is not a dict: {item!r}")
            continue
        fid = item.get("id")
        if not fid or not isinstance(fid, str):
            errors.append(f"verifier item missing string 'id': {item!r}")
            continue
        action = item.get("action")
        if action not in ("keep", "drop"):
            errors.append(f"verifier item {fid!r} has invalid action {action!r}")
            continue
        severity = item.get("severity")
        if severity is not None and (
            not isinstance(severity, str) or severity not in _VALID_SEVERITIES
        ):
            severity = None
        confidence = item.get("confidence")
        if confidence is not None and (
            not isinstance(confidence, str) or confidence not in _VALID_CONFIDENCES
        ):
            confidence = None
        verdicts[fid] = Verdict(
            action=action,
            severity=severity,
            confidence=confidence,
            reason=str(item.get("reason", "")),
        )
    if collect_errors:
        return errors, verdicts
    return verdicts
