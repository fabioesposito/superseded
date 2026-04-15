from __future__ import annotations

import re


class InvalidInputError(ValueError):
    """Raised when user input fails validation."""


ISSUE_ID_RE = re.compile(r"^SUP-\d{3,}$")
GIT_URL_RE = re.compile(
    r"^(https://[a-zA-Z0-9._/:~-]+|git@[a-zA-Z0-9._-]+:[a-zA-Z0-9._/-]+\.git|ssh://[a-zA-Z0-9._/:@~-]+)$"
)


def validate_issue_id(value: str) -> str:
    value = value.strip()
    if not ISSUE_ID_RE.match(value):
        raise InvalidInputError(f"Invalid issue ID: {value!r}")
    return value


def validate_git_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise InvalidInputError("Git URL cannot be empty")
    if not GIT_URL_RE.match(value):
        raise InvalidInputError(f"Invalid git URL: {value!r}")
    return value


def validate_repo_path(value: str) -> str:
    from pathlib import Path

    p = Path(value)
    if not p.is_absolute():
        raise InvalidInputError(f"Path must be absolute: {value!r}")
    resolved = p.resolve()
    if ".." in value and str(resolved) != value:
        raise InvalidInputError(f"Path traversal detected: {value!r}")
    return str(resolved)


def validate_directory_path(value: str) -> str:
    from pathlib import Path

    if not value:
        return ""
    p = Path(value)
    if not p.is_absolute():
        raise InvalidInputError(f"Path must be absolute: {value!r}")
    resolved = p.resolve()
    if ".." in value and str(resolved) != value:
        raise InvalidInputError(f"Path traversal detected: {value!r}")
    return str(resolved)


MAX_PROMPT_LENGTH = 100_000


def sanitize_agent_prompt(value: str) -> str:
    value = value.replace("\x00", "")
    if len(value) > MAX_PROMPT_LENGTH:
        value = value[:MAX_PROMPT_LENGTH]
    return value
