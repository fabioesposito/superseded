# MECE Docs Structure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure `docs/` into 4 MECE categories with YAML frontmatter and enhance ContextAssembler to produce a categorized index for agents.

**Architecture:** Move existing docs into `architecture/`, `guides/`, `adrs/`, `operations/` folders. Add YAML frontmatter (title, category, summary, tags, date) to every doc file. Update `ContextAssembler._build_docs_index_layer()` to parse frontmatter via PyYAML and group docs by category.

**Tech Stack:** Python 3.12+, PyYAML (already a dependency), existing test framework (pytest)

---

### Task 1: Create directory structure

**Files:**
- Create: `docs/architecture/`
- Create: `docs/guides/`
- Create: `docs/operations/`
- Rename: `docs/plans/` → `docs/adrs/`

**Step 1: Create new directories and rename plans/**

```bash
mkdir -p docs/architecture docs/guides docs/operations
git mv docs/plans docs/adrs
```

**Step 2: Verify structure**

```bash
ls docs/
```
Expected: `adrs/  architecture/  guides/  operations/`

**Step 3: Commit**

```bash
git add docs/
git commit -m "refactor: create MECE docs directory structure"
```

---

### Task 2: Move existing docs into categories

**Files:**
- Move: `docs/multi-repo.md` → `docs/architecture/multi-repo.md`
- Move: `docs/tickets.md` → `docs/guides/tickets.md`
- Move: `docs/user-guide.md` → `docs/guides/user-guide.md`

**Step 1: Move files**

```bash
git mv docs/multi-repo.md docs/architecture/multi-repo.md
git mv docs/tickets.md docs/guides/tickets.md
git mv docs/user-guide.md docs/guides/user-guide.md
```

**Step 2: Verify moves**

```bash
ls docs/architecture/ docs/guides/
```
Expected: `multi-repo.md` in architecture, `tickets.md` + `user-guide.md` in guides

**Step 3: Commit**

```bash
git add docs/
git commit -m "refactor: move existing docs into MECE categories"
```

---

### Task 3: Add frontmatter to existing docs

**Files:**
- Modify: `docs/architecture/multi-repo.md`
- Modify: `docs/guides/tickets.md`
- Modify: `docs/guides/user-guide.md`

**Step 1: Add frontmatter to multi-repo.md**

Edit `docs/architecture/multi-repo.md`, add before the first `#`:

```yaml
---
title: Multi-Repo Support
category: architecture
summary: How Superseded fans out pipeline stages across multiple repositories
tags: [multi-repo, pipeline, config]
date: 2026-04-11
---
```

**Step 2: Add frontmatter to tickets.md**

Edit `docs/guides/tickets.md`, add before the first `#`:

```yaml
---
title: Ticket Format
category: guides
summary: Markdown + YAML frontmatter format for pipeline tickets
tags: [tickets, format, frontmatter]
date: 2026-04-11
---
```

**Step 3: Add frontmatter to user-guide.md**

Edit `docs/guides/user-guide.md`, add before the first `#`:

```yaml
---
title: User Guide
category: guides
summary: Complete guide from first ticket to multi-repo pipelines
tags: [guide, getting-started]
date: 2026-04-11
---
```

**Step 4: Commit**

```bash
git add docs/
git commit -m "docs: add YAML frontmatter to existing docs"
```

---

### Task 4: Add frontmatter to ADR files

**Files:**
- Modify: All 25 files in `docs/adrs/`

**Step 1: Add frontmatter to each ADR**

For each `.md` file in `docs/adrs/`, extract the title from the first `# heading` line and add frontmatter. Example for `2026-04-11-harness-design.md`:

```yaml
---
title: Harness Design
category: adrs
summary: Architecture decisions for the agent harness
tags: [harness, architecture]
date: 2026-04-11
---
```

Pattern: title = first `# heading` text, date = from filename prefix, category = `adrs`.

**Step 2: Commit**

```bash
git add docs/adrs/
git commit -m "docs: add YAML frontmatter to all ADR files"
```

---

### Task 5: Create stub docs with frontmatter

**Files:**
- Create: `docs/architecture/pipeline.md`
- Create: `docs/architecture/agent-harness.md`
- Create: `docs/operations/setup.md`
- Create: `docs/operations/troubleshooting.md`

**Step 1: Create pipeline.md**

```markdown
---
title: Pipeline Engine
category: architecture
summary: Pipeline stage flow, retry logic, and worktree isolation
tags: [pipeline, stages, worktree]
date: 2026-04-19
---

# Pipeline Engine

The pipeline processes tickets through six stages: Spec → Plan → Build → Verify → Review → Ship.

## Stage Flow

Each stage runs as an isolated agent subprocess. The agent receives progressive context
(layers of increasing specificity) and writes artifacts to `.superseded/artifacts/{id}/`.

## Retry Logic

Failed stages retry up to `max_retries` (configurable in `.superseded/config.yaml`).
Error context from previous attempts is injected into the re-prompt.

## Worktree Isolation

BUILD, VERIFY, and REVIEW stages run in isolated git worktrees. Changes merge on
success, discard on failure.
```

**Step 2: Create agent-harness.md**

```markdown
---
title: Agent Harness
category: architecture
summary: How the harness orchestrates multi-agent pipelines with feedback loops
tags: [harness, agents, orchestration]
date: 2026-04-19
---

# Agent Harness

Superseded is an agent harness that orchestrates AI agents through a structured pipeline.

## Features

- **Feedback loops**: Stages retry on failure with error context
- **Execution plans**: Plan stage writes structured plan.md consumed by downstream stages
- **Progressive context**: Agents receive context in layers (AGENTS.md → docs → ticket → artifacts → rules → skill prompt)
- **Worktree isolation**: Changes are sandboxed until success
- **Quality enforcement**: Review findings loop back to BUILD
- **Iteration history**: Every attempt tracked in database and UI
```

**Step 3: Create setup.md**

```markdown
---
title: Development Setup
category: operations
summary: How to set up the development environment
tags: [setup, development, uv]
date: 2026-04-19
---

# Development Setup

## Prerequisites

- Python 3.12+
- Node.js (for Playwright tests)
- `uv` for dependency management

## Commands

```bash
uv sync                            # Install dependencies
uv run superseded                  # Start the server
uv run pytest tests/ -v           # Run all tests
uv run ruff check src/ tests/     # Lint
uv run ruff format src/ tests/    # Format
npx playwright test                # Run Playwright browser tests
```
```

**Step 4: Create troubleshooting.md**

```markdown
---
title: Troubleshooting
category: operations
summary: Common issues and fixes
tags: [troubleshooting, debugging]
date: 2026-04-19
---

# Troubleshooting

## Common Issues

### Agent subprocess fails silently

Check `.superseded/state.db` for the stage result. The `error` column contains the failure message.

### Worktree conflicts

Worktrees use `{issue_id}__{repo}` naming. If a worktree already exists from a previous run,
delete it: `rm -rf .superseded/worktrees/{name}`.

### Pipeline stuck in paused state

Reset the ticket status to `new` and stage to `spec` in the ticket frontmatter.
```

**Step 5: Commit**

```bash
git add docs/
git commit -m "docs: add architecture and operations stubs with frontmatter"
```

---

### Task 6: Add frontmatter parsing helper to ContextAssembler

**Files:**
- Create: `tests/test_frontmatter.py`
- Modify: `src/superseded/pipeline/context.py:36-49`

**Step 1: Write the failing test for frontmatter parsing**

```python
# tests/test_frontmatter.py
from __future__ import annotations

from pathlib import Path

from superseded.pipeline.context import parse_frontmatter


def test_parse_frontmatter_valid():
    content = """---
title: Test Doc
category: architecture
summary: A test document
tags: [test, example]
date: 2026-04-19
---

# Test Doc

Body content here.
"""
    meta, body = parse_frontmatter(content)
    assert meta["title"] == "Test Doc"
    assert meta["category"] == "architecture"
    assert meta["summary"] == "A test document"
    assert meta["tags"] == ["test", "example"]
    assert meta["date"] == "2026-04-19"
    assert "# Test Doc" in body
    assert "---" not in body


def test_parse_frontmatter_missing():
    content = "# No Frontmatter\n\nJust a regular doc."
    meta, body = parse_frontmatter(content)
    assert meta == {}
    assert body == content


def test_parse_frontmatter_malformed():
    content = "---\nnot yaml: [broken\n---\n# Doc"
    meta, body = parse_frontmatter(content)
    assert meta == {}
    assert "not yaml" in body
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_frontmatter.py -v
```
Expected: FAIL — `parse_frontmatter` does not exist yet

**Step 3: Implement `parse_frontmatter` in context.py**

Add this function before the `ContextAssembler` class:

```python
import yaml


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content.

    Returns (metadata dict, body text). If no frontmatter is found,
    returns ({}, original content).
    """
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
        if not isinstance(meta, dict):
            return {}, content
        return meta, parts[2].lstrip("\n")
    except yaml.YAMLError:
        return {}, content
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_frontmatter.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/pipeline/context.py tests/test_frontmatter.py
git commit -m "feat: add frontmatter parsing to ContextAssembler"
```

---

### Task 7: Update `_build_docs_index_layer` to produce categorized output

**Files:**
- Modify: `src/superseded/pipeline/context.py:36-49`
- Modify: `tests/test_context.py:85-97`

**Step 1: Write the failing test for categorized docs index**

Add to `tests/test_context.py`:

```python
def test_context_assembler_categorized_docs_index(tmp_path):
    """Docs index groups files by category from frontmatter."""
    docs_dir = tmp_path / "docs"
    arch_dir = docs_dir / "architecture"
    guides_dir = docs_dir / "guides"
    arch_dir.mkdir(parents=True)
    guides_dir.mkdir(parents=True)

    (arch_dir / "pipeline.md").write_text(
        "---\ntitle: Pipeline\ncategory: architecture\nsummary: Pipeline design\n---\n# Pipeline"
    )
    (guides_dir / "setup.md").write_text(
        "---\ntitle: Setup Guide\ncategory: guides\nsummary: How to set up\n---\n# Setup"
    )

    assembler = ContextAssembler(repo_path=str(tmp_path))
    prompt = assembler.build(
        stage=Stage.PLAN,
        issue=_make_issue(),
        artifacts_path=str(tmp_path / ".superseded" / "artifacts" / "SUP-001"),
    )

    assert "### Architecture" in prompt
    assert "### Guides" in prompt
    assert "pipeline.md" in prompt
    assert "Pipeline design" in prompt
    assert "setup.md" in prompt
    assert "How to set up" in prompt


def test_context_assembler_docs_fallback_no_frontmatter(tmp_path):
    """Docs without frontmatter fall back to first-line extraction."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "legacy.md").write_text("# Legacy Doc\nOld style content.")

    assembler = ContextAssembler(repo_path=str(tmp_path))
    prompt = assembler.build(
        stage=Stage.PLAN,
        issue=_make_issue(),
        artifacts_path=str(tmp_path / ".superseded" / "artifacts" / "SUP-001"),
    )

    assert "legacy.md" in prompt
    assert "Legacy Doc" in prompt
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_context.py::test_context_assembler_categorized_docs_index -v
```
Expected: FAIL — current implementation produces flat list, not categorized

**Step 3: Replace `_build_docs_index_layer` implementation**

Replace `context.py:36-49` with:

```python
    def _build_docs_index_layer(self, repo: str | None = None) -> str | None:
        repo_path = self._get_repo_path(repo)
        docs_dir = repo_path / "docs"
        if not docs_dir.exists():
            return None

        # Collect docs grouped by category
        categories: dict[str, list[tuple[str, str]]] = {}
        uncategorized: list[tuple[str, str]] = []

        for md_file in sorted(docs_dir.glob("**/*.md")):
            rel = md_file.relative_to(docs_dir)
            content = md_file.read_text(encoding="utf-8")
            meta, _ = parse_frontmatter(content)

            summary = meta.get("summary", "").strip()
            if not summary:
                # Fallback: first line without markdown heading markers
                summary = content.split("\n")[0].strip("# ").strip()

            category = meta.get("category", "").strip()
            if category and category in ("architecture", "guides", "adrs", "operations"):
                categories.setdefault(category, []).append((str(rel), summary))
            else:
                uncategorized.append((str(rel), summary))

        if not categories and not uncategorized:
            return None

        label = f"{repo} repo" if repo else "Documentation"
        sections: list[str] = [f"## {label} Index\n"]

        category_order = ["architecture", "guides", "adrs", "operations"]
        for cat in category_order:
            if cat in categories:
                sections.append(f"### {cat.title()}")
                for rel, summary in categories[cat]:
                    sections.append(f"- {rel}: {summary}")
                sections.append("")

        if uncategorized:
            sections.append("### Other")
            for rel, summary in uncategorized:
                sections.append(f"- {rel}: {summary}")

        return "\n".join(sections)
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_context.py -v
```
Expected: PASS (all context tests)

**Step 5: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: PASS

**Step 6: Lint**

```bash
uv run ruff check src/superseded/pipeline/context.py tests/test_frontmatter.py tests/test_context.py
```
Expected: No errors

**Step 7: Commit**

```bash
git add src/superseded/pipeline/context.py tests/test_context.py
git commit -m "feat: categorize docs index by frontmatter category"
```

---

### Task 8: Update AGENTS.md docs references

**Files:**
- Modify: `AGENTS.md`

**Step 1: Update the docs reference in AGENTS.md**

Find the section that references `docs/` and update to reflect the new structure:

```markdown
- `docs/` — Structured project documentation:
  - `docs/architecture/` — System design, component diagrams, data flow
  - `docs/guides/` — How-to docs (user guide, ticket format)
  - `docs/adrs/` — Architectural Decision Records (dated design/plan docs)
  - `docs/operations/` — Runbooks, setup, troubleshooting
```

**Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md to reference MECE docs structure"
```

---

### Task 9: Final verification

**Step 1: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: All PASS

**Step 2: Run linter**

```bash
uv run ruff check src/ tests/
```
Expected: No errors

**Step 3: Run formatter check**

```bash
uv run ruff format --check src/ tests/
```
Expected: No changes needed

**Step 4: Verify docs structure**

```bash
find docs/ -name "*.md" | head -20
```
Expected: All files in correct MECE folders with frontmatter
