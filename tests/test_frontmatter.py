from __future__ import annotations

from datetime import date

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
    assert meta["date"] == date(2026, 4, 19)
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
