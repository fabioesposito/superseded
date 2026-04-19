# Superseded Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local-first agentic pipeline tool where a solo engineer writes tickets (markdown) and delegates implementation, testing, and release to an automated pipeline powered by Claude Code and OpenCode.

**Architecture:** Monolithic FastAPI + HTMX application. Single process serves web UI and runs the pipeline engine. Agent adapters spawn CLI tools as subprocesses. In-repo `.superseded/` directories hold tickets (markdown with YAML frontmatter) and pipeline state (SQLite).

**Tech Stack:** Python 3.12+, uv, FastAPI, Uvicorn, Jinja2, HTMX, Alpine.js, Tailwind CDN, SQLite (aiosqlite), python-frontmatter, pyyaml, pytest, pytest-asyncio

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/superseded/__init__.py`

**Step 1: Initialize the project with uv**

Run:
```bash
cd /home/debian/workspace/superseded
uv init --name superseded --no-readme
```

**Step 2: Configure pyproject.toml**

Replace the generated `pyproject.toml` with:

```toml
[project]
name = "superseded"
version = "0.1.0"
description = "Local-first agentic pipeline tool — write tickets, delegate the rest"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "jinja2>=3.1.0",
    "python-multipart>=0.0.18",
    "aiosqlite>=0.21.0",
    "python-frontmatter>=1.1.0",
    "pyyaml>=6.0.0",
    "pydantic>=2.10.0",
    "sse-starlette>=2.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.25.0",
    "httpx>=0.28.0",
]

[project.scripts]
superseded = "superseded.main:cli"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/superseded"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 3: Install dependencies**

Run:
```bash
cd /home/debian/workspace/superseded && uv sync
```

**Step 4: Create the package directories**

Run:
```bash
mkdir -p /home/debian/workspace/superseded/src/superseded/{pipeline,agents,tickets,routes}
mkdir -p /home/debian/workspace/superseded/tests
mkdir -p /home/debian/workspace/superseded/templates/components
mkdir -p /home/debian/workspace/superseded/static
touch /home/debian/workspace/superseded/src/superseded/__init__.py
touch /home/debian/workspace/superseded/src/superseded/{pipeline,agents,tickets,routes}/__init__.py
touch /home/debian/workspace/superseded/tests/__init__.py
```

**Step 5: Verify the project runs**

Run:
```bash
cd /home/debian/workspace/superseded && uv run python -c "import superseded; print('OK')"
```
Expected: `OK`

**Step 6: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: project scaffolding with uv"
```

---

### Task 2: Pydantic Models

**Files:**
- Create: `src/superseded/models.py`
- Create: `tests/test_models.py`

**Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
from superseded.models import Issue, IssueStatus, Stage, StageResult, AgentResult, AgentContext


def test_issue_from_frontmatter():
    content = """---
id: SUP-001
title: Refactor auth module
status: in-progress
stage: build
created: "2026-04-11"
assignee: claude-code
labels:
  - backend
  - security
---

## Description
Refactor the auth module.
"""
    issue = Issue.from_frontmatter(content, filepath=".superseded/issues/SUP-001-refactor-auth.md")
    assert issue.id == "SUP-001"
    assert issue.title == "Refactor auth module"
    assert issue.status == IssueStatus.IN_PROGRESS
    assert issue.stage == Stage.BUILD
    assert issue.assignee == "claude-code"
    assert "backend" in issue.labels


def test_issue_defaults():
    issue = Issue(id="SUP-999", title="Test issue", filepath=".superseded/issues/SUP-999-test.md")
    assert issue.status == IssueStatus.NEW
    assert issue.stage == Stage.SPEC
    assert issue.assignee == ""


def test_stage_result_pass():
    result = StageResult(stage=Stage.BUILD, passed=True, output="done", artifacts=["src/auth.py"])
    assert result.passed is True
    assert result.stage == Stage.BUILD


def test_agent_result():
    result = AgentResult(exit_code=0, stdout="ok", stderr="", files_changed=["src/main.py"])
    assert result.exit_code == 0
    assert len(result.files_changed) == 1


def test_agent_context():
    ctx = AgentContext(
        repo_path="/tmp/myrepo",
        issue=Issue(id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"),
        skill_prompt="You are a planner...",
        artifacts_path=".superseded/artifacts/SUP-001",
    )
    assert ctx.repo_path == "/tmp/myrepo"
    assert ctx.skill_prompt == "You are a planner..."
```

**Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_models.py -v`
Expected: FAIL — `ImportError` or `ModuleNotFoundError`

**Step 3: Write the implementation**

Create `src/superseded/models.py`:

```python
from __future__ import annotations

import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field


class IssueStatus(str, Enum):
    NEW = "new"
    IN_PROGRESS = "in-progress"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"


class Stage(str, Enum):
    SPEC = "spec"
    PLAN = "plan"
    BUILD = "build"
    VERIFY = "verify"
    REVIEW = "review"
    SHIP = "ship"


STAGE_ORDER: list[Stage] = [Stage.SPEC, Stage.PLAN, Stage.BUILD, Stage.VERIFY, Stage.REVIEW, Stage.SHIP]


class Issue(BaseModel):
    id: str
    title: str
    status: IssueStatus = IssueStatus.NEW
    stage: Stage = Stage.SPEC
    created: datetime.date = Field(default_factory=datetime.date.today)
    assignee: str = ""
    labels: list[str] = Field(default_factory=list)
    filepath: str = ""

    @classmethod
    def from_frontmatter(cls, content: str, filepath: str = "") -> "Issue":
        post = frontmatter.loads(content)
        return cls(
            id=post.get("id", "SUP-000"),
            title=post.get("title", "Untitled"),
            status=IssueStatus(post.get("status", "new")),
            stage=Stage(post.get("stage", "spec")),
            created=post.get("created", datetime.date.today()),
            assignee=post.get("assignee", ""),
            labels=post.get("labels", []),
            filepath=filepath,
        )

    def next_stage(self) -> Stage | None:
        idx = STAGE_ORDER.index(self.stage)
        if idx + 1 < len(STAGE_ORDER):
            return STAGE_ORDER[idx + 1]
        return None


class StageResult(BaseModel):
    stage: Stage
    passed: bool
    output: str = ""
    error: str = ""
    artifacts: list[str] = Field(default_factory=list)
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None


class AgentResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    files_changed: list[str] = Field(default_factory=list)


class AgentContext(BaseModel):
    repo_path: str
    issue: Issue
    skill_prompt: str
    artifacts_path: str = ""
```

**Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_models.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: pydantic models for issue, stage, agent"
```

---

### Task 3: Configuration Module

**Files:**
- Create: `src/superseded/config.py`
- Create: `tests/test_config.py`

**Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import tempfile
from pathlib import Path

import yaml

from superseded.config import SupersededConfig, load_config


def write_yaml_config(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def test_default_config():
    config = SupersededConfig()
    assert config.default_agent == "claude-code"
    assert config.stage_timeout_seconds == 600
    assert config.port == 8000


def test_load_config_from_file():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(config_path, {
            "default_agent": "opencode",
            "stage_timeout_seconds": 300,
            "repo_path": tmp,
        })
        config = load_config(Path(tmp))
        assert config.default_agent == "opencode"
        assert config.stage_timeout_seconds == 300
        assert config.repo_path == tmp


def test_load_config_missing_file_uses_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        config = load_config(Path(tmp))
        assert config.default_agent == "claude-code"


def test_load_config_partial_override():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / ".superseded" / "config.yaml"
        write_yaml_config(config_path, {"port": 9000})
        config = load_config(Path(tmp))
        assert config.port == 9000
        assert config.default_agent == "claude-code"
```

**Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_config.py -v`
Expected: FAIL

**Step 3: Write the implementation**

Create `src/superseded/config.py`:

```python
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class SupersededConfig(BaseModel):
    default_agent: str = "claude-code"
    stage_timeout_seconds: int = 600
    repo_path: str = ""
    port: int = 8000
    host: str = "127.0.0.1"
    db_path: str = ".superseded/state.db"
    issues_dir: str = ".superseded/issues"
    artifacts_dir: str = ".superseded/artifacts"


def load_config(repo_path: Path) -> SupersededConfig:
    config_file = repo_path / ".superseded" / "config.yaml"
    overrides: dict = {}
    if config_file.exists():
        with open(config_file) as f:
            overrides = yaml.safe_load(f) or {}
    overrides.setdefault("repo_path", str(repo_path))
    return SupersededConfig(**overrides)
```

**Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_config.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: configuration module with yaml loading"
```

---

### Task 4: Ticket Reader and Writer

**Files:**
- Create: `src/superseded/tickets/reader.py`
- Create: `src/superseded/tickets/writer.py`
- Create: `tests/test_tickets.py`

**Step 1: Write the failing test**

Create `tests/test_tickets.py`:

```python
import tempfile
from pathlib import Path

from superseded.models import Issue, IssueStatus, Stage
from superseded.tickets.reader import list_issues, read_issue
from superseded.tickets.writer import write_issue


SAMPLE_TICKET = """---
id: SUP-001
title: Add rate limiting
status: new
stage: spec
created: "2026-04-11"
assignee: ""
labels:
  - backend
---

## Description
Add rate limiting to the API.

## Acceptance Criteria
- [ ] Rate limiter middleware added
- [ ] Configurable limits per endpoint
"""


def test_write_and_read_issue():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        issues_dir = repo / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)

        filepath = str(issues_dir / "SUP-001-add-rate-limiting.md")
        write_issue(filepath, SAMPLE_TICKET)

        assert Path(filepath).exists()
        issue = read_issue(filepath)
        assert issue.id == "SUP-001"
        assert issue.title == "Add rate limiting"
        assert issue.stage == Stage.SPEC


def test_list_issues():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        issues_dir = repo / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)

        write_issue(str(issues_dir / "SUP-001-add-rate-limiting.md"), SAMPLE_TICKET)
        write_issue(str(issues_dir / "SUP-002-fix-bug.md"), SAMPLE_TICKET.replace("SUP-001", "SUP-002").replace("Add rate limiting", "Fix bug"))

        issues = list_issues(str(issues_dir))
        assert len(issues) == 2
        ids = {i.id for i in issues}
        assert "SUP-001" in ids
        assert "SUP-002" in ids


def test_list_issues_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        issues_dir = Path(tmp) / "issues"
        issues_dir.mkdir()
        issues = list_issues(str(issues_dir))
        assert issues == []


def test_write_issue_updates_frontmatter():
    with tempfile.TemporaryDirectory() as tmp:
        issues_dir = Path(tmp) / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)
        filepath = str(issues_dir / "SUP-001-add-rate-limiting.md")

        write_issue(filepath, SAMPLE_TICKET)
        issue = read_issue(filepath)
        assert issue.status == IssueStatus.NEW

        from superseded.tickets.writer import update_issue_status
        update_issue_status(filepath, IssueStatus.IN_PROGRESS, Stage.BUILD)
        updated = read_issue(filepath)
        assert updated.status == IssueStatus.IN_PROGRESS
        assert updated.stage == Stage.BUILD
```

**Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_tickets.py -v`
Expected: FAIL

**Step 3: Write the implementation**

Create `src/superseded/tickets/reader.py`:

```python
from __future__ import annotations

from pathlib import Path

import frontmatter

from superseded.models import Issue, Stage, IssueStatus


def read_issue(filepath: str) -> Issue:
    path = Path(filepath)
    post = frontmatter.load(path)
    return Issue(
        id=post.get("id", "SUP-000"),
        title=post.get("title", "Untitled"),
        status=IssueStatus(post.get("status", "new")),
        stage=Stage(post.get("stage", "spec")),
        created=post.get("created", ""),
        assignee=post.get("assignee", ""),
        labels=post.get("labels", []),
        filepath=str(path),
    )


def list_issues(issues_dir: str) -> list[Issue]:
    path = Path(issues_dir)
    if not path.exists():
        return []
    issues: list[Issue] = []
    for md_file in sorted(path.glob("*.md")):
        issues.append(read_issue(str(md_file)))
    return issues
```

Create `src/superseded/tickets/writer.py`:

```python
from __future__ import annotations

from pathlib import Path

import frontmatter
from pydantic import AliasPath

from superseded.models import Issue, IssueStatus, Stage


def write_issue(filepath: str, content: str) -> None:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def update_issue_status(filepath: str, status: IssueStatus, stage: Stage) -> None:
    path = Path(filepath)
    post = frontmatter.load(path)
    post["status"] = status.value
    post["stage"] = stage.value
    path.write_text(frontmatter.dumps(post))
```

**Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_tickets.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: ticket reader and writer"
```

---

### Task 5: SQLite Database Layer

**Files:**
- Create: `src/superseded/db.py`
- Create: `tests/test_db.py`

**Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
import tempfile
from pathlib import Path

from superseded.db import Database
from superseded.models import Issue, IssueStatus, Stage, StageResult


async def test_db_initialize_and_operations():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-001", title="Test issue", filepath=".superseded/issues/SUP-001-test.md")
        await db.upsert_issue(issue)

        fetched = await db.get_issue("SUP-001")
        assert fetched is not None
        assert fetched["id"] == "SUP-001"
        assert fetched["title"] == "Test issue"
        assert fetched["status"] == "new"
        assert fetched["stage"] == "spec"

        all_issues = await db.list_issues()
        assert len(all_issues) == 1

        result = StageResult(stage=Stage.BUILD, passed=True, output="built successfully", artifacts=["src/main.py"])
        await db.save_stage_result("SUP-001", result)

        results = await db.get_stage_results("SUP-001")
        assert len(results) == 1
        assert results[0]["stage"] == "build"
        assert results[0]["passed"] is True

        await db.close()


async def test_db_update_issue_status():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-002", title="Another issue", filepath=".superseded/issues/SUP-002-another.md")
        await db.upsert_issue(issue)

        await db.update_issue_status("SUP-002", IssueStatus.IN_PROGRESS, Stage.BUILD)
        fetched = await db.get_issue("SUP-002")
        assert fetched["status"] == "in-progress"
        assert fetched["stage"] == "build"

        await db.close()
```

**Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_db.py -v`
Expected: FAIL

**Step 3: Write the implementation**

Create `src/superseded/db.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from superseded.models import Issue, IssueStatus, Stage, StageResult


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS issues (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                stage TEXT NOT NULL DEFAULT 'spec',
                assignee TEXT DEFAULT '',
                labels TEXT DEFAULT '[]',
                filepath TEXT DEFAULT '',
                created TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS stage_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                passed INTEGER NOT NULL,
                output TEXT DEFAULT '',
                error TEXT DEFAULT '',
                artifacts TEXT DEFAULT '[]',
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY (issue_id) REFERENCES issues(id)
            );
        """)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def upsert_issue(self, issue: Issue) -> None:
        assert self._conn
        await self._conn.execute(
            """INSERT INTO issues (id, title, status, stage, assignee, labels, filepath, created)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET title=?, status=?, stage=?, assignee=?, labels=?, filepath=?, updated_at=CURRENT_TIMESTAMP""",
            (issue.id, issue.title, issue.status.value, issue.stage.value,
             issue.assignee, json.dumps(issue.labels), issue.filepath, str(issue.created),
             issue.title, issue.status.value, issue.stage.value, issue.assignee,
             json.dumps(issue.labels), issue.filepath),
        )
        await self._conn.commit()

    async def get_issue(self, issue_id: str) -> dict[str, Any] | None:
        assert self._conn
        cursor = await self._conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in cursor.description]
        result = dict(zip(cols, row))
        result["labels"] = json.loads(result["labels"])
        return result

    async def list_issues(self) -> list[dict[str, Any]]:
        assert self._conn
        cursor = await self._conn.execute("SELECT * FROM issues ORDER BY id")
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["labels"] = json.loads(d["labels"])
            results.append(d)
        return results

    async def update_issue_status(self, issue_id: str, status: IssueStatus, stage: Stage) -> None:
        assert self._conn
        await self._conn.execute(
            "UPDATE issues SET status=?, stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status.value, stage.value, issue_id),
        )
        await self._conn.commit()

    async def save_stage_result(self, issue_id: str, result: StageResult) -> None:
        assert self._conn
        await self._conn.execute(
            """INSERT INTO stage_results (issue_id, stage, passed, output, error, artifacts, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (issue_id, result.stage.value, int(result.passed), result.output, result.error,
             json.dumps(result.artifacts), str(result.started_at) if result.started_at else None,
             str(result.finished_at) if result.finished_at else None),
        )
        await self._conn.commit()

    async def get_stage_results(self, issue_id: str) -> list[dict[str, Any]]:
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT * FROM stage_results WHERE issue_id = ? ORDER BY id", (issue_id,)
        )
        rows = await cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["passed"] = bool(d["passed"])
            d["artifacts"] = json.loads(d["artifacts"])
            results.append(d)
        return results
```

**Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_db.py -v`
Expected: All 2 tests PASS

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: sqlite database layer for pipeline state"
```

---

### Task 6: Agent Adapters

**Files:**
- Create: `src/superseded/agents/base.py`
- Create: `src/superseded/agents/claude_code.py`
- Create: `src/superseded/agents/opencode.py`
- Create: `tests/test_agents.py`

**Step 1: Write the failing test**

Create `tests/test_agents.py`:

```python
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from superseded.agents.base import AgentAdapter, AgentContext, AgentResult
from superseded.agents.claude_code import ClaudeCodeAdapter
from superseded.agents.opencode import OpenCodeAdapter
from superseded.models import Issue


def _make_context(tmp: str) -> AgentContext:
    return AgentContext(
        repo_path=tmp,
        issue=Issue(id="SUP-001", title="Test", filepath=".superseded/issues/SUP-001-test.md"),
        skill_prompt="Write a plan for this feature.",
        artifacts_path=".superseded/artifacts/SUP-001",
    )


async def test_claude_code_adapter_builds_command():
    ctx = _make_context("/tmp/repo")
    adapter = ClaudeCodeAdapter()
    cmd_parts = adapter._build_command(ctx)
    assert "claude" in cmd_parts[0] or "claude" in " ".join(cmd_parts)
    assert "--print" in cmd_parts or "-p" in cmd_parts


async def test_opencode_adapter_builds_command():
    ctx = _make_context("/tmp/repo")
    adapter = OpenCodeAdapter()
    cmd_parts = adapter._build_command(ctx)
    assert "opencode" in cmd_parts[0] or "opencode" in " ".join(cmd_parts)


async def test_adapter_protocol_enforced():
    assert hasattr(ClaudeCodeAdapter, "run")
    assert hasattr(OpenCodeAdapter, "run")
    assert isinstance(ClaudeCodeAdapter(), AgentAdapter) is True
    assert isinstance(OpenCodeAdapter(), AgentAdapter) is True


async def test_adapter_timeout_default():
    adapter = ClaudeCodeAdapter()
    assert adapter.timeout == 600


async def test_adapter_timeout_custom():
    adapter = ClaudeCodeAdapter(timeout=300)
    assert adapter.timeout == 300


async def test_agent_result_model():
    result = AgentResult(exit_code=0, stdout="done", stderr="", files_changed=["a.py"])
    assert result.exit_code == 0
    assert result.files_changed == ["a.py"]
```

**Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_agents.py -v`
Expected: FAIL

**Step 3: Write the implementation**

Create `src/superseded/agents/base.py`:

```python
from __future__ import annotations

from typing import Protocol, runtime_checkable

from superseded.models import AgentContext, AgentResult


@runtime_checkable
class AgentAdapter(Protocol):
    timeout: int

    async def run(self, prompt: str, context: AgentContext) -> AgentResult: ...
```

Create `src/superseded/agents/claude_code.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from superseded.models import AgentContext, AgentResult


class ClaudeCodeAdapter:
    def __init__(self, timeout: int = 600) -> None:
        self.timeout = timeout

    def _build_command(self, context: AgentContext) -> list[str]:
        return [
            "claude",
            "--print",
            "--output-format", "text",
            context.skill_prompt,
        ]

    async def run(self, prompt: str, context: AgentContext) -> AgentResult:
        cmd = self._build_command(context)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=context.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            return AgentResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            proc.kill()
            return AgentResult(exit_code=-1, stdout="", stderr=f"Agent timed out after {self.timeout}s")
```

Create `src/superseded/agents/opencode.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from superseded.models import AgentContext, AgentResult


class OpenCodeAdapter:
    def __init__(self, timeout: int = 600) -> None:
        self.timeout = timeout

    def _build_command(self, context: AgentContext) -> list[str]:
        return [
            "opencode",
            "--non-interactive",
            context.skill_prompt,
        ]

    async def run(self, prompt: str, context: AgentContext) -> AgentResult:
        cmd = self._build_command(context)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=context.repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            return AgentResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            proc.kill()
            return AgentResult(exit_code=-1, stdout="", stderr=f"Agent timed out after {self.timeout}s")
```

**Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_agents.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: agent adapters for claude-code and opencode"
```

---

### Task 7: Pipeline Engine

**Files:**
- Create: `src/superseded/pipeline/engine.py`
- Create: `src/superseded/pipeline/stages.py`
- Create: `src/superseded/pipeline/prompts.py`
- Create: `tests/test_pipeline.py`

**Step 1: Write the failing test**

Create `tests/test_pipeline.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from superseded.models import AgentContext, AgentResult, Issue, IssueStatus, Stage, StageResult
from superseded.pipeline.engine import PipelineEngine
from superseded.pipeline.stages import StageDefinition, STAGE_DEFINITIONS
from superseded.pipeline.prompts import get_prompt_for_stage


def test_stage_definitions_exist():
    assert len(STAGE_DEFINITIONS) == 6
    stages = [s.stage for s in STAGE_DEFINITIONS]
    assert stages == [Stage.SPEC, Stage.PLAN, Stage.BUILD, Stage.VERIFY, Stage.REVIEW, Stage.SHIP]


def test_each_stage_has_prompt():
    for stage_def in STAGE_DEFINITIONS:
        prompt = get_prompt_for_stage(stage_def.stage)
        assert len(prompt) > 50, f"Stage {stage_def.stage} has no prompt"


def test_stage_order():
    from superseded.models import STAGE_ORDER
    assert STAGE_ORDER == [Stage.SPEC, Stage.PLAN, Stage.BUILD, Stage.VERIFY, Stage.REVIEW, Stage.SHIP]


async def test_engine_processes_stage():
    mock_agent = AsyncMock()
    mock_agent.run.return_value = AgentResult(exit_code=0, stdout="spec written", stderr="")

    engine = PipelineEngine(agent=mock_agent, repo_path="/tmp/testrepo")

    issue = Issue(id="SUP-001", title="Add rate limiting", filepath=".superseded/issues/SUP-001-add-rate-limiting.md")
    result = await engine.run_stage(issue, Stage.SPEC)

    assert result.passed is True
    assert result.stage == Stage.SPEC
    mock_agent.run.assert_called_once()


async def test_engine_halts_on_failure():
    mock_agent = AsyncMock()
    mock_agent.run.return_value = AgentResult(exit_code=1, stdout="", stderr="agent crashed")

    engine = PipelineEngine(agent=mock_agent, repo_path="/tmp/testrepo")

    issue = Issue(id="SUP-001", title="Add rate limiting", filepath=".superseded/issues/SUP-001-add-rate-limiting.md")
    result = await engine.run_stage(issue, Stage.BUILD)

    assert result.passed is False
    assert result.error == "Agent exited with code 1"
```

**Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_pipeline.py -v`
Expected: FAIL

**Step 3: Write the implementation**

Create `src/superseded/pipeline/stages.py`:

```python
from __future__ import annotations

from superseded.models import Stage


class StageDefinition:
    def __init__(self, stage: Stage, name: str, description: str, skill: str) -> None:
        self.stage = stage
        self.name = name
        self.description = description
        self.skill = skill


STAGE_DEFINITIONS: list[StageDefinition] = [
    StageDefinition(Stage.SPEC, "Spec", "Generate a detailed spec from the ticket", "spec-driven-development"),
    StageDefinition(Stage.PLAN, "Plan", "Break the spec into implementable tasks", "planning-and-task-breakdown"),
    StageDefinition(Stage.BUILD, "Build", "Implement the code changes", "incremental-implementation"),
    StageDefinition(Stage.VERIFY, "Verify", "Run tests and fix failures", "test-driven-development"),
    StageDefinition(Stage.REVIEW, "Review", "Review code quality and security", "code-review-and-quality"),
    StageDefinition(Stage.SHIP, "Ship", "Commit and create PR", "git-workflow-and-versioning"),
]
```

Create `src/superseded/pipeline/prompts.py`:

```python
from __future__ import annotations

from superseded.models import Stage

PROMPTS: dict[Stage, str] = {
    Stage.SPEC: """You are a spec writer. Read the issue description below and produce a detailed spec document.

Follow the spec-driven-development skill:
1. Define objectives, scope, and boundaries
2. List all commands, interfaces, and data structures
3. Define code style, testing requirements, and acceptance criteria
4. Be explicit about what is NOT in scope

Write the spec in markdown format. Focus on WHAT to build, not HOW to implement it.""",

    Stage.PLAN: """You are a technical planner. Read the spec below and break it into small, atomic, verifiable tasks.

Follow the planning-and-task-breakdown skill:
1. Decompose into tasks that can be verified independently
2. Order tasks by dependency (what must be done first)
3. Each task should change ~2-5 files and take 2-5 minutes
4. Include acceptance criteria for each task

Write the plan in markdown format with numbered tasks.""",

    Stage.BUILD: """You are an implementation engineer. Read the plan below and implement the code changes.

Follow the incremental-implementation skill:
1. Implement one vertical slice at a time
2. Use feature flags and safe defaults
3. Make changes rollback-friendly
4. Commit after each logical change

Implement the changes described in the plan.""",

    Stage.VERIFY: """You are a test engineer. Read the implementation below and verify it works.

Follow the test-driven-development skill:
1. Run the existing test suite
2. Write tests for any untested behavior
3. Fix any failing tests
4. Verify the build passes
5. Report a summary of test results

Run tests and fix failures until everything passes.""",

    Stage.REVIEW: """You are a code reviewer. Review the changes below for quality and security.

Follow the code-review-and-quality skill:
1. Five-axis review: correctness, complexity, consistency, naming, tests
2. Check for security issues (OWASP Top 10)
3. Change should be ~100 lines or explain why it's larger
4. Label issues as Nit / Optional / FYI
5. Approve or request changes

Write a structured review with findings.""",

    Stage.SHIP: """You are a release engineer. Ship the reviewed changes.

Follow the git-workflow-and-versioning skill:
1. Create an atomic commit with a clear message
2. Push to the remote branch
3. Create a pull request with a description of changes
4. Include test results and review notes in the PR

Commit, push, and create a PR.""",
}


def get_prompt_for_stage(stage: Stage) -> str:
    return PROMPTS[stage]
```

Create `src/superseded/pipeline/engine.py`:

```python
from __future__ import annotations

import datetime
from pathlib import Path

from superseded.agents.base import AgentAdapter
from superseded.models import AgentContext, AgentResult, Issue, Stage, StageResult


class PipelineEngine:
    def __init__(self, agent: AgentAdapter, repo_path: str, timeout: int = 600) -> None:
        self.agent = agent
        self.repo_path = repo_path
        self.timeout = timeout

    async def run_stage(self, issue: Issue, stage: Stage) -> StageResult:
        from superseded.pipeline.prompts import get_prompt_for_stage

        prompt = get_prompt_for_stage(stage)
        artifacts_path = Path(self.repo_path) / ".superseded" / "artifacts" / issue.id
        artifacts_path.mkdir(parents=True, exist_ok=True)

        context = AgentContext(
            repo_path=self.repo_path,
            issue=issue,
            skill_prompt=prompt,
            artifacts_path=str(artifacts_path),
        )

        started = datetime.datetime.now()
        agent_result: AgentResult = await self.agent.run(prompt, context)
        finished = datetime.datetime.now()

        passed = agent_result.exit_code == 0
        error = ""
        if not passed:
            error = agent_result.stderr if agent_result.stderr else f"Agent exited with code {agent_result.exit_code}"

        return StageResult(
            stage=stage,
            passed=passed,
            output=agent_result.stdout,
            error=error,
            artifacts=agent_result.files_changed,
            started_at=started,
            finished_at=finished,
        )
```

**Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_pipeline.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: pipeline engine with stages, prompts, and execution"
```

---

### Task 8: FastAPI Application and Dashboard Route

**Files:**
- Create: `src/superseded/main.py`
- Create: `src/superseded/routes/dashboard.py`
- Create: `src/superseded/routes/__init__.py` (already created)
- Create: `templates/base.html`
- Create: `templates/dashboard.html`
- Create: `static/app.js`
- Create: `tests/test_routes.py`

**Step 1: Write the failing test**

Create `tests/test_routes.py`:

```python
import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from superseded.main import create_app


@pytest.fixture
def tmp_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        issues_dir = repo / ".superseded" / "issues"
        issues_dir.mkdir(parents=True)
        (issues_dir / "SUP-001-test.md").write_text("""---
id: SUP-001
title: Test issue
status: new
stage: spec
created: "2026-04-11"
assignee: ""
labels: []
---

Test body.
""")
        yield str(repo)


async def test_dashboard_loads(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "Superseded" in response.text


async def test_dashboard_shows_issues(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "SUP-001" in response.text
        assert "Test issue" in response.text
```

**Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_routes.py -v`
Expected: FAIL

**Step 3: Write the implementation**

Create `src/superseded/main.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from superseded.config import SupersededConfig, load_config
from superseded.db import Database
from superseded.routes.dashboard import router as dashboard_router


def create_app(repo_path: str | None = None, config: SupersededConfig | None = None) -> FastAPI:
    if config is None:
        if repo_path is None:
            repo_path = str(Path.cwd())
        config = load_config(Path(repo_path))

    app = FastAPI(title="Superseded", version="0.1.0")

    app.state.config = config
    app.state.db = Database(str(Path(config.repo_path) / config.db_path))

    templates_dir = Path(__file__).parent.parent.parent / "templates"
    static_dir = Path(__file__).parent.parent.parent / "static"

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    from superseded.routes.dashboard import set_deps
    set_deps(config, app.state.db)

    app.include_router(dashboard_router)

    @app.on_event("startup")
    async def startup():
        await app.state.db.initialize()

    @app.on_event("shutdown")
    async def shutdown():
        await app.state.db.close()

    return app


def cli() -> None:
    parser = argparse.ArgumentParser(description="Superseded - agentic pipeline tool")
    parser.add_argument("repo_path", nargs="?", default=".", help="Path to the git repository")
    parser.add_argument("--port", type=int, default=None, help="Port to run the server on")
    parser.add_argument("--host", type=str, default=None, help="Host to bind to")
    args = parser.parse_args()

    config = load_config(Path(args.repo_path).resolve())
    port = args.port or config.port
    host = args.host or config.host

    import uvicorn
    uvicorn.run(f"superseded.main:create_app", host=host, port=port, factory=True, reload=False)
```

Create `src/superseded/routes/dashboard.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.models import Stage
from superseded.tickets.reader import list_issues

router = APIRouter()

_config: SupersededConfig | None = None
_db: Database | None = None

_templates_dir = Path(__file__).parent.parent.parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_templates_dir))


def set_deps(config: SupersededConfig, db: Database) -> None:
    global _config, _db
    _config = config
    _db = db


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    assert _config
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    issues = list_issues(issues_dir)
    stage_names = [s.value for s in Stage]
    return _templates.TemplateResponse("dashboard.html", {
        "request": request,
        "issues": issues,
        "stage_names": stage_names,
    })
```

Create `templates/base.html`:

```html
<!DOCTYPE html>
<html lang="en" class="h-full bg-gray-900">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Superseded{% endblock %}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <script src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.min.js" defer></script>
</head>
<body class="h-full bg-gray-900 text-gray-100">
    <nav class="bg-gray-800 border-b border-gray-700 px-6 py-3">
        <div class="flex items-center justify-between">
            <a href="/" class="text-xl font-bold text-white">Superseded</a>
            <span class="text-gray-400 text-sm">Local-first agentic pipeline</span>
        </div>
    </nav>
    <main class="max-w-7xl mx-auto px-6 py-8">
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

Create `templates/dashboard.html`:

```html
{% extends "base.html" %}
{% block title %}Dashboard - Superseded{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-6">Dashboard</h1>

<div class="mb-6">
    <a href="/issues/new" class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg">+ New Issue</a>
</div>

<div class="grid grid-cols-6 gap-4 mb-8">
    {% for stage in stage_names %}
    <div class="bg-gray-800 rounded-lg p-3 text-center">
        <div class="text-sm font-semibold text-gray-400 uppercase">{{ stage }}</div>
        <div class="text-2xl font-bold mt-1">
            {{ issues | selectattr("stage.value", "equalto", stage) | list | length }}
        </div>
    </div>
    {% endfor %}
</div>

<div class="bg-gray-800 rounded-lg overflow-hidden">
    <table class="w-full">
        <thead>
            <tr class="bg-gray-700 text-left">
                <th class="px-4 py-3 text-sm font-semibold">ID</th>
                <th class="px-4 py-3 text-sm font-semibold">Title</th>
                <th class="px-4 py-3 text-sm font-semibold">Stage</th>
                <th class="px-4 py-3 text-sm font-semibold">Status</th>
                <th class="px-4 py-3 text-sm font-semibold">Agent</th>
            </tr>
        </thead>
        <tbody>
            {% for issue in issues %}
            <tr class="border-t border-gray-700 hover:bg-gray-750">
                <td class="px-4 py-3">
                    <a href="/issues/{{ issue.id }}" class="text-blue-400 hover:text-blue-300">{{ issue.id }}</a>
                </td>
                <td class="px-4 py-3">{{ issue.title }}</td>
                <td class="px-4 py-3">
                    <span class="px-2 py-1 rounded text-xs font-medium bg-purple-900 text-purple-200">{{ issue.stage.value }}</span>
                </td>
                <td class="px-4 py-3">
                    <span class="px-2 py-1 rounded text-xs font-medium
                        {% if issue.status.value == 'done' %}bg-green-900 text-green-200
                        {% elif issue.status.value == 'failed' %}bg-red-900 text-red-200
                        {% elif issue.status.value == 'in-progress' %}bg-yellow-900 text-yellow-200
                        {% else %}bg-gray-700 text-gray-300{% endif %}">
                        {{ issue.status.value }}
                    </span>
                </td>
                <td class="px-4 py-3 text-gray-400">{{ issue.assignee or "—" }}</td>
            </tr>
            {% endfor %}
            {% if not issues %}
            <tr>
                <td colspan="5" class="px-4 py-8 text-center text-gray-500">No issues yet. Create one to get started.</td>
            </tr>
            {% endif %}
        </tbody>
    </table>
</div>
{% endblock %}
```

Create `static/app.js`:

```javascript
// Superseded - minimal Alpine.js enhancements
document.addEventListener('alpine:init', () => {
    console.log('Superseded UI initialized');
});
```

**Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_routes.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: fastapi app, dashboard route, and templates"
```

---

### Task 9: Issue Detail and New Issue Routes

**Files:**
- Create: `src/superseded/routes/issues.py`
- Create: `templates/issue_detail.html`
- Create: `templates/issue_new.html`
- Extend: `src/superseded/main.py` (add issues router)
- Extend: `tests/test_routes.py`

**Step 1: Write the failing test**

Add to `tests/test_routes.py`:

```python
async def test_issue_detail_page(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/issues/SUP-001")
        assert response.status_code == 200
        assert "SUP-001" in response.text
        assert "Test issue" in response.text


async def test_new_issue_form(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/issues/new")
        assert response.status_code == 200
        assert "New Issue" in response.text


async def test_create_issue(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/issues/new", data={
            "title": "My new feature",
            "body": "Add a cool feature",
            "labels": "frontend",
            "assignee": "claude-code",
        }, follow_redirects=True)
        assert response.status_code == 200
        assert "My new feature" in response.text or response.status_code == 303
```

**Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_routes.py -v`
Expected: New tests FAIL

**Step 3: Write the implementation**

Create `src/superseded/routes/issues.py`:

```python
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import frontmatter
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.models import Issue, IssueStatus, Stage
from superseded.tickets.reader import read_issue, list_issues
from superseded.tickets.writer import write_issue

router = APIRouter(prefix="/issues")

_config: SupersededConfig | None = None
_db: Database | None = None

_templates_dir = Path(__file__).parent.parent.parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_templates_dir))


def set_deps(config: SupersededConfig, db: Database) -> None:
    global _config, _db
    _config = config
    _db = db


def _next_id(issues_dir: str) -> str:
    issues = list_issues(issues_dir)
    max_num = 0
    for issue in issues:
        num = int(issue.id.replace("SUP-", ""))
        max_num = max(max_num, num)
    return f"SUP-{max_num + 1:03d}"


@router.get("/new", response_class=HTMLResponse)
async def new_issue_form(request: Request):
    return _templates.TemplateResponse("issue_new.html", {"request": request})


@router.post("/new", response_class=RedirectResponse)
async def create_issue(request: Request):
    assert _config
    form = await request.form()
    title = str(form.get("title", "")).strip()
    body = str(form.get("body", "")).strip()
    labels_str = str(form.get("labels", "")).strip()
    assignee = str(form.get("assignee", "")).strip()

    labels = [l.strip() for l in labels_str.split(",") if l.strip()] if labels_str else []

    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    Path(issues_dir).mkdir(parents=True, exist_ok=True)

    issue_id = _next_id(issues_dir)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    filepath = str(Path(issues_dir) / f"{issue_id}-{slug}.md")

    content = f"""---
id: {issue_id}
title: {title}
status: new
stage: spec
created: "{date.today().isoformat()}"
assignee: {assignee}
labels:
{chr(10).join(f'  - {l}' for l in labels) if labels else '  []'}
---

{body}
"""
    write_issue(filepath, content)

    issue = Issue(id=issue_id, title=title, filepath=filepath, assignee=assignee, labels=labels)
    if _db:
        await _db.upsert_issue(issue)

    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@router.get("/{issue_id}", response_class=HTMLResponse)
async def issue_detail(request: Request, issue_id: str):
    assert _config
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not matching:
        return _templates.TemplateResponse("issue_detail.html", {"request": request, "issue": None, "error": "Issue not found"}, status_code=404)

    issue = matching[0]
    stage_results = []
    if _db:
        stage_results = await _db.get_stage_results(issue_id)

    return _templates.TemplateResponse("issue_detail.html", {
        "request": request,
        "issue": issue,
        "stage_results": stage_results,
        "stage_order": [s.value for s in Stage],
    })
```

Create `templates/issue_new.html`:

```html
{% extends "base.html" %}
{% block title %}New Issue - Superseded{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-6">New Issue</h1>

<form action="/issues/new" method="post" class="max-w-2xl space-y-4">
    <div>
        <label class="block text-sm font-medium text-gray-300 mb-1">Title</label>
        <input type="text" name="title" required
            class="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-white focus:outline-none focus:border-blue-500">
    </div>
    <div>
        <label class="block text-sm font-medium text-gray-300 mb-1">Description</label>
        <textarea name="body" rows="10"
            class="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-white focus:outline-none focus:border-blue-500"></textarea>
    </div>
    <div>
        <label class="block text-sm font-medium text-gray-300 mb-1">Labels (comma-separated)</label>
        <input type="text" name="labels"
            class="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-white focus:outline-none focus:border-blue-500">
    </div>
    <div>
        <label class="block text-sm font-medium text-gray-300 mb-1">Assign to agent</label>
        <select name="assignee"
            class="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-white focus:outline-none focus:border-blue-500">
            <option value="">auto</option>
            <option value="claude-code">claude-code</option>
            <option value="opencode">opencode</option>
        </select>
    </div>
    <div class="flex gap-3">
        <button type="submit" class="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded-lg">Create Issue</button>
        <a href="/" class="bg-gray-700 hover:bg-gray-600 text-gray-300 px-6 py-2 rounded-lg">Cancel</a>
    </div>
</form>
{% endblock %}
```

Create `templates/issue_detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ issue.title if issue else 'Not Found' }} - Superseded{% endblock %}
{% block content %}
{% if not issue %}
<p class="text-red-400">{{ error }}</p>
<a href="/" class="text-blue-400 hover:text-blue-300">Back to dashboard</a>
{% else %}
<h1 class="text-2xl font-bold mb-2">{{ issue.id }}: {{ issue.title }}</h1>
<div class="flex gap-3 mb-6">
    <span class="px-3 py-1 rounded-lg text-sm font-medium bg-purple-900 text-purple-200">{{ issue.stage.value }}</span>
    <span class="px-3 py-1 rounded-lg text-sm font-medium
        {% if issue.status.value == 'done' %}bg-green-900 text-green-200
        {% elif issue.status.value == 'failed' %}bg-red-900 text-red-200
        {% elif issue.status.value == 'in-progress' %}bg-yellow-900 text-yellow-200
        {% else %}bg-gray-700 text-gray-300{% endif %}">
        {{ issue.status.value }}
    </span>
    {% for label in issue.labels %}
    <span class="px-2 py-1 rounded text-xs bg-gray-700 text-gray-300">{{ label }}</span>
    {% endfor %}
</div>

<div class="mb-6">
    <h2 class="text-lg font-semibold mb-2">Pipeline Progress</h2>
    <div class="flex gap-2">
        {% for stage_name in stage_order %}
        <div class="flex-1 rounded-lg p-3 text-center text-sm font-medium
            {% if stage_name == issue.stage.value %}bg-blue-600 text-white
            {% elif stage_name in passed_stages %}bg-green-800 text-green-200
            {% else %}bg-gray-800 text-gray-500{% endif %}">
            {{ stage_name }}
        </div>
        {% endfor %}
    </div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
    <div class="lg:col-span-2 bg-gray-800 rounded-lg p-6">
        <h2 class="text-lg font-semibold mb-3">Ticket</h2>
        <div class="prose prose-invert max-w-none">
            <pre class="text-sm text-gray-300 whitespace-pre-wrap">{{ issue.description if issue.description else 'No description' }}</pre>
        </div>
    </div>
    <div class="space-y-4">
        <div class="bg-gray-800 rounded-lg p-4">
            <h3 class="font-semibold mb-2">Actions</h3>
            {% if issue.status.value != 'done' %}
            <button class="w-full bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg mb-2 text-sm"
                hx-post="/issues/{{ issue.id }}/advance" hx-target="#pipeline-status" hx-swap="innerHTML">
                Advance Stage
            </button>
            <button class="w-full bg-yellow-600 hover:bg-yellow-700 text-white px-4 py-2 rounded-lg mb-2 text-sm"
                hx-post="/issues/{{ issue.id }}/retry" hx-target="#pipeline-status" hx-swap="innerHTML">
                Retry Stage
            </button>
            {% endif %}
            <a href="/" class="block text-center text-blue-400 hover:text-blue-300 text-sm mt-2">Back to Dashboard</a>
        </div>
        {% if stage_results %}
        <div class="bg-gray-800 rounded-lg p-4" id="pipeline-status">
            <h3 class="font-semibold mb-2">Stage Results</h3>
            {% for result in stage_results %}
            <div class="mb-3 border-l-2 {% if result.passed %}border-green-500{% else %}border-red-500{% endif %} pl-3">
                <div class="flex items-center gap-2">
                    <span class="font-medium">{{ result.stage }}</span>
                    {% if result.passed %}<span class="text-green-400 text-xs">PASS</span>{% else %}<span class="text-red-400 text-xs">FAIL</span>{% endif %}
                </div>
                {% if result.error %}<p class="text-red-400 text-xs mt-1">{{ result.error }}</p>{% endif %}
            </div>
            {% endfor %}
        </div>
        {% endif %}
    </div>
</div>
{% endif %}
{% endblock %}
```

Update `src/superseded/main.py` — add the issues router registration. After `app.include_router(dashboard_router)`, add:

```python
from superseded.routes.issues import router as issues_router, set_deps as set_issues_deps
set_issues_deps(config, app.state.db)
app.include_router(issues_router)
```

**Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_routes.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: issue detail and new issue routes with templates"
```

---

### Task 10: Pipeline Control Routes (Advance, Retry, SSE)

**Files:**
- Create: `src/superseded/routes/pipeline.py`
- Extend: `src/superseded/main.py` (add pipeline router)
- Extend: `tests/test_routes.py`

**Step 1: Write the failing test**

Add to `tests/test_routes.py`:

```python
async def test_advance_issue(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/issues/SUP-001/advance", follow_redirects=True)
        assert response.status_code == 200


async def test_pipeline_sse_endpoint_exists(tmp_repo):
    app = create_app(repo_path=tmp_repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/pipeline/events")
        assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_routes.py::test_advance_issue -v`
Expected: FAIL (404)

**Step 3: Write the implementation**

Create `src/superseded/routes/pipeline.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from superseded.config import SupersededConfig
from superseded.db import Database
from superseded.models import Issue, IssueStatus, Stage
from superseded.pipeline.engine import PipelineEngine
from superseded.pipeline.stages import STAGE_DEFINITIONS
from superseded.tickets.reader import read_issue
from superseded.tickets.writer import update_issue_status

router = APIRouter(prefix="/pipeline")

_config: SupersededConfig | None = None
_db: Database | None = None


def set_deps(config: SupersededConfig, db: Database) -> None:
    global _config, _db
    _config = config
    _db = db


@router.post("/issues/{issue_id}/advance")
async def advance_issue(request: Request, issue_id: str):
    assert _config and _db
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    issues = [i for i in __import__("superseded.tickets.reader", fromlist=["list_issues"]).list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return RedirectResponse(url="/", status_code=303)

    issue = issues[0]
    next_stage = issue.next_stage()
    if next_stage is None:
        await _db.update_issue_status(issue_id, IssueStatus.DONE, Stage.SHIP)
    else:
        await _db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, next_stage)
        update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, next_stage)

    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@router.post("/issues/{issue_id}/retry")
async def retry_issue(request: Request, issue_id: str):
    assert _config and _db
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    issues = [i for i in __import__("superseded.tickets.reader", fromlist=["list_issues"]).list_issues(issues_dir) if i.id == issue_id]
    if not issues:
        return RedirectResponse(url="/", status_code=303)

    issue = issues[0]
    await _db.update_issue_status(issue_id, IssueStatus.IN_PROGRESS, issue.stage)
    update_issue_status(issue.filepath, IssueStatus.IN_PROGRESS, issue.stage)

    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@router.get("/events")
async def pipeline_events(request: Request):
    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            assert _db
            issues = await _db.list_issues()
            data = json.dumps(issues)
            yield {"event": "update", "data": data}
            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())
```

**Step 4: Run test to verify it passes**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_routes.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: pipeline control routes with advance, retry, and SSE"
```

---

### Task 11: Stage Detail Template

**Files:**
- Create: `templates/stage_detail.html`
- Add route to `src/superseded/routes/issues.py`

**Step 1: Add route to issues.py**

Add to `src/superseded/routes/issues.py`:

```python
@router.get("/{issue_id}/stage/{stage_name}", response_class=HTMLResponse)
async def stage_detail(request: Request, issue_id: str, stage_name: str):
    assert _config
    stage = Stage(stage_name)
    issues_dir = str(Path(_config.repo_path) / _config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not matching:
        return _templates.TemplateResponse("stage_detail.html", {"request": request, "issue": None, "stage": stage, "error": "Issue not found"}, status_code=404)

    issue = matching[0]
    result = None
    if _db:
        results = await _db.get_stage_results(issue_id)
        for r in results:
            if r["stage"] == stage_name:
                result = r
                break

    return _templates.TemplateResponse("stage_detail.html", {
        "request": request,
        "issue": issue,
        "stage": stage,
        "result": result,
    })
```

Create `templates/stage_detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ issue.id }} / {{ stage.value }} - Superseded{% endblock %}
{% block content %}
{% if not issue %}
<p class="text-red-400">{{ error }}</p>
<a href="/" class="text-blue-400 hover:text-blue-300">Back to dashboard</a>
{% else %}
<div class="mb-4">
    <a href="/issues/{{ issue.id }}" class="text-blue-400 hover:text-blue-300">&larr; Back to {{ issue.id }}</a>
</div>
<h1 class="text-2xl font-bold mb-2">{{ issue.id }}: {{ stage.value }}</h1>
<h2 class="text-lg text-gray-400 mb-6">{{ issue.title }}</h2>

<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
    <div class="bg-gray-800 rounded-lg p-6">
        <h3 class="font-semibold mb-3">Stage: {{ stage.value }}</h3>
        <p class="text-gray-400 text-sm mb-4">
            {% if stage.value == "spec" %}Define what to build from the ticket{% elif stage.value == "plan" %}Break the spec into implementable tasks{% elif stage.value == "build" %}Implement the code changes{% elif stage.value == "verify" %}Run tests and verify correctness{% elif stage.value == "review" %}Review code quality and security{% elif stage.value == "ship" %}Commit, push, and create PR{% endif %}
        </p>
        <div class="space-y-2">
            <button class="w-full bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg" hx-post="/pipeline/issues/{{ issue.id }}/advance" hx-redirect="/issues/{{ issue.id }}">Run Stage</button>
            <button class="w-full bg-yellow-600 hover:bg-yellow-700 text-white px-4 py-2 rounded-lg" hx-post="/pipeline/issues/{{ issue.id }}/retry" hx-redirect="/issues/{{ issue.id }}">Retry</button>
        </div>
    </div>
    <div class="bg-gray-800 rounded-lg p-6">
        <h3 class="font-semibold mb-3">Result</h3>
        {% if result %}
        <div class="mb-2">
            <span class="px-2 py-1 rounded text-sm {% if result.passed %}bg-green-900 text-green-200{% else %}bg-red-900 text-red-200{% endif %}">
                {% if result.passed %}PASSED{% else %}FAILED{% endif %}
            </span>
        </div>
        {% if result.output %}
        <pre class="bg-gray-900 rounded p-3 text-sm text-gray-300 overflow-auto max-h-96">{{ result.output }}</pre>
        {% endif %}
        {% if result.error %}
        <div class="mt-2 p-3 bg-red-900/30 rounded text-red-300 text-sm">{{ result.error }}</div>
        {% endif %}
        {% else %}
        <p class="text-gray-500">No result yet. Run the stage to generate output.</p>
        {% endif %}
    </div>
</div>
{% endif %}
{% endblock %}
```

**Step 2: Verify the app still works**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/ -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: stage detail template with run and retry"
```

---

### Task 12: CLI Entry Point and Integration

**Files:**
- Modify: `src/superseded/main.py` (finalize CLI)
- Add integration test: `tests/test_integration.py`

**Step 1: Write the integration test**

Create `tests/test_integration.py`:

```python
"""End-to-end integration test: create repo, init superseded, create ticket, verify flow."""

import tempfile
from pathlib import Path

from superseded.config import load_config, SupersededConfig
from superseded.db import Database
from superseded.models import Issue, IssueStatus, Stage
from superseded.tickets.reader import list_issues, read_issue
from superseded.tickets.writer import write_issue, update_issue_status


SAMPLE_TICKET = """---
id: SUP-001
title: Integrate payment API
status: new
stage: spec
created: "2026-04-11"
assignee: claude-code
labels:
  - backend
  - integration
---

## Description
Integrate the payment gateway API into the checkout flow.

## Acceptance Criteria
- [ ] Payment API client created
- [ ] Checkout flow updated
- [ ] Tests for happy path and failures
"""


async def test_full_ticket_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        config_dir = repo / ".superseded"
        config_dir.mkdir()
        issues_dir = config_dir / "issues"
        issues_dir.mkdir()

        config = load_config(repo)
        assert config.default_agent == "claude-code"

        filepath = str(issues_dir / "SUP-001-integrate-payment-api.md")
        write_issue(filepath, SAMPLE_TICKET)

        issue = read_issue(filepath)
        assert issue.id == "SUP-001"
        assert issue.title == "Integrate payment API"
        assert issue.stage == Stage.SPEC
        assert issue.status == IssueStatus.NEW

        update_issue_status(filepath, IssueStatus.IN_PROGRESS, Stage.PLAN)
        updated = read_issue(filepath)
        assert updated.status == IssueStatus.IN_PROGRESS
        assert updated.stage == Stage.PLAN

        next_stage = updated.next_stage()
        assert next_stage == Stage.BUILD

        db = Database(str(config_dir / "state.db"))
        await db.initialize()
        await db.upsert_issue(issue)
        fetched = await db.get_issue("SUP-001")
        assert fetched["title"] == "Integrate payment API"

        await db.close()


async def test_list_issues_across_multiple():
    with tempfile.TemporaryDirectory() as tmp:
        issues_dir = Path(tmp) / "issues"
        issues_dir.mkdir(parents=True)

        for i in range(1, 4):
            content = SAMPLE_TICKET.replace("SUP-001", f"SUP-00{i}").replace("Integrate payment API", f"Issue {i}")
            write_issue(str(issues_dir / f"SUP-00{i}-issue-{i}.md"), content)

        issues = list_issues(str(issues_dir))
        assert len(issues) == 3
```

**Step 2: Run integration test**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_integration.py -v`
Expected: All tests PASS

**Step 3: Verify all tests pass**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/ -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "feat: integration tests and full lifecycle verification"
```

---

### Task 13: Update PRD with Design Decisions

**Files:**
- Modify: `prd.md`

Replace the contents of `prd.md` with the key design decisions and a link to the full design doc. Add at the top:

```markdown
# Superseded — Product Requirements

## Core Concept
Local-first agentic pipeline tool. You write the ticket, the pipeline does the rest.
- Tickets are markdown files in `.superseded/issues/` (single source of truth)
- Pipeline: Spec → Plan → Build → Verify → Review → Ship
- Agents: Claude Code and OpenCode, run as local CLI subprocesses
- Web UI: FastAPI + HTMX dashboard for ticket management, pipeline visualization, and review

## Tech Stack
Python 3.12+ / uv / FastAPI / HTMX / Alpine.js / Tailwind CDN / SQLite / Jinja2

## Key Decisions
- Monolith (single process, single `uv run superseded`)
- Local agents only (agents run on your machine)
- In-repo `.superseded/` directory for tickets, artifacts, and state
- SQLite for pipeline state (markdown is canonical, SQLite is a cache)
- SSE for real-time pipeline updates
- Agent-skills inspired stage prompts

See `docs/plans/2026-04-11-superseded-design.md` for full design.
```

**Step 5: Commit**

```bash
cd /home/debian/workspace/superseded && git add -A && git commit -m "docs: update prd with design decisions"
```

---

## Summary

| Task | What it builds | Tests |
|------|---------------|-------|
| 1 | Project scaffolding with uv | Import check |
| 2 | Pydantic models (Issue, Stage, AgentResult) | 5 unit tests |
| 3 | Config module (YAML loading) | 3 unit tests |
| 4 | Ticket reader/writer (markdown + frontmatter) | 4 unit tests |
| 5 | SQLite database layer | 2 async tests |
| 6 | Agent adapters (Claude Code, OpenCode) | 5 unit tests |
| 7 | Pipeline engine (stages, prompts, execution) | 5 unit tests |
| 8 | FastAPI app + dashboard route + templates | 2 route tests |
| 9 | Issue detail + new issue routes + templates | 3 route tests |
| 10 | Pipeline control routes (advance, retry, SSE) | 2 route tests |
| 11 | Stage detail template | Manual verification |
| 12 | CLI entry point + integration tests | 2 integration tests |
| 13 | PRD update | N/A |