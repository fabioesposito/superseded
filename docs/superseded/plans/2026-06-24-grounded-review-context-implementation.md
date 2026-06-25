# Grounded Review Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic static-analysis pre-pass and cross-file usage retrieval to the review pipeline, injecting curated context into agent prompts.

**Architecture:** New `src/superseded/context/` package with a pluggable `Tool` protocol for static analysis and a regex-based caller-retrieval module via `ripgrep`. Both are pure functions called from `cli.py`, threaded through `engine.review()` → `build_prompt()` as new optional kwargs. Per-block char caps enforced in each module. Config gains `static_analysis` and `usage_retrieval` bools (on by default, opt-out via `.superseded.yaml`).

**Tech Stack:** Python 3.14+, subprocess for tool/rg invocations, pydantic for config, pytest for testing

---

## File Structure

| File | Role | New/Modified |
|---|---|---|
| `src/superseded/context/__init__.py` | Package marker | New |
| `src/superseded/context/static_analysis.py` | Tool protocol, registry, `run_static_analysis()` | New |
| `src/superseded/context/usage_retrieval.py` | Symbol extraction, `retrieve_usages()` | New |
| `src/superseded/diff.py` | Add `repo_root()` helper | Modified |
| `src/superseded/config.py` | Add `static_analysis`/`usage_retrieval` bools | Modified |
| `src/superseded/cli.py` | Call new context functions, pass new kwargs | Modified |
| `src/superseded/review/prompts.py` | Add `static_signals`/`usage_signals` kwargs, new prompt sections | Modified |
| `tests/test_context_static.py` | Tool detection, command building, output parsing, budget | New |
| `tests/test_context_usage.py` | Symbol extraction, rg mocking, budget | New |
| `tests/test_prompts.py` | Prompt section ordering, empty-kwarg handling | New |
| `tests/test_integration.py` | End-to-end with monkeypatched context | Modified |

---

### Task 0: Fix github_pr.py exception syntax

**Files:**
- Modify: `src/superseded/output/github_pr.py:74`

- [ ] **Step 1: Fix the except clause**

```python
# src/superseded/output/github_pr.py:74
# Before (Python 2 comma form — silently treats FileNotFoundError as the
# exception variable name, so actual FileNotFoundError is uncaught):
except subprocess.CalledProcessError, FileNotFoundError:

# After (correct Python 3 tuple form):
except (subprocess.CalledProcessError, FileNotFoundError):
```

- [ ] **Step 2: Run existing tests to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/superseded/output/github_pr.py
git commit -m "fix: catch exception tuple correctly in github_pr.current_repo"
```

---

### Task 1: Add repo_root() helper to diff.py

**Files:**
- Modify: `src/superseded/diff.py`
- Modify: `tests/test_diff.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diff.py — append to existing file

import subprocess
from pathlib import Path
from unittest.mock import MagicMock


def test_repo_root_returns_path(monkeypatch):
    from superseded.diff import repo_root
    mock = MagicMock(returncode=0, stdout="/mock/repo\n")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock)
    result = repo_root()
    assert result == Path("/mock/repo")


def test_repo_root_falls_back_to_cwd(monkeypatch):
    from superseded.diff import repo_root
    def fail(*a, **kw):
        raise subprocess.CalledProcessError(1, "git")
    monkeypatch.setattr("subprocess.run", fail)
    result = repo_root()
    assert result == Path.cwd()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diff.py::test_repo_root_returns_path tests/test_diff.py::test_repo_root_falls_back_to_cwd -v`
Expected: FAIL — `ImportError: cannot import name 'repo_root'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/superseded/diff.py — append after compute_file_context (after line 92)

def repo_root() -> Path:
    """Return the git repo root, falling back to cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diff.py::test_repo_root_returns_path tests/test_diff.py::test_repo_root_falls_back_to_cwd -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/diff.py tests/test_diff.py
git commit -m "feat: add repo_root() helper to diff.py"
```

---

### Task 2: Add config fields

**Files:**
- Modify: `src/superseded/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py — append to existing file

from superseded.config import Config, load_config


def test_config_defaults():
    c = Config()
    assert c.static_analysis is True
    assert c.usage_retrieval is True


def test_load_config_with_enrichment_flags(tmp_path):
    cfg = tmp_path / ".superseded.yaml"
    cfg.write_text("static_analysis: false\nusage_retrieval: false\n")
    c = load_config(cfg)
    assert c.static_analysis is False
    assert c.usage_retrieval is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_config_defaults tests/test_config.py::test_load_config_with_enrichment_flags -v`
Expected: FAIL — `Config.__init__() got unexpected keyword argument 'static_analysis'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/superseded/config.py — add two fields to Config class after memory

class Config(BaseModel):
    agent: str = "opencode"
    model: str | None = "deepseek-v4-pro"
    passes: PassConfig = PassConfig()
    post_to_pr: bool = False
    format: str = "table"
    memory: bool = True
    static_analysis: bool = True      # <-- NEW
    usage_retrieval: bool = True      # <-- NEW
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_config_defaults tests/test_config.py::test_load_config_with_enrichment_flags -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat: add static_analysis and usage_retrieval config flags"
```

---

### Task 3: Implement static analysis module

**Files:**
- Create: `src/superseded/context/__init__.py`
- Create: `src/superseded/context/static_analysis.py`
- Create: `tests/test_context_static.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_context_static.py — new file

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from superseded.context.static_analysis import (
    STATIC_BUDGET,
    RuffTool,
    run_static_analysis,
)


def test_ruff_detect_true(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
    assert RuffTool().detect(tmp_path) is True


def test_ruff_detect_false(tmp_path):
    assert RuffTool().detect(tmp_path) is False


def test_ruff_build_command():
    tool = RuffTool()
    cmd = tool.build_command(["a.py", "b.py"], Path("/repo"))
    assert cmd[0] == "ruff"
    assert "check" in cmd
    assert "--output-format=concise" in cmd
    assert "a.py" in cmd
    assert "b.py" in cmd


def test_ruff_parse_output():
    tool = RuffTool()
    out = "a.py:1:1: F401 unused\n"
    result = tool.parse_output(out, "", Path("/repo"))
    assert "F401" in result


def test_budget_truncation(monkeypatch):
    tool_output = "x" * (STATIC_BUDGET + 500)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: MagicMock(returncode=1, stdout=tool_output, stderr=""),
    )
    result = run_static_analysis(["a.py"], Path("/repo"), {"python"})
    assert "omitted by static-analysis budget" in result
    assert len(result) <= STATIC_BUDGET + 200  # budget + tail text


def test_missing_binary_skipped(monkeypatch, caplog):
    def fail(*a, **kw):
        raise FileNotFoundError("no such binary")
    monkeypatch.setattr("subprocess.run", fail)
    with caplog.at_level("WARNING"):
        result = run_static_analysis(["a.py"], Path("/repo"), {"python"})
    assert result is None
    assert "not on PATH" in caplog.text


def test_timeout_skipped(monkeypatch, caplog):
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="ruff", timeout=30)
    monkeypatch.setattr("subprocess.run", timeout)
    with caplog.at_level("WARNING"):
        result = run_static_analysis(["a.py"], Path("/repo"), {"python"})
    assert result is None
    assert "timed out" in caplog.text


def test_nonzero_exit_still_parses(monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: MagicMock(returncode=1, stdout="err:1:1: bad\n", stderr=""),
    )
    result = run_static_analysis(["a.py"], Path("/repo"), {"python"})
    assert result is not None
    assert "bad" in result


def test_no_tools_detected_returns_none(monkeypatch):
    """When no tools match repo languages, return None."""
    result = run_static_analysis(["a.py"], Path("/repo"), {"rust"})
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_static.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'superseded.context'`

- [ ] **Step 3: Create package and implement**

Create `src/superseded/context/__init__.py` (empty file):
```python
```

Create `src/superseded/context/static_analysis.py`:
```python
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

STATIC_BUDGET = 4000

LANG_PYTHON = "python"
LANG_JS = "js"
LANG_TS = "ts"
LANG_GO = "go"
LANG_ANY = "*"

EXT_MAP: dict[str, str] = {
    ".py": LANG_PYTHON,
    ".js": LANG_JS,
    ".jsx": LANG_JS,
    ".mjs": LANG_JS,
    ".cjs": LANG_JS,
    ".ts": LANG_TS,
    ".tsx": LANG_TS,
    ".go": LANG_GO,
}


class Tool(Protocol):
    name: str
    languages: list[str]

    def detect(self, root: Path) -> bool: ...
    def build_command(self, changed_files: list[str], root: Path) -> list[str]: ...
    def parse_output(self, stdout: str, stderr: str, root: Path) -> str: ...


def _detect_pyproject_dep(root: Path, dep: str) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    text = pyproject.read_text()
    return f"[tool.{dep}]" in text or f'"{dep}"' in text or f"'{dep}'" in text


class RuffTool:
    name = "ruff"
    languages = [LANG_PYTHON]

    def detect(self, root: Path) -> bool:
        return _detect_pyproject_dep(root, "ruff")

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["ruff", "check", "--output-format=concise", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path) -> str:
        return stdout.strip() if stdout.strip() else ""


class MypyTool:
    name = "mypy"
    languages = [LANG_PYTHON]

    def detect(self, root: Path) -> bool:
        return _detect_pyproject_dep(root, "mypy")

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["mypy", "--no-error-summary", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path) -> str:
        return stdout.strip() if stdout.strip() else ""


class BanditTool:
    name = "bandit"
    languages = [LANG_PYTHON]

    def detect(self, root: Path) -> bool:
        return _detect_pyproject_dep(root, "bandit")

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["bandit", "-q", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path) -> str:
        return stdout.strip() if stdout.strip() else ""


class EslintTool:
    name = "eslint"
    languages = [LANG_JS, LANG_TS]

    def detect(self, root: Path) -> bool:
        if list(root.glob(".eslintrc*")):
            return True
        if list(root.glob("eslint.config.*")):
            return True
        pkg = root / "package.json"
        if pkg.exists() and "eslintConfig" in pkg.read_text():
            return True
        return False

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["eslint", "--format=compact", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path) -> str:
        return stdout.strip() if stdout.strip() else ""


class TscTool:
    name = "tsc"
    languages = [LANG_TS]

    def detect(self, root: Path) -> bool:
        return (root / "tsconfig.json").exists()

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["tsc", "--noEmit"]

    def parse_output(self, stdout: str, stderr: str, root: Path) -> str:
        return stderr.strip() if stderr.strip() else ""


class GofmtTool:
    name = "gofmt"
    languages = [LANG_GO]

    def detect(self, root: Path) -> bool:
        return (root / "go.mod").exists()

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        go_files = [f for f in changed_files if f.endswith(".go")]
        return ["gofmt", "-l", *go_files] if go_files else ["gofmt", "-l", "."]

    def parse_output(self, stdout: str, stderr: str, root: Path) -> str:
        if not stdout.strip():
            return ""
        files = [f"  {f}" for f in stdout.strip().splitlines()]
        return "Files needing formatting:\n" + "\n".join(files)


class GoVetTool:
    name = "go vet"
    languages = [LANG_GO]

    def detect(self, root: Path) -> bool:
        return (root / "go.mod").exists()

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["go", "vet", "."]

    def parse_output(self, stdout: str, stderr: str, root: Path) -> str:
        combined = (stdout + "\n" + stderr).strip()
        return combined if combined else ""


class StaticcheckTool:
    name = "staticcheck"
    languages = [LANG_GO]

    def detect(self, root: Path) -> bool:
        if not (root / "go.mod").exists():
            return False
        from shutil import which
        return which("staticcheck") is not None

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["staticcheck", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path) -> str:
        return stdout.strip() if stdout.strip() else ""


class GitleaksTool:
    name = "gitleaks"
    languages = [LANG_ANY]

    def detect(self, root: Path) -> bool:
        from shutil import which
        return which("gitleaks") is not None

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return [
            "gitleaks", "dir", "scan",
            "--source", str(root),
            "--no-banner", "--report-format", "json",
        ]

    def parse_output(self, stdout: str, stderr: str, root: Path) -> str:
        import json
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return ""
        findings = []
        for item in data:
            desc = item.get("Description", "")
            line = item.get("StartLine", "")
            file = item.get("File", "")
            findings.append(f"  {file}:{line} — {desc}")
        return "Secrets detected:\n" + "\n".join(findings) if findings else ""


TOOLS: list[Tool] = [
    RuffTool(),
    MypyTool(),
    BanditTool(),
    EslintTool(),
    TscTool(),
    GofmtTool(),
    GoVetTool(),
    StaticcheckTool(),
    GitleaksTool(),
]


def _languages_in_files(changed_files: list[str]) -> set[str]:
    langs: set[str] = set()
    for f in changed_files:
        ext = Path(f).suffix
        lang = EXT_MAP.get(ext)
        if lang:
            langs.add(lang)
    return langs


def run_static_analysis(
    changed_files: list[str],
    root: Path,
    repo_langs: set[str] | None = None,
) -> str | None:
    if not changed_files:
        return None

    detected_langs = repo_langs or _languages_in_files(changed_files)
    blocks: list[str] = []

    for tool in TOOLS:
        tool_langs = set(tool.languages)
        if not (tool_langs & detected_langs or LANG_ANY in tool_langs):
            continue
        if not tool.detect(root):
            continue
        cmd = tool.build_command(changed_files, root)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            logger.warning("Static tool %s not on PATH, skipping", tool.name)
            continue
        except subprocess.TimeoutExpired:
            logger.warning("Static tool %s timed out after 30s, skipping", tool.name)
            continue

        # Non-zero exit is normal for linters — still parse output
        if result.returncode != 0 and not result.stdout.strip() and not result.stderr.strip():
            logger.warning(
                "Static tool %s exited %d with no output, skipping", tool.name, result.returncode
            )
            continue

        block = tool.parse_output(result.stdout, result.stderr, root)
        if block:
            blocks.append(f"### {tool.name}\n{block}")

    if not blocks:
        return None

    aggregate = "\n\n".join(blocks)
    if len(aggregate) > STATIC_BUDGET:
        included: list[str] = []
        current_len = 0
        for block in blocks:
            sep = "\n\n" if included else ""
            if current_len + len(sep) + len(block) > STATIC_BUDGET:
                break
            included.append(block)
            current_len += len(sep) + len(block)
        omitted = len(blocks) - len(included)
        aggregate = "\n\n".join(included)
        aggregate += f"\n… ({omitted} tool output(s) omitted by static-analysis budget)"
    return aggregate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_context_static.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/context/ tests/test_context_static.py
git commit -m "feat: add static analysis pre-pass with pluggable Tool protocol"
```

---

### Task 4: Implement usage retrieval module

**Files:**
- Create: `src/superseded/context/usage_retrieval.py`
- Create: `tests/test_context_usage.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_context_usage.py — new file

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from superseded.context.usage_retrieval import (
    USAGE_BUDGET,
    extract_symbols,
    retrieve_usages,
)


def test_extract_symbols_python():
    diff = """@@ -1,3 +1,5 @@
+def calculate_total(items):
+    class TaxCalculator:
+        pass
+TOTAL_RATE = 0.1
"""
    syms = extract_symbols(diff, "python")
    assert "calculate_total" in syms
    assert "TaxCalculator" in syms
    assert "TOTAL_RATE" in syms


def test_extract_symbols_go():
    diff = """@@ -1,3 +1,5 @@
+func HandleRequest(w http.ResponseWriter, r *http.Request) {
+type User struct {
+var DefaultTimeout = 30
+const MaxRetries = 3
"""
    syms = extract_symbols(diff, "go")
    assert "HandleRequest" in syms
    assert "User" in syms
    assert "DefaultTimeout" in syms
    assert "MaxRetries" in syms


def test_extract_symbols_js():
    diff = """@@ -1,3 +1,5 @@
+function fetchData() {
+class DataLoader {
+const MAX_RETRIES = 5;
+interface Config {
+type Options = {
"""
    syms = extract_symbols(diff, "js")
    assert "fetchData" in syms
    assert "DataLoader" in syms
    assert "MAX_RETRIES" in syms
    assert "Config" in syms
    assert "Options" in syms


def test_extract_symbols_dedup():
    diff = """@@ -1,3 +1,5 @@
+def foo():
+    foo()
"""
    syms = extract_symbols(diff, "python")
    assert syms.count("foo") == 1


def test_extract_symbols_cap():
    lines = "\n".join(f"+def func_{i}():" for i in range(50))
    diff = f"@@ -1,1 +1,50 @@\n{lines}"
    syms = extract_symbols(diff, "python")
    assert len(syms) <= 25


def test_extract_symbols_filters_keywords():
    diff = """@@ -1,3 +1,5 @@
+def process():
+    return None
"""
    syms = extract_symbols(diff, "python")
    assert "process" in syms
    assert "return" not in syms
    assert "None" not in syms


def test_rg_invocation(monkeypatch):
    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            return MagicMock(returncode=0, stdout="other.py:10: foo()\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)
    result = retrieve_usages(
        "@@ -1,3 +1,5 @@\n+def foo():\n+    pass\n",
        Path("/repo"),
    )
    assert result is not None
    assert "foo()" in result


def test_rg_missing_returns_none(monkeypatch, caplog):
    def fail(*a, **kw):
        raise FileNotFoundError("no rg")
    monkeypatch.setattr("subprocess.run", fail)
    with caplog.at_level("WARNING"):
        result = retrieve_usages("@@ -1,3 +1,5 @@\n+def foo():\n", Path("/repo"))
    assert result is None
    assert "ripgrep not on PATH" in caplog.text


def test_budget_truncation(monkeypatch):
    big_match = "file.py:{}: call_to_sym()\n"
    matches = "".join(big_match.format(i) for i in range(200))

    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            return MagicMock(returncode=0, stdout=matches, stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = retrieve_usages(
        "@@ -1,3 +1,5 @@\n+def sym():\n",
        Path("/repo"),
    )
    assert "omitted by retrieval budget" in result


def test_no_symbols_returns_none():
    result = retrieve_usages("@@ -1,3 +1,5 @@\n unchanged\n", Path("/repo"))
    assert result is None


def test_changed_file_excluded_from_rg(monkeypatch):
    """The changed file itself should be excluded from rg results."""
    calls = []
    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="other.py:5: call()\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)
    retrieve_usages(
        "diff --git a/foo.py b/foo.py\n@@ -1,3 +1,5 @@\n+def call():\n",
        Path("/repo"),
    )
    assert calls
    assert "--glob" in calls[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_usage.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/superseded/context/usage_retrieval.py — new file

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

USAGE_BUDGET = 6000
MAX_SYMBOLS = 25

_PYTHON_SYMBOL_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+(\w+)|"
    r"^\s*class\s+(\w+)|"
    r"^\s*([A-Z]\w*)\s*=",
    re.MULTILINE,
)

_JS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|"
    r"^\s*(?:export\s+)?class\s+(\w+)|"
    r"^\s*(?:export\s+)?const\s+(\w+)\s*=|"
    r"^\s*(?:export\s+)?interface\s+(\w+)|"
    r"^\s*(?:export\s+)?type\s+(\w+)",
    re.MULTILINE,
)

_GO_SYMBOL_RE = re.compile(
    r"^\s*func(?:\s+\([^)]+\))?\s+(\w+)|"
    r"^\s*type\s+(\w+)\s+struct|"
    r"^\s*type\s+(\w+)\s+interface|"
    r"^\s*var\s+(\w+)|"
    r"^\s*const\s+(\w+)",
    re.MULTILINE,
)

_GENERIC_RE = re.compile(r"\b([A-Za-z_]\w{3,})\b")

_KEYWORDS = frozenset({
    "self", "cls", "return", "if", "else", "for", "while", "import", "from",
    "const", "let", "var", "func", "type", "package", "struct", "interface",
    "def", "class", "async", "await", "yield", "with", "as", "try", "except",
    "finally", "raise", "pass", "break", "continue", "elif", "lambda",
    "not", "and", "or", "in", "is", "None", "True", "False", "print",
    "this", "new", "delete", "typeof", "instanceof", "void",
})

_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts",
    ".go": "go",
}


def extract_symbols(diff: str, lang: str) -> list[str]:
    """Extract changed symbol names from added diff lines."""
    added_lines = "\n".join(
        line[1:] for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )

    if lang == "python":
        matches = _PYTHON_SYMBOL_RE.finditer(added_lines)
    elif lang in ("js", "ts"):
        matches = _JS_SYMBOL_RE.finditer(added_lines)
    elif lang == "go":
        matches = _GO_SYMBOL_RE.finditer(added_lines)
    else:
        matches = _GENERIC_RE.finditer(added_lines)

    seen: set[str] = set()
    symbols: list[str] = []
    for m in matches:
        groups = m.groups()
        name = next((g for g in groups if g is not None), None)
        if name and name not in _KEYWORDS and name not in seen:
            seen.add(name)
            symbols.append(name)
            if len(symbols) >= MAX_SYMBOLS:
                break

    return symbols


def retrieve_usages(diff: str, root: Path) -> str | None:
    """Find callers of changed symbols via ripgrep."""
    files_match = re.search(r"^diff --git a/(.+?) b/", diff, re.MULTILINE)
    changed_file = files_match.group(1) if files_match else None

    lang = _LANG_MAP.get(Path(changed_file).suffix) if changed_file else None
    if not lang:
        return None

    symbols = extract_symbols(diff, lang)
    if not symbols:
        return None

    blocks: list[str] = []
    total_chars = 0

    for sym in symbols:
        glob_args = [
            "--glob", "!.venv/**",
            "--glob", "!node_modules/**",
            "--glob", "!.git/**",
            "--glob", "!*.lock",
        ]
        if changed_file:
            glob_args += ["--glob", f"!{changed_file}"]

        try:
            result = subprocess.run(
                ["rg", "-n", "--max-count", "4", rf"\b{sym}\b", str(root), *glob_args],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.warning("ripgrep not on PATH or timed out, skipping usage retrieval")
            return None

        if result.returncode == 0 and result.stdout.strip():
            block = f"### Usages of `{sym}`\n{result.stdout.strip()}"
            if total_chars + len(block) > USAGE_BUDGET:
                omitted = len(symbols) - len(blocks)
                blocks.append(
                    f"\u2026 ({omitted} more usages omitted by retrieval budget)"
                )
                break
            blocks.append(block)
            total_chars += len(block)

    if not blocks:
        return None
    return "\n\n".join(blocks)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_context_usage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/context/usage_retrieval.py tests/test_context_usage.py
git commit -m "feat: add cross-file usage retrieval via ripgrep"
```

---

### Task 5: Update prompts.py to accept new kwargs

**Files:**
- Modify: `src/superseded/review/prompts.py`
- Create: `tests/test_prompts.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_prompts.py — new file

from __future__ import annotations

from superseded.review.prompts import build_prompt, JSON_FORMAT_INSTRUCTIONS


def test_new_sections_present():
    prompt = build_prompt(
        pass_name="security",
        diff="diff --git a/x.py b/x.py\n+bad",
        pr_description=None,
        file_context=None,
        memory_context=None,
        static_signals="### ruff\nF401 unused",
        usage_signals="### Usages of `foo`\nbar.py:5: foo()",
    )
    assert "### Static analysis signals" in prompt
    assert "F401 unused" in prompt
    assert "### Cross-file usages" in prompt
    assert "bar.py:5: foo()" in prompt


def test_new_sections_absent_when_none():
    prompt = build_prompt(
        pass_name="security",
        diff="diff --git a/x.py b/x.py\n+bad",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "No static analysis tools detected" in prompt
    assert "No usages retrieved" in prompt


def test_section_ordering():
    prompt = build_prompt(
        pass_name="security",
        diff="diff --git a/x.py b/x.py\n+bad",
        pr_description=None,
        file_context=None,
        memory_context=None,
        static_signals="ruff output",
        usage_signals="rg output",
    )
    diff_pos = prompt.index("### Changed Files (diff)")
    static_pos = prompt.index("### Static analysis signals")
    usage_pos = prompt.index("### Cross-file usages")
    file_pos = prompt.index("### File Context")
    assert diff_pos < static_pos < usage_pos < file_pos


def test_reasoning_in_json_format():
    assert "reasoning" in JSON_FORMAT_INSTRUCTIONS


def test_reasoning_rule_in_prompt():
    prompt = build_prompt(
        pass_name="correctness",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "1-3 sentences" in prompt
    assert "evidence led you to flag" in prompt


def test_existing_sections_unchanged():
    """When new kwargs are None, the prompt still contains all original sections."""
    prompt = build_prompt(
        pass_name="performance",
        diff="diff --git a/x.py b/x.py\n+old",
        pr_description="My PR",
        file_context="some context",
        memory_context="some memory",
    )
    assert "### PR Description" in prompt
    assert "My PR" in prompt
    assert "### Changed Files (diff)" in prompt
    assert "### File Context" in prompt
    assert "some context" in prompt
    assert "### Past Feedback" in prompt
    assert "some memory" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL — `build_prompt() got an unexpected keyword argument 'static_signals'`

- [ ] **Step 3: Update prompts.py**

Replace the full content of `src/superseded/review/prompts.py`:

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
    "confidence": "high|medium|low",
    "file": "path/to/file.py",
    "line": 42,
    "end_line": 45,
    "title": "Short description",
    "description": "Detailed explanation of the issue",
    "suggestion": "Code fix or suggestion",
    "reasoning": "1-3 sentences explaining what evidence led to this finding."
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
    static_signals: str | None = None,
    usage_signals: str | None = None,
) -> str:
    instructions = PASS_INSTRUCTIONS.get(pass_name, "Review for issues.")
    pr_desc = pr_description or "No description provided."
    ctx = file_context or "No additional file context available."
    mem = memory_context or "No past feedback."
    static = static_signals or "No static analysis tools detected or available."
    usage = usage_signals or "No usages retrieved."

    return f"""You are performing a {pass_name} code review.

## Your Role
{instructions}

## Rules
- Only report genuine issues, not style preferences unless they impact readability
- Be specific: cite exact file, line numbers, and code
- Provide actionable suggestions with code examples
- Do NOT report issues in unchanged code unless directly related to the change
- If there are no issues in this category, return an empty array
- For each finding, briefly (1-3 sentences) explain what evidence led you to flag it

## Context

### PR Description
{pr_desc}

### Changed Files (diff)
{diff}

### Static analysis signals (run before AI; deterministic)
{static}

### Cross-file usages (callers of changed symbols, ±3 lines)
{usage}

### File Context (surrounding code for changed files, ±20 lines from changes)
{ctx}

### Past Feedback (findings dismissed by humans — avoid similar)
{mem}

{JSON_FORMAT_INSTRUCTIONS}"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/review/prompts.py tests/test_prompts.py
git commit -m "feat: add static_signals/usage_signals kwargs and reasoning to prompts"
```

---

### Task 6: Wire context into cli.py

**Files:**
- Modify: `src/superseded/cli.py`
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_integration.py — append

from unittest.mock import MagicMock
from superseded.cli import _run_review


def test_context_enrichment_called(monkeypatch):
    """Verify run_static_analysis and retrieve_usages are called and kwargs forwarded."""
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "diff --git a/x.py b/x.py\n+x")
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr("superseded.cli.compute_file_context", lambda d: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)

    called_static = []
    called_usage = []

    def fake_static(changed_files, root):
        called_static.append(True)
        return "static output"

    def fake_usage(diff, root):
        called_usage.append(True)
        return "usage output"

    monkeypatch.setattr("superseded.cli.run_static_analysis", fake_static)
    monkeypatch.setattr("superseded.cli.retrieve_usages", fake_usage)

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[])
    monkeypatch.setattr("superseded.cli.ReviewEngine.select", lambda *a, **kw: mock_engine)

    _run_review(
        pr=None, diff_range="HEAD~1..HEAD", agent=None, model=None,
        output_format="json", post=False, passes=None,
    )

    assert called_static
    assert called_usage
    call_kwargs = mock_engine.review.call_args
    assert call_kwargs[1].get("static_signals") == "static output"
    assert call_kwargs[1].get("usage_signals") == "usage output"


def test_context_disabled_skips_enrichment(monkeypatch):
    """When config disables enrichment, functions are not called."""
    monkeypatch.setattr("superseded.cli.fetch_diff", lambda **kw: "diff --git a/x.py b/x.py\n+x")
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr("superseded.cli.compute_file_context", lambda d: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)

    called = []
    monkeypatch.setattr(
        "superseded.cli.run_static_analysis",
        lambda *a, **kw: (called.append("static"), None)[1],
    )
    monkeypatch.setattr(
        "superseded.cli.retrieve_usages",
        lambda *a, **kw: (called.append("usage"), None)[1],
    )

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[])
    monkeypatch.setattr("superseded.cli.ReviewEngine.select", lambda *a, **kw: mock_engine)

    # Load a config with both disabled
    from superseded.config import Config
    monkeypatch.setattr("superseded.cli.load_config", lambda: Config(static_analysis=False, usage_retrieval=False))

    _run_review(
        pr=None, diff_range="HEAD~1..HEAD", agent=None, model=None,
        output_format="json", post=False, passes=None,
    )

    assert not called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_integration.py::test_context_enrichment_called -v`
Expected: FAIL — `_run_review()` got unexpected keyword arguments `static_signals` / `usage_signals`

- [ ] **Step 3: Update cli.py**

Update imports (line 11):
```python
from superseded.diff import compute_file_context, fetch_diff, fetch_pr_description, parse_diff_files, repo_root
```

Add new imports after existing context imports:
```python
from superseded.context.static_analysis import run_static_analysis
from superseded.context.usage_retrieval import retrieve_usages
```

In `_run_review`, after the `file_context = compute_file_context(diff)` line (~line 111), add:
```python
    root = repo_root()

    static_signals: str | None = None
    usage_signals: str | None = None
    if config.static_analysis:
        changed_files = [e["file"] for e in parse_diff_files(diff)]
        static_signals = run_static_analysis(changed_files, root)
    if config.usage_retrieval:
        usage_signals = retrieve_usages(diff, root)
```

Update the `engine.review` call to pass new kwargs:
```python
    result = engine.review(
        diff=diff,
        pr_description=pr_description,
        file_context=file_context,
        memory_context=memory_context,
        static_signals=static_signals,
        usage_signals=usage_signals,
        passes=pass_list,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_integration.py::test_context_enrichment_called tests/test_integration.py::test_context_disabled_skips_enrichment -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/cli.py tests/test_integration.py
git commit -m "feat: wire static analysis and usage retrieval into review pipeline"
```

---

### Task 7: Final integration pass

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Run lint and format**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: PASS (no errors, no reformatting needed)

- [ ] **Step 3: Commit any lint fixes if needed**

```bash
git add -A
git commit -m "chore: lint and format after context enrichment feature"
```
(Skip if nothing changed.)

---

### Task Summary

| Task | Files created | Files modified | Tests |
|---|---|---|---|
| 0: github_pr fix | — | `github_pr.py` | existing suite |
| 1: repo_root | — | `diff.py`, `test_diff.py` | 2 new tests |
| 2: config fields | — | `config.py`, `test_config.py` | 2 new tests |
| 3: static analysis | `context/__init__.py`, `context/static_analysis.py` | — | `test_context_static.py` (9 tests) |
| 4: usage retrieval | `context/usage_retrieval.py` | — | `test_context_usage.py` (10 tests) |
| 5: prompts update | — | `prompts.py` | `test_prompts.py` (6 tests) |
| 6: cli wiring | — | `cli.py`, `test_integration.py` | 2 new tests |
| 7: final pass | — | — | full suite + lint |
