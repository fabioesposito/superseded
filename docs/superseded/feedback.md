# Feedback & Adaptive Learning

Superseded gets smarter the more you use it. Every finding it produces is tracked, and your reactions (via GitHub or manual commands) teach it what matters to your team.

## How Feedback Works

| Action | Meaning | Effect |
|---|---|---|
| 👍 reaction on a review comment | Finding was helpful | Reinforces similar findings |
| 👎 reaction on a review comment | Finding was dismissed | Suppresses similar findings |
| Resolved thread on a review comment | Issue addressed | Counts as dismissal |
| `--helpful <comment_id>` | Manual approval | Same as 👍 reaction |
| `--dismiss <comment_id>` | Manual dismissal | Same as 👎 reaction |

## Checking Feedback

After posting a review to a PR with `superseded review --pr 123 --post`, team members can react to comments. Run this to ingest their feedback:

```bash
# Check current branch's PR
superseded feedback --check

# Check a specific PR
superseded feedback --check --pr 123
```

This scans the PR's review comments via the GitHub API, looking for:
- Reactions (`+1` / `-1`)
- Resolved threads

Each matching comment is recorded in the memory store and linked back to the original finding.

## Manual Feedback

If you're not posting to GitHub (local reviews), you can record feedback manually:

```bash
# Mark a finding as helpful
superseded feedback --helpful 42

# Dismiss a finding
superseded feedback --dismiss 42
```

Comment IDs come from the output of `superseded review --post`. Without `--post`, there is no comment ID mapping and manual feedback won't work.

## How Adaptive Learning Works

Superseded has a three-stage learning pipeline:

### 1. Stats Aggregation

After every review, stats are compiled from all past findings and feedback:

```
- security/critical findings on test files: 85% dismissal rate → suppress
- correctness/important: 90% acceptance rate → continue current approach
- style findings overall: 60% dismissal rate → prefer higher severity
```

This statistical context is injected into every review prompt so the AI agent knows your team's preferences at the pattern level.

### 2. Pattern Reflection

When you've accumulated enough feedback events (default: 5 new events since last reflection), superseded sends a batch of accepted and dismissed findings to an AI agent with a reflection prompt:

> "Here are findings the team accepted and findings they dismissed. Infer 3-5 concrete, actionable rules that describe the team's code-review preferences."

The agent returns rules like:

- "Flag missing docstrings on public APIs as important, not nit"
- "Do not flag positional-only parameters (`/`) as style issues"
- "Accept pattern-matching findings on domain logic, suppress on config files"

Rules include a confidence score based on how consistently the feedback supports them.

### 3. Rule Injection

Before each review, the top `max_learned_rules` (default: 5) rules are injected into the prompt under a "Learned Review Guidelines" section. The AI agent uses these to calibrate its findings to your team's standards.

## Configuration

```yaml
# .superseded.yaml
learned_review: true              # Enable/disable adaptive learning
reflection_threshold: 5           # Feedback events needed before AI reflection
max_learned_rules: 5              # Max rules to inject into prompts
```

- **`learned_review: false`**: Disables all adaptive learning. Reviews are stateless.
- **`reflection_threshold: 1`**: Reflect after every new feedback event. Highest adaptability, highest token cost.
- **`reflection_threshold: 20`**: Reflect only after substantial feedback. Slower adaptation.
- **`max_learned_rules: 1`**: Keep prompts minimal. Only inject the single most-confident rule.

## The Memory Database

All feedback and findings live in `.superseded/memory.db` (SQLite, gitignored). Schema:

| Table | Contents |
|---|---|
| `findings` | Every finding produced, with severity, file, line, title, description, and dismissal status |
| `feedback` | User actions (helpful/dismiss) linked to findings |
| `review_watermarks` | Per-PR head SHAs for progressive review |
| `review_stats` | Aggregated acceptance/dismissal rates per pass/severity/file-pattern |
| `learned_rules` | AI-inferred rules with confidence scores |
| `reflection_state` | Tracks which feedback has been processed by the reflector |

The database is auto-created and kept at the latest schema by Alembic migrations, which run automatically every time the store opens (and on server startup). Pre-existing databases from older superseded versions are adopted transparently on first run — no manual step, no data loss. You can delete the file at any time to reset all learning; a fresh one is created on next run. To run or inspect migrations deliberately, use `superseded migrate`.
