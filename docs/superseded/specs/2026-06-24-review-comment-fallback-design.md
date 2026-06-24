# Review Comment Fallback Design

## Problem

`post_review_to_pr` (`output/github_pr.py:47-54`) sends all findings as inline review
comments in a single `POST /repos/{owner}/{repo}/pulls/{pr}/reviews` call. GitHub
maps `path` + `line`/`end_line` to diff hunks. If any comment's line range falls
outside the PR diff, GitHub rejects the **entire** payload with a 422 error. All
valid inline comments are lost and the review body never reaches the PR.

The subprocess call uses `check=True`, so `CalledProcessError` is raised with no
recovery.

## Solution

Catch `CalledProcessError` on the initial post. When it fires and there are inline
comments, isolate the out-of-range comments via binary search, move them to the
review body, and repost the payload with only valid inline comments.

### Happy path (unchanged)

1. `build_review_payload(result)` → payload with inline comments
2. `subprocess.run(["gh", "api", ..., "--input", "-"], input=json.dumps(payload))`
3. Return comment IDs

### Fallback path (new)

1. Post fails with `CalledProcessError`
2. If no inline comments → re-raise (the body itself is broken)
3. Binary-search `payload["comments"]` to partition into valid / out-of-range
4. If any comments are out-of-range:
   - Build a fallback text block listing each finding (file, line, severity, title)
   - Append it to `payload["body"]`
   - Repost with `payload["comments"]` = only the valid ones
5. Return comment IDs from the repost

### Binary search

Given a list of comment dicts and a payload template:

```
def _partition_comments(comments, base_payload, pr, repo) -> (valid, bad):
    if len(comments) <= 1:
        try post with just this comment
        return (comments, []) on success, ([], comments) on failure
    mid = len(comments) // 2
    left_valid, left_bad = _partition_comments(comments[:mid], ...)
    right_valid, right_bad = _partition_comments(comments[mid:], ...)
    return (left_valid + right_valid, left_bad + right_bad)
```

Worst case O(n) calls (every comment is bad), but typical case is 1--2 bad comments
with O(log n) overhead.

### Fallback text format

```
## Out-of-range findings

The following findings could not be placed as inline comments because their line
numbers fall outside the PR diff hunk:

- **src/auth.py:142** [critical] SQL injection in login handler
- **src/api.py:300** [important] Missing input validation
```

### Edge cases

- **All comments out of range**: Repost becomes body-only review (no inline
  comments). The fallback text block is the only place findings appear.
- **No comments in payload**: `CalledProcessError` re-raised — the body itself
  is malformed and there's nothing to fall back to.
- **Empty finding list**: Not a concern; `build_review_payload` produces
  `comments: []` and the review body posts fine.

## Design decisions

- **Binary search over one-by-one retry**: A one-at-a-time approach always takes
  N+1 calls (body-only + N individual posts). Binary search adds overhead only
  on failure and scales logarithmically.
- **CLI-only scope**: The server path (`server/github.py:post_review`) hits the
  REST API directly via httpx and would need a separate fix. This design covers
  the `gh`-based CLI output path only.
- **No per-finding retry granularity below binary search**: When a batch of two
  comments fails, we try each individually. A comment is "bad" if posting it alone
  still fails.

## Testing

- `post_review_to_pr` with all-valid comments → one call, returns IDs (existing
  test coverage).
- `CalledProcessError` with mixed valid/invalid → body-only call succeeds,
  binary search isolates bad comment, repost with valid comments + fallback text.
- `CalledProcessError` with all-bad comments → body-only review succeeds, all
  findings in fallback text block.
- `CalledProcessError` with no comments → re-raised (body is broken).
- Fallback text block correctly formats file, line, severity, title.
