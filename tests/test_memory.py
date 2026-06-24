from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from superseded.memory.store import MemoryStore


async def _test_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = MemoryStore(db_path)
        await store.init()

        await store.record_finding(
            finding_id="sec-abc123",
            repo="owner/repo",
            pass_name="security",
            severity="critical",
            file="a.py",
            line=42,
            title="SQL injection",
            description="desc",
        )

        findings = await store.get_dismissed_findings("owner/repo")
        assert len(findings) == 0  # not dismissed yet

        await store.record_feedback("sec-abc123", "dismiss")
        findings = await store.get_dismissed_findings("owner/repo")
        assert len(findings) == 1
        assert findings[0]["id"] == "sec-abc123"


def test_memory_store():
    asyncio.run(_test_store())
