# Resolved-Thread Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Query GitHub GraphQL API for resolved PR review threads and inject `resolved: True` into `check_pr_feedback` results so `_classify_feedback` can dismiss resolved findings.

**Architecture:** Add `check_resolved_threads()` to `feedback.py` that runs `gh api graphql` with cursor-based pagination, returning a set of resolved comment `databaseId` values. Update `check_pr_feedback()` to call it and merge the resolved flag into the existing REST comment list. `_classify_feedback` is unchanged.

**Tech Stack:** `gh api graphql` subprocess, Python `json`. No new deps.

---

### Task 1: Add `check_resolved_threads` function

**Files:**
- Modify: `src/superseded/memory/feedback.py` (add function after `_parse_comment_lines`)
- Test: `tests/test_memory.py` (add tests)

- [x] **Step 1: Write failing tests for `check_resolved_threads`**

Add these imports and tests to `tests/test_memory.py`:

```python
import json
import subprocess

from superseded.memory.feedback import check_resolved_threads
```

```python
@patch("subprocess.run")
def test_check_resolved_threads_empty(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }),
    )
    resolved = check_resolved_threads(pr=123, owner="o", repo="r")
    assert resolved == set()


@patch("subprocess.run")
def test_check_resolved_threads_finds_resolved(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": True,
                                    "comments": {"nodes": [{"databaseId": 9001}]},
                                },
                                {
                                    "isResolved": False,
                                    "comments": {"nodes": [{"databaseId": 9002}]},
                                },
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }),
    )
    resolved = check_resolved_threads(pr=1, owner="o", repo="r")
    assert resolved == {9001}


@patch("subprocess.run")
def test_check_resolved_threads_pagination(mock_run):
    mock_run.side_effect = [
        MagicMock(
            returncode=0,
            stdout=json.dumps({
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": True,
                                        "comments": {"nodes": [{"databaseId": 1}]},
                                    }
                                ],
                                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                            }
                        }
                    }
                }
            }),
        ),
        MagicMock(
            returncode=0,
            stdout=json.dumps({
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": True,
                                        "comments": {"nodes": [{"databaseId": 2}]},
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            }),
        ),
    ]
    resolved = check_resolved_threads(pr=1, owner="o", repo="r")
    assert resolved == {1, 2}
    assert mock_run.call_count == 2
    # Second call should pass the cursor
    second_call_args = mock_run.call_args_list[1].args[0]
    cursor_idx = second_call_args.index("cursor") + 1
    assert second_call_args[cursor_idx] == "c1"


@patch("subprocess.run")
def test_check_resolved_threads_error_returns_empty(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, "gh")
    resolved = check_resolved_threads(pr=1, owner="o", repo="r")
    assert resolved == set()


@patch("subprocess.run")
def test_check_resolved_threads_invalid_json_returns_empty(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="not json")
    resolved = check_resolved_threads(pr=1, owner="o", repo="r")
    assert resolved == set()
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_memory.py -v -k "check_resolved_threads"
```

Expected: 5 FAIL (function not defined).

- [x] **Step 3: Implement `check_resolved_threads`**

Add after `_parse_comment_lines` in `src/superseded/memory/feedback.py`:

```python
def check_resolved_threads(pr: int, owner: str, repo: str) -> set[int]:
    """Return set of comment databaseIds for resolved review threads.

    Queries the GitHub GraphQL API for PullRequestReviewThread.isResolved,
    paginating via cursor until all threads are fetched. Returns an empty set
    on any error (graceful degradation — the reaction-based dismissal path
    still works).
    """
    query = (
        "query($owner:String!,$repo:String!,$pr:Int!,$cursor:String){"
        "repository(owner:$owner,name:$repo){"
        "pullRequest(number:$pr){"
        "reviewThreads(first:100,after:$cursor){"
        "nodes{isResolved,comments(first:1){nodes{databaseId}}},"
        "pageInfo{hasNextPage,endCursor}"
        "}}}}}"
    )

    resolved_ids: set[int] = set()
    cursor: str | None = None

    while True:
        cmd = [
            "gh", "api", "graphql",
            "-f", f"query={query}",
            "-f", f"owner={owner}",
            "-f", f"repo={repo}",
            "-f", f"pr={pr}",
        ]
        if cursor is not None:
            cmd.extend(["-f", f"cursor={cursor}"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError:
            return set()

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return set()

        threads = data.get("data", {}).get("repository", {}).get(
            "pullRequest", {}
        ).get("reviewThreads", {})
        if not threads:
            return set()

        for node in threads.get("nodes", []):
            if node.get("isResolved"):
                for comment in node.get("comments", {}).get("nodes", []):
                    cid = comment.get("databaseId")
                    if cid is not None:
                        resolved_ids.add(cid)

        page_info = threads.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if cursor is None:
            break

    return resolved_ids
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory.py -v -k "check_resolved_threads"
```

Expected: 5 PASS.

- [x] **Step 5: Run lint**

```bash
uv run ruff check src/superseded/memory/feedback.py tests/test_memory.py
```

- [x] **Step 6: Commit**

```bash
git add src/superseded/memory/feedback.py tests/test_memory.py
git commit -m "feat: add GraphQL resolved-thread detection"
```

---

### Task 2: Update existing tests for `check_pr_feedback`

**Files:**
- Modify: `tests/test_memory.py:95-126` (existing `check_pr_feedback` tests)

- [x] **Step 1: Add `check_resolved_threads` mock to existing tests**

The three existing `check_pr_feedback` tests call `check_pr_feedback` with only `subprocess.run` mocked. After Task 1, `check_pr_feedback` internally calls `check_resolved_threads`, which also calls `subprocess.run` — the mock will return the wrong data for the GraphQL call.

Add `@patch("superseded.memory.feedback.check_resolved_threads")` decorator to each of the three existing `check_pr_feedback` tests, returning `set()`, and add the `mock_resolved` parameter. Update `test_check_pr_feedback_returns_reactions_and_resolution` to NOT check for `resolved` in the feedback dict (it now comes from GraphQL, not the REST stdout):

```python
@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_returns_reactions_and_resolution(mock_run, mock_resolved):
    mock_resolved.return_value = set()
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            '{"id": 1, "body": "test", "path": "a.py", "line": 1, "reactions": {"+1": 0, "-1": 2}}\n'
            '{"id": 2, "body": "good", "path": "b.py", "line": 2, "reactions": {"+1": 3, "-1": 0}}\n'
        ),
    )
    feedback = check_pr_feedback(pr=123, repo="owner/repo")
    assert len(feedback) == 2
    assert isinstance(feedback[0], dict)
    assert feedback[0]["id"] == 1
    assert feedback[0]["reactions"]["-1"] == 2
    assert feedback[1]["reactions"]["+1"] == 3


@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_empty(mock_run, mock_resolved):
    mock_resolved.return_value = set()
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    assert check_pr_feedback(pr=123, repo="owner/repo") == []


@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_jq_uses_top_level_line(mock_run, mock_resolved):
    mock_resolved.return_value = set()
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    check_pr_feedback(pr=123, repo="owner/repo")
    cmd = mock_run.call_args.args[0]
    jq_expr = cmd[cmd.index("--jq") + 1]
    assert "..line" not in jq_expr
    assert "line: .line" in jq_expr
    assert "_resolved" not in jq_expr
```

- [x] **Step 2: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory.py -v -k "test_check_pr_feedback and not (merges or no_resolved)"
```

Expected: 3 PASS (existing tests work with the GraphQL stub).

- [x] **Step 3: Commit**

```bash
git add tests/test_memory.py
git commit -m "test: update feedback tests for resolved-thread integration"
```

---

### Task 3: Merge resolved data into `check_pr_feedback`

**Files:**
- Modify: `src/superseded/memory/feedback.py:7-24` (`check_pr_feedback`)

- [x] **Step 1: Write failing integration test**

Add to `tests/test_memory.py`:

```python
@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_merges_resolved_threads(mock_run, mock_resolved):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            '{"id": 1, "body": "x", "path": "a.py", "line": 1, "reactions": {"+1": 0, "-1": 0}}\n'
            '{"id": 2, "body": "y", "path": "b.py", "line": 2, "reactions": {"+1": 0, "-1": 0}}\n'
        ),
    )
    mock_resolved.return_value = {2}

    feedback = check_pr_feedback(pr=1, repo="o/r")

    assert len(feedback) == 2
    assert feedback[0].get("resolved") is not True
    assert feedback[1]["resolved"] is True


@patch("superseded.memory.feedback.check_resolved_threads")
@patch("subprocess.run")
def test_check_pr_feedback_no_resolved_threads(mock_run, mock_resolved):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"id": 1, "body": "x", "path": "a.py", "line": 1, "reactions": {"+1": 0, "-1": 0}}\n',
    )
    mock_resolved.return_value = set()

    feedback = check_pr_feedback(pr=1, repo="o/r")

    assert len(feedback) == 1
    assert feedback[0].get("resolved") is not True
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_memory.py -v -k "merges_resolved or no_resolved_threads"
```

Expected: 2 FAIL (no resolved field set).

- [x] **Step 3: Update `check_pr_feedback`**

Replace `check_pr_feedback` in `src/superseded/memory/feedback.py`:

```python
def check_pr_feedback(pr: int, repo: str) -> list[dict]:
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/pulls/{pr}/comments",
                "--jq",
                ".[] | {id: .id, body: .body, path: .path, line: .line, reactions: .reactions}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    comments = _parse_comment_lines(result.stdout)

    owner, _, name = repo.partition("/")
    resolved_ids = check_resolved_threads(pr=pr, owner=owner, repo=name)
    if resolved_ids:
        for c in comments:
            if c.get("id") in resolved_ids:
                c["resolved"] = True

    return comments
```

- [x] **Step 4: Run all feedback tests**

```bash
uv run pytest tests/test_memory.py -v
```

Expected: all ~10 tests PASS (including existing ones).

- [x] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v -q --ignore=tests/test_server_app.py
```

Expected: all 186+ tests PASS.

- [x] **Step 6: Run lint and format**

```bash
uv run ruff check src/superseded/memory/feedback.py tests/test_memory.py
uv run ruff format src/superseded/memory/feedback.py tests/test_memory.py
```

- [x] **Step 7: Commit**

```bash
git add src/superseded/memory/feedback.py tests/test_memory.py
git commit -m "feat: merge resolved-thread data into check_pr_feedback"
```

---

### Task 4: Mark TODO item complete

**Files:**
- Modify: `TODO.md` (line 7)

- [x] **Step 1: Check the box in TODO.md**

Change line 7 from `- [ ]` to `- [x]`:

```
- [x] **Resolved-thread detection via GraphQL.**
```

- [x] **Step 2: Commit**

```bash
git add TODO.md
git commit -m "docs: mark resolved-thread detection as done in TODO"
```
