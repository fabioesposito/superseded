# Resolved-Thread Detection via GraphQL

## Problem

The spec (`code-review-tool-design.md:292`) requires checking whether past review
comments have been resolved before re-reviewing code. The feedback loop currently
only detects dismissals via 👎 reactions (`check_pr_feedback` + `_classify_feedback`).

The `_classify_feedback` function at `cli.py:334` already checks
`comment.get("resolved")`, but the REST API's PR review comment objects never
include a `resolved` field. The GraphQL `PullRequestReviewThread.isResolved`
field exposes this data.

## Solution

Add a new `check_resolved_threads()` function to `feedback.py` that queries the
GitHub GraphQL API for resolved review threads, then merge the result into the
existing comment list in `check_pr_feedback()`.

### Data flow

```
check_pr_feedback(pr, repo)
  ├── gh api repos/.../comments       → list of comment dicts (REST, existing)
  ├── check_resolved_threads(pr, owner, repo)  → set of resolved comment IDs (GraphQL, new)
  └── merge: for each comment in REST list, if comment.id in resolved set,
      set comment["resolved"] = True
  └── return merged list
```

`_classify_feedback` in `cli.py` is unchanged — it already reads
`comment.get("resolved")` at line 334.

### GraphQL query

```graphql
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $cursor) {
        nodes {
          isResolved
          comments(first: 1) { nodes { databaseId } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
```

### Pagination

`gh api graphql` does not auto-paginate GraphQL cursor queries. Handle pagination
manually in Python:

1. Execute query with `cursor: None`
2. Collect resolved thread comment IDs from `reviewThreads.nodes`
3. If `pageInfo.hasNextPage`, repeat with `cursor = pageInfo.endCursor`
4. Stop when `hasNextPage` is false

### Transport

Use `gh api graphql -f query=... -f owner=... -f repo=... -f pr=... [-f cursor=...]`
via `subprocess.run`. Same `gh`-based pattern as existing `check_pr_feedback`.

### Graceful degradation

Any error (subprocess failure, JSON parse error, GraphQL errors) returns an empty
set. The existing reaction-based classification still functions, so learn-back is
not broken — this adds a second intended signal.

## Testing

- `check_resolved_threads` with empty results → returns empty set
- `check_resolved_threads` with resolved thread → returns set with matching ID
- `check_resolved_threads` pagination → multiple pages, IDs aggregated
- `check_resolved_threads` error path → empty set
- `check_pr_feedback` integration → resolved threads inject `resolved: True`
- `_classify_feedback` with `resolved: True` → returns "dismiss" (existing behavior)
