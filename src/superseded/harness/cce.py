from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CCESearchResult:
    file: str
    chunk: str
    score: float
    compressed: str


class CCEClient:
    def __init__(self, repo_path: str, cce_bin: str = "cce") -> None:
        self.repo_path = Path(repo_path)
        self.cce_bin = cce_bin
        self._available = shutil.which(cce_bin) is not None

    @property
    def available(self) -> bool:
        return self._available

    async def _run(self, *args: str, timeout: float = 60.0) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cce_bin, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.repo_path),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                logger.warning("cce %s failed: %s", args[0], stderr.decode()[:200])
                return ""
            return stdout.decode()
        except TimeoutError:
            logger.warning("cce %s timed out after %ds", args[0], int(timeout))
            return ""
        except FileNotFoundError:
            logger.warning("cce binary not found: %s", self.cce_bin)
            return ""

    async def index(self) -> bool:
        result = await self._run("index", timeout=120.0)
        return "Indexed" in result or "indexed" in result

    async def reindex(self) -> bool:
        result = await self._run("reindex", timeout=120.0)
        return bool(result)

    async def search(self, query: str, top_k: int = 10) -> list[CCESearchResult]:
        result = await self._run("search", query, "--top-k", str(top_k))
        return self._parse_search_results(result)

    async def session_recall(self, topic: str = "") -> str:
        return await self._run("sessions", "export", timeout=30.0)

    def is_indexed(self) -> bool:
        return (self.repo_path / ".context-engine").exists()

    def is_stale(self, max_age_minutes: int = 60) -> bool:
        index_dir = self.repo_path / ".context-engine"
        if not index_dir.exists():
            return True
        db_file = index_dir / "index.db"
        if not db_file.exists():
            return True
        return (time.time() - db_file.stat().st_mtime) > max_age_minutes * 60

    def _parse_search_results(self, raw: str) -> list[CCESearchResult]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                return []
            return [
                CCESearchResult(
                    file=item.get("file", ""),
                    chunk=item.get("chunk", ""),
                    score=item.get("score", 0.0),
                    compressed=item.get("compressed", ""),
                )
                for item in data
            ]
        except (json.JSONDecodeError, TypeError):
            return []
