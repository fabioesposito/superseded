from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from superseded.memory.feedback import check_pr_feedback
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


@patch("subprocess.run")
def test_check_pr_feedback(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='[{"id": 1, "body": "test", "path": "a.py", "line": 1}]',
    )
    feedback = check_pr_feedback(pr=123, repo="owner/repo")
    assert isinstance(feedback, list)
