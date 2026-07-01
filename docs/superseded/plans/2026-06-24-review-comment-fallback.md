# Review Comment Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch `CalledProcessError` when GitHub rejects inline review comments outside the PR diff, isolate bad comments via binary search, and repost with valid comments inline plus out-of-range findings appended to the review body.

**Architecture:** Extract `_post_review_payload()` helper from `post_review_to_pr`. Add `_partition_comments()` binary-search helper returning bad-comment indices. Add `_build_fallback_text()` formatter. Wrap `post_review_to_pr` initial call in try/except; on failure, binary-search to find bad comments, move them to body, repost once. Happy path unchanged.

**Tech Stack:** Python stdlib (`subprocess`, `json`), `gh` CLI. No new deps.

---

### Task 1: Extract `_post_review_payload` helper

**Files:**
- Modify: `src/superseded/output/github_pr.py:47-54`

- [x] **Step 1: Extract the subprocess call and comment-ID extraction into a helper**

Replace the body of `post_review_to_pr` (lines 48-54) with a call through the new helper:

```python
def _post_review_payload(payload: dict, target_repo: str, pr: int) -> list[int]:
    cmd = ["gh", "api", f"repos/{target_repo}/pulls/{pr}/reviews", "--input", "-"]
    response = subprocess.run(
        cmd, input=json.dumps(payload), text=True, capture_output=True, check=True
    )
    return _extract_comment_ids(response.stdout)


def post_review_to_pr(pr: int, result: ReviewResult, repo: str | None = None) -> list[int]:
    payload = build_review_payload(result)
    target_repo = repo if repo is not None else _repo()
    return _post_review_payload(payload, target_repo, pr)
```

- [x] **Step 2: Run existing tests to verify the refactor is transparent**

```bash
uv run pytest tests/test_output.py -v
```

Expected: all 18 tests pass (no behavior change).

- [x] **Step 3: Commit**

```bash
git add src/superseded/output/github_pr.py
git commit -m "refactor: extract _post_review_payload helper"
```

---

### Task 2: Add `_partition_comments` binary-search helper

**Files:**
- Modify: `src/superseded/output/github_pr.py` (add function after `_post_review_payload`)

- [x] **Step 1: Write failing tests for `_partition_comments`**

Add these tests to `tests/test_output.py`:

```python
def test_partition_comments_all_valid():
    from superseded.output.github_pr import _partition_comments, _post_review_payload

    payload = {"body": "test", "comments": [{"path": "a.py", "line": 1, "body": "x"}]}
    with patch(
        "superseded.output.github_pr.subprocess.run",
        return_value=MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        ),
    ):
        bad = _partition_comments(payload["comments"], payload, "r", 1)
        assert bad == set()


def test_partition_comments_one_bad():
    from superseded.output.github_pr import _partition_comments, _post_review_payload

    payload = {"body": "test", "comments": [{"path": "gone.py", "line": 999, "body": "x"}]}
    with patch(
        "superseded.output.github_pr.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "gh"),
    ):
        bad = _partition_comments(payload["comments"], payload, "r", 1)
        assert bad == {0}


def test_partition_comments_mixed():
    from superseded.output.github_pr import _partition_comments, _post_review_payload

    payload = {
        "body": "test",
        "comments": [
            {"path": "a.py", "line": 1, "body": "good"},
            {"path": "b.py", "line": 999, "body": "bad"},
        ],
    }

    def side_effect(cmd, **kwargs):
        input_json = json.loads(kwargs.get("input", "{}"))
        comment_bodies = [c["body"] for c in input_json.get("comments", [])]
        if "bad" in comment_bodies:
            raise subprocess.CalledProcessError(1, "gh")
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )

    with patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect):
        bad = _partition_comments(payload["comments"], payload, "r", 1)
        assert bad == {1}


def test_partition_comments_empty():
    from superseded.output.github_pr import _partition_comments

    bad = _partition_comments([], {"body": "test"}, "r", 1)
    assert bad == set()
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_output.py -v -k "partition_comments"
```

Expected: 4 FAIL (function not defined).

- [x] **Step 3: Implement `_partition_comments`**

Add after `_post_review_payload` in `src/superseded/output/github_pr.py`:

```python
def _partition_comments(
    comments: list[dict], base_payload: dict, target_repo: str, pr: int
) -> set[int]:
    """Binary-search to identify indices of invalid (out-of-diff-hunk) comments.

    Returns a set of indices into *comments* that cannot be posted as inline
    comments.  Uses _post_review_payload to test batches — a batch that succeeds
    has no bad comments; a failing batch is split and recursed.
    """
    if len(comments) == 0:
        return set()
    if len(comments) == 1:
        try:
            _post_review_payload({**base_payload, "comments": [comments[0]]}, target_repo, pr)
            return set()
        except subprocess.CalledProcessError:
            return {0}

    mid = len(comments) // 2
    left_bad = _partition_comments(comments[:mid], base_payload, target_repo, pr)
    right_bad = _partition_comments(comments[mid:], base_payload, target_repo, pr)
    return left_bad | {i + mid for i in right_bad}
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_output.py -v -k "partition_comments"
```

Expected: 4 PASS.

- [x] **Step 5: Commit**

```bash
git add src/superseded/output/github_pr.py tests/test_output.py
git commit -m "feat: add binary-search partition for invalid review comments"
```

---

### Task 3: Add `_build_fallback_text` helper

**Files:**
- Modify: `src/superseded/output/github_pr.py` (add function after `_partition_comments`)

- [x] **Step 1: Write failing tests**

Add to `tests/test_output.py`:

```python
def test_build_fallback_text_single():
    from superseded.output.github_pr import _build_fallback_text

    f = Finding(
        pass_name="security",
        severity="critical",
        file="src/auth.py",
        line=142,
        end_line=142,
        title="SQL injection",
        description="User input in SQL",
        suggestion="Use params",
    )
    text = _build_fallback_text([f])
    assert "## Out-of-range findings" in text
    assert "src/auth.py:142" in text
    assert "[critical]" in text
    assert "SQL injection" in text


def test_build_fallback_text_multiple():
    from superseded.output.github_pr import _build_fallback_text

    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        end_line=1,
        title="t1",
        description="d",
        suggestion="s",
    )
    f2 = Finding(
        pass_name="style",
        severity="nit",
        file="b.py",
        line=2,
        end_line=2,
        title="t2",
        description="d",
        suggestion="s",
    )
    text = _build_fallback_text([f1, f2])
    assert text.count("- **") == 2
    assert "a.py:1" in text
    assert "b.py:2" in text
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_output.py -v -k "build_fallback_text"
```

Expected: 2 FAIL (function not defined).

- [x] **Step 3: Implement `_build_fallback_text`**

Add after `_partition_comments` in `src/superseded/output/github_pr.py`:

```python
def _build_fallback_text(findings: list[Finding]) -> str:
    lines = [
        "\n\n## Out-of-range findings\n\n",
        "These findings could not be placed as inline comments because their ",
        "line numbers fall outside the PR diff hunk:\n\n",
    ]
    for f in findings:
        lines.append(f"- **{f.file}:{f.line}** [{f.severity}] {f.title}\n")
    return "".join(lines)
```

Note: `Finding` is already imported at the top of the file — no import changes needed.

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_output.py -v -k "build_fallback_text"
```

Expected: 2 PASS.

- [x] **Step 5: Commit**

```bash
git add src/superseded/output/github_pr.py tests/test_output.py
git commit -m "feat: add fallback text formatter for out-of-range findings"
```

---

### Task 4: Wire fallback logic into `post_review_to_pr`

**Files:**
- Modify: `src/superseded/output/github_pr.py:47-54`

- [x] **Step 1: Write failing test for mixed valid/invalid fallback**

Add to `tests/test_output.py`:

```python
def test_post_review_fallback_mixed():
    """Happy path fails, binary search isolates bad comment, repost succeeds."""
    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security", severity="critical", file="ok.py",
                line=1, end_line=1, title="good", description="d", suggestion="s",
            ),
            Finding(
                pass_name="style", severity="nit", file="bad.py",
                line=999, end_line=999, title="out-of-range", description="d", suggestion="s",
            ),
        ]
    )

    call_count = [0]
    payloads = []

    def side_effect(cmd, **kwargs):
        call_count[0] += 1
        input_json = json.loads(kwargs.get("input", "{}"))
        payloads.append(input_json)
        # First call: full payload with 2 comments -> fail
        if call_count[0] == 1:
            raise subprocess.CalledProcessError(1, "gh")
        # Binary search: single comment batches
        # "good" (ok.py line 1) succeeds, "bad" (bad.py line 999) fails
        comment_bodies = [c.get("body", "") for c in input_json.get("comments", [])]
        if any("out-of-range" in b for b in comment_bodies):
            raise subprocess.CalledProcessError(1, "gh")
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"comments": [{"id": 1}]}),
            stderr="",
        )

    with (
        patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        post_review_to_pr(pr=1, result=result)

    # Last call payload has 1 comment (the valid one) + fallback body
    final_payload = payloads[-1]
    assert len(final_payload["comments"]) == 1
    assert "ok.py" in final_payload["comments"][0]["path"]
    assert "## Out-of-range findings" in final_payload["body"]
    assert "bad.py:999" in final_payload["body"]


def test_post_review_fallback_all_bad():
    """All comments out of range -> body-only review with fallback text."""
    result = ReviewResult(
        findings=[
            Finding(
                pass_name="security", severity="critical", file="a.py",
                line=999, end_line=999, title="bad1", description="d", suggestion="s",
            ),
            Finding(
                pass_name="style", severity="nit", file="b.py",
                line=999, end_line=999, title="bad2", description="d", suggestion="s",
            ),
        ]
    )

    payloads = []

    def side_effect(cmd, **kwargs):
        input_json = json.loads(kwargs.get("input", "{}"))
        payloads.append(input_json)
        if input_json.get("comments"):
            raise subprocess.CalledProcessError(1, "gh")
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"id": 1, "comments": []}),
            stderr="",
        )

    with (
        patch("superseded.output.github_pr.subprocess.run", side_effect=side_effect),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        post_review_to_pr(pr=1, result=result)

    final_payload = payloads[-1]
    assert final_payload["comments"] == []
    assert "## Out-of-range findings" in final_payload["body"]
    assert "a.py:999" in final_payload["body"]
    assert "b.py:999" in final_payload["body"]


def test_post_review_no_comments_raises():
    """CalledProcessError with empty comments re-raises."""
    result = ReviewResult(findings=[])

    with (
        patch(
            "superseded.output.github_pr.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        ),
        patch("superseded.output.github_pr._repo", return_value="owner/repo"),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            post_review_to_pr(pr=1, result=result)
```

Note: `pytest` is already imported via `from unittest.mock import ...` — add `import pytest` at the top of `tests/test_output.py`.

- [x] **Step 2: Add `import pytest` to tests**

Add `import pytest` after `from unittest.mock import MagicMock, patch` on line 4 of `tests/test_output.py`.

- [x] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_output.py -v -k "fallback"
```

Expected: 3 FAIL (fallback not implemented).

- [x] **Step 4: Update `post_review_to_pr` with fallback logic**

Replace `post_review_to_pr` in `src/superseded/output/github_pr.py`:

```python
def post_review_to_pr(pr: int, result: ReviewResult, repo: str | None = None) -> list[int]:
    payload = build_review_payload(result)
    target_repo = repo if repo is not None else _repo()

    try:
        return _post_review_payload(payload, target_repo, pr)
    except subprocess.CalledProcessError:
        if not payload["comments"]:
            raise

        bad_indices = _partition_comments(payload["comments"], payload, target_repo, pr)
        if bad_indices:
            valid_comments = [
                c for i, c in enumerate(payload["comments"]) if i not in bad_indices
            ]
            bad_findings = [result.findings[i] for i in sorted(bad_indices)]
            payload["comments"] = valid_comments
            payload["body"] += _build_fallback_text(bad_findings)
        else:
            raise

    return _post_review_payload(payload, target_repo, pr)
```

- [x] **Step 5: Run all tests to verify entire suite passes**

```bash
uv run pytest tests/test_output.py -v
```

Expected: all ~24 tests PASS.

- [x] **Step 6: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASS.

- [x] **Step 7: Run lint and format**

```bash
uv run ruff check src/superseded/output/github_pr.py tests/test_output.py
uv run ruff format src/superseded/output/github_pr.py tests/test_output.py
```

- [x] **Step 8: Commit**

```bash
git add src/superseded/output/github_pr.py tests/test_output.py
git commit -m "fix: fallback to review body for out-of-range inline comments"
```

---

### Task 5: Mark TODO item complete

**Files:**
- Modify: `TODO.md:26`

- [x] **Step 1: Check the box in TODO.md**

Change line 26 from `- [ ]` to `- [x]`:

```
- [x] **GitHub review comments can fail silently** ...
```

- [x] **Step 2: Commit**

```bash
git add TODO.md
git commit -m "docs: mark review comment fallback as done in TODO"
```
