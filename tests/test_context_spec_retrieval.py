from __future__ import annotations

import os
from pathlib import Path

import pytest

from superseded.context.spec_retrieval import SPEC_BUDGET, derive_slug, discover_repo_specs


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


# --- discover_repo_specs tests ---


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_returns_none_when_no_docs_dir(tmp_path: Path):
    diff = "diff --git a/x.py b/x.py\n+x"
    assert discover_repo_specs(diff, tmp_path) is None


def test_filename_in_body_match(tmp_path: Path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-foo-design.md"
    _write(spec, "This change touches src/superseded/review/prompts.py.\n")
    diff = "diff --git a/src/superseded/review/prompts.py b/src/superseded/review/prompts.py\n+x"
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None


def test_basename_in_body_match(tmp_path: Path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-bar-design.md"
    _write(spec, "We edit prompts.py here.\n")
    diff = "diff --git a/src/superseded/review/prompts.py b/src/superseded/review/prompts.py\n+x"
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None


def test_irrelevant_spec_not_selected(tmp_path: Path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-unrelated-design.md"
    _write(spec, "This is about something else entirely.\n")
    diff = "diff --git a/src/superseded/review/prompts.py b/src/superseded/review/prompts.py\n+x"
    assert discover_repo_specs(diff, tmp_path) is None


def test_slug_as_path_component_match(tmp_path: Path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-grounded-review-context-design.md"
    _write(spec, "no filenames mentioned here\n")
    diff = (
        "diff --git a/src/superseded/grounded-review-context/foo.py "
        "b/src/superseded/grounded-review-context/foo.py\n+x"
    )
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None


def test_ordering_specs_before_plans_newest_first(tmp_path: Path):
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


def test_budget_truncation_tail(tmp_path: Path):
    spec = tmp_path / "docs/superseded/specs/2026-06-25-big-design.md"
    _write(spec, "touches prompts.py\n" + "x" * (SPEC_BUDGET + 500))
    diff = "diff --git a/prompts.py b/prompts.py\n+x"
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None
    assert "omitted by spec-retrieval budget" in out


def test_rg_missing_returns_none_and_warns(tmp_path: Path, monkeypatch, caplog):
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


def test_skill_discovered(tmp_path: Path):
    skill = tmp_path / ".agents/skills/foo/SKILL.md"
    _write(skill, "mentions prompts.py\n")
    diff = "diff --git a/prompts.py b/prompts.py\n+x"
    out = discover_repo_specs(diff, tmp_path)
    assert out is not None
    assert ".agents/skills/foo/SKILL.md" in out
