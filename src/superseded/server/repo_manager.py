from __future__ import annotations

import shutil
from pathlib import Path


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

    def job_dir(self, installation_id: int, owner: str, repo: str, pr_number: int) -> Path:
        return self.base_path / str(installation_id) / owner / repo / str(pr_number)
