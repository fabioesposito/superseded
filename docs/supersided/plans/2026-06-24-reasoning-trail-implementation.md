# Reasoning Trail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each finding's reasoning rationale to the memory store for future-run learn-back, and render the reasoning trail in the current run's output (markdown + GitHub PR comments).

**Architecture:** Add `reasoning: str = ""` to the `Finding` model, add `reasoning TEXT DEFAULT ''` column to the SQLite `findings` table (with migration for existing DBs), update `format_memory_context` to show abbreviated rationale for dismissed findings (capped at 300 chars per finding), and add collapsible `<details>` blocks to markdown and GitHub PR comment output when reasoning is non-empty.

**Tech Stack:** Python 3.14+, pydantic v2 models, aiosqlite, pytest

---

## File Structure

| File | Role | New/Modified |
|---|---|---|
| `src/superseded/models.py` | Add `reasoning` field to `Finding` | Modified |
| `src/superseded/review/prompts.py` | Add `reasoning` to JSON_FORMAT_INSTRUCTIONS + rules | Modified (shared with context plan) |
| `src/superseded/memory/store.py` | Schema migration + `record_finding` signature | Modified |
| `src/superseded/cli.py` | Persist reasoning, update `format_memory_context` | Modified |
| `src/superseded/output/markdown.py` | Add `<details>` block for reasoning | Modified |
| `src/superseded/output/github_pr.py` | Add `<details>` block to PR comments | Modified |
| `tests/test_models.py` | Reasoning field defaults + roundtrip | Modified |
| `tests/test_prompts.py` | Reasoning in JSON format + rules | Modified (shared with context plan) |
| `tests/test_memory_store.py` | Migration, roundtrip, backwards compat | New |
| `tests/test_cli.py` | `format_memory_context` with reasoning | Modified |
| `tests/test_output.py` | Markdown `<details>` rendering | Modified |

---

### Task 1: Add reasoning field to Finding model

**Files:**
- Modify: `src/superseded/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py — append to existing file

from superseded.models import Finding


def test_reasoning_defaults_empty():
    f = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="desc",
        suggestion="fix",
    )
    assert f.reasoning == ""


def test_reasoning_roundtrip():
    f = Finding(
        pass_name="performance",
        severity="suggestion",
        file="b.py",
        line=10,
        end_line=12,
        title="slow",
        description="desc",
        suggestion="fix",
        reasoning="N+1 query in loop",
    )
    data = f.model_dump()
    f2 = Finding(**data)
    assert f2.reasoning == "N+1 query in loop"


def test_reasoning_does_not_affect_id():
    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="d",
        suggestion="s",
        reasoning="because X",
    )
    f2 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="d",
        suggestion="s",
        reasoning="because Y",
    )
    assert f1.id == f2.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_reasoning_defaults_empty -v`
Expected: FAIL — `Finding.__init__() got unexpected keyword argument 'reasoning'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/superseded/models.py — add reasoning field after confidence (line 22)

class Finding(BaseModel):
    pass_name: PassName
    severity: Severity
    file: str
    line: int
    end_line: int
    title: str
    description: str
    suggestion: str
    confidence: Confidence = "high"
    reasoning: str = Field(default="")    # <-- NEW
    id: str = Field(default="")
```

`model_post_init` is **not** changed — `reasoning` does not factor into the `id` hash (line 27-28 still uses `pass_name-file-line-title` only).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/models.py tests/test_models.py
git commit -m "feat: add reasoning field to Finding model"
```

---

### Task 2: Update prompts for reasoning

**Files:**
- Modify: `src/superseded/review/prompts.py`

This task only touches the JSON_FORMAT_INSTRUCTIONS and rules block — the new kwargs for static/usage context are handled by the context plan's Task 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts.py — append (or extend from context plan)

def test_reasoning_in_json_format():
    from superseded.review.prompts import JSON_FORMAT_INSTRUCTIONS
    assert "reasoning" in JSON_FORMAT_INSTRUCTIONS


def test_reasoning_rule_in_prompt():
    from superseded.review.prompts import build_prompt
    prompt = build_prompt(
        pass_name="correctness",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "1-3 sentences" in prompt
    assert "evidence led you to flag" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompts.py::test_reasoning_in_json_format tests/test_prompts.py::test_reasoning_rule_in_prompt -v`
Expected: FAIL — `"reasoning" not in JSON_FORMAT_INSTRUCTIONS`

- [ ] **Step 3: Update prompts.py**

Add `"reasoning"` to the example JSON array in `JSON_FORMAT_INSTRUCTIONS` (after `"suggestion"`):
```jsonc
    "suggestion": "Code fix or suggestion",
    "reasoning": "1-3 sentences explaining what evidence led to this finding."
```

Add a rule to the rules list in `build_prompt` (after the last existing rule):
```text
- For each finding, briefly (1-3 sentences) explain what evidence led you to flag it
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prompts.py::test_reasoning_in_json_format tests/test_prompts.py::test_reasoning_rule_in_prompt -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/review/prompts.py tests/test_prompts.py
git commit -m "feat: add reasoning to JSON format instructions and prompt rules"
```

---

### Task 3: Update memory store for reasoning

**Files:**
- Modify: `src/superseded/memory/store.py`
- Create: `tests/test_memory_store.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_memory_store.py — new file

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from superseded.memory.store import MemoryStore, SCHEMA


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    s = MemoryStore(db_path=db_path)
    asyncio.run(s.init())
    return s


def test_reasoning_column_exists(store):
    """After init, the findings table should have a reasoning column."""
    async def _check():
        import aiosqlite
        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute("PRAGMA table_info(findings)")
            cols = {row[1] for row in await cursor.fetchall()}
            return "reasoning" in cols
    assert asyncio.run(_check())


def test_reasoning_roundtrip(store):
    """record_finding with reasoning should persist and retrieve it."""
    asyncio.run(store.record_finding(
        finding_id="test-1",
        repo="owner/repo",
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="bad",
        description="desc",
        reasoning="because X",
    ))

    async def _get():
        import aiosqlite
        store.db_path
        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT reasoning FROM findings WHERE id = 'test-1'")
            row = await cursor.fetchone()
            return dict(row) if row else None

    result = asyncio.run(_get())
    assert result is not None
    assert result["reasoning"] == "because X"


def test_reasoning_empty_by_default(store):
    """record_finding without reasoning should store empty string."""
    asyncio.run(store.record_finding(
        finding_id="test-2",
        repo="owner/repo",
        pass_name="style",
        severity="nit",
        file="b.py",
        line=5,
        title="naming",
        description="desc",
    ))

    async def _get():
        import aiosqlite
        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT reasoning FROM findings WHERE id = 'test-2'")
            row = await cursor.fetchone()
            return dict(row) if row else None

    result = asyncio.run(_get())
    assert result is not None
    assert result["reasoning"] == ""


def test_migration_adds_reasoning_column(tmp_path):
    """An old DB without reasoning should get the column via _migrate."""
    import aiosqlite

    # Create DB with old schema (no reasoning column)
    db_path = tmp_path / "old.db"
    asyncio.run(_init_old_db(db_path))

    # Now init with the new schema — should migrate
    store = MemoryStore(db_path=db_path)
    asyncio.run(store.init())

    async def _check():
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info(findings)")
            cols = {row[1] for row in await cursor.fetchall()}
            return "reasoning" in cols

    assert asyncio.run(_check())


async def _init_old_db(db_path: Path) -> None:
    """Create a DB with the pre-reasoning schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS findings (
                id TEXT PRIMARY KEY,
                repo TEXT,
                pass TEXT,
                severity TEXT,
                file TEXT,
                line INTEGER,
                title TEXT,
                description TEXT,
                dismissed BOOLEAN DEFAULT FALSE,
                comment_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finding_id TEXT REFERENCES findings(id),
                action TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)


def test_dismissed_findings_include_reasoning(store):
    """get_dismissed_findings should return rows with reasoning."""
    asyncio.run(store.record_finding(
        finding_id="test-3",
        repo="owner/repo",
        pass_name="performance",
        severity="suggestion",
        file="c.py",
        line=10,
        title="slow",
        description="desc",
        reasoning="N+1 query",
    ))
    asyncio.run(store.record_feedback("test-3", "dismiss"))
    dismissed = asyncio.run(store.get_dismissed_findings("owner/repo"))
    assert len(dismissed) == 1
    assert dismissed[0]["reasoning"] == "N+1 query"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory_store.py -v`
Expected: FAIL — either `ModuleNotFoundError` or `record_finding() got an unexpected keyword argument 'reasoning'`

- [ ] **Step 3: Update store.py**

Update `SCHEMA` — add `reasoning TEXT DEFAULT ''` to the `findings` CREATE TABLE (between `line INTEGER` and `title TEXT`):
```sql
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    repo TEXT,
    pass TEXT,
    severity TEXT,
    file TEXT,
    line INTEGER,
    reasoning TEXT DEFAULT ''    -- <-- NEW
    title TEXT,
    description TEXT,
    dismissed BOOLEAN DEFAULT FALSE,
    comment_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Update `_migrate` to add the column for existing DBs:
```python
async def _migrate(self, db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(findings)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "comment_id" not in columns:
        await db.execute("ALTER TABLE findings ADD COLUMN comment_id INTEGER")
    if "reasoning" not in columns:
        await db.execute("ALTER TABLE findings ADD COLUMN reasoning TEXT DEFAULT ''")
```

Update `record_finding` signature — add `reasoning` arg:
```python
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
    reasoning: str = "",    # <-- NEW
) -> None:
    async with aiosqlite.connect(self.db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO findings "
            "(id, repo, pass, severity, file, line, title, description, reasoning) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (finding_id, repo, pass_name, severity, file, line, title, description, reasoning),
        )
        await db.commit()
```

`get_dismissed_findings` is **unchanged** — `SELECT *` already returns the new column.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_memory_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/memory/store.py tests/test_memory_store.py
git commit -m "feat: add reasoning column to findings table with migration"
```

---

### Task 4: Persist reasoning and update learn-back

**Files:**
- Modify: `src/superseded/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli.py — append

from superseded.cli import format_memory_context


def test_format_memory_context_with_reasoning():
    dismissed = [
        {
            "pass": "performance",
            "title": "N+1 query",
            "reasoning": "Loops over 1000 rows; will hit DB N times per request.",
        }
    ]
    result = format_memory_context(dismissed)
    assert "N+1 query" in result
    assert "Loops over 1000 rows" in result
    assert "Rationale then was:" in result


def test_format_memory_context_without_reasoning():
    dismissed = [
        {
            "pass": "style",
            "title": "unclear naming",
            "reasoning": "",
        }
    ]
    result = format_memory_context(dismissed)
    assert "unclear naming" in result
    assert "Rationale then was:" not in result


def test_format_memory_context_truncates_long_reasoning():
    dismissed = [
        {
            "pass": "security",
            "title": "injection",
            "reasoning": "x" * 500,
        }
    ]
    result = format_memory_context(dismissed)
    assert len(result) < 600  # title + truncation + tail
    assert "\u2026" in result  # contains truncation char


def test_persist_findings_passes_reasoning(monkeypatch):
    """_persist_findings should pass reasoning to record_finding."""
    from superseded.cli import _persist_findings
    from superseded.models import Finding, ReviewResult

    f = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="d",
        suggestion="s",
        reasoning="suspicious input",
    )
    result = ReviewResult(findings=[f])

    calls = []
    def fake_record(**kwargs):
        calls.append(kwargs)

    mock_store = type("FakeStore", (), {"record_finding": staticmethod(lambda **kw: fake_record(**kw))})()
    # Make it async-compatible
    import asyncio
    async def async_record(**kwargs):
        calls.append(kwargs)
    mock_store.record_finding = staticmethod(async_record)

    asyncio.run(_persist_findings(mock_store, result, "owner/repo"))
    assert len(calls) == 1
    assert calls[0]["reasoning"] == "suspicious input"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_format_memory_context_with_reasoning -v`
Expected: FAIL — `format_memory_context` doesn't handle reasoning yet

- [ ] **Step 3: Update cli.py**

Update `format_memory_context` (cli.py:35-43):
```python
def format_memory_context(dismissed: list[dict]) -> str | None:
    if not dismissed:
        return None
    lines = []
    for f in dismissed:
        pass_name = f.get("pass") or f.get("pass_name") or "review"
        title = f.get("title", "")
        reasoning = f.get("reasoning", "")
        line = f"- {pass_name.title()} pass: \"{title}\" — dismissed by human review."
        if reasoning:
            truncated = reasoning[:300]
            if len(reasoning) > 300:
                truncated += f"\u2026 ({len(reasoning)} chars)"
            line += f"\n  Rationale then was: \"{truncated}\""
        lines.append(line)
    return "\n".join(lines)
```

Update `_persist_findings` (cli.py:151-164) to pass `reasoning`:
```python
def _persist_findings(store: MemoryStore, result: ReviewResult, repo: str) -> None:
    for f in result.findings:
        asyncio.run(
            store.record_finding(
                finding_id=f.id,
                repo=repo,
                pass_name=f.pass_name,
                severity=f.severity,
                file=f.file,
                line=f.line,
                title=f.title,
                description=f.description,
                reasoning=f.reasoning,    # <-- NEW
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/cli.py tests/test_cli.py
git commit -m "feat: persist reasoning to store and enhance dismissed-findings learn-back"
```

---

### Task 5: Render reasoning in markdown output

**Files:**
- Modify: `src/superseded/output/markdown.py`
- Modify: `tests/test_output.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_output.py — append

from superseded.models import Finding, ReviewResult
from superseded.output.markdown import format_markdown


def _finding(**overrides):
    defaults = dict(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=2,
        title="bad",
        description="desc",
        suggestion="fix",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def test_reasoning_renders_when_present():
    f = _finding(reasoning="Suspicious input from user request")
    result = format_markdown(ReviewResult(findings=[f]))
    assert "<details>" in result
    assert "Suspicious input from user request" in result
    assert "Reasoning" in result


def test_reasoning_absent_when_empty():
    f = _finding(reasoning="")
    result = format_markdown(ReviewResult(findings=[f]))
    assert "<details>" not in result


def test_reasoning_in_correct_position():
    f = _finding(reasoning="because X")
    result = format_markdown(ReviewResult(findings=[f]))
    desc_pos = result.index("desc")
    details_pos = result.index("<details>")
    suggestion_pos = result.index("**Suggestion:**")
    assert desc_pos < details_pos < suggestion_pos
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_output.py::test_reasoning_renders_when_present -v`
Expected: FAIL — `<details>` not in output (reasoning not rendered yet)

- [ ] **Step 3: Update markdown.py**

```python
# src/superseded/output/markdown.py — update the per-finding block (lines 25-32)

        for f in group:
            lines.append(f"### {f.title}")
            lines.append(f"**{f.file}:{f.line}-{f.end_line}** ({f.pass_name})")
            lines.append("")
            lines.append(f.description)
            lines.append("")
            if f.reasoning:                       # <-- NEW
                lines.append("<details><summary>Reasoning</summary>")
                lines.append("")
                lines.append(f.reasoning)
                lines.append("")
                lines.append("</details>")
                lines.append("")
            lines.append(f"**Suggestion:** {f.suggestion}")
            lines.append("")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_output.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/output/markdown.py tests/test_output.py
git commit -m "feat: render reasoning as collapsible details in markdown output"
```

---

### Task 6: Render reasoning in GitHub PR comments

**Files:**
- Modify: `src/superseded/output/github_pr.py`
- Modify: `tests/test_output.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_output.py — append

import json
from unittest.mock import MagicMock, patch
from superseded.output.github_pr import post_review_to_pr


def test_pr_comment_includes_reasoning_when_present():
    f = _finding(reasoning="Suspicious pattern detected")
    result = ReviewResult(findings=[f])
    # Mock subprocess to capture the JSON payload
    payloads = []
    def fake_run(cmd, **kwargs):
        payloads.append(json.loads(kwargs.get("input", "{}")))
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )
    with patch("superseded.output.github_pr.subprocess.run", side_effect=fake_run):
        with patch("superseded.output.github_pr._repo", return_value="owner/repo"):
            post_review_to_pr(pr=1, result=result)

    assert payloads
    body = payloads[0]["comments"][0]["body"]
    assert "<details>" in body
    assert "Suspicious pattern detected" in body


def test_pr_comment_excludes_reasoning_when_empty():
    f = _finding(reasoning="")
    result = ReviewResult(findings=[f])
    payloads = []
    def fake_run(cmd, **kwargs):
        payloads.append(json.loads(kwargs.get("input", "{}")))
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )
    with patch("superseded.output.github_pr.subprocess.run", side_effect=fake_run):
        with patch("superseded.output.github_pr._repo", return_value="owner/repo"):
            post_review_to_pr(pr=1, result=result)

    body = payloads[0]["comments"][0]["body"]
    assert "<details>" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_output.py::test_pr_comment_includes_reasoning_when_present -v`
Expected: FAIL — `<details>` not in comment body

- [ ] **Step 3: Update github_pr.py**

In `post_review_to_pr` (github_pr.py:11-23), update the comment body construction:
```python
        body_text = (
            f"**[{f.severity.upper()}] {f.title}** ({f.pass_name})\n\n"
            f"{f.description}\n\n"
        )
        if f.reasoning:
            body_text += (
                "<details><summary>Reasoning</summary>\n\n"
                f"{f.reasoning}\n\n"
                "</details>\n\n"
            )
        body_text += f"**Suggestion:** {f.suggestion}"
        comment: dict = {
            "path": f.file,
            "line": f.end_line,
            "body": body_text,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_output.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/output/github_pr.py tests/test_output.py
git commit -m "feat: render reasoning as collapsible details in GitHub PR comments"
```

---

### Task 7: Final integration pass

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Run lint and format**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: PASS

- [ ] **Step 3: Commit any lint fixes if needed**

```bash
git add -A
git commit -m "chore: lint and format after reasoning trail feature"
```
(Skip if nothing changed.)

---

### Task Summary

| Task | Files created | Files modified | Tests |
|---|---|---|---|
| 1: Finding model | — | `models.py`, `test_models.py` | 3 new tests |
| 2: Prompts update | — | `prompts.py`, `test_prompts.py` | 2 new tests |
| 3: Memory store | — | `store.py` | `test_memory_store.py` (6 tests) |
| 4: CLI learn-back | — | `cli.py`, `test_cli.py` | 4 new tests |
| 5: Markdown output | — | `markdown.py`, `test_output.py` | 3 new tests |
| 6: GitHub PR output | — | `github_pr.py`, `test_output.py` | 2 new tests |
| 7: final pass | — | — | full suite + lint |
