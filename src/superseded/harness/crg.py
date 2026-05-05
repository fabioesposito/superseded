from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class CRGClient:
    def __init__(self, repo_path: str, crg_bin: str = "code-review-graph") -> None:
        self.repo_path = Path(repo_path)
        self.crg_bin = crg_bin
        self._available = shutil.which(crg_bin) is not None

    @property
    def available(self) -> bool:
        return self._available

    async def _run(self, *args: str, timeout: float = 60.0) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.crg_bin,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.repo_path),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                logger.warning("crg %s failed: %s", args[0], stderr.decode()[:200])
                return ""
            return stdout.decode()
        except TimeoutError:
            logger.warning("crg %s timed out after %ds", args[0], int(timeout))
            return ""
        except FileNotFoundError:
            logger.warning("crg binary not found: %s", self.crg_bin)
            return ""

    async def build(self) -> bool:
        result = await self._run("build", timeout=120.0)
        return bool(result)

    async def update(self) -> bool:
        result = await self._run("update", timeout=120.0)
        return bool(result)

    async def status(self) -> str:
        return await self._run("status", timeout=30.0)

    async def detect_changes(self) -> str:
        return await self._run("detect-changes", timeout=30.0)

    def is_built(self) -> bool:
        graph_dir = self.repo_path / ".code-review-graph"
        return graph_dir.exists()

    def is_stale(self, max_age_minutes: int = 60) -> bool:
        graph_dir = self.repo_path / ".code-review-graph"
        if not graph_dir.exists():
            return True
        db_file = graph_dir / "graph.db"
        if not db_file.exists():
            return True
        return (time.time() - db_file.stat().st_mtime) > max_age_minutes * 60
