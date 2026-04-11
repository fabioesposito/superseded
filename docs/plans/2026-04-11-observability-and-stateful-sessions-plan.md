# Observability & Stateful Sessions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add live agent log streaming, session history persistence, and pipeline metrics to Superseded.

**Architecture:** Two new SQLite tables (session_turns, agent_events). Agent adapter gains async streaming. SSE endpoint streams events to UI. ContextAssembler reads prior session history. Metrics computed from existing + new tables.

**Tech Stack:** Python 3.12+, aiosqlite, FastAPI, SSE-Starlette, HTMX (SSE extension), Jinja2, Pydantic

---

### Task 1: Pydantic Models for Sessions and Events

**Files:**
- Create: `tests/test_session_models.py`
- Modify: `src/superseded/models.py:100`

**Step 1: Write failing tests**

```python
# tests/test_session_models.py
from superseded.models import AgentEvent, PipelineMetrics, SessionTurn, Stage


def test_session_turn_creation():
    turn = SessionTurn(
        role="user",
        content="Write a plan for this feature.",
        stage=Stage.SPEC,
        attempt=0,
    )
    assert turn.role == "user"
    assert turn.content == "Write a plan for this feature."
    assert turn.stage == Stage.SPEC
    assert turn.attempt == 0
    assert turn.metadata == {}


def test_session_turn_with_metadata():
    turn = SessionTurn(
        role="assistant",
        content="Plan written successfully.",
        stage=Stage.SPEC,
        attempt=0,
        metadata={"exit_code": 0, "files_changed": ["plan.md"]},
    )
    assert turn.metadata["exit_code"] == 0


def test_agent_event_stdout():
    event = AgentEvent(
        event_type="stdout",
        content="Building project...",
        stage=Stage.BUILD,
    )
    assert event.event_type == "stdout"
    assert event.content == "Building project..."
    assert event.stage == Stage.BUILD


def test_agent_event_status():
    event = AgentEvent(
        event_type="status",
        content="",
        stage=Stage.BUILD,
        metadata={"exit_code": 0, "duration_ms": 5432},
    )
    assert event.event_type == "status"
    assert event.metadata["exit_code"] == 0


def test_pipeline_metrics():
    metrics = PipelineMetrics(
        total_issues=10,
        issues_by_status={"done": 5, "in-progress": 3, "new": 2},
        stage_success_rates={"build": 0.8, "verify": 0.9},
        avg_stage_duration_ms={"build": 45000.0, "verify": 30000.0},
        total_retries=7,
        retries_by_stage={"build": 5, "verify": 2},
        recent_events=[],
    )
    assert metrics.total_issues == 10
    assert metrics.stage_success_rates["build"] == 0.8
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_session_models.py -v`
Expected: FAIL with "cannot import name 'AgentEvent'"

**Step 3: Add models to `src/superseded/models.py`**

Append after `AgentContext` class (line 100):

```python
class SessionTurn(BaseModel):
    role: str
    content: str
    stage: Stage
    attempt: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    event_type: str
    content: str = ""
    stage: Stage
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineMetrics(BaseModel):
    total_issues: int
    issues_by_status: dict[str, int]
    stage_success_rates: dict[str, float]
    avg_stage_duration_ms: dict[str, float]
    total_retries: int
    retries_by_stage: dict[str, int]
    recent_events: list[AgentEvent] = Field(default_factory=list)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_session_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_session_models.py src/superseded/models.py
git commit -m "feat: add SessionTurn, AgentEvent, PipelineMetrics models"
```

---

### Task 2: Database Tables and CRUD for Sessions and Events

**Files:**
- Create: `tests/test_db_sessions.py`
- Modify: `src/superseded/db.py:57` (add tables) + end of file (add methods)

**Step 1: Write failing tests**

```python
# tests/test_db_sessions.py
import tempfile
from pathlib import Path

from superseded.db import Database
from superseded.models import AgentEvent, Issue, SessionTurn, Stage


async def test_save_and_get_session_turns():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-001", title="Test", filepath="")
        await db.upsert_issue(issue)

        turn = SessionTurn(
            role="user",
            content="Write a spec for this feature.",
            stage=Stage.SPEC,
            attempt=0,
        )
        await db.save_session_turn("SUP-001", turn)

        turns = await db.get_session_turns("SUP-001")
        assert len(turns) == 1
        assert turns[0]["role"] == "user"
        assert turns[0]["content"] == "Write a spec for this feature."
        assert turns[0]["stage"] == "spec"

        await db.close()


async def test_get_session_turns_filters_by_stage():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-002", title="Test", filepath="")
        await db.upsert_issue(issue)

        await db.save_session_turn(
            "SUP-002",
            SessionTurn(role="user", content="spec prompt", stage=Stage.SPEC, attempt=0),
        )
        await db.save_session_turn(
            "SUP-002",
            SessionTurn(role="assistant", content="spec output", stage=Stage.SPEC, attempt=0),
        )
        await db.save_session_turn(
            "SUP-002",
            SessionTurn(role="user", content="build prompt", stage=Stage.BUILD, attempt=0),
        )

        spec_turns = await db.get_session_turns("SUP-002", stage=Stage.SPEC)
        assert len(spec_turns) == 2

        all_turns = await db.get_session_turns("SUP-002")
        assert len(all_turns) == 3

        await db.close()


async def test_save_and_get_agent_events():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-003", title="Test", filepath="")
        await db.upsert_issue(issue)

        event = AgentEvent(
            event_type="stdout",
            content="Building project...",
            stage=Stage.BUILD,
        )
        await db.save_agent_event("SUP-003", event)

        events = await db.get_agent_events("SUP-003")
        assert len(events) == 1
        assert events[0]["event_type"] == "stdout"
        assert events[0]["content"] == "Building project..."

        await db.close()


async def test_get_agent_events_limit():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-004", title="Test", filepath="")
        await db.upsert_issue(issue)

        for i in range(5):
            await db.save_agent_event(
                "SUP-004",
                AgentEvent(event_type="stdout", content=f"line {i}", stage=Stage.BUILD),
            )

        events = await db.get_agent_events("SUP-004", limit=3)
        assert len(events) == 3
        assert events[0]["content"] == "line 2"

        await db.close()


async def test_get_recent_events_across_issues():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        for issue_id in ["SUP-010", "SUP-011"]:
            await db.upsert_issue(Issue(id=issue_id, title="Test", filepath=""))
            await db.save_agent_event(
                issue_id,
                AgentEvent(event_type="stdout", content="output", stage=Stage.BUILD),
            )

        events = await db.get_recent_events(limit=10)
        assert len(events) == 2

        await db.close()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db_sessions.py -v`
Expected: FAIL with "Database has no attribute 'save_session_turn'"

**Step 3: Add tables and methods to `src/superseded/db.py`**

In `initialize()`, after the `harness_iterations` CREATE TABLE (before `self._conn.commit()`), add:

```sql
CREATE TABLE IF NOT EXISTS session_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
);
CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    event_type TEXT NOT NULL,
    content TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (issue_id) REFERENCES issues(id)
);
```

At the end of the class, add these methods:

```python
async def save_session_turn(self, issue_id: str, turn: SessionTurn) -> None:
    assert self._conn
    await self._conn.execute(
        """INSERT INTO session_turns (issue_id, stage, attempt, role, content, metadata)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            issue_id,
            turn.stage.value,
            turn.attempt,
            turn.role,
            turn.content,
            json.dumps(turn.metadata),
        ),
    )
    await self._conn.commit()

async def get_session_turns(
    self, issue_id: str, stage: Stage | None = None
) -> list[dict[str, Any]]:
    assert self._conn
    if stage:
        cursor = await self._conn.execute(
            "SELECT * FROM session_turns WHERE issue_id = ? AND stage = ? ORDER BY id",
            (issue_id, stage.value),
        )
    else:
        cursor = await self._conn.execute(
            "SELECT * FROM session_turns WHERE issue_id = ? ORDER BY id", (issue_id,)
        )
    rows = await cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    results = []
    for row in rows:
        d = dict(zip(cols, row))
        d["metadata"] = json.loads(d["metadata"])
        results.append(d)
    return results

async def save_agent_event(self, issue_id: str, event: AgentEvent) -> None:
    assert self._conn
    await self._conn.execute(
        """INSERT INTO agent_events (issue_id, stage, event_type, content, metadata)
           VALUES (?, ?, ?, ?, ?)""",
        (
            issue_id,
            event.stage.value,
            event.event_type,
            event.content,
            json.dumps(event.metadata),
        ),
    )
    await self._conn.commit()

async def get_agent_events(
    self, issue_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    assert self._conn
    cursor = await self._conn.execute(
        "SELECT * FROM agent_events WHERE issue_id = ? ORDER BY id DESC LIMIT ?",
        (issue_id, limit),
    )
    rows = await cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    results = []
    for row in rows:
        d = dict(zip(cols, row))
        d["metadata"] = json.loads(d["metadata"])
        results.append(d)
    return list(reversed(results))

async def get_recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
    assert self._conn
    cursor = await self._conn.execute(
        "SELECT * FROM agent_events ORDER BY id DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    results = []
    for row in rows:
        d = dict(zip(cols, row))
        d["metadata"] = json.loads(d["metadata"])
        results.append(d)
    return list(reversed(results))
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db_sessions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_db_sessions.py src/superseded/db.py
git commit -m "feat: add session_turns and agent_events tables with CRUD"
```

---

### Task 3: Streaming Agent Adapter

**Files:**
- Create: `tests/test_streaming_adapter.py`
- Modify: `src/superseded/agents/base.py`

**Step 1: Write failing tests**

```python
# tests/test_streaming_adapter.py
from unittest.mock import AsyncMock

from superseded.agents.base import SubprocessAgentAdapter
from superseded.models import AgentContext, AgentEvent, AgentResult, Issue, Stage


class DummyAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str) -> list[str]:
        return ["echo", prompt]


def _make_context() -> AgentContext:
    return AgentContext(
        repo_path="/tmp",
        issue=Issue(id="SUP-001", title="Test", filepath="", stage=Stage.BUILD),
        skill_prompt="test prompt",
    )


async def test_run_streaming_default_yields_stdout():
    adapter = DummyAdapter()
    adapter.run = AsyncMock(
        return_value=AgentResult(exit_code=0, stdout="line1\nline2\n", stderr="")
    )
    events = []
    async for event in adapter.run_streaming("test", _make_context()):
        events.append(event)

    stdout_events = [e for e in events if e.event_type == "stdout"]
    status_events = [e for e in events if e.event_type == "status"]
    assert len(stdout_events) == 2
    assert stdout_events[0].content == "line1"
    assert stdout_events[1].content == "line2"
    assert len(status_events) == 1
    assert status_events[0].metadata["exit_code"] == 0


async def test_run_streaming_subprocess_streams_real_output():
    adapter = DummyAdapter()
    events = []
    async for event in adapter.run_streaming("hello world", _make_context()):
        events.append(event)

    stdout_events = [e for e in events if e.event_type == "stdout"]
    status_events = [e for e in events if e.event_type == "status"]
    assert len(stdout_events) >= 1
    assert "hello world" in stdout_events[0].content
    assert len(status_events) == 1
    assert status_events[0].metadata["exit_code"] == 0


async def test_run_streaming_stderr_events():
    adapter = SubprocessAgentAdapter()

    class FailingAdapter(SubprocessAgentAdapter):
        def _build_command(self, prompt: str) -> list[str]:
            return ["sh", "-c", "echo oops >&2; exit 1"]

    failing = FailingAdapter()
    events = []
    async for event in failing.run_streaming("test", _make_context()):
        events.append(event)

    stderr_events = [e for e in events if e.event_type == "stderr"]
    status_events = [e for e in events if e.event_type == "status"]
    assert len(stderr_events) >= 1
    assert "oops" in stderr_events[0].content
    assert status_events[0].metadata["exit_code"] == 1


async def test_run_streaming_includes_duration():
    adapter = DummyAdapter()
    events = []
    async for event in adapter.run_streaming("test", _make_context()):
        events.append(event)

    status_events = [e for e in events if e.event_type == "status"]
    assert "duration_ms" in status_events[0].metadata
    assert status_events[0].metadata["duration_ms"] >= 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_streaming_adapter.py -v`
Expected: FAIL with "run_streaming not found" or similar

**Step 3: Implement `run_streaming` on `SubprocessAgentAdapter`**

Modify `src/superseded/agents/base.py`:

Add imports at top:
```python
import time
from collections.asyncio import AsyncIterator  # or from typing import AsyncIterator
```

Add to the `AgentAdapter` Protocol:
```python
async def run_streaming(self, prompt: str, context: AgentContext) -> AsyncIterator: ...
```

Add method to `SubprocessAgentAdapter` class (after `run()`):

```python
async def run_streaming(self, prompt: str, context: AgentContext):
    import time
    from superseded.models import AgentEvent

    cmd = self._build_command(prompt)
    cwd = self._get_cwd(context)
    stage = context.issue.stage
    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def read_lines(stream, event_type):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n\r")
                if text:
                    yield AgentEvent(event_type=event_type, content=text, stage=stage)

        async def merge_streams():
            stdout_done = False
            stderr_done = False
            stdout_gen = read_lines(proc.stdout, "stdout") if proc.stdout else None
            stderr_gen = read_lines(proc.stderr, "stderr") if proc.stderr else None
            pending = set()

            if stdout_gen:
                pending.add(asyncio.create_task(self._anext_safe(stdout_gen, "stdout")))
            if stderr_gen:
                pending.add(asyncio.create_task(self._anext_safe(stderr_gen, "stderr")))

            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    result = task.result()
                    if result is not None:
                        event, gen_name = result
                        yield event
                        gen = stdout_gen if gen_name == "stdout" else stderr_gen
                        if gen:
                            pending.add(
                                asyncio.create_task(self._anext_safe(gen, gen_name))
                            )

        try:
            async for event in asyncio.wait_for(merge_streams(), timeout=self.timeout):
                yield event
        except asyncio.TimeoutError:
            proc.kill()
            duration_ms = int((time.monotonic() - start) * 1000)
            yield AgentEvent(
                event_type="stderr",
                content=f"Agent timed out after {self.timeout}s",
                stage=stage,
            )
            yield AgentEvent(
                event_type="status",
                content="",
                stage=stage,
                metadata={"exit_code": -1, "duration_ms": duration_ms},
            )
            return

        await proc.wait()
        duration_ms = int((time.monotonic() - start) * 1000)
        yield AgentEvent(
            event_type="status",
            content="",
            stage=stage,
            metadata={"exit_code": proc.returncode or 0, "duration_ms": duration_ms},
        )
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        yield AgentEvent(
            event_type="stderr",
            content=str(e),
            stage=stage,
        )
        yield AgentEvent(
            event_type="status",
            content="",
            stage=stage,
            metadata={"exit_code": -1, "duration_ms": duration_ms},
        )

async def _anext_safe(self, gen, gen_name):
    try:
        event = await gen.__anext__()
        return (event, gen_name)
    except StopAsyncIteration:
        return None
```

Also update `AgentAdapter` protocol to import properly and add `run_streaming` with a default implementation.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_streaming_adapter.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_streaming_adapter.py src/superseded/agents/base.py
git commit -m "feat: add run_streaming to SubprocessAgentAdapter"
```

---

### Task 4: Pipeline Event Manager

**Files:**
- Create: `src/superseded/pipeline/events.py`
- Create: `tests/test_events.py`

**Step 1: Write failing tests**

```python
# tests/test_events.py
import asyncio

from superseded.models import AgentEvent, Stage
from superseded.pipeline.events import PipelineEventManager


async def test_publish_and_subscribe():
    manager = PipelineEventManager()
    manager.start("SUP-001")

    event = AgentEvent(event_type="stdout", content="hello", stage=Stage.BUILD)
    await manager.publish("SUP-001", event)

    received = []
    async for evt in manager.subscribe("SUP-001"):
        received.append(evt)
        if len(received) >= 1:
            manager.stop("SUP-001")

    assert len(received) == 1
    assert received[0].content == "hello"


async def test_stop_clears_queue():
    manager = PipelineEventManager()
    manager.start("SUP-001")
    manager.stop("SUP-001")
    assert "SUP-001" not in manager._queues


async def test_publish_to_nonexistent_issue_raises():
    manager = PipelineEventManager()
    event = AgentEvent(event_type="stdout", content="hello", stage=Stage.BUILD)
    try:
        await manager.publish("SUP-999", event)
        assert False, "Should have raised"
    except KeyError:
        pass
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_events.py -v`
Expected: FAIL

**Step 3: Implement `PipelineEventManager`**

```python
# src/superseded/pipeline/events.py
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from superseded.models import AgentEvent


class PipelineEventManager:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[AgentEvent | None]] = {}

    def start(self, issue_id: str) -> None:
        self._queues[issue_id] = asyncio.Queue()

    def stop(self, issue_id: str) -> None:
        queue = self._queues.pop(issue_id, None)
        if queue:
            asyncio.create_task(queue.put(None))

    async def publish(self, issue_id: str, event: AgentEvent) -> None:
        queue = self._queues.get(issue_id)
        if queue is None:
            raise KeyError(f"No active session for issue {issue_id}")
        await queue.put(event)

    async def subscribe(self, issue_id: str) -> AsyncIterator[AgentEvent]:
        queue = self._queues.get(issue_id)
        if queue is None:
            return
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_events.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_events.py src/superseded/pipeline/events.py
git commit -m "feat: add PipelineEventManager for SSE event queues"
```

---

### Task 5: HarnessRunner Streaming + Session Logging

**Files:**
- Modify: `src/superseded/pipeline/harness.py`
- Create: `tests/test_harness_streaming.py`

**Step 1: Write failing tests**

```python
# tests/test_harness_streaming.py
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from superseded.db import Database
from superseded.models import AgentEvent, AgentResult, Issue, Stage
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.harness import HarnessRunner


def _make_issue() -> Issue:
    return Issue(
        id="SUP-001",
        title="Test issue",
        filepath=".superseded/issues/SUP-001-test.md",
    )


async def test_streaming_saves_session_turns():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        mock_agent = AsyncMock()

        async def fake_stream(prompt, context):
            yield AgentEvent(event_type="stdout", content="building...", stage=Stage.BUILD)
            yield AgentEvent(
                event_type="status",
                content="",
                stage=Stage.BUILD,
                metadata={"exit_code": 0, "duration_ms": 1000},
            )

        mock_agent.run_streaming = fake_stream

        runner = HarnessRunner(agent=mock_agent, repo_path="/tmp/testrepo")
        event_manager = PipelineEventManager()

        artifacts_path = Path(tmp) / "artifacts"
        artifacts_path.mkdir()

        result = await runner.run_stage_streaming(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            db=db,
            event_manager=event_manager,
        )

        assert result.passed is True
        turns = await db.get_session_turns("SUP-001")
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"

        await db.close()


async def test_streaming_saves_agent_events():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        mock_agent = AsyncMock()

        async def fake_stream(prompt, context):
            yield AgentEvent(event_type="stdout", content="line 1", stage=Stage.BUILD)
            yield AgentEvent(event_type="stdout", content="line 2", stage=Stage.BUILD)
            yield AgentEvent(
                event_type="status",
                content="",
                stage=Stage.BUILD,
                metadata={"exit_code": 0, "duration_ms": 500},
            )

        mock_agent.run_streaming = fake_stream

        runner = HarnessRunner(agent=mock_agent, repo_path="/tmp/testrepo")
        event_manager = PipelineEventManager()

        artifacts_path = Path(tmp) / "artifacts"
        artifacts_path.mkdir()

        await runner.run_stage_streaming(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            db=db,
            event_manager=event_manager,
        )

        events = await db.get_agent_events("SUP-001")
        assert len(events) == 3
        assert events[0]["content"] == "line 1"
        assert events[1]["content"] == "line 2"
        assert events[2]["event_type"] == "status"

        await db.close()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_harness_streaming.py -v`
Expected: FAIL

**Step 3: Add `run_stage_streaming` to `HarnessRunner`**

Add to `src/superseded/pipeline/harness.py`:

```python
from superseded.db import Database
from superseded.pipeline.events import PipelineEventManager


async def run_stage_streaming(
    self,
    issue: Issue,
    stage: Stage,
    artifacts_path: str,
    db: Database,
    event_manager: PipelineEventManager,
    previous_errors: list[str] | None = None,
) -> StageResult:
    errors: list[str] = previous_errors or []
    effective_max = self.max_retries if stage.value in self.retryable_stages else 1

    for attempt in range(effective_max):
        prompt = self.context_assembler.build(
            stage=stage,
            issue=issue,
            artifacts_path=artifacts_path,
            previous_errors=errors if errors else None,
            iteration=attempt,
        )

        context = AgentContext(
            repo_path=self.repo_path,
            issue=issue,
            skill_prompt=prompt,
            artifacts_path=artifacts_path,
            iteration=attempt,
            previous_errors=errors,
        )

        await db.save_session_turn(
            issue.id,
            SessionTurn(
                role="user",
                content=prompt,
                stage=stage,
                attempt=attempt,
            ),
        )

        event_manager.start(issue.id)
        stdout_parts: list[str] = []
        started = datetime.datetime.now()
        exit_code = -1
        duration_ms = 0

        try:
            async for event in self.agent.run_streaming(prompt, context):
                await db.save_agent_event(issue.id, event)
                await event_manager.publish(issue.id, event)

                if event.event_type == "stdout":
                    stdout_parts.append(event.content)
                elif event.event_type == "status":
                    exit_code = event.metadata.get("exit_code", -1)
                    duration_ms = event.metadata.get("duration_ms", 0)
        finally:
            event_manager.stop(issue.id)

        finished = datetime.datetime.now()
        stdout = "\n".join(stdout_parts)

        await db.save_session_turn(
            issue.id,
            SessionTurn(
                role="assistant",
                content=stdout[:2000],
                stage=stage,
                attempt=attempt,
                metadata={
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                },
            ),
        )

        passed = exit_code == 0

        if passed:
            return StageResult(
                stage=stage,
                passed=True,
                output=stdout,
                error="",
                artifacts=[],
                started_at=started,
                finished_at=finished,
            )

        error_msg = stdout if stdout else f"Agent exited with code {exit_code}"
        errors.append(error_msg)

    combined_errors = "; ".join(errors)
    return StageResult(
        stage=stage,
        passed=False,
        output="",
        error=combined_errors,
        artifacts=[],
        started_at=datetime.datetime.now(),
        finished_at=datetime.datetime.now(),
    )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_harness_streaming.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_harness_streaming.py src/superseded/pipeline/harness.py
git commit -m "feat: add run_stage_streaming with session turn logging"
```

---

### Task 6: SSE and Historical Event Endpoints

**Files:**
- Modify: `src/superseded/routes/pipeline.py`
- Create: `tests/test_routes_streaming.py`

**Step 1: Write failing tests**

```python
# tests/test_routes_streaming.py
import json
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.db import Database
from superseded.main import create_app
from superseded.models import AgentEvent, Issue, Stage


@pytest.fixture
async def client():
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        (repo_path / ".superseded" / "issues").mkdir(parents=True)
        (repo_path / ".superseded" / "artifacts").mkdir(parents=True)

        db_path = str(repo_path / ".superseded" / "state.db")
        db = Database(db_path)
        await db.initialize()

        issue = Issue(
            id="SUP-001",
            title="Test",
            filepath=str(repo_path / ".superseded" / "issues" / "SUP-001-test.md"),
        )
        await db.upsert_issue(issue)

        await db.save_agent_event(
            "SUP-001",
            AgentEvent(event_type="stdout", content="past output", stage=Stage.BUILD),
        )

        app = create_app(repo_path=str(repo_path), db=db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        await db.close()


async def test_historical_events_endpoint(client):
    resp = await client.get("/pipeline/issues/SUP-001/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["content"] == "past output"


async def test_historical_events_empty(client):
    resp = await client.get("/pipeline/issues/SUP-999/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes_streaming.py -v`
Expected: FAIL

**Step 3: Add endpoints to `src/superseded/routes/pipeline.py`**

Add at the end of the file:

```python
@router.get("/issues/{issue_id}/events")
async def get_historical_events(
    request: Request, issue_id: str, deps: Deps = Depends(get_deps)
):
    events = await deps.db.get_agent_events(issue_id)
    return events


@router.get("/issues/{issue_id}/events/stream")
async def stream_events(
    request: Request, issue_id: str, deps: Deps = Depends(get_deps)
):
    from sse_starlette.sse import EventSourceResponse

    runner = _get_harness_runner(deps)
    event_manager = runner.event_manager

    async def event_generator():
        async for event in event_manager.subscribe(issue_id):
            yield {
                "event": event.event_type,
                "data": json.dumps(
                    {"content": event.content, "metadata": event.metadata}
                ),
            }
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())
```

Update `_get_harness_runner` to create a shared `PipelineEventManager`:

```python
_cached_event_manager: PipelineEventManager | None = None

def _get_harness_runner(deps: Deps) -> HarnessRunner:
    global _cached_runner, _cached_event_manager
    if _cached_runner is None:
        from superseded.agents.claude_code import ClaudeCodeAdapter
        from superseded.pipeline.events import PipelineEventManager

        agent = ClaudeCodeAdapter(timeout=deps.config.stage_timeout_seconds)
        _cached_event_manager = PipelineEventManager()
        _cached_runner = HarnessRunner(
            agent=agent,
            repo_path=deps.config.repo_path,
            max_retries=deps.config.max_retries,
            retryable_stages=deps.config.retryable_stages,
            event_manager=_cached_event_manager,
        )
    return _cached_runner
```

Update `HarnessRunner.__init__` to accept optional `event_manager`:

```python
def __init__(
    self,
    agent: AgentAdapter,
    repo_path: str,
    max_retries: int = 3,
    retryable_stages: list[str] | None = None,
    event_manager: PipelineEventManager | None = None,
) -> None:
    # ... existing code ...
    self.event_manager = event_manager or PipelineEventManager()
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes_streaming.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_routes_streaming.py src/superseded/routes/pipeline.py src/superseded/pipeline/harness.py
git commit -m "feat: add SSE stream and historical events endpoints"
```

---

### Task 7: ContextAssembler Session History Layer

**Files:**
- Modify: `src/superseded/pipeline/context.py`
- Create: `tests/test_context_sessions.py`

**Step 1: Write failing tests**

```python
# tests/test_context_sessions.py
import tempfile
from pathlib import Path

from superseded.db import Database
from superseded.models import Issue, SessionTurn, Stage
from superseded.pipeline.context import ContextAssembler


async def test_session_history_layer_included():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-001", title="Test", filepath="")
        await db.upsert_issue(issue)

        await db.save_session_turn(
            "SUP-001",
            SessionTurn(role="user", content="spec prompt", stage=Stage.SPEC, attempt=0),
        )
        await db.save_session_turn(
            "SUP-001",
            SessionTurn(role="assistant", content="spec output here", stage=Stage.SPEC, attempt=0),
        )

        assembler = ContextAssembler(tmp)
        result = assembler.build(
            stage=Stage.PLAN,
            issue=issue,
            artifacts_path="",
            db=db,
        )

        assert "Previous Session History" in result
        assert "spec prompt" in result
        assert "spec output here" in result

        await db.close()


async def test_session_history_excludes_current_stage():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(id="SUP-002", title="Test", filepath="")
        await db.upsert_issue(issue)

        await db.save_session_turn(
            "SUP-002",
            SessionTurn(role="user", content="build prompt", stage=Stage.BUILD, attempt=0),
        )

        assembler = ContextAssembler(tmp)
        result = assembler.build(
            stage=Stage.BUILD,
            issue=issue,
            artifacts_path="",
            db=db,
        )

        assert "Previous Session History" not in result

        await db.close()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_context_sessions.py -v`
Expected: FAIL

**Step 3: Add session history layer to `ContextAssembler`**

Modify `src/superseded/pipeline/context.py`:

Add optional `db` parameter to `build()`:

```python
def build(
    self,
    stage: Stage,
    issue: Issue,
    artifacts_path: str,
    previous_errors: list[str] | None = None,
    iteration: int = 0,
    db: Any = None,
) -> str:
```

Add new method:

```python
def _build_session_history_layer(self, issue_id: str, current_stage: Stage, db: Any) -> str | None:
    if db is None:
        return None
    import asyncio

    turns = asyncio.get_event_loop().run_until_complete(
        db.get_session_turns(issue_id)
    )

    prior_turns = [t for t in turns if t["stage"] != current_stage.value]
    if not prior_turns:
        return None

    parts: list[str] = []
    current_stage_name = None
    current_attempt = None
    for turn in prior_turns:
        stage_key = f"{turn['stage']} (attempt {turn['attempt'] + 1})"
        if stage_key != current_stage_name:
            current_stage_name = stage_key
            parts.append(f"### {stage_key}")

        role_label = "You asked" if turn["role"] == "user" else "Agent responded"
        content = turn["content"]
        if len(content) > 2000:
            content = content[:2000] + "... [truncated]"
        parts.append(f"**{role_label}:**\n{content}")

    if not parts:
        return None
    return "## Previous Session History\n\n" + "\n\n".join(parts)
```

Insert into `build()` method, after artifacts layer and before rules layer:

```python
session_history = self._build_session_history_layer(issue.id, stage, db)
if session_history:
    layers.append(session_history)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_context_sessions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_context_sessions.py src/superseded/pipeline/context.py
git commit -m "feat: inject session history into ContextAssembler"
```

---

### Task 8: Metrics Endpoint and Dashboard

**Files:**
- Modify: `src/superseded/routes/pipeline.py`
- Create: `templates/metrics.html`
- Create: `tests/test_metrics.py`

**Step 1: Write failing tests**

```python
# tests/test_metrics.py
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from superseded.db import Database
from superseded.main import create_app
from superseded.models import Issue, IssueStatus, Stage, StageResult


@pytest.fixture
async def client():
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        (repo_path / ".superseded" / "issues").mkdir(parents=True)
        (repo_path / ".superseded" / "artifacts").mkdir(parents=True)

        db_path = str(repo_path / ".superseded" / "state.db")
        db = Database(db_path)
        await db.initialize()

        for i in range(3):
            issue = Issue(
                id=f"SUP-{i:03d}",
                title=f"Issue {i}",
                filepath="",
                status=IssueStatus.DONE if i < 2 else IssueStatus.IN_PROGRESS,
            )
            await db.upsert_issue(issue)

        await db.save_stage_result(
            "SUP-000",
            StageResult(stage=Stage.BUILD, passed=True, output="ok"),
        )
        await db.save_stage_result(
            "SUP-001",
            StageResult(stage=Stage.BUILD, passed=False, output="", error="failed"),
        )

        app = create_app(repo_path=str(repo_path), db=db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        await db.close()


async def test_metrics_endpoint_returns_json(client):
    resp = await client.get("/pipeline/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_issues"] == 3
    assert "issues_by_status" in data
    assert "stage_success_rates" in data
    assert data["stage_success_rates"]["build"] == 0.5


async def test_metrics_dashboard_renders(client):
    resp = await client.get("/pipeline/metrics/dashboard")
    assert resp.status_code == 200
    assert "Pipeline Metrics" in resp.text
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_metrics.py -v`
Expected: FAIL

**Step 3: Add metrics endpoint**

Add to `src/superseded/routes/pipeline.py`:

```python
@router.get("/metrics")
async def get_metrics(request: Request, deps: Deps = Depends(get_deps)):
    issues = await deps.db.list_issues()
    total = len(issues)
    by_status: dict[str, int] = {}
    for issue in issues:
        s = issue["status"]
        by_status[s] = by_status.get(s, 0) + 1

    all_results = []
    for issue in issues:
        results = await deps.db.get_stage_results(issue["id"])
        all_results.extend(results)

    stage_attempts: dict[str, list[bool]] = {}
    for r in all_results:
        stage_attempts.setdefault(r["stage"], []).append(r["passed"])

    success_rates = {
        stage: sum(1 for p in passes if p) / len(passes)
        for stage, passes in stage_attempts.items()
    }

    all_iterations = []
    for issue in issues:
        iters = await deps.db.get_harness_iterations(issue["id"])
        all_iterations.extend(iters)

    total_retries = len(all_iterations)
    retries_by_stage: dict[str, int] = {}
    for it in all_iterations:
        retries_by_stage[it["stage"]] = retries_by_stage.get(it["stage"], 0) + 1

    recent_events = await deps.db.get_recent_events(limit=20)

    from superseded.models import PipelineMetrics

    metrics = PipelineMetrics(
        total_issues=total,
        issues_by_status=by_status,
        stage_success_rates=success_rates,
        avg_stage_duration_ms={},
        total_retries=total_retries,
        retries_by_stage=retries_by_stage,
        recent_events=[],
    )
    return metrics.model_dump()


@router.get("/metrics/dashboard", response_class=HTMLResponse)
async def metrics_dashboard(request: Request, deps: Deps = Depends(get_deps)):
    metrics_resp = await get_metrics(request, deps)
    return _templates.TemplateResponse(
        request,
        "metrics.html",
        {"metrics": metrics_resp},
    )
```

**Step 4: Create metrics template**

```html
<!-- templates/metrics.html -->
{% extends "base.html" %}
{% block title %}Pipeline Metrics - Superseded{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold mb-6">Pipeline Metrics</h1>

<div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
    <div class="bg-gray-800 rounded-lg p-6">
        <h3 class="text-sm font-medium text-gray-400 mb-1">Total Issues</h3>
        <p class="text-3xl font-bold">{{ metrics.total_issues }}</p>
    </div>
    <div class="bg-gray-800 rounded-lg p-6">
        <h3 class="text-sm font-medium text-gray-400 mb-1">Total Retries</h3>
        <p class="text-3xl font-bold">{{ metrics.total_retries }}</p>
    </div>
    <div class="bg-gray-800 rounded-lg p-6">
        <h3 class="text-sm font-medium text-gray-400 mb-1">Issues by Status</h3>
        {% for status, count in metrics.issues_by_status.items() %}
        <div class="flex justify-between text-sm mt-1">
            <span class="text-gray-300">{{ status }}</span>
            <span class="font-medium">{{ count }}</span>
        </div>
        {% endfor %}
    </div>
</div>

<div class="bg-gray-800 rounded-lg p-6 mb-8">
    <h2 class="text-lg font-semibold mb-4">Stage Success Rates</h2>
    {% for stage, rate in metrics.stage_success_rates.items() %}
    <div class="mb-3">
        <div class="flex justify-between text-sm mb-1">
            <span>{{ stage }}</span>
            <span>{{ (rate * 100) | int }}%</span>
        </div>
        <progress class="w-full h-3 rounded" value="{{ rate }}" max="1"
            {% if rate >= 0.8 %}class="text-green-500"{% elif rate >= 0.5 %}class="text-yellow-500"{% else %}class="text-red-500"{% endif %}>
        </progress>
    </div>
    {% endfor %}
</div>

{% if metrics.retries_by_stage %}
<div class="bg-gray-800 rounded-lg p-6 mb-8">
    <h2 class="text-lg font-semibold mb-4">Retries by Stage</h2>
    {% for stage, count in metrics.retries_by_stage.items() %}
    <div class="flex justify-between text-sm py-1 border-b border-gray-700">
        <span>{{ stage }}</span>
        <span class="font-medium">{{ count }}</span>
    </div>
    {% endfor %}
</div>
{% endif %}

<a href="/" class="text-blue-400 hover:text-blue-300 text-sm">Back to Dashboard</a>
{% endblock %}
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_metrics.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add tests/test_metrics.py src/superseded/routes/pipeline.py templates/metrics.html
git commit -m "feat: add metrics endpoint and dashboard"
```

---

### Task 9: Wire Up Live Log Viewer in Issue Detail

**Files:**
- Modify: `templates/issue_detail.html`
- Modify: `templates/base.html` (add HTMX SSE extension script)

**Step 1: Add HTMX SSE extension to base template**

In `templates/base.html`, add before closing `</head>`:

```html
<script src="https://unpkg.com/htmx-ext-sse@2.2.2/sse.js"></script>
```

**Step 2: Add live log section to issue detail**

In `templates/issue_detail.html`, add after the Actions sidebar section (before `{% endif %}`):

```html
<div class="mt-6 bg-gray-900 rounded-lg p-4">
    <h2 class="text-lg font-semibold mb-3">Agent Log</h2>
    <div id="agent-log"
         class="font-mono text-xs bg-black rounded p-4 h-64 overflow-y-auto text-green-300"
         hx-ext="sse"
         sse-connect="/pipeline/issues/{{ issue.id }}/events/stream"
         sse-swap="stdout"
         hx-swap="beforeend">
        <div class="text-gray-500" id="log-placeholder">Waiting for agent output...</div>
    </div>
    <div id="agent-log-status"
         sse-connect="/pipeline/issues/{{ issue.id }}/events/stream"
         sse-swap="done"
         hx-swap="innerHTML"
         class="mt-2 text-sm">
    </div>
</div>
```

Also add SSE listeners for stderr events. Add a script block:

```html
<script>
document.addEventListener('DOMContentLoaded', function() {
    const logEl = document.getElementById('agent-log');
    if (logEl) {
        logEl.addEventListener('sse:stderr', function(evt) {
            const line = document.createElement('div');
            line.className = 'text-red-400';
            line.textContent = evt.detail.data;
            logEl.appendChild(line);
            logEl.scrollTop = logEl.scrollHeight;
        });
        logEl.addEventListener('sse:stdout', function(evt) {
            const placeholder = document.getElementById('log-placeholder');
            if (placeholder) placeholder.remove();
            logEl.scrollTop = logEl.scrollHeight;
        });
    }
});
</script>
```

**Step 3: Verify manually**

Start the server with `uv run superseded` and visit an issue page. The log viewer should appear. When an agent runs, lines should stream in real-time.

**Step 4: Commit**

```bash
git add templates/issue_detail.html templates/base.html
git commit -m "feat: add live agent log viewer to issue detail page"
```

---

### Task 10: End-to-End Integration Test

**Files:**
- Create: `tests/test_integration_streaming.py`

**Step 1: Write integration test**

```python
# tests/test_integration_streaming.py
import tempfile
from pathlib import Path

from superseded.db import Database
from superseded.models import Issue, Stage
from superseded.pipeline.events import PipelineEventManager
from superseded.pipeline.harness import HarnessRunner
from superseded.agents.base import SubprocessAgentAdapter


class EchoAdapter(SubprocessAgentAdapter):
    def _build_command(self, prompt: str) -> list[str]:
        return ["echo", prompt]


async def test_full_streaming_pipeline():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        issue = Issue(
            id="SUP-001",
            title="Integration test",
            filepath=".superseded/issues/SUP-001-test.md",
        )
        await db.upsert_issue(issue)

        agent = EchoAdapter()
        event_manager = PipelineEventManager()
        runner = HarnessRunner(
            agent=agent,
            repo_path="/tmp/testrepo",
            event_manager=event_manager,
        )

        artifacts_path = Path(tmp) / "artifacts"
        artifacts_path.mkdir()

        result = await runner.run_stage_streaming(
            issue=issue,
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            db=db,
            event_manager=event_manager,
        )

        assert result.passed is True
        assert result.output

        turns = await db.get_session_turns("SUP-001")
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"

        events = await db.get_agent_events("SUP-001")
        assert len(events) >= 2

        # Verify session history is injected in next stage
        from superseded.pipeline.context import ContextAssembler
        assembler = ContextAssembler("/tmp/testrepo")
        context = assembler.build(
            stage=Stage.VERIFY,
            issue=issue,
            artifacts_path=str(artifacts_path),
            db=db,
        )
        assert "Previous Session History" in context

        await db.close()
```

**Step 2: Run test**

Run: `pytest tests/test_integration_streaming.py -v`
Expected: PASS

**Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add tests/test_integration_streaming.py
git commit -m "test: add end-to-end streaming + session history integration test"
```
