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
