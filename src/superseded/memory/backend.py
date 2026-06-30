from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from superseded.memory.store import MemoryStore


@runtime_checkable
class Store(Protocol):
    """The persistence surface consumed by the server path.

    `MemoryStore` (SQLite) and `PostgresStore` both satisfy this structurally.
    The local CLI path uses `MemoryStore` directly and does not depend on this
    Protocol at runtime.
    """

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def init(self) -> None: ...

    async def record_finding(
        self,
        finding_id: str,
        repo: str,
        pass_name: str,
        severity: str,
        file: str,
        line: int,
        title: str,
        description: str,
        reasoning: str = "",
    ) -> None: ...

    async def set_comment_id(self, finding_id: str, comment_id: int) -> None: ...
    async def get_finding_by_comment_id(self, comment_id: int) -> dict | None: ...
    async def record_feedback(self, finding_id: str, action: str) -> None: ...
    async def record_feedback_by_comment_id(self, comment_id: int, action: str) -> bool: ...
    async def get_dismissed_findings(self, repo: str) -> list[dict]: ...

    async def record_installation(
        self, installation_id: int, owner: str, repos: list[str]
    ) -> None: ...
    async def get_installation(self, installation_id: int) -> dict | None: ...
    async def remove_installation(self, installation_id: int) -> None: ...

    async def get_watermark(self, repo: str, pr_number: int) -> str | None: ...
    async def set_watermark(self, repo: str, pr_number: int, head_sha: str) -> None: ...

    async def get_learned_rules(self, repo: str, limit: int = 5) -> list[dict]: ...
    async def get_reflection_state(self, repo: str) -> int: ...
    async def set_reflection_state(self, repo: str, last_feedback_id: int) -> None: ...

    async def refresh_review_stats(self, repo: str) -> None: ...
    async def get_review_stats(self, repo: str, min_sample: int) -> list[dict]: ...


def make_store(database_url: str | None, *, max_size: int | None = None) -> Store:
    """Return the appropriate store for a database URL.

    - `None` / empty / `sqlite://`        -> MemoryStore (default SQLite path)
    - `sqlite:///path/to.db`              -> MemoryStore at that path
    - `postgres://...` / `postgresql://...`-> PostgresStore
    - anything else                        -> ValueError
    """
    if not database_url:
        return MemoryStore()

    parsed = urlparse(database_url)
    scheme = parsed.scheme

    if scheme in ("", "sqlite"):
        if scheme == "sqlite" and parsed.path:
            return MemoryStore(db_path=Path(parsed.path))
        return MemoryStore()

    if scheme in ("postgres", "postgresql"):
        from superseded.memory.postgres import PostgresStore

        return PostgresStore(database_url, max_size=max_size)

    raise ValueError(f"Unsupported database scheme: {scheme!r}")
