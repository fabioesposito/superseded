from __future__ import annotations

import pytest

from superseded.validation import (
    InvalidInputError,
    sanitize_agent_prompt,
    validate_git_url,
    validate_issue_id,
    validate_repo_path,
)


class TestValidateIssueId:
    def test_valid_id(self):
        assert validate_issue_id("SUP-001") == "SUP-001"
        assert validate_issue_id("SUP-123") == "SUP-123"

    def test_rejects_traversal(self):
        with pytest.raises(InvalidInputError):
            validate_issue_id("../../etc/passwd")

    def test_rejects_empty(self):
        with pytest.raises(InvalidInputError):
            validate_issue_id("")

    def test_rejects_special_chars(self):
        with pytest.raises(InvalidInputError):
            validate_issue_id("SUP-001; rm -rf /")


class TestValidateGitUrl:
    def test_https_url(self):
        assert (
            validate_git_url("https://github.com/user/repo.git")
            == "https://github.com/user/repo.git"
        )

    def test_ssh_url(self):
        assert validate_git_url("git@github.com:user/repo.git") == "git@github.com:user/repo.git"

    def test_rejects_shell_injection(self):
        with pytest.raises(InvalidInputError):
            validate_git_url("https://example.com; rm -rf /")

    def test_rejects_file_protocol(self):
        with pytest.raises(InvalidInputError):
            validate_git_url("file:///etc/passwd")

    def test_rejects_empty(self):
        with pytest.raises(InvalidInputError):
            validate_git_url("")


class TestValidateRepoPath:
    def test_absolute_path(self, tmp_path):
        result = validate_repo_path(str(tmp_path))
        assert result == str(tmp_path)

    def test_rejects_relative(self):
        with pytest.raises(InvalidInputError):
            validate_repo_path("relative/path")

    def test_rejects_traversal(self):
        with pytest.raises(InvalidInputError):
            validate_repo_path("/foo/../../../etc")


class TestSanitizeAgentPrompt:
    def test_strips_null_bytes(self):
        assert sanitize_agent_prompt("hello\x00world") == "helloworld"

    def test_truncates_long(self):
        long = "a" * 200_000
        result = sanitize_agent_prompt(long)
        assert len(result) == 100_000
