# Project Conventions & Repo-Spec Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich every per-pass review prompt with two new curated, repo-grounded context blocks — **Project Conventions** (auto-discovered from root-level convention docs with non-convention sections stripped) and **Relevant Design Specs & Plans** (auto-discovered from `docs/superseded/specs/`, `docs/superseded/plans/`, and skill-definition files, filtered to those relevant to the diff) — plus explicit prompt rules making the AI enforce conventions at calibrated severity.

**Architecture:** Two new pure-function modules under `src/superseded/context/` (`conventions.py`, `spec_retrieval.py`), called from `cli.py` and `server/worker.py` alongside the existing `run_static_analysis`/`retrieve_usages` steps, threaded through `engine.review()` → `build_prompt()` as two new optional kwargs. Mirrors the established grounded-context pipeline. `Agent`, `ReviewEngine.review` fan-out, `merger`, `MemoryStore` untouched.

**Tech Stack:** Python 3.14+, uv, pytest, ruff, Pydantic v2, click. No new deps.

**Spec:** `docs/superpowers/specs/2026-06-25-project-conventions-enforcement-design.md`

**Commands (run from repo root):**
```bash
uv run pytest tests/test_context_conventions.py -v        # one new test file
uv run pytest tests/test_context_spec_retrieval.py -v     # one new test file
uv run pytest tests/test_prompts.py -v                    # extended
uv run pytest tests/test_integration.py -v                # extended
uv run pytest tests/ -v                                   # full suite
uv run ruff check src/ tests/                             # lint
uv run ruff format src/ tests/                            # format
```

---

## File Structure

**Create:**
- `src/superseded/context/conventions.py` — `discover_conventions(root: Path) -> str | None`. Auto-discovers root-level convention docs, strips blocklisted sections, concatenates in fixed order, budget-caps. Single responsibility: produce the conventions block.
- `src/superseded/context/spec_retrieval.py` — `discover_repo_specs(diff: str, root: Path) -> str | None`. Discovers specs/plans/skills, filters to diff-relevant via filename/slug match, budget-caps. Single responsibility: produce the spec block.
- `tests/test_context_conventions.py` — tests for the conventions module.
- `tests/test_context_spec_retrieval.py` — tests for the spec-retrieval module.

**Modify:**
- `src/superseded/review/prompts.py` — add two kwargs to `build_prompt`, two new `###` sections at the top of `## Context`, amend + add Rules.
- `src/superseded/config.py` — add `conventions: bool = True` and `spec_retrieval: bool = True` to `Config`.
- `src/superseded/review/engine.py` — add two kwargs to `ReviewEngine.review`, forward to `build_prompt`.
- `src/superseded/cli.py` — add `--no-conventions`/`--no-specs` flags, call the two new functions, thread kwargs.
- `src/superseded/server/worker.py` — call the two new functions (gated on config), thread kwargs.
- `tests/test_prompts.py` — new sections, ordering, enforcement rules, regression.
- `tests/test_integration.py` — signals land in prompt; `--no-*` / config-false skip.

---

## Task 1: Config fields

Add the two new config bools. Foundation for everything else (gating logic depends on them).

**Files:**
- Modify: `src/superseded/config.py:17-26` (the `Config` class body)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py` is not new — extend the existing one. First read it to find the pattern.

Run: `uv run pytest tests/test_config.py -v` to see current passing tests and the import pattern.

Append to `tests/test_config.py`:

```python
def test_conventions_default_true():
    from superseded.config import Config
    assert Config().conventions is True
    assert Config().spec_retrieval is True


def test_conventions_can_be_disabled():
    from superseded.config import Config
    cfg = Config(conventions=False, spec_retrieval=False)
    assert cfg.conventions is False
    assert cfg.spec_retrieval is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_conventions_default_true -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'conventions'`

- [ ] **Step 3: Write minimal implementation**

Edit `src/superseded/config.py`. In the `Config` class body, after `usage_retrieval: bool = True` (line 25), add:

```python
    conventions: bool = True
    spec_retrieval: bool = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_conventions_default_true tests/test_config.py::test_conventions_can_be_disabled -v`
Expected: PASS

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/config.py tests/test_config.py && uv run ruff format src/superseded/config.py tests/test_config.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat(config): add conventions and spec_retrieval bools"
```

---

## Task 2: Conventions module — heading stripper helper

The conventions module needs to strip blocklisted markdown sections. Start with the pure helper that does heading parsing + section splitting, since it's the most logic-dense part and easy to test in isolation.

**Files:**
- Create: `src/superseded/context/conventions.py`
- Test: `tests/test_context_conventions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_conventions.py`:

```python
from __future__ import annotations

from superseded.context.conventions import strip_blocklisted_sections, BLOCKLIST


def test_strip_removes_blocklisted_section_and_body():
    doc = (
        "# AGENTS.md\n\n"
        "## Conventions\n\nKeep this.\n\n"
        "## Toolchain & environment\n\nDrop this body.\n\n"
        "## Architecture notes\n\nKeep this too.\n"
    )
    out = strip_blocklisted_sections(doc)
    assert "Keep this." in out
    assert "Keep this too." in out
    assert "Toolchain" not in out
    assert "Drop this body." not in out


def test_strip_is_case_insensitive_substring_match():
    doc = "## PACKAGING / GitHub Action\n\nbody to drop\n\n## Conventions\n\nkeep\n"
    out = strip_blocklisted_sections(doc)
    assert "PACKAGING" not in out
    assert "body to drop" not in out
    assert "Conventions" in out
    assert "keep" in out


def test_strip_preserves_non_blocklisted_sections_intact():
    doc = "## Conventions\n\nbody line 1\nbody line 2\n"
    out = strip_blocklisted_sections(doc)
    assert out.strip() == "## Conventions\n\nbody line 1\nbody line 2"


def test_strip_handles_no_heading_doc():
    doc = "Just prose, no headings at all.\n"
    out = strip_blocklisted_sections(doc)
    assert out == doc


def test_blocklist_contains_expected_terms():
    for term in ["toolchain", "environment", "commands", "packaging",
                 "github action", "gitignore", "docs"]:
        assert term in BLOCKLIST
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_conventions.py -v`
Expected: FAIL with `ImportError: cannot import name 'strip_blocklisted_sections'`

- [ ] **Step 3: Write minimal implementation**

Create `src/superseded/context/conventions.py`:

```python
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CONVENTIONS_BUDGET = 4000

BLOCKLIST: list[str] = [
    "toolchain",
    "environment",
    "commands",
    "packaging",
    "github action",
    "gitignore",
    "docs",
]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

CONVENTION_FILES: list[str] = [
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "CONTRIBUTING.md",
    ".editorconfig",
]


def strip_blocklisted_sections(text: str) -> str:
    """Drop markdown sections whose heading matches a BLOCKLIST term (substring, case-insensitive).

    A section is the heading line plus all lines until the next heading of any level.
    Non-markdown text (no headings) is returned unchanged.
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return text

    kept: list[str] = []
    cursor = 0
    for i, m in enumerate(matches):
        heading_text = m.group(2).lower()
        section_start = m.start()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[section_start:section_end]
        if any(term in heading_text for term in BLOCKLIST):
            if section_start > cursor:
                kept.append(text[cursor:section_start])
        else:
            kept.append(section)
        cursor = section_end
    if cursor < len(text):
        kept.append(text[cursor:])
    return "".join(kept)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_conventions.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/context/conventions.py tests/test_context_conventions.py && uv run ruff format src/superseded/context/conventions.py tests/test_context_conventions.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superseded/context/conventions.py tests/test_context_conventions.py
git commit -m "feat(context): add conventions heading stripper helper"
```

---

## Task 3: Conventions module — `discover_conventions` discovery + assembly

Add the public function that discovers root-level docs, strips them, concatenates in fixed order, budget-caps, returns `None` when empty.

**Files:**
- Modify: `src/superseded/context/conventions.py` (append `discover_conventions`)
- Test: `tests/test_context_conventions.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context_conventions.py`:

```python
from pathlib import Path

from superseded.context.conventions import discover_conventions, CONVENTIONS_BUDGET


def test_discover_returns_none_when_no_docs(tmp_path):
    assert discover_conventions(tmp_path) is None


def test_discover_finds_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("## Conventions\n\nuse double quotes\n")
    out = discover_conventions(tmp_path)
    assert out is not None
    assert "## AGENTS.md" in out
    assert "use double quotes" in out


def test_discover_case_insensitive_filename(tmp_path):
    (tmp_path / "agents.md").write_text("## Conventions\n\nkeep\n")
    out = discover_conventions(tmp_path)
    assert out is not None
    assert "keep" in out


def test_discover_strips_blocklisted_sections(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "## Conventions\n\nkeep this\n\n## Toolchain & environment\n\ndrop this\n"
    )
    out = discover_conventions(tmp_path)
    assert "keep this" in out
    assert "Toolchain" not in out
    assert "drop this" not in out


def test_discover_editorconfig_injected_whole(tmp_path):
    (tmp_path / ".editorconfig").write_text("root = true\n[*]\nindent_style = space\n")
    out = discover_conventions(tmp_path)
    assert out is not None
    assert "## .editorconfig" in out
    assert "indent_style = space" in out


def test_discover_concatenation_order(tmp_path):
    (tmp_path / "AGENTS.md").write_text("agents body\n")
    (tmp_path / "CLAUDE.md").write_text("claude body\n")
    (tmp_path / "GEMINI.md").write_text("gemini body\n")
    (tmp_path / "CONTRIBUTING.md").write_text("contributing body\n")
    (tmp_path / ".editorconfig").write_text("editorconfig body\n")
    out = discover_conventions(tmp_path)
    assert out is not None
    assert out.index("agents body") < out.index("claude body")
    assert out.index("claude body") < out.index("gemini body")
    assert out.index("gemini body") < out.index("contributing body")
    assert out.index("contributing body") < out.index("editorconfig body")


def test_discover_budget_truncation_tail(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x" * (CONVENTIONS_BUDGET + 500))
    out = discover_conventions(tmp_path)
    assert out is not None
    assert "omitted by conventions budget" in out
    assert len(out) <= CONVENTIONS_BUDGET + 200


def test_discover_skips_unreadable_doc(tmp_path, caplog):
    (tmp_path / "AGENTS.md").write_text("## Conventions\nkeep\n")
    (tmp_path / "AGENTS.md").chmod(0o000)
    try:
        with caplog.at_level("WARNING"):
            out = discover_conventions(tmp_path)
    finally:
        (tmp_path / "AGENTS.md").chmod(0o644)
    # No readable docs -> None (or other docs still included). Here only one doc, so None.
    assert out is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_conventions.py -v`
Expected: FAIL with `ImportError: cannot import name 'discover_conventions'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/superseded/context/conventions.py`:

```python
def _read_optional(root: Path, filename: str) -> str | None:
    path = root / filename
    if not path.exists():
        return None
    try:
        return path.read_text()
    except OSError as err:
        logger.warning("Could not read convention doc %s: %s", path, err)
        return None


def discover_conventions(root: Path) -> str | None:
    """Discover root-level convention docs, strip non-convention sections, concatenate, budget-cap."""
    blocks: list[str] = []
    for filename in CONVENTION_FILES:
        text = _read_optional(root, filename)
        if text is None:
            continue
        if filename.endswith(".md"):
            text = strip_blocklisted_sections(text)
        blocks.append(f"## {filename}\n{text.strip()}")

    if not blocks:
        return None

    aggregate = "\n\n".join(blocks)
    if len(aggregate) > CONVENTIONS_BUDGET:
        omitted = len(aggregate) - CONVENTIONS_BUDGET
        aggregate = aggregate[:CONVENTIONS_BUDGET] + (
            f"\n… ({omitted} more chars omitted by conventions budget)"
        )
    return aggregate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_conventions.py -v`
Expected: PASS (all tests, including the earlier 5)

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/context/conventions.py tests/test_context_conventions.py && uv run ruff format src/superseded/context/conventions.py tests/test_context_conventions.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superseded/context/conventions.py tests/test_context_conventions.py
git commit -m "feat(context): add discover_conventions root-doc discovery"
```

---

## Task 4: Spec retrieval module — slug derivation helper

Start the spec-retrieval module with the pure slug-derivation helper, testable in isolation.

**Files:**
- Create: `src/superseded/context/spec_retrieval.py`
- Test: `tests/test_context_spec_retrieval.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_spec_retrieval.py`:

```python
from __future__ import annotations

import pytest

from superseded.context.spec_retrieval import derive_slug


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("2026-06-25-grounded-review-context-design.md", "grounded-review-context"),
        ("2026-06-24-todo-fixes.md", "todo-fixes"),
        ("2026-06-24-code-review-tool-implementation.md", "code-review-tool"),
        ("my-skill.md", "my-skill"),
        ("README.md", "readme"),
        ("no-suffix.md", "no-suffix"),
    ],
)
def test_derive_slug(filename, expected):
    assert derive_slug(filename) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_spec_retrieval.py -v`
Expected: FAIL with `ImportError: cannot import name 'derive_slug'`

- [ ] **Step 3: Write minimal implementation**

Create `src/superseded/context/spec_retrieval.py`:

```python
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from superseded.diff import parse_diff_files

logger = logging.getLogger(__name__)

SPEC_BUDGET = 6000

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_SUFFIX_RE = re.compile(r"-(?:design|implementation|plan)$")

SPEC_GLOBS: list[str] = [
    "docs/superseded/specs/*.md",
    "docs/superseded/plans/*.md",
    ".opencode/skills/**/*.md",
    ".agents/skills/**/*.md",
    "skills/**/*.md",
]


def derive_slug(filename: str) -> str:
    """Derive a lowercase slug from a spec/plan/skill filename.

    Strips a leading YYYY-MM-DD- date prefix and a trailing -design/-implementation/-plan suffix,
    plus the .md extension. For skill files (no date prefix), the slug is the filename stem.
    """
    stem = Path(filename).stem
    stem = _DATE_PREFIX_RE.sub("", stem)
    stem = _SUFFIX_RE.sub("", stem)
    return stem.lower()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_spec_retrieval.py -v`
Expected: PASS (all 6 parametrized cases)

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/context/spec_retrieval.py tests/test_context_spec_retrieval.py && uv run ruff format src/superseded/context/spec_retrieval.py tests/test_context_spec_retrieval.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superseded/context/spec_retrieval.py tests/test_context_spec_retrieval.py
git commit -m "feat(context): add spec slug derivation helper"
```

---

## Task 5: Spec retrieval module — relevance filtering + `discover_repo_specs`

Add the discovery glob scan, the relevance filter (filename/slug match in body via `rg`), concatenation with mtime ordering, budget cap, and the `None` return paths.

**Files:**
- Modify: `src/superseded/context/spec_retrieval.py` (append `discover_repo_specs` + helpers)
- Test: `tests/test_context_spec_retrieval.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context_spec_retrieval.py`:

```python
import os
from pathlib import Path
from unittest.mock import MagicMock

from superseded.context.spec_retrieval import discover_repo_specs, SPEC_BUDGET


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_returns_none_when_no_docs_dir(tmp_path):
    diff = "diff --git a/x.py b/x.py\n+x"
    assert discover_repo_specs(diff, tmp_path) is None


def test_filename_in_body_match(tmp_path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-foo-design.md"
    _write(spec, "This change touches src/superseded/review/prompts.py.\n")
    diff = "diff --git a/src/superseded/review/prompts.py b/src/superseded/review/prompts.py\n+x"
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None
    assert "foo" in out or "prompts.py" in out


def test_basename_in_body_match(tmp_path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-bar-design.md"
    _write(spec, "We edit prompts.py here.\n")
    diff = "diff --git a/src/superseded/review/prompts.py b/src/superseded/review/prompts.py\n+x"
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None


def test_irrelevant_spec_not_selected(tmp_path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-unrelated-design.md"
    _write(spec, "This is about something else entirely.\n")
    diff = "diff --git a/src/superseded/review/prompts.py b/src/superseded/review/prompts.py\n+x"
    assert discover_repo_specs(diff, tmp_path) is None


def test_slug_as_path_component_match(tmp_path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-grounded-review-context-design.md"
    _write(spec, "no filenames mentioned here\n")
    diff = (
        "diff --git a/src/superseded/grounded-review-context/foo.py "
        "b/src/superseded/grounded-review-context/foo.py\n+x"
    )
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None


def test_ordering_specs_before_plans_newest_first(tmp_path):
    old_spec = tmp_path / "docs/superseded/specs/2026-06-20-old-design.md"
    new_spec = tmp_path / "docs/superseded/specs/2026-06-25-new-design.md"
    plan = tmp_path / "docs/superseded/plans/2026-06-25-new-implementation.md"
    for p in [old_spec, new_spec, plan]:
        _write(p, "touches prompts.py\n")
    os.utime(old_spec, (1, 1))
    os.utime(new_spec, (2, 2))
    os.utime(plan, (3, 3))
    diff = "diff --git a/prompts.py b/prompts.py\n+x"
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None
    assert out.index("new-design") < out.index("old-design")
    assert out.index("new-design") < out.index("new-implementation")


def test_budget_truncation_tail(tmp_path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-big-design.md"
    _write(spec, "touches prompts.py\n" + "x" * (SPEC_BUDGET + 500))
    diff = "diff --git a/prompts.py b/prompts.py\n+x"
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None
    assert "omitted by spec-retrieval budget" in out


def test_rg_missing_returns_none_and_warns(tmp_path, monkeypatch, caplog):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-foo-design.md"
    _write(spec, "touches prompts.py\n")
    diff = "diff --git a/prompts.py b/prompts.py\n+x"

    def fail(*a, **kw):
        raise FileNotFoundError("no rg")

    monkeypatch.setattr("subprocess.run", fail)
    with caplog.at_level("WARNING"):
        out = discover_repo_specs(diff, tmp_path)
    assert out is None
    assert "ripgrep" in caplog.text.lower()


def test_skill_discovered(tmp_path):
    skill = tmp_path / ".agents/skills/foo/SKILL.md"
    _write(skill, "mentions prompts.py\n")
    diff = "diff --git a/prompts.py b/prompts.py\n+x"
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None
    assert ".agents/skills/foo/SKILL.md" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_spec_retrieval.py -v`
Expected: FAIL with `ImportError: cannot import name 'discover_repo_specs'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/superseded/context/spec_retrieval.py`:

```python
def _candidate_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for pattern in SPEC_GLOBS:
        candidates.extend(root.glob(pattern))
    return [c for c in candidates if c.is_file()]


def _slug_in_paths(slug: str, changed_paths: list[str]) -> bool:
    slug_l = slug.lower()
    for p in changed_paths:
        parts = [part.lower() for part in p.split("/")]
        if slug_l in parts:
            return True
    return False


def _body_mentions_paths(body: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    try:
        result = subprocess.run(
            ["rg", "--fixed-strings", *sum([["-e", pat] for pat in patterns], []), "-l"],
            input=body,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        logger.warning("ripgrep not on PATH, skipping spec retrieval")
        raise
    except subprocess.TimeoutExpired:
        logger.warning("ripgrep timed out during spec retrieval, skipping")
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _is_relevant(doc_path: Path, body: str, slug: str, changed_paths: list[str]) -> bool:
    if _slug_in_paths(slug, changed_paths):
        return True
    patterns = list({*changed_paths, *[Path(p).name for p in changed_paths]})
    try:
        return _body_mentions_paths(body, patterns)
    except FileNotFoundError:
        raise


def discover_repo_specs(diff: str, root: Path) -> str | None:
    """Discover specs/plans/skills relevant to the diff, concatenate, budget-cap."""
    entries = parse_diff_files(diff)
    changed_paths = [e["file"] for e in entries]
    if not changed_paths:
        return None

    candidates = _candidate_files(root)
    if not candidates:
        return None

    relevant: list[tuple[float, str, str]] = []  # (mtime, relative_path, body)
    for path in candidates:
        try:
            body = path.read_text()
        except OSError as err:
            logger.warning("Could not read spec/plan %s: %s", path, err)
            continue
        slug = derive_slug(path.name)
        try:
            if not _is_relevant(path, body, slug, changed_paths):
                continue
        except FileNotFoundError:
            return None
        rel = str(path.relative_to(root))
        relevant.append((path.stat().st_mtime, rel, body))

    if not relevant:
        return None

    specs = sorted(
        [(mt, rel, body) for mt, rel, body in relevant if "/specs/" in rel.replace("\\", "/")],
        key=lambda t: t[0],
        reverse=True,
    )
    plans = sorted(
        [(mt, rel, body) for mt, rel, body in relevant if "/plans/" in rel.replace("\\", "/")],
        key=lambda t: t[0],
        reverse=True,
    )
    skills = sorted(
        [(mt, rel, body) for mt, rel, body in relevant if "/specs/" not in rel.replace("\\", "/")
         and "/plans/" not in rel.replace("\\", "/")],
        key=lambda t: t[0],
        reverse=True,
    )

    blocks: list[str] = []
    for _mt, rel, body in [*specs, *plans, *skills]:
        blocks.append(f"## {rel}\n{body.strip()}")

    aggregate = "\n\n".join(blocks)
    if len(aggregate) > SPEC_BUDGET:
        omitted = len(aggregate) - SPEC_BUDGET
        aggregate = aggregate[:SPEC_BUDGET] + (
            f"\n… ({omitted} more chars omitted by spec-retrieval budget)"
        )
    return aggregate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_spec_retrieval.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/context/spec_retrieval.py tests/test_context_spec_retrieval.py && uv run ruff format src/superseded/context/spec_retrieval.py tests/test_context_spec_retrieval.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superseded/context/spec_retrieval.py tests/test_context_spec_retrieval.py
git commit -m "feat(context): add discover_repo_specs diff-relevant spec retrieval"
```

---

## Task 6: Prompt changes — new kwargs, sections, rules

Add the two new kwargs to `build_prompt`, two new `###` sections at the top of `## Context`, and the enforcement rules. This is the prompt-shape change everything threads toward.

**Files:**
- Modify: `src/superseded/review/prompts.py:48-97` (the `build_prompt` function)
- Test: `tests/test_prompts.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompts.py`:

```python
def test_conventions_section_present_when_kwarg_non_empty():
    prompt = build_prompt(
        pass_name="security",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
        conventions_signals="## AGENTS.md\nuse double quotes",
    )
    assert "### Project Conventions" in prompt
    assert "use double quotes" in prompt


def test_conventions_placeholder_when_none():
    prompt = build_prompt(
        pass_name="security",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "No project conventions discovered." in prompt


def test_spec_section_present_when_kwarg_non_empty():
    prompt = build_prompt(
        pass_name="correctness",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
        spec_signals="## docs/spec.md\nintent: do foo",
    )
    assert "### Relevant Design Specs & Plans" in prompt
    assert "intent: do foo" in prompt


def test_spec_placeholder_when_none():
    prompt = build_prompt(
        pass_name="correctness",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "No relevant specs/plans found." in prompt


def test_conventions_and_spec_before_pr_description():
    prompt = build_prompt(
        pass_name="architecture",
        diff="x",
        pr_description="my PR",
        file_context=None,
        memory_context=None,
        conventions_signals="conv",
        spec_signals="spec",
    )
    conv_pos = prompt.index("### Project Conventions")
    spec_pos = prompt.index("### Relevant Design Specs & Plans")
    pr_pos = prompt.index("### PR Description")
    assert conv_pos < spec_pos < pr_pos


def test_enforcement_rules_present():
    prompt = build_prompt(
        pass_name="style",
        diff="x",
        pr_description=None,
        file_context=None,
        memory_context=None,
    )
    assert "Enforce the Project Conventions" in prompt
    assert "except deviations from the Project Conventions" in prompt
    assert "authoritative intent" in prompt


def test_old_sections_unchanged_when_new_kwargs_none():
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

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL on the new tests (e.g. `test_conventions_section_present_when_kwarg_non_empty` fails — no `### Project Conventions` in prompt)

- [ ] **Step 3: Write minimal implementation**

Edit `src/superseded/review/prompts.py`. Replace the entire `build_prompt` function (lines 48-97) with:

```python
def build_prompt(
    pass_name: str,
    diff: str,
    pr_description: str | None,
    file_context: str | None,
    memory_context: str | None,
    static_signals: str | None = None,
    usage_signals: str | None = None,
    conventions_signals: str | None = None,
    spec_signals: str | None = None,
) -> str:
    instructions = PASS_INSTRUCTIONS.get(pass_name, "Review for issues.")
    pr_desc = pr_description or "No description provided."
    ctx = file_context or "No additional file context available."
    mem = memory_context or "No past feedback."
    static = static_signals or "No static analysis tools detected or available."
    usage = usage_signals or "No usages retrieved."
    conv = conventions_signals or "No project conventions discovered."
    spec = spec_signals or "No relevant specs/plans found."

    return f"""You are performing a {pass_name} code review.

## Your Role
{instructions}

## Rules
- Only report genuine issues, not style preferences — except deviations from the Project Conventions below, which are reportable.
- Be specific: cite exact file, line numbers, and code
- Provide actionable suggestions with code examples
- Do NOT report issues in unchanged code unless directly related to the change
- If there are no issues in this category, return an empty array
- For each finding, briefly (1-3 sentences) explain what evidence led you to flag it
- Enforce the Project Conventions listed below: flag deviations as findings. Use severity `nit`/`suggestion` by default; use `important` only when the deviation breaks correctness or security. Do not flag code that conforms to the conventions.
- Use the Relevant Design Specs & Plans as authoritative intent. If changed code contradicts a spec, flag it at severity `important` or higher, citing the spec path.

## Context

### Project Conventions
{conv}

### Relevant Design Specs & Plans
{spec}

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

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS (all tests, including the existing ones)

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/review/prompts.py tests/test_prompts.py && uv run ruff format src/superseded/review/prompts.py tests/test_prompts.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superseded/review/prompts.py tests/test_prompts.py
git commit -m "feat(prompts): add conventions and spec context blocks + enforcement rules"
```

---

## Task 7: Engine forwarding

Add the two new kwargs to `ReviewEngine.review` and forward them to `build_prompt`. Mechanical change.

**Files:**
- Modify: `src/superseded/review/engine.py:90-128`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine.py` (read it first to match its style):

```python
def test_review_forwards_conventions_and_spec_signals(monkeypatch):
    from superseded.review.engine import ReviewEngine
    from superseded.models import ReviewResult
    from unittest.mock import MagicMock

    agent = MagicMock()
    agent.is_available.return_value = True
    agent.build_command.return_value = ["echo"]
    agent.parse_output.return_value = []
    engine = ReviewEngine(agent=agent, config=MagicMock(is_pass_enabled=lambda n: True))

    captured = {}
    monkeypatch.setattr(
        "superseded.review.engine.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="[]", stderr=""),
    )
    monkeypatch.setattr(
        "superseded.review.engine.build_prompt",
        lambda **kw: captured.update(kw) or "prompt",
    )

    engine.review(
        diff="diff",
        conventions_signals="conv-block",
        spec_signals="spec-block",
        passes=["security"],
    )
    assert captured.get("conventions_signals") == "conv-block"
    assert captured.get("spec_signals") == "spec-block"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_engine.py::test_review_forwards_conventions_and_spec_signals -v`
Expected: FAIL with `TypeError: review() got an unexpected keyword argument 'conventions_signals'`

- [ ] **Step 3: Write minimal implementation**

Edit `src/superseded/review/engine.py`. In the `review` method signature (lines 90-100), add two kwargs after `usage_signals`:

```python
        static_signals: str | None = None,
        usage_signals: str | None = None,
        conventions_signals: str | None = None,
        spec_signals: str | None = None,
        passes: list[str] | None = None,
```

In the `build_prompt(...)` call inside the loop (lines 120-128), add the two kwargs:

```python
                prompt = build_prompt(
                    pass_name=pass_name,
                    diff=diff,
                    pr_description=pr_description,
                    file_context=file_context,
                    memory_context=memory_context,
                    static_signals=static_signals,
                    usage_signals=usage_signals,
                    conventions_signals=conventions_signals,
                    spec_signals=spec_signals,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_engine.py -v`
Expected: PASS (all engine tests)

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/review/engine.py tests/test_engine.py && uv run ruff format src/superseded/review/engine.py tests/test_engine.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superseded/review/engine.py tests/test_engine.py
git commit -m "feat(engine): forward conventions and spec signals to build_prompt"
```

---

## Task 8: CLI flags + wiring

Add `--no-conventions`/`--no-specs` flags, call the two new discover functions, thread the kwargs through `_run_review` → `engine.review`.

**Files:**
- Modify: `src/superseded/cli.py:15-19` (imports), `:167-185` (flags + signature), `:210-224` (call), `:227-303` (`_run_review` body)
- Test: `tests/test_integration.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_integration.py`:

```python
def test_conventions_and_specs_called_and_forwarded(monkeypatch):
    """discover_conventions and discover_repo_specs are called and kwargs forwarded."""
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None: "diff --git a/x.py b/x.py\n+x",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr("superseded.cli.compute_file_context", lambda d, root=None: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)
    monkeypatch.setattr("superseded.cli.run_static_analysis", lambda *a, **kw: None)
    monkeypatch.setattr("superseded.cli.retrieve_usages", lambda *a, **kw: None)

    called_conv = []
    called_spec = []
    monkeypatch.setattr(
        "superseded.cli.discover_conventions",
        lambda root: (called_conv.append(True), "conv block")[1],
    )
    monkeypatch.setattr(
        "superseded.cli.discover_repo_specs",
        lambda diff, root: (called_spec.append(True), "spec block")[1],
    )

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[])
    monkeypatch.setattr("superseded.cli.ReviewEngine.select", lambda *a, **kw: mock_engine)

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        agent=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
    )

    assert called_conv
    assert called_spec
    call_kwargs = mock_engine.review.call_args
    assert call_kwargs[1].get("conventions_signals") == "conv block"
    assert call_kwargs[1].get("spec_signals") == "spec block"


def test_no_conventions_flag_skips_discover(monkeypatch):
    monkeypatch.setattr(
        "superseded.cli.fetch_diff",
        lambda pr=None, diff_range=None, files=None: "diff --git a/x.py b/x.py\n+x",
    )
    monkeypatch.setattr("superseded.cli.fetch_pr_description", lambda pr: None)
    monkeypatch.setattr("superseded.cli.compute_file_context", lambda d, root=None: None)
    monkeypatch.setattr("superseded.cli.current_repo", lambda: None)
    monkeypatch.setattr("superseded.cli.run_static_analysis", lambda *a, **kw: None)
    monkeypatch.setattr("superseded.cli.retrieve_usages", lambda *a, **kw: None)

    called = []
    monkeypatch.setattr(
        "superseded.cli.discover_conventions",
        lambda root: (called.append("conv"), None)[1],
    )
    monkeypatch.setattr(
        "superseded.cli.discover_repo_specs",
        lambda diff, root: (called.append("spec"), None)[1],
    )

    mock_engine = MagicMock()
    mock_engine.review.return_value = MagicMock(findings=[])
    monkeypatch.setattr("superseded.cli.ReviewEngine.select", lambda *a, **kw: mock_engine)

    _run_review(
        pr=None,
        diff_range="HEAD~1..HEAD",
        agent=None,
        model=None,
        output_format="json",
        post=False,
        passes=None,
        no_conventions=True,
        no_specs=True,
    )

    assert not called
    call_kwargs = mock_engine.review.call_args
    assert call_kwargs[1].get("conventions_signals") is None
    assert call_kwargs[1].get("spec_signals") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_integration.py::test_conventions_and_specs_called_and_forwarded -v`
Expected: FAIL — either `AttributeError: module 'superseded.cli' has no attribute 'discover_conventions'` or `TypeError: _run_review() got an unexpected keyword argument 'no_conventions'`

- [ ] **Step 3: Write minimal implementation**

Edit `src/superseded/cli.py`.

**3a. Imports** — find the existing block (around lines 15-18) that imports `run_static_analysis`/`retrieve_usages` from `superseded.context.*` and add:

```python
from superseded.context.conventions import discover_conventions
from superseded.context.spec_retrieval import discover_repo_specs
```

(Place them immediately after the existing `run_static_analysis`/`retrieve_usages` imports, preserving isort order.)

**3b. Flags** — after the `--no-usage` line (line 169), add:

```python
@click.option("--no-conventions", is_flag=True, help="Disable project conventions injection")
@click.option("--no-specs", is_flag=True, help="Disable design spec/plan retrieval")
```

**3c. `review` signature** — add two params after `no_usage: bool,` (line 183):

```python
    no_conventions: bool,
    no_specs: bool,
```

**3d. `_run_review` call** — in the `_run_review(...)` call inside `review` (around line 220-223), add:

```python
        no_conventions=no_conventions,
        no_specs=no_specs,
```

**3e. `_run_review` signature** — add two kwargs after `no_usage: bool = False,` (line 240):

```python
    no_conventions: bool = False,
    no_specs: bool = False,
```

**3f. Body** — after the existing `usage_signals` block (around line 280, after `usage_signals = retrieve_usages(diff, root)`), add:

```python
    enable_conventions = config.conventions and not no_conventions
    enable_specs = config.spec_retrieval and not no_specs
    conventions_signals: str | None = None
    spec_signals: str | None = None
    if enable_conventions:
        conventions_signals = discover_conventions(root)
    if enable_specs:
        spec_signals = discover_repo_specs(diff, root)
```

**3g. `engine.review(...)` call** — add the two kwargs (around line 298-299, after `usage_signals=usage_signals`):

```python
            conventions_signals=conventions_signals,
            spec_signals=spec_signals,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_integration.py -v`
Expected: PASS (all integration tests)

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/cli.py tests/test_integration.py && uv run ruff format src/superseded/cli.py tests/test_integration.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superseded/cli.py tests/test_integration.py
git commit -m "feat(cli): add --no-conventions/--no-specs flags and wire discover calls"
```

---

## Task 9: Server worker wiring

Thread the two new discover calls through `server/worker.py`, gated on config (no `--no-*` flags here).

**Files:**
- Modify: `src/superseded/server/worker.py:190-211`

- [ ] **Step 1: Write the failing test**

First read `tests/test_server_worker.py` to match its mocking pattern. Then append:

```python
def test_worker_forwards_conventions_and_specs(monkeypatch):
    # Read the existing test_server_worker.py to find the fixture/helper pattern,
    # then adapt this test to call the worker's review path with config.conventions=True
    # and config.spec_retrieval=True, monkeypatching discover_conventions/discover_repo_specs
    # to canned strings, and asserting those strings appear in the engine.review call kwargs.
    # Match the existing mock pattern for run_static_analysis/retrieve_usages in this file.
    ...
```

(If the existing `test_server_worker.py` doesn't have a clean single-entry mock pattern for the review path, write a focused test that monkeypatches `superseded.server.worker.ReviewEngine.select` and the discover functions, calls the worker's review function with a canned job, and asserts the kwargs.)

Concretely, after reading the file, the test should:
- Build a `Job` and `Config` with `conventions=True`, `spec_retrieval=True`.
- Monkeypatch `superseded.server.worker.github` methods to return canned diff + description.
- Monkeypatch `superseded.server.worker.discover_conventions` → `"conv block"`, `superseded.server.worker.discover_repo_specs` → `"spec block"`.
- Monkeypatch `superseded.server.worker.ReviewEngine.select` → a `MagicMock` whose `.review.return_value` is a `ReviewResult(findings=[])`.
- Monkeypatch `superseded.server.worker.build_review_payload` → a canned payload, and `github.post_review` → a coroutine returning `[1]`.
- Run the worker; assert `mock_engine.review.call_args.kwargs["conventions_signals"] == "conv block"` and `["spec_signals"] == "spec block"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server_worker.py::test_worker_forwards_conventions_and_specs -v`
Expected: FAIL — `AttributeError: module 'superseded.server.worker' has no attribute 'discover_conventions'` or kwargs not forwarded

- [ ] **Step 3: Write minimal implementation**

Edit `src/superseded/server/worker.py`.

**3a. Imports** — in the local import block around line 190-192, add:

```python
        from superseded.context.conventions import discover_conventions
        from superseded.context.spec_retrieval import discover_repo_specs
```

(Place after the existing `from superseded.context.static_analysis import run_static_analysis` and `from superseded.context.usage_retrieval import retrieve_usages` lines.)

**3b. Body** — after the existing `usage_signals` block (around line 202, after `usage_signals = retrieve_usages(diff, repo_path)`), add:

```python
        conventions_signals: str | None = None
        spec_signals: str | None = None
        if config.conventions:
            conventions_signals = discover_conventions(repo_path)
        if config.spec_retrieval:
            spec_signals = discover_repo_specs(diff, repo_path)
```

**3c. `engine.review(...)` call** — add the two kwargs (around line 210, after `usage_signals=usage_signals`):

```python
            conventions_signals=conventions_signals,
            spec_signals=spec_signals,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server_worker.py -v`
Expected: PASS (all server worker tests)

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/superseded/server/worker.py tests/test_server_worker.py && uv run ruff format src/superseded/server/worker.py tests/test_server_worker.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superseded/server/worker.py tests/test_server_worker.py
git commit -m "feat(server): wire conventions and spec retrieval into worker"
```

---

## Task 10: Full-suite verification + AGENTS.md note

Run the entire test suite + lint + format, then add a one-line note to `AGENTS.md` so future agents know about the two new toggles.

**Files:**
- Modify: `AGENTS.md` (add the two flags to the conventions list)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS

- [ ] **Step 2: Run ruff check + format over everything**

Run: `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
Expected: no errors

- [ ] **Step 3: Smoke-test the CLI end-to-end (mock-free, local diff)**

Run: `uv run superseded review --diff HEAD~1..HEAD --format json --no-static --no-usage --no-memory 2>&1 | head -40`
Expected: a JSON result (or a clean agent-CLI-not-found error if no AI CLI is installed — both confirm the plumbing doesn't crash). The important check: no `AttributeError`/`TypeError` from the new code paths.

- [ ] **Step 4: Add the AGENTS.md note**

In `AGENTS.md`, in the `## Conventions` section (around line 27), after the bullet about the 5 pass names, add a bullet:

```markdown
- `Config.conventions` and `Config.spec_retrieval` (default `true`) inject repo-grounded convention docs and diff-relevant specs/plans/skills into every pass prompt. Disable with `.superseded.yaml` `conventions: false` / `spec_retrieval: false`, or `--no-conventions` / `--no-specs`. See `context/conventions.py` and `context/spec_retrieval.py`.
```

- [ ] **Step 5: Lint the doc change too**

Run: `uv run ruff check src/ tests/`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): note conventions/spec_retrieval toggles"
```

---

## Self-Review

After writing this plan, re-checked it against the spec (`docs/superpowers/specs/2026-06-25-project-conventions-enforcement-design.md`):

**Spec coverage:**
- Module 1 `context/conventions.py` with `discover_conventions`, blocklist stripping, fixed-order concat, budget, `None`-when-empty → Tasks 2-3. ✓
- Module 2 `context/spec_retrieval.py` with `discover_repo_specs`, slug derivation, filename/slug-in-body relevance, mtime ordering, budget, `None` paths, `rg`-missing handling → Tasks 4-5. ✓
- Prompt changes: two new kwargs, two new `###` sections at top of `## Context`, amended + new Rules → Task 6. ✓
- Config: `conventions` + `spec_retrieval` bools → Task 1. ✓
- CLI: `--no-conventions`/`--no-specs` flags + wiring → Task 8. ✓
- Server worker wiring → Task 9. ✓
- Engine forwarding → Task 7. ✓
- Testing plan: `test_context_conventions.py` (Tasks 2-3), `test_context_spec_retrieval.py` (Tasks 4-5), `test_prompts.py` extensions (Task 6), `test_integration.py` extensions (Task 8), `test_engine.py` extension (Task 7), `test_server_worker.py` extension (Task 9). ✓
- Failure handling table: covered by the `None`/`logger.warning`/`rg`-missing paths in Tasks 3 + 5. ✓
- Out of scope items: none implemented — correct. ✓

**Placeholder scan:** No "TBD"/"TODO" in implementation steps. Task 9 Step 1 has a descriptive block (not a placeholder) because the exact test shape depends on the existing `test_server_worker.py` fixture pattern, which the agent should read before writing — the block specifies exactly what to assert and how to mock. The concrete instructions are complete.

**Type consistency:** `discover_conventions(root: Path) -> str | None` (Task 3) matches the call in Task 8 (`discover_conventions(root)`) and Task 9. `discover_repo_specs(diff: str, root: Path) -> str | None` (Task 5) matches calls in Task 8 (`discover_repo_specs(diff, root)`) and Task 9. `build_prompt` kwargs `conventions_signals`/`spec_signals` (Task 6) match `engine.review` kwargs (Task 7) match `cli.py`/`worker.py` kwargs (Tasks 8-9). `derive_slug(filename: str) -> str` (Task 4) matches usage in Task 5. Config fields `conventions`/`spec_retrieval` (Task 1) match gating in Tasks 8-9.

No issues found.
