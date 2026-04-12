from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from superseded.github import (
    GhComment,
    fetch_github_issue,
    format_description,
    parse_github_url,
)


@pytest.mark.asyncio
async def test_fetch_github_issue_parses_response():
    gh_output = json.dumps(
        {
            "title": "Fix login bug",
            "body": "The login page crashes on Firefox.",
            "labels": [{"name": "bug"}, {"name": "priority-high"}],
            "assignee": {"login": "claude-code"},
            "comments": [
                {
                    "author": {"login": "alice"},
                    "body": "I can reproduce this.",
                    "createdAt": "2026-04-10T12:00:00Z",
                },
                {
                    "author": {"login": "bob"},
                    "body": "Working on a fix.",
                    "createdAt": "2026-04-11T09:00:00Z",
                },
            ],
        }
    )

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (gh_output.encode(), b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await fetch_github_issue("https://github.com/owner/repo/issues/42")

    assert result.title == "Fix login bug"
    assert result.body == "The login page crashes on Firefox."
    assert result.labels == ["bug", "priority-high"]
    assert result.assignee == "claude-code"
    assert len(result.comments) == 2
    assert result.comments[0].author == "alice"
    assert "I can reproduce this." in result.comments[0].body


@pytest.mark.asyncio
async def test_fetch_github_issue_handles_no_assignee():
    gh_output = json.dumps(
        {
            "title": "Add dark mode",
            "body": "Feature request",
            "labels": [],
            "assignee": None,
            "comments": [],
        }
    )

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (gh_output.encode(), b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await fetch_github_issue("https://github.com/owner/repo/issues/1")

    assert result.assignee == ""
    assert result.labels == []


@pytest.mark.asyncio
async def test_fetch_github_issue_invalid_url():
    with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
        await fetch_github_issue("https://not-github.com/foo")


@pytest.mark.asyncio
async def test_fetch_github_issue_gh_error():
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"gh: could not resolve to an Issue")
    mock_proc.returncode = 1

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(RuntimeError, match="gh: could not resolve"),
    ):
        await fetch_github_issue("https://github.com/owner/repo/issues/999")


# --- parse_github_url ---


def test_parse_github_url_valid():
    owner, repo, number = parse_github_url("https://github.com/acme/widget/issues/7")
    assert owner == "acme"
    assert repo == "widget"
    assert number == 7


def test_parse_github_url_trailing_slash():
    owner, repo, number = parse_github_url("https://github.com/acme/widget/issues/7/")
    assert owner == "acme"
    assert repo == "widget"
    assert number == 7


def test_parse_github_url_invalid():
    with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
        parse_github_url("https://gitlab.com/owner/repo/issues/1")


def test_parse_github_url_missing_number():
    with pytest.raises(ValueError, match="Invalid GitHub issue URL"):
        parse_github_url("https://github.com/owner/repo/issues/")


# --- format_description ---


def test_format_description_body_and_comments():
    comments = [
        GhComment(author="alice", body="looks good", created_at="2026-04-10T12:00:00Z"),
        GhComment(author="bob", body="fixed it", created_at="2026-04-11T09:00:00Z"),
    ]
    result = format_description("The body", comments)

    assert result.startswith("The body")
    assert "---\n**@alice** (2026-04-10):\n\nlooks good" in result
    assert "---\n**@bob** (2026-04-11):\n\nfixed it" in result


def test_format_description_empty_body_with_comments():
    comments = [
        GhComment(author="alice", body="only comment", created_at="2026-04-10T12:00:00Z"),
    ]
    result = format_description("", comments)

    assert result == "---\n**@alice** (2026-04-10):\n\nonly comment"


def test_format_description_no_comments():
    result = format_description("Just the body", [])
    assert result == "Just the body"
