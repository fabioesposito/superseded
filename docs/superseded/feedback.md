# Feedback & Adaptive Learning

Superseded gets smarter the more you use it. Every finding it produces is tracked, and your reactions (via GitHub or manual commands) teach it what matters to your team.

## How Feedback Works

| Action | Meaning | Effect |
|---|---|---|
| 👍 reaction on a review comment | Finding was helpful | Reinforces similar findings |
| 👎 reaction on a review comment | Finding was dismissed | Suppresses similar findings |
| Resolved thread on a review comment | Issue addressed / thread resolved | Marks the finding `resolved`; it does **not** count as a dismissal, and the issue can be re-raised if it reappears |
| `--helpful <comment_id>` | Manual approval | Same as 👍 reaction |
| `--dismiss <comment_id>` | Manual dismissal | Same as 👎 reaction |
| Verifier drop | Machine rejected a finding before posting | Tracked separately; never trains learned rules or the human-dismissal stats |

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

Each matching comment is recorded in the memory store and linked back to the original finding. Resolved threads update `pr_findings.status` to `resolved`; thumbs-down and manual dismissals set it to `dismissed`. Those statuses drive review convergence across pushes.

## Outcome Stats

See how the reviewer is doing without reading the database:

```bash
superseded feedback --stats
```

The report includes:

- total findings and human dismissal rate
- verifier-drop count and rate (machine false positives caught before posting)
- open PR findings (unresolved convergence state)
- max review round seen for the repo
- per-pass/severity breakdown with dismissal rates

Verifier drops are deliberately excluded from the human dismissal rate and from learned-rule inputs. If the numbers diverge, the reviewer is either getting noisier or the verifier is doing more filtering — both are useful signals when tuning prompts or `REVIEW.md`.

## Manual Feedback

If you're not posting to GitHub (local reviews), you can record feedback manually:

```bash
# Mark a finding as helpful
superseded feedback --helpful 42

# Dismiss a finding
superseded feedback --dismiss 42
```

Comment IDs come from the output of `superseded review --post`. Without `--post`, there is no comment ID mapping and manual feedback won't work.

## Managing Learned Rules

Learned rules (section 3 below) can be inspected and overridden from the CLI:

```bash
# List all learned rules with their confidence and provenance
superseded feedback --rules

# Dismiss a rule so it stops being injected (by rule ID from --rules)
superseded feedback --dismiss-rule 3

# Reinforce a rule, raising its confidence (by rule ID from --rules)
superseded feedback --helpful-rule 3
```

Dismissing a rule is permanent until the next reflection creates a replacement;
it is the manual escape hatch if a rule does not match your team's preferences.

## How Adaptive Learning Works

Superseded has a three-stage learning pipeline:

### 1. Stats Aggregation

After every review, stats are compiled from all past findings and **human** feedback. Verifier drops are counted separately and never enter this table:

```
- security/critical findings on test files: 85% dismissal rate → suppress
- correctness/important: 90% acceptance rate → continue current approach
- style findings overall: 60% dismissal rate → prefer higher severity
```

This statistical context is injected into every review prompt so the AI agent knows your team's preferences at the pattern level.

### 2. Pattern Reflection

When you've accumulated enough **human** feedback events (default: 5 new events since last reflection), superseded sends a batch of accepted and dismissed findings to an AI agent with a reflection prompt. Verifier drops are excluded — the reflector learns from people, not from the reviewer's own filter:

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
| `findings` | Every finding produced, with severity, file, line, title, description, verification/grounding status, and dismissal status |
| `feedback` | Human actions (helpful/dismiss/resolved) and verifier drops, with a `source` column |
| `pr_findings` | Per-PR convergence state: fingerprint, status (`open`/`resolved`/`dismissed`), comment id |
| `review_watermarks` | Per-PR head SHAs and `review_round` counters |
| `review_stats` | Aggregated human acceptance/dismissal rates per pass/severity/file-pattern |
| `learned_rules` | AI-inferred rules with confidence scores |
| `reflection_state` | Tracks which human feedback has been processed by the reflector |

The database is auto-created and kept at the latest schema by Alembic migrations, which run automatically every time the store opens (and on server startup). Pre-existing databases from older superseded versions are adopted transparently on first run — no manual step, no data loss. You can delete the file at any time to reset all learning; a fresh one is created on next run. To run or inspect migrations deliberately, use `superseded migrate`.
