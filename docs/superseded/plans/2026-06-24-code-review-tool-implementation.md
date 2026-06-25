# Code Review Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-pass AI code review tool that delegates to AI CLIs (claude-code, opencode, codex), outputs structured findings locally and to GitHub PRs, and learns from human feedback.

**Architecture:** Python CLI tool that fetches PR diffs, runs parallel review passes via AI CLI subprocesses, merges/deduplicates findings, and outputs as JSON/markdown/table or GitHub PR comments. Memory store (SQLite) tracks feedback to improve future reviews.

**Tech Stack:** Python 3.14+, uv, Pydantic, click, aiosqlite, `gh` CLI

**Spec:** `docs/superseded/specs/2026-06-24-code-review-tool-design.md`

---

## File Structure

```
superseded/
├── pyproject.toml                          # Project config, dependencies, entry point
├── src/
│   └── superseded/
│       ├── __init__.py
│       ├── cli.py                          # CLI entry point (click)
│       ├── config.py                       # Config loader (.superseded.yaml)
│       ├── models.py                       # Pydantic models (Finding, ReviewResult, PassConfig)
│       ├── diff.py                         # Diff fetching (gh pr diff / git diff)
│       ├── review/
│       │   ├── __init__.py
│       │   ├── engine.py                   # Orchestrates parallel passes
│       │   ├── prompts.py                  # Prompt templates per pass
│       │   └── merger.py                   # Dedupe + rank findings
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── base.py                     # Agent ABC
│       │   ├── claude_code.py              # Claude Code adapter
│       │   ├── opencode.py                 # OpenCode adapter
│       │   └── codex.py                    # Codex adapter
│       ├── output/
│       │   ├── __init__.py
│       │   ├── json_out.py                 # JSON formatter
│       │   ├── markdown.py                 # Markdown formatter
│       │   ├── table.py                    # Terminal table formatter
│       │   └── github_pr.py               # Post to PR via gh api
│       └── memory/
│           ├── __init__.py
│           ├── store.py                    # SQLite memory store
│           └── feedback.py                 # Check reactions/resolutions
├── action.yml                              # GitHub Action definition
├── Dockerfile                              # For GH Action
└── tests/
    ├── __init__.py
    ├── test_config.py
    ├── test_diff.py
    ├── test_agents.py
    ├── test_engine.py
    ├── test_merger.py
    ├── test_output.py
    ├── test_memory.py
    └── test_cli.py
```

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/superseded/__init__.py`
- Create: `src/superseded/cli.py`
- Create: `tests/__init__.py`

- [x] **Step 1: Remove old source code**

```bash
rm -rf src/superseded/ templates/ static/ migrations/ alembic.ini docker-compose.yml Dockerfile start.sh
rm -rf e2e/ playwright.config.ts index.html node_modules/ package.json package-lock.json
rm -rf vendor/ .superseded/ .claude/ .code-review-graph/
```

Keep: `docs/`, `.git/`, `.gitignore`, `LICENSE`, `README.md`, `AGENTS.md`, `prd.md`

- [x] **Step 2: Write new pyproject.toml**

```toml
[project]
name = "superseded"
version = "0.1.0"
description = "Multi-pass AI code review tool"
requires-python = ">=3.14"
dependencies = [
    "click>=8.1.0",
    "pydantic>=2.10.0",
    "pyyaml>=6.0.0",
    "aiosqlite>=0.21.0",
    "rich>=13.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.25.0",
    "httpx>=0.28.0",
    "ruff>=0.9.0",
]

[project.scripts]
superseded = "superseded.cli:cli"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/superseded"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py314"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "W", "F", "I", "N", "UP", "B", "SIM", "TCH", "RUF"]
ignore = ["E501", "B008", "TC001", "TC002", "TC003", "E741"]

[tool.ruff.lint.isort]
known-first-party = ["superseded"]

[tool.ruff.format]
quote-style = "double"
```

- [x] **Step 3: Write src/superseded/__init__.py**

```python
from __future__ import annotations

__version__ = "0.1.0"
```

- [x] **Step 4: Write minimal CLI**

```python
from __future__ import annotations

import click


@click.group()
@click.version_option()
def cli() -> None:
    """Superseded — multi-pass AI code review tool."""
    pass


@cli.command()
@click.option("--pr", type=int, help="PR number to review")
@click.option("--diff", "diff_range", help="Git diff range (e.g. HEAD~3..HEAD)")
@click.option("--agent", default=None, help="AI CLI agent (claude-code, opencode, codex)")
@click.option("--model", default=None, help="Model to use")
@click.option("--format", "output_format", type=click.Choice(["json", "markdown", "table"]), default="table")
@click.option("--post", is_flag=True, help="Post review to GitHub PR")
@click.option("--passes", default=None, help="Comma-separated passes to run")
def review(pr, diff_range, agent, model, output_format, post, passes):
    """Review code changes."""
    click.echo("Not yet implemented")


@cli.command()
@click.option("--check", is_flag=True, help="Check for feedback on past reviews")
@click.argument("comment_id", required=False)
@click.option("--helpful", is_flag=True)
@click.option("--dismiss", is_flag=True)
def feedback(check, comment_id, helpful, dismiss):
    """Manage review feedback."""
    click.echo("Not yet implemented")
```

- [x] **Step 5: Write tests/__init__.py**

```python
```

- [x] **Step 6: Install dependencies and verify CLI runs**

```bash
uv sync
uv run superseded --version
uv run superseded review --help
```

Expected: version prints, help shows review subcommand with all options.

- [x] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: scaffold project structure for code review tool"
```

---

## Task 2: Pydantic Models

**Files:**
- Create: `src/superseded/models.py`
- Create: `tests/test_models.py`

- [x] **Step 1: Write failing tests for models**

```python
from __future__ import annotations

from superseded.models import Finding, ReviewResult


def test_finding_creation():
    f = Finding(
        pass_name="security",
        severity="critical",
        file="src/auth.py",
        line=42,
        end_line=45,
        title="SQL injection",
        description="User input interpolated into SQL",
        suggestion="Use parameterized queries",
    )
    assert f.pass_name == "security"
    assert f.severity == "critical"
    assert f.file == "src/auth.py"
    assert f.line == 42


def test_finding_generates_id():
    f = Finding(
        pass_name="security",
        severity="critical",
        file="src/auth.py",
        line=42,
        end_line=45,
        title="SQL injection",
        description="desc",
        suggestion="fix",
    )
    assert f.id.startswith("security-")
    assert len(f.id) > 10


def test_review_result_from_findings():
    findings = [
        Finding(
            pass_name="security",
            severity="critical",
            file="a.py",
            line=1,
            end_line=2,
            title="t",
            description="d",
            suggestion="s",
        ),
        Finding(
            pass_name="style",
            severity="nit",
            file="b.py",
            line=5,
            end_line=5,
            title="t2",
            description="d2",
            suggestion="s2",
        ),
    ]
    result = ReviewResult(findings=findings)
    assert len(result.findings) == 2
    assert result.summary["critical"] == 1
    assert result.summary["nit"] == 1
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_models.py -v
```

Expected: FAIL — module not found.

- [x] **Step 3: Write models.py**

```python
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "important", "suggestion", "nit"]
PassName = Literal["security", "correctness", "performance", "style", "architecture"]


class Finding(BaseModel):
    pass_name: str
    severity: Severity
    file: str
    line: int
    end_line: int
    title: str
    description: str
    suggestion: str
    id: str = Field(default="")

    def model_post_init(self, __context) -> None:
        if not self.id:
            raw = f"{self.pass_name}-{self.file}-{self.line}-{self.title}"
            self.id = f"{self.pass_name}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


class ReviewResult(BaseModel):
    findings: list[Finding] = []

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_models.py -v
```

Expected: 3 passed.

- [x] **Step 5: Commit**

```bash
git add src/superseded/models.py tests/test_models.py
git commit -m "feat: add Pydantic models for Finding and ReviewResult"
```

---

## Task 3: Config Loader

**Files:**
- Create: `src/superseded/config.py`
- Create: `tests/test_config.py`

- [x] **Step 1: Write failing tests for config**

```python
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from superseded.config import Config, load_config


def test_default_config():
    cfg = Config()
    assert cfg.agent == "claude-code"
    assert cfg.model is None
    assert cfg.passes.security is True
    assert cfg.post_to_pr is False
    assert cfg.format == "table"
    assert cfg.memory is True


def test_load_config_from_yaml():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("agent: opencode\ngpt-4o\npasses:\n  security: false\n  style: false\n")
        f.flush()
        cfg = load_config(Path(f.name))
        assert cfg.agent == "opencode"
        assert cfg.passes.security is False
    os.unlink(f.name)


def test_config_passes_override():
    cfg = Config()
    assert cfg.is_pass_enabled("security") is True
    assert cfg.is_pass_enabled("nonexistent") is False
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL — module not found.

- [x] **Step 3: Write config.py**

```python
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class PassConfig(BaseModel):
    security: bool = True
    correctness: bool = True
    performance: bool = True
    style: bool = True
    architecture: bool = True


class Config(BaseModel):
    agent: str = "opencode"
    model: str | None = "deepseek-v4-pro"
    passes: PassConfig = PassConfig()
    post_to_pr: bool = False
    format: str = "table"
    memory: bool = True

    def is_pass_enabled(self, name: str) -> bool:
        return getattr(self.passes, name, False)


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path(".superseded.yaml")
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text()) or {}
    return Config(**data)
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -v
```

Expected: 3 passed.

- [x] **Step 5: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat: add config loader for .superseded.yaml"
```

---

## Task 4: Diff Fetching

**Files:**
- Create: `src/superseded/diff.py`
- Create: `tests/test_diff.py`

- [x] **Step 1: Write failing tests for diff fetching**

```python
from __future__ import annotations

from unittest.mock import patch, MagicMock

from superseded.diff import fetch_diff, parse_diff_files


def test_parse_diff_files():
    diff = """diff --git a/src/auth.py b/src/auth.py
index abc1234..def5678 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,6 +10,8 @@ def login():
     username = request.args.get("user")
+    password = request.args.get("pass")
+    return authenticate(username, password)
"""
    files = parse_diff_files(diff)
    assert len(files) == 1
    assert files[0]["file"] == "src/auth.py"
    assert "password" in files[0]["diff"]


def test_parse_diff_files_multiple():
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old
+new
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1 +1 @@
-foo
+bar
"""
    files = parse_diff_files(diff)
    assert len(files) == 2
    assert files[0]["file"] == "a.py"
    assert files[1]["file"] == "b.py"
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_diff.py -v
```

Expected: FAIL — module not found.

- [x] **Step 3: Write diff.py**

```python
from __future__ import annotations

import re
import subprocess


def fetch_diff(pr: int | None = None, diff_range: str | None = None) -> str:
    if pr is not None:
        return _fetch_pr_diff(pr)
    if diff_range is not None:
        return _fetch_git_diff(diff_range)
    raise ValueError("Either --pr or --diff must be provided")


def _fetch_pr_diff(pr: int) -> str:
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _fetch_git_diff(diff_range: str) -> str:
    result = subprocess.run(
        ["git", "diff", diff_range],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def parse_diff_files(diff: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    parts = re.split(r"^diff --git ", diff, flags=re.MULTILINE)
    for part in parts:
        if not part.strip():
            continue
        match = re.search(r"a/(.+?) b/", part)
        if match:
            files.append({"file": match.group(1), "diff": "diff --git " + part})
    return files
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_diff.py -v
```

Expected: 2 passed.

- [x] **Step 5: Commit**

```bash
git add src/superseded/diff.py tests/test_diff.py
git commit -m "feat: add diff fetching from gh pr and git"
```

---

## Task 5: Agent Base Class + Claude Code Adapter

**Files:**
- Create: `src/superseded/agents/__init__.py`
- Create: `src/superseded/agents/base.py`
- Create: `src/superseded/agents/claude_code.py`
- Create: `tests/test_agents.py`

- [x] **Step 1: Write failing tests for agent base and Claude Code adapter**

```python
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

from superseded.agents.base import Agent
from superseded.agents.claude_code import ClaudeCodeAgent


def test_agent_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        Agent()


def test_claude_code_agent_name():
    agent = ClaudeCodeAgent(model="claude-sonnet-4-20250514")
    assert agent.name == "claude-code"


def test_claude_code_build_command():
    agent = ClaudeCodeAgent(model="claude-sonnet-4-20250514")
    cmd = agent.build_command("Review this code")
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "--bare" in cmd
    assert "--model" in cmd
    assert "claude-sonnet-4-20250514" in cmd


def test_claude_code_parse_output():
    agent = ClaudeCodeAgent()
    raw = '''Here are the findings:
```json
[{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "t", "description": "d", "suggestion": "s"}]
```
'''
    findings = agent.parse_output(raw, "security")
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"
    assert findings[0]["pass_name"] == "security"
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_agents.py -v
```

Expected: FAIL — module not found.

- [x] **Step 3: Write agents/__init__.py**

```python
from __future__ import annotations
```

- [x] **Step 4: Write agents/base.py**

```python
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod


class Agent(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def build_command(self, prompt: str) -> list[str]: ...

    @abstractmethod
    def parse_output(self, raw: str, pass_name: str) -> list[dict]: ...

    def is_available(self) -> bool:
        binary = self.build_command("test")[0]
        return shutil.which(binary) is not None
```

- [x] **Step 5: Write agents/claude_code.py**

```python
from __future__ import annotations

import json
import re

from superseded.agents.base import Agent


class ClaudeCodeAgent(Agent):
    def __init__(self, model: str | None = None) -> None:
        self._model = model or "claude-sonnet-4-20250514"

    @property
    def name(self) -> str:
        return "claude-code"

    def build_command(self, prompt: str) -> list[str]:
        return ["claude", "-p", prompt, "--bare", "--model", self._model]

    def parse_output(self, raw: str, pass_name: str) -> list[dict]:
        return _extract_findings(raw, pass_name)


def _extract_findings(raw: str, pass_name: str) -> list[dict]:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    findings = []
    for item in items:
        item["pass_name"] = pass_name
        findings.append(item)
    return findings
```

- [x] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_agents.py -v
```

Expected: 4 passed.

- [x] **Step 7: Commit**

```bash
git add src/superseded/agents/ tests/test_agents.py
git commit -m "feat: add agent base class and Claude Code adapter"
```

---

## Task 6: OpenCode + Codex Adapters

**Files:**
- Create: `src/superseded/agents/opencode.py`
- Create: `src/superseded/agents/codex.py`
- Modify: `tests/test_agents.py`

- [x] **Step 1: Add tests for OpenCode and Codex agents**

Append to `tests/test_agents.py`:

```python
from superseded.agents.opencode import OpenCodeAgent
from superseded.agents.codex import CodexAgent


def test_opencode_agent_name():
    agent = OpenCodeAgent()
    assert agent.name == "opencode"


def test_opencode_build_command():
    agent = OpenCodeAgent()
    cmd = agent.build_command("Review this code")
    assert cmd[0] == "opencode"
    assert "run" in cmd


def test_codex_agent_name():
    agent = CodexAgent(model="gpt-4o")
    assert agent.name == "codex"


def test_codex_build_command():
    agent = CodexAgent(model="gpt-4o")
    cmd = agent.build_command("Review this code")
    assert cmd[0] == "codex"
    assert "exec" in cmd
    assert "--json" in cmd
    assert "--model" in cmd
    assert "gpt-4o" in cmd
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_agents.py -v -k "opencode or codex"
```

Expected: FAIL — module not found.

- [x] **Step 3: Write agents/opencode.py**

```python
from __future__ import annotations

import json
import re

from superseded.agents.base import Agent


class OpenCodeAgent(Agent):
    def __init__(self, model: str | None = None) -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "opencode"

    def build_command(self, prompt: str) -> list[str]:
        cmd = ["opencode", "run", prompt]
        return cmd

    def parse_output(self, raw: str, pass_name: str) -> list[dict]:
        return _extract_findings(raw, pass_name)


def _extract_findings(raw: str, pass_name: str) -> list[dict]:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    findings = []
    for item in items:
        item["pass_name"] = pass_name
        findings.append(item)
    return findings
```

- [x] **Step 4: Write agents/codex.py**

```python
from __future__ import annotations

import json
import re

from superseded.agents.base import Agent


class CodexAgent(Agent):
    def __init__(self, model: str | None = None) -> None:
        self._model = model or "gpt-4o"

    @property
    def name(self) -> str:
        return "codex"

    def build_command(self, prompt: str) -> list[str]:
        return ["codex", "exec", prompt, "--json", "--model", self._model]

    def parse_output(self, raw: str, pass_name: str) -> list[dict]:
        return _extract_findings_jsonl(raw, pass_name)


def _extract_findings_jsonl(raw: str, pass_name: str) -> list[dict]:
    # Codex outputs JSON Lines — find the last assistant message
    assistant_text = ""
    for line in raw.strip().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "message" and event.get("role") == "assistant":
            content = event.get("content", [])
            for block in content:
                if block.get("type") == "text":
                    assistant_text = block["text"]
    if not assistant_text:
        return []
    match = re.search(r"\[.*\]", assistant_text, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    findings = []
    for item in items:
        item["pass_name"] = pass_name
        findings.append(item)
    return findings
```

- [x] **Step 5: Run all agent tests**

```bash
uv run pytest tests/test_agents.py -v
```

Expected: 6 passed.

- [x] **Step 6: Commit**

```bash
git add src/superseded/agents/opencode.py src/superseded/agents/codex.py tests/test_agents.py
git commit -m "feat: add OpenCode and Codex agent adapters"
```

---

## Task 7: Review Prompts

**Files:**
- Create: `src/superseded/review/__init__.py`
- Create: `src/superseded/review/prompts.py`
- Create: `tests/test_prompts.py`

- [x] **Step 1: Write failing tests for prompts**

```python
from __future__ import annotations

from superseded.review.prompts import build_prompt, PASS_INSTRUCTIONS


def test_build_prompt_includes_diff():
    prompt = build_prompt(
        pass_name="security",
        diff="diff --git a/a.py ...",
        pr_description="Fix login bug",
        file_context=None,
        memory_context=None,
    )
    assert "security" in prompt.lower()
    assert "diff --git a/a.py" in prompt
    assert "Fix login bug" in prompt


def test_build_prompt_includes_memory():
    prompt = build_prompt(
        pass_name="style",
        diff="diff",
        pr_description=None,
        file_context=None,
        memory_context="Past dismissed: missing type hints — not enforced",
    )
    assert "Past dismissed" in prompt


def test_build_prompt_includes_file_context():
    prompt = build_prompt(
        pass_name="correctness",
        diff="diff",
        pr_description=None,
        file_context="def login():\n    pass",
        memory_context=None,
    )
    assert "def login():" in prompt


def test_all_passes_have_instructions():
    for name in ["security", "correctness", "performance", "style", "architecture"]:
        assert name in PASS_INSTRUCTIONS
        assert len(PASS_INSTRUCTIONS[name]) > 10
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_prompts.py -v
```

Expected: FAIL — module not found.

- [x] **Step 3: Write review/__init__.py**

```python
from __future__ import annotations
```

- [x] **Step 4: Write review/prompts.py**

```python
from __future__ import annotations

PASS_INSTRUCTIONS: dict[str, str] = {
    "security": (
        "Focus on: injection vulnerabilities, auth bypass, secret exposure, "
        "unsafe deserialization, path traversal, SSRF, XSS. Think like an attacker."
    ),
    "correctness": (
        "Focus on: logic errors, off-by-one, null/undefined handling, race conditions, "
        "error handling gaps, incorrect assumptions. Does the code match the PR description?"
    ),
    "performance": (
        "Focus on: N+1 queries, unnecessary allocations, blocking I/O in async paths, "
        "O(n²) where O(n) is possible, missing caching opportunities."
    ),
    "style": (
        "Focus on: unclear naming, dead code, overly complex logic, inconsistent patterns "
        "with the rest of the codebase, missing type hints."
    ),
    "architecture": (
        "Focus on: separation of concerns, API contract changes, dependency direction, "
        "coupling between modules, public interface changes."
    ),
}

JSON_FORMAT_INSTRUCTIONS = """
## Output Format
Return ONLY a JSON array. No explanation text before or after.

[
  {
    "severity": "critical|important|suggestion|nit",
    "file": "path/to/file.py",
    "line": 42,
    "end_line": 45,
    "title": "Short description",
    "description": "Detailed explanation of the issue",
    "suggestion": "Code fix or suggestion"
  }
]

If no issues found, return: []
"""


def build_prompt(
    pass_name: str,
    diff: str,
    pr_description: str | None,
    file_context: str | None,
    memory_context: str | None,
) -> str:
    instructions = PASS_INSTRUCTIONS.get(pass_name, "Review for issues.")
    pr_desc = pr_description or "No description provided."
    ctx = file_context or "No additional file context available."
    mem = memory_context or "No past feedback."

    return f"""You are performing a {pass_name} code review.

## Your Role
{instructions}

## Rules
- Only report genuine issues, not style preferences unless they impact readability
- Be specific: cite exact file, line numbers, and code
- Provide actionable suggestions with code examples
- Do NOT report issues in unchanged code unless directly related to the change
- If there are no issues in this category, return an empty array

## Context

### PR Description
{pr_desc}

### Changed Files (diff)
{diff}

### File Context (surrounding code for changed files, ±20 lines from changes)
{ctx}

### Past Feedback (findings dismissed by humans — avoid similar)
{mem}

{JSON_FORMAT_INSTRUCTIONS}"""
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_prompts.py -v
```

Expected: 4 passed.

- [x] **Step 6: Commit**

```bash
git add src/superseded/review/ tests/test_prompts.py
git commit -m "feat: add review prompt templates per pass"
```

---

## Task 8: Review Engine

**Files:**
- Create: `src/superseded/review/engine.py`
- Create: `tests/test_engine.py`

- [x] **Step 1: Write failing tests for review engine**

```python
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from superseded.models import Finding
from superseded.review.engine import ReviewEngine


def make_finding(pass_name="security", severity="critical", file="a.py", line=1):
    return Finding(
        pass_name=pass_name,
        severity=severity,
        file=file,
        line=line,
        end_line=line + 1,
        title="test issue",
        description="desc",
        suggestion="fix",
    )


def test_engine_deduplicates():
    f1 = make_finding()
    f2 = make_finding()  # same finding
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[f1], [f2]])
    assert len(result.findings) == 1


def test_engine_sorts_by_severity():
    f1 = make_finding(severity="nit")
    f2 = make_finding(severity="critical")
    f3 = make_finding(severity="suggestion")
    engine = ReviewEngine(agent=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[f1, f2, f3]])
    assert result.findings[0].severity == "critical"
    assert result.findings[-1].severity == "nit"


def test_engine_selects_agent():
    from superseded.agents.claude_code import ClaudeCodeAgent
    from superseded.agents.opencode import OpenCodeAgent
    from superseded.agents.codex import CodexAgent

    engine = ReviewEngine.select("claude-code", model="m")
    assert isinstance(engine.agent, ClaudeCodeAgent)

    engine = ReviewEngine.select("opencode", model="m")
    assert isinstance(engine.agent, OpenCodeAgent)

    engine = ReviewEngine.select("codex", model="m")
    assert isinstance(engine.agent, CodexAgent)
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_engine.py -v
```

Expected: FAIL — module not found.

- [x] **Step 3: Write review/engine.py**

```python
from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING

from superseded.models import Finding, ReviewResult
from superseded.agents.base import Agent
from superseded.agents.claude_code import ClaudeCodeAgent
from superseded.agents.opencode import OpenCodeAgent
from superseded.agents.codex import CodexAgent
from superseded.review.prompts import build_prompt

if TYPE_CHECKING:
    from superseded.config import Config

SEVERITY_ORDER = {"critical": 0, "important": 1, "suggestion": 2, "nit": 3}

AGENT_MAP: dict[str, type[Agent]] = {
    "claude-code": ClaudeCodeAgent,
    "opencode": OpenCodeAgent,
    "codex": CodexAgent,
}


class ReviewEngine:
    def __init__(self, agent: Agent, config: Config) -> None:
        self.agent = agent
        self.config = config

    @classmethod
    def select(cls, agent_name: str, model: str | None) -> ReviewEngine:
        from superseded.config import Config

        agent_cls = AGENT_MAP.get(agent_name)
        if agent_cls is None:
            raise ValueError(f"Unknown agent: {agent_name}. Choose from: {list(AGENT_MAP)}")
        agent = agent_cls(model=model)
        return cls(agent=agent, config=Config())

    def run_pass(self, pass_name: str, prompt: str) -> list[Finding]:
        cmd = self.agent.build_command(prompt)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            raise RuntimeError(
                f"Agent CLI '{cmd[0]}' not found on PATH. "
                f"Install it or choose a different agent with --agent."
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Agent timed out after 300 seconds for pass: {pass_name}")

        raw_findings = self.agent.parse_output(result.stdout, pass_name)
        findings = []
        for item in raw_findings:
            try:
                findings.append(Finding(**item))
            except Exception:
                continue
        return findings

    def review(
        self,
        diff: str,
        pr_description: str | None = None,
        file_context: str | None = None,
        memory_context: str | None = None,
        passes: list[str] | None = None,
    ) -> ReviewResult:
        if passes is None:
            passes = [n for n in ["security", "correctness", "performance", "style", "architecture"]
                      if self.config.is_pass_enabled(n)]

        all_findings: list[list[Finding]] = []
        for pass_name in passes:
            prompt = build_prompt(
                pass_name=pass_name,
                diff=diff,
                pr_description=pr_description,
                file_context=file_context,
                memory_context=memory_context,
            )
            findings = self.run_pass(pass_name, prompt)
            all_findings.append(findings)

        return self.merge_findings(all_findings)

    def merge_findings(self, finding_groups: list[list[Finding]]) -> ReviewResult:
        seen: dict[str, Finding] = {}
        for group in finding_groups:
            for f in group:
                if f.id not in seen:
                    seen[f.id] = f

        sorted_findings = sorted(seen.values(), key=lambda f: SEVERITY_ORDER.get(f.severity, 99))
        return ReviewResult(findings=sorted_findings)
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_engine.py -v
```

Expected: 3 passed.

- [x] **Step 5: Commit**

```bash
git add src/superseded/review/engine.py tests/test_engine.py
git commit -m "feat: add review engine with parallel pass orchestration"
```

---

## Task 9: Output Formatters

**Files:**
- Create: `src/superseded/output/__init__.py`
- Create: `src/superseded/output/json_out.py`
- Create: `src/superseded/output/markdown.py`
- Create: `src/superseded/output/table.py`
- Create: `tests/test_output.py`

- [x] **Step 1: Write failing tests for output formatters**

```python
from __future__ import annotations

import json

from superseded.models import Finding, ReviewResult
from superseded.output.json_out import format_json
from superseded.output.markdown import format_markdown
from superseded.output.table import format_table


def make_result():
    return ReviewResult(
        findings=[
            Finding(
                pass_name="security",
                severity="critical",
                file="src/auth.py",
                line=42,
                end_line=45,
                title="SQL injection",
                description="User input in SQL",
                suggestion="Use params",
            ),
        ]
    )


def test_json_output():
    result = make_result()
    out = format_json(result)
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["severity"] == "critical"


def test_markdown_output():
    result = make_result()
    out = format_markdown(result)
    assert "# Code Review" in out
    assert "critical" in out.lower()
    assert "SQL injection" in out


def test_table_output():
    result = make_result()
    out = format_table(result)
    assert "critical" in out
    assert "SQL injection" in out


def test_empty_result():
    result = ReviewResult(findings=[])
    assert "No issues" in format_markdown(result).lower() or format_markdown(result).strip() != ""
    assert "No issues" in format_table(result).lower() or format_table(result).strip() != ""
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_output.py -v
```

Expected: FAIL — module not found.

- [x] **Step 3: Write output/__init__.py**

```python
from __future__ import annotations
```

- [x] **Step 4: Write output/json_out.py**

```python
from __future__ import annotations

import json

from superseded.models import ReviewResult


def format_json(result: ReviewResult) -> str:
    data = [f.model_dump(exclude={"id"}) for f in result.findings]
    return json.dumps(data, indent=2)
```

- [x] **Step 5: Write output/markdown.py**

```python
from __future__ import annotations

from superseded.models import ReviewResult


def format_markdown(result: ReviewResult) -> str:
    if not result.findings:
        return "# Code Review\n\nNo issues found.\n"

    lines = ["# Code Review", ""]

    severity_labels = {
        "critical": "Critical",
        "important": "Important",
        "suggestion": "Suggestion",
        "nit": "Nit",
    }

    for severity in ["critical", "important", "suggestion", "nit"]:
        group = [f for f in result.findings if f.severity == severity]
        if not group:
            continue
        lines.append(f"## {severity_labels[severity]} ({len(group)})")
        lines.append("")
        for f in group:
            lines.append(f"### {f.title}")
            lines.append(f"**{f.file}:{f.line}-{f.end_line}** ({f.pass_name})")
            lines.append("")
            lines.append(f.description)
            lines.append("")
            lines.append(f"**Suggestion:** {f.suggestion}")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)
```

- [x] **Step 6: Write output/table.py**

```python
from __future__ import annotations

from superseded.models import ReviewResult

SEVERITY_ICONS = {
    "critical": "🔴",
    "important": "🟠",
    "suggestion": "🟡",
    "nit": "⚪",
}


def format_table(result: ReviewResult) -> str:
    if not result.findings:
        return "No issues found."

    lines = []
    header = f"{'Sev':<12} {'Pass':<14} {'File':<30} {'Line':<6} {'Title'}"
    lines.append(header)
    lines.append("-" * len(header))

    for f in result.findings:
        icon = SEVERITY_ICONS.get(f.severity, "⚪")
        lines.append(
            f"{icon} {f.severity:<10} {f.pass_name:<14} {f.file:<30} {f.line:<6} {f.title}"
        )

    lines.append("")
    lines.append(f"Total: {len(result.findings)} findings")
    for sev, count in result.summary.items():
        lines.append(f"  {sev}: {count}")

    return "\n".join(lines)
```

- [x] **Step 7: Run tests to verify they pass**

```bash
uv run pytest tests/test_output.py -v
```

Expected: 4 passed.

- [x] **Step 8: Commit**

```bash
git add src/superseded/output/ tests/test_output.py
git commit -m "feat: add JSON, markdown, and table output formatters"
```

---

## Task 10: GitHub PR Output

**Files:**
- Create: `src/superseded/output/github_pr.py`
- Modify: `tests/test_output.py`

- [x] **Step 1: Add test for GitHub PR posting**

Append to `tests/test_output.py`:

```python
from unittest.mock import patch, MagicMock
from superseded.output.github_pr import post_review_to_pr


@patch("subprocess.run")
def test_post_review_to_pr(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    result = make_result()
    post_review_to_pr(pr=123, result=result, repo="owner/repo")
    # Should call gh api to create a review
    assert mock_run.called
    args = mock_run.call_args[0][0]
    assert "gh" in args
    assert "api" in args
```

- [x] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_output.py -v -k "post_review"
```

Expected: FAIL — module not found.

- [x] **Step 3: Write output/github_pr.py**

```python
from __future__ import annotations

import json
import subprocess

from superseded.models import ReviewResult


def post_review_to_pr(pr: int, result: ReviewResult, repo: str | None = None) -> None:
    comments = []
    for f in result.findings:
        comments.append({
            "path": f.file,
            "line": f.end_line,
            "body": (
                f"**[{f.severity.upper()}] {f.title}** ({f.pass_name})\n\n"
                f"{f.description}\n\n"
                f"**Suggestion:** {f.suggestion}"
            ),
        })

    event = "REQUEST_CHANGES" if result.summary.get("critical", 0) > 0 else "COMMENT"

    body = f"## Superseded Code Review\n\n"
    for sev, count in result.summary.items():
        body += f"- **{sev}:** {count}\n"

    payload = {
        "event": event,
        "body": body,
        "comments": comments,
    }

    cmd = ["gh", "api", f"repos/{_repo(pr)}/pulls/{pr}/reviews", "--input", "-"]
    subprocess.run(cmd, input=json.dumps(payload), text=True, check=True)


def _repo(pr: int) -> str:
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name", "-q", ".owner.login + \"/\" + .name"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
```

- [x] **Step 4: Run tests**

```bash
uv run pytest tests/test_output.py -v
```

Expected: 5 passed.

- [x] **Step 5: Commit**

```bash
git add src/superseded/output/github_pr.py tests/test_output.py
git commit -m "feat: add GitHub PR review posting via gh api"
```

---

## Task 11: Memory Store

**Files:**
- Create: `src/superseded/memory/__init__.py`
- Create: `src/superseded/memory/store.py`
- Create: `tests/test_memory.py`

- [x] **Step 1: Write failing tests for memory store**

```python
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
        assert findings[0]["finding_id"] == "sec-abc123"


def test_memory_store():
    asyncio.run(_test_store())
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_memory.py -v
```

Expected: FAIL — module not found.

- [x] **Step 3: Write memory/__init__.py**

```python
from __future__ import annotations
```

- [x] **Step 4: Write memory/store.py**

```python
from __future__ import annotations

from pathlib import Path

import aiosqlite

DEFAULT_DB_PATH = Path(".superseded/memory.db")

SCHEMA = """
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id TEXT REFERENCES findings(id),
    action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class MemoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)

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
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO findings (id, repo, pass, severity, file, line, title, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (finding_id, repo, pass_name, severity, file, line, title, description),
            )
            await db.commit()

    async def record_feedback(self, finding_id: str, action: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO feedback (finding_id, action) VALUES (?, ?)",
                (finding_id, action),
            )
            if action == "dismiss":
                await db.execute(
                    "UPDATE findings SET dismissed = TRUE WHERE id = ?",
                    (finding_id,),
                )
            await db.commit()

    async def get_dismissed_findings(self, repo: str) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM findings WHERE repo = ? AND dismissed = TRUE",
                (repo,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory.py -v
```

Expected: 1 passed.

- [x] **Step 6: Commit**

```bash
git add src/superseded/memory/ tests/test_memory.py
git commit -m "feat: add SQLite memory store for findings and feedback"
```

---

## Task 12: Feedback Collection

**Files:**
- Create: `src/superseded/memory/feedback.py`
- Modify: `tests/test_memory.py`

- [x] **Step 1: Add test for feedback collection**

Append to `tests/test_memory.py`:

```python
from unittest.mock import patch, MagicMock
from superseded.memory.feedback import check_pr_feedback


@patch("subprocess.run")
def test_check_pr_feedback(mock_run):
    # Mock gh api response for review comments
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='[{"id": 1, "body": "test", "path": "a.py", "line": 1}]',
    )
    feedback = check_pr_feedback(pr=123, repo="owner/repo")
    assert isinstance(feedback, list)
```

- [x] **Step 2: Write memory/feedback.py**

```python
from __future__ import annotations

import json
import subprocess


def check_pr_feedback(pr: int, repo: str) -> list[dict]:
    try:
        result = subprocess.run(
            [
                "gh", "api",
                f"repos/{repo}/pulls/{pr}/comments",
                "--jq", ".[] | {id: .id, body: .body, path: .path, line: .line}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    comments = []
    for line in result.stdout.strip().splitlines():
        if line:
            try:
                comments.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return comments
```

- [x] **Step 3: Run tests**

```bash
uv run pytest tests/test_memory.py -v
```

Expected: 2 passed.

- [x] **Step 4: Commit**

```bash
git add src/superseded/memory/feedback.py tests/test_memory.py
git commit -m "feat: add feedback collection from PR comments"
```

---

## Task 13: Wire Up CLI

**Files:**
- Modify: `src/superseded/cli.py`
- Create: `tests/test_cli.py`

- [x] **Step 1: Write failing tests for CLI integration**

```python
from __future__ import annotations

from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from superseded.cli import cli


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_review_requires_pr_or_diff():
    runner = CliRunner()
    result = runner.invoke(cli, ["review"])
    assert result.exit_code != 0
    assert "pr" in result.output.lower() or "diff" in result.output.lower()


@patch("superseded.cli._run_review")
def test_review_with_pr(mock_review):
    mock_review.return_value = None
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "123"])
    assert result.exit_code == 0
    mock_review.assert_called_once()
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: FAIL — _run_review not found.

- [x] **Step 3: Update cli.py with full implementation**

```python
from __future__ import annotations

import asyncio
import sys

import click

from superseded.config import load_config
from superseded.diff import fetch_diff, parse_diff_files
from superseded.models import ReviewResult
from superseded.review.engine import ReviewEngine


@click.group()
@click.version_option(version="0.1.0")
def cli() -> None:
    """Superseded — multi-pass AI code review tool."""
    pass


@cli.command()
@click.option("--pr", type=int, help="PR number to review")
@click.option("--diff", "diff_range", help="Git diff range (e.g. HEAD~3..HEAD)")
@click.option("--agent", default=None, help="AI CLI agent (claude-code, opencode, codex)")
@click.option("--model", default=None, help="Model to use")
@click.option(
    "--format", "output_format",
    type=click.Choice(["json", "markdown", "table"]),
    default=None,
    help="Output format",
)
@click.option("--post", is_flag=True, help="Post review to GitHub PR")
@click.option("--passes", default=None, help="Comma-separated passes to run")
def review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: str | None,
) -> None:
    """Review code changes."""
    if pr is None and diff_range is None:
        click.echo("Error: Either --pr or --diff must be provided.", err=True)
        sys.exit(1)

    _run_review(pr=pr, diff_range=diff_range, agent=agent, model=model,
                output_format=output_format, post=post, passes=passes)


def _run_review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: str | None,
) -> None:
    config = load_config()
    agent_name = agent or config.agent
    model_name = model or config.model
    fmt = output_format or config.format

    click.echo(f"Fetching diff...")
    diff = fetch_diff(pr=pr, diff_range=diff_range)

    pass_list = passes.split(",") if passes else None

    click.echo(f"Running review with {agent_name}...")
    engine = ReviewEngine.select(agent_name, model=model_name)
    result = engine.review(diff=diff, passes=pass_list)

    from superseded.output.json_out import format_json
    from superseded.output.markdown import format_markdown
    from superseded.output.table import format_table

    if fmt == "json":
        click.echo(format_json(result))
    elif fmt == "markdown":
        click.echo(format_markdown(result))
    else:
        click.echo(format_table(result))

    if post and pr is not None:
        from superseded.output.github_pr import post_review_to_pr
        click.echo("Posting to GitHub PR...")
        post_review_to_pr(pr=pr, result=result)
        click.echo("Done.")


@cli.command()
@click.option("--check", is_flag=True, help="Check for feedback on past reviews")
@click.argument("comment_id", required=False)
@click.option("--helpful", is_flag=True)
@click.option("--dismiss", is_flag=True)
def feedback(check: bool, comment_id: str | None, helpful: bool, dismiss: bool) -> None:
    """Manage review feedback."""
    if check:
        click.echo("Checking for feedback on past reviews...")
        from superseded.memory.feedback import check_pr_feedback
        from superseded.memory.store import MemoryStore
        config = load_config()
        store = MemoryStore()
        asyncio.run(store.init())
        # For --check, user must provide --pr
        click.echo("Use: superseded feedback <comment-id> --helpful/--dismiss")
        return

    if comment_id and (helpful or dismiss):
        action = "helpful" if helpful else "dismiss"
        click.echo(f"Recording {action} for {comment_id}...")
        from superseded.memory.store import MemoryStore
        store = MemoryStore()
        asyncio.run(store.init())
        asyncio.run(store.record_feedback(comment_id, action))
        click.echo(f"Recorded {action} for {comment_id}.")
        return

    click.echo("Usage: superseded feedback --check OR superseded feedback <id> --helpful/--dismiss")
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: 3 passed.

- [x] **Step 5: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [x] **Step 6: Lint and format**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

- [x] **Step 7: Commit**

```bash
git add src/superseded/cli.py tests/test_cli.py
git commit -m "feat: wire up CLI with review and feedback commands"
```

---

## Task 14: GitHub Action

**Files:**
- Create: `action.yml`
- Create: `Dockerfile`

- [x] **Step 1: Write action.yml**

```yaml
name: "Superseded Code Review"
description: "Multi-pass AI code review on pull requests"
inputs:
  agent:
    description: "AI CLI agent (claude-code, opencode, codex)"
    required: false
    default: "opencode"
  model:
    description: "Model to use"
    required: false
    default: ""
  passes:
    description: "Comma-separated passes to run"
    required: false
    default: "security,correctness,performance,style,architecture"
  post:
    description: "Post review to GitHub PR"
    required: false
    default: "true"
runs:
  using: "docker"
  image: "Dockerfile"
  env:
    GITHUB_TOKEN: ${{ github.token }}
```

- [x] **Step 2: Write Dockerfile**

```dockerfile
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl \
    && rm -rf /var/lib/apt/lists/*

# Install gh CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install gh -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

ENTRYPOINT ["superseded"]
CMD ["review", "--pr", "${PR_NUMBER}", "--post"]
```

- [x] **Step 3: Write action entrypoint script**

Create `entrypoint.sh`:

```bash
#!/bin/bash
set -e

AGENT="${INPUT_AGENT:-opencode}"
MODEL="${INPUT_MODEL:-deepseek-v4-pro}"
PASSES="${INPUT_PASSES:-security,correctness,performance,style,architecture}"
POST="${INPUT_POST:-true}"

PR_NUMBER="${GITHUB_EVENT_PULL_REQUEST_NUMBER}"

if [ -z "$PR_NUMBER" ]; then
    echo "Error: Not a pull request event."
    exit 1
fi

CMD="superseded review --pr $PR_NUMBER --agent $AGENT --passes $PASSES"

if [ -n "$MODEL" ]; then
    CMD="$CMD --model $MODEL"
fi

if [ "$POST" = "true" ]; then
    CMD="$CMD --post"
fi

echo "Running: $CMD"
eval "$CMD"
```

- [x] **Step 4: Update Dockerfile to use entrypoint**

```dockerfile
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl \
    && rm -rf /var/lib/apt/lists/*

# Install gh CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install gh -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
```

- [x] **Step 5: Commit**

```bash
git add action.yml Dockerfile entrypoint.sh
git commit -m "feat: add GitHub Action for PR code review"
```

---

## Task 15: Default Config File

**Files:**
- Create: `.superseded.yaml`

- [x] **Step 1: Write default config**

```yaml
agent: opencode
model: deepseek-v4-pro
passes:
  security: true
  correctness: true
  performance: true
  style: true
  architecture: true
post_to_pr: false
format: table
memory: true
```

- [x] **Step 2: Commit**

```bash
git add .superseded.yaml
git commit -m "chore: add default .superseded.yaml config"
```

---

## Task 16: Final Integration Test

**Files:**
- Create: `tests/test_integration.py`

- [x] **Step 1: Write integration test**

```python
from __future__ import annotations

from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from superseded.cli import cli


@patch("superseded.cli.fetch_diff")
@patch("superseded.cli.ReviewEngine")
def test_full_review_flow(mock_engine_cls, mock_fetch):
    mock_fetch.return_value = "diff --git a/a.py b/a.py\n+new line"
    mock_engine = MagicMock()
    mock_engine_cls.select.return_value = mock_engine
    mock_engine.review.return_value = MagicMock(findings=[], summary={})

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--pr", "123", "--agent", "claude-code"])

    assert result.exit_code == 0
    mock_fetch.assert_called_once_with(pr=123, diff_range=None)
    mock_engine.review.assert_called_once()
```

- [x] **Step 2: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [x] **Step 3: Lint and format**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

- [x] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration test for full review flow"
```

---

## Summary

| Task | Description | Key Files |
|------|-------------|-----------|
| 1 | Project scaffolding | `pyproject.toml`, `cli.py` |
| 2 | Pydantic models | `models.py` |
| 3 | Config loader | `config.py` |
| 4 | Diff fetching | `diff.py` |
| 5 | Agent base + Claude Code | `agents/base.py`, `agents/claude_code.py` |
| 6 | OpenCode + Codex adapters | `agents/opencode.py`, `agents/codex.py` |
| 7 | Review prompts | `review/prompts.py` |
| 8 | Review engine | `review/engine.py` |
| 9 | Output formatters | `output/json_out.py`, `output/markdown.py`, `output/table.py` |
| 10 | GitHub PR output | `output/github_pr.py` |
| 11 | Memory store | `memory/store.py` |
| 12 | Feedback collection | `memory/feedback.py` |
| 13 | Wire up CLI | `cli.py` |
| 14 | GitHub Action | `action.yml`, `Dockerfile` |
| 15 | Default config | `.superseded.yaml` |
| 16 | Integration test | `tests/test_integration.py` |
