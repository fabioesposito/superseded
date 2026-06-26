from __future__ import annotations

import re
import shutil
from pathlib import Path

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_segment(value: str, label: str) -> str:
    if not value or "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"invalid {label}: {value!r}")
    if not _SAFE_NAME_RE.match(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


class RepoManager:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def disk_usage(self) -> float:
        usage = shutil.disk_usage(str(self.base_path))
        return usage.used / usage.total if usage.total > 0 else 0.0

    def cleanup(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def job_dir(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        pr_number: int,
        job_id: str = "",
    ) -> Path:
        if installation_id < 0:
            raise ValueError(f"invalid installation_id: {installation_id!r}")
        if pr_number < 0:
            raise ValueError(f"invalid pr: {pr_number!r}")
        _validate_segment(owner, "owner")
        _validate_segment(repo, "repo")
        pr_dir = self.base_path / str(installation_id) / owner / repo / str(pr_number)
        if job_id:
            _validate_segment(job_id, "job_id")
            candidate = pr_dir / job_id
        else:
            candidate = pr_dir
        resolved = candidate.resolve()
        base_resolved = self.base_path.resolve()
        if not resolved.is_relative_to(base_resolved):
            raise ValueError(f"job_dir escapes base_path: {resolved}")
        return candidate
