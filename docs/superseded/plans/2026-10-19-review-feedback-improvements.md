# Review Grounding, Convergence, and Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the transferable mechanics from Anthropic's code-review article to `superseded`: mechanically ground findings, make repeated reviews converge without losing unresolved issues, and measure reviewer noise/quality.

**Architecture:** Add a deterministic grounding layer between finding merge and output (pre-existing detection, static-analysis dedupe, evidence validation). Add a PR-scoped convergence layer backed by a new `pr_findings` table plus a review-round counter, and use it in both server and local paths. Add outcome statistics and an offline eval runner. Keep the existing 5-pass + verify architecture; do not add new passes.

**Tech Stack:** Python 3.14, Pydantic v2, Click, SQLite/aiosqlite + Postgres/asyncpg via SQLAlchemy/Alembic, pytest, ruff.

**Spec:** This plan implements the three bundles in `docs/superseded/specs/` conversation: (1) grounding/noise, (2) convergence, (3) eval/measurement. The source article is `https://claude.dev/blog/solving-code-review-at-anthropic/`.

## Global Constraints

- Every module starts with `from __future__ import annotations`.
- Python >= 3.14. Use `uv run` for all commands.
- Ruff rules: `E,W,F,I,N,UP,B,SIM,TCH,RUF`; line length 100; double quotes; isort `known-first-party = ["superseded"]`.
- Pydantic v2 models for data; `Finding.id` is auto-derived and must not be set except when reconstructing an existing persisted finding.
- `except A, B:` comma syntax is intentional; do not change it.
- Do not commit `.superseded/`, `*.db`, `.venv/`, `.pytest_cache/`, `.ruff_cache/`, or `.code-review-graph/`.
- Do not create `docs/superpowers/`.
- No network calls in tests. Mock `gh`, GitHub HTTP, and provider APIs.
- Verify every task with the named tests plus `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/`.

## Review Focus

- A finding outside the changed diff hunks must never block a PR: pre-existing detection must be conservative when the diff is empty (leave `pre_existing=False`).
- Evidence matching must tolerate whitespace and diff `+`/`-` prefixes, or real findings will be dropped.
- Duplicate suppression must not hide a real bug that reappeared after its thread was resolved; resolved findings may be re-posted, dismissed findings may not.
- A later diff-only round with no new issues must still report and block on unresolved critical/important findings from earlier rounds.
- Round-cap behavior must not suppress critical findings; it may suppress advisory ones.
- Both SQLite and Postgres must satisfy the same store contract; server code must not import `MemoryStore`-specific internals.
- Stats must distinguish human dismissals from verifier drops; orphan `feedback` rows are a bug, so record dropped findings before writing verifier feedback.
- Eval matching must not pass a case just because the file name matches; line and title assertions are required.

---

### Task 1: Grounding primitives

**Files:**
- Modify: `src/superseded/models.py`
- Modify: `src/superseded/diff.py`
- Create: `src/superseded/review/grounding.py`
- Modify: `src/superseded/review/engine.py`
- Modify: `src/superseded/review/prompts.py`
- Test: `tests/test_models.py`, `tests/test_diff.py`, `tests/test_grounding.py`, `tests/test_engine.py`, `tests/test_prompts.py`

**Interfaces:**
- `Finding` gains `evidence: str = ""`, `evidence_verified: bool = False`, `pre_existing: bool = False`, `duplicate_of_static: bool = False`, property `is_blocking -> bool`, property `fingerprint -> str`.
- `ReviewResult` gains `carried_findings: list[Finding] = []`, `round_number: int = 0`, `at_round_cap: bool = False`, `suppressed_count: int = 0`, `convergence_note: str = ""`.
- `diff.file_hunk_windows(diff: str) -> dict[str, list[tuple[int, int]]]` moves the hunk parsing currently in `output/github_pr.py::_file_hunk_windows`.
- `grounding.mark_pre_existing(findings, diff) -> list[Finding]`.
- `grounding.filter_static_duplicates(findings, static_signals) -> list[Finding]`.
- `grounding.validate_evidence(findings, diff, file_context) -> list[Finding]`.

- [ ] **Step 1: Write failing model tests** for defaults, `is_blocking` ignoring `pre_existing`, `fingerprint` ignoring line number, and `ReviewResult` convergence defaults.
- [ ] **Step 2: Run `uv run pytest tests/test_models.py -q` and confirm failure.**
- [ ] **Step 3: Add the fields/properties to `models.py`.**
- [ ] **Step 4: Write failing tests** for `file_hunk_windows` in `tests/test_diff.py`, then move the helper from `output/github_pr.py` into `diff.py` and update `github_pr.py` imports. Keep `_out_of_range_indices` behavior unchanged.
- [ ] **Step 5: Write failing tests** in `tests/test_grounding.py`:
  - finding line inside hunk is not pre-existing; line outside is pre-existing; empty diff leaves it false
  - static locations parsed from `ruff`-style `path.py:10:1: E501`, `mypy`-style `path.py:10: error`, and gitleaks `  path.py:10 — secret`
  - duplicate `suggestion`/`nit` findings are dropped; duplicate `critical`/`important` are kept and flagged
  - evidence found in diff/file context is verified; whitespace and leading `+`/`-` normalize; unverified `suggestion` is dropped; unverified `critical` is kept but not verified
- [ ] **Step 6: Implement `grounding.py` minimally to pass those tests.**
- [ ] **Step 7: Write failing prompt tests** asserting `evidence` is in `JSON_FORMAT_INSTRUCTIONS`, the retry prompt requires `evidence`, and `build_verify_prompt` includes `evidence` and `pre_existing` in the findings JSON.
- [ ] **Step 8: Update prompts. Add the rule: every finding must quote 1-3 exact lines of changed code in `evidence`; if you cannot quote it, do not report it.**
- [ ] **Step 9: Integrate into `ReviewEngine.review`:** after merge, filter static duplicates, mark pre-existing, run verify, validate evidence, then re-rank by severity. Add an engine test with a fake provider that returns one grounded and one ungrounded suggestion.
- [ ] **Step 10: Run `uv run pytest tests/test_models.py tests/test_diff.py tests/test_grounding.py tests/test_engine.py tests/test_prompts.py -q`, then ruff check/format.**
- [ ] **Step 11: Commit** with `feat(review): ground findings in changed code and evidence`.

### Task 2: REVIEW.md and review summary tags

**Files:**
- Modify: `src/superseded/context/conventions.py`
- Modify: `src/superseded/output/github_pr.py`
- Modify: `src/superseded/server/worker.py`
- Test: `tests/test_context_conventions.py`, `tests/test_output.py`, `tests/test_server_worker.py`

**Interfaces:**
- `CONVENTION_FILES` starts with `"REVIEW.md"`.
- `build_review_payload(result)` keeps its signature but reads `result.carried_findings`, `result.round_number`, `result.at_round_cap`, `result.suppressed_count`, `result.convergence_note`.
- Comment bodies contain a hidden `<!-- superseded:fingerprint=<fp> -->` marker.
- Tags: severity tag, plus `PRE-EXISTING`, `UNVERIFIED`, `STATIC` where applicable.
- `build_check_run_title(result)` includes new vs still-open counts.

- [ ] **Step 1: Write failing convention tests** asserting `REVIEW.md` is discovered, appears first, and survives the 4000-char budget ahead of `AGENTS.md`.
- [ ] **Step 2: Add `REVIEW.md` to `CONVENTION_FILES` first.**
- [ ] **Step 3: Write failing output tests:**
  - empty result body says `No new issues found.`
  - non-empty body starts with a count and blocking count
  - pre-existing critical does not set `REQUEST_CHANGES`
  - carried critical sets `REQUEST_CHANGES`
  - comment body contains fingerprint marker and `PRE-EXISTING`/`UNVERIFIED`/`STATIC` tags
  - evidence appears in a `<details>` block
  - round-cap note appears in the body
- [ ] **Step 4: Implement the payload/body/tag changes.**
- [ ] **Step 5: Update `build_check_run_title` tests and implementation for carried findings.**
- [ ] **Step 6: Run targeted tests and ruff; commit** `feat(review): add REVIEW.md policy and review summary tags`.

### Task 3: PR-scoped convergence schema and store methods

**Files:**
- Modify: `src/superseded/memory/models.py`
- Create: `src/superseded/memory/migrations/versions/0004_convergence.py`
- Modify: `src/superseded/memory/store.py`
- Modify: `src/superseded/memory/postgres.py`
- Modify: `src/superseded/memory/backend.py`
- Test: `tests/test_memory_store.py`, `tests/test_postgres_store.py`, `tests/test_migrations.py`

**Interfaces:**
- New SQLAlchemy model `PRFinding` (table `pr_findings`) with PK `(repo, pr_number, fingerprint)` and columns `finding_id`, `pass`, `severity`, `file`, `line`, `title`, `comment_id`, `status`, `created_at`, `updated_at`.
- `ReviewWatermark` gains `review_round: int` (column `review_round`, default 0, not null).
- Store methods on both backends:
  - `record_pr_findings_batch(repo, pr_number, findings: list[dict]) -> None`
  - `get_pr_findings(repo, pr_number) -> list[dict]`
  - `get_open_pr_findings(repo, pr_number) -> list[dict]`
  - `set_pr_comment_ids(repo, pr_number, pairs: list[tuple[str, int]]) -> None` (key is fingerprint)
  - `update_pr_finding_status(comment_id: int, status: str) -> None`
  - `get_review_round(repo, pr_number) -> int`
  - `set_review_round(repo, pr_number, review_round: int) -> None`
- `record_pr_findings_batch` upserts on `(repo, pr_number, fingerprint)`, preserving an existing `comment_id` when the incoming value is null.
- Add all methods to `memory/backend.py::Store`.

- [ ] **Step 1: Write failing SQLite store tests** for upsert, open filtering, comment-id mapping, status update by comment id, round round-trip, and preserving comment_id on re-upsert.
- [ ] **Step 2: Add the models and migration `0004_convergence.py` (down_revision `0003`), then implement SQLite methods.**
- [ ] **Step 3: Implement matching Postgres methods. Postgres tests are `@pytest.mark.postgres`; they may be skipped without `SUPERSEDED_POSTGRES_TEST_DSN`.**
- [ ] **Step 4: Add the Protocol entries.**
- [ ] **Step 5: Run targeted tests, `uv run pytest tests/test_migrations.py -q`, and ruff; commit** `feat(memory): add PR finding state and review rounds`.

### Task 4: Convergence planner and fingerprinting

**Files:**
- Create: `src/superseded/review/convergence.py`
- Test: `tests/test_convergence.py`

**Interfaces:**
- `PriorFinding(fingerprint, severity, file, line, title, comment_id, status)` dataclass.
- `prior_from_rows(rows: list[dict]) -> list[PriorFinding]`.
- `ConvergencePlan(findings_to_post, suppressed_count, carried_findings, round_number, at_round_cap, note)` dataclass.
- `plan_convergence(findings, prior, round_number, max_rounds=3) -> ConvergencePlan`.
- Rules: suppress `open` and `dismissed` fingerprints; allow `resolved` to be re-posted and reopened; carry all still-open critical/important prior findings; after `round_number > max_rounds`, only post critical findings and set `at_round_cap=True`.

- [ ] **Step 1: Write failing unit tests for each rule above, including line-shifted duplicate and round-cap advisory suppression.**
- [ ] **Step 2: Implement `convergence.py` minimally.**
- [ ] **Step 3: Run tests and ruff; commit** `feat(review): add convergence planner`.

### Task 5: Server convergence integration

**Files:**
- Modify: `src/superseded/server/github.py`
- Modify: `src/superseded/server/worker.py`
- Modify: `src/superseded/config.py`
- Test: `tests/test_server_github.py`, `tests/test_server_worker.py`, `tests/test_config.py`

**Interfaces:**
- `GitHubApp.fetch_review_threads(token, owner, repo, pr_number) -> list[dict]` returns `{"comment_id": int | None, "is_resolved": bool}` with GraphQL pagination.
- `Config.max_review_rounds: int = 3`.
- In `_run_review_for_job`, when `job.post` and `store` is available: load round and open prior findings, mark resolved threads, run the engine, call `plan_convergence`, set the `ReviewResult` convergence fields, post only `findings_to_post`, persist new `pr_findings`, set comment IDs, and advance the round. A check-run failure is required when any new or carried finding is blocking.
- When `job.post` is false or no store exists, keep the current single-shot behavior.

- [ ] **Step 1: Write failing GitHub GraphQL tests** for one page and two pages, and for a null `databaseId`.
- [ ] **Step 2: Implement `fetch_review_threads`.**
- [ ] **Step 3: Write failing worker tests** for: duplicate open finding not posted; carried critical makes the check run fail even with no new findings; round increments; resolved duplicate is re-posted; round-cap suppresses advisory findings.
- [ ] **Step 4: Add `max_review_rounds` to `Config`, then integrate convergence into the worker.**
- [ ] **Step 5: Run targeted tests and ruff; commit** `feat(server): converge reviews across PR rounds`.

### Task 6: Local CLI convergence integration

**Files:**
- Modify: `src/superseded/cli.py`
- Modify: `src/superseded/memory/store.py` (only if needed for feedback status)
- Test: `tests/test_cli.py`, `tests/test_memory.py`

**Interfaces:**
- Local `review --pr` loads the current round and open `pr_findings`, marks resolved threads via the existing `check_resolved_threads`, plans convergence, and posts only new findings.
- `_post_review_store` records `pr_findings`, comment IDs, the watermark, and the new round.
- `feedback --check` updates `pr_findings.status` to `resolved` for resolved threads and `dismissed` for thumbs-down/manual dismiss.

- [ ] **Step 1: Write failing CLI tests** with a fake/temporary store for round increment, duplicate suppression, carried findings, and noop reviews not incrementing the round.
- [ ] **Step 2: Implement the local convergence path and feedback status updates.**
- [ ] **Step 3: Run targeted tests and ruff; commit** `feat(cli): converge local PR reviews`.

### Task 7: Outcome statistics and verifier-drop persistence

**Files:**
- Modify: `src/superseded/audit/stats.py`
- Modify: `src/superseded/memory/store.py`
- Modify: `src/superseded/memory/postgres.py`
- Modify: `src/superseded/memory/backend.py`
- Modify: `src/superseded/cli.py`
- Modify: `src/superseded/server/worker.py`
- Test: `tests/test_audit_stats.py`, `tests/test_memory_store.py`, `tests/test_cli.py`, `tests/test_server_worker.py`

**Interfaces:**
- `Store.get_finding_outcome_counts(repo) -> dict` with `total`, `dismissed`, `verifier_dropped`.
- `Store.get_open_pr_finding_count(repo) -> int`.
- `Store.get_max_review_round(repo) -> int`.
- `StatsAggregator.get_report(repo) -> dict` with overall counts/rates, per pass/severity rows, open PR findings, and max round.
- `superseded feedback --stats` prints the report; JSON output is not required.
- Both local `_post_review_store` and server `_run_review_for_job` record `result.dropped_findings` before writing verifier feedback, so no orphan feedback rows are created.

- [ ] **Step 1: Write failing store tests** for outcome counts (human vs verifier), open PR count, and max round.
- [ ] **Step 2: Implement the store methods and `StatsAggregator.get_report`.**
- [ ] **Step 3: Write failing CLI test** for `feedback --stats` output containing total, dismissal rate, verifier drops, and open findings. Add `--stats` to the Click command without breaking existing flags.
- [ ] **Step 4: Write failing worker/local tests** proving dropped findings are persisted and verifier feedback is linked.
- [ ] **Step 5: Implement persistence fixes in both paths.**
- [ ] **Step 6: Run targeted tests and ruff; commit** `feat(audit): measure review outcomes and persist verifier drops`.

### Task 8: Offline eval runner and 20-case corpus

**Files:**
- Create: `src/superseded/eval/__init__.py`
- Create: `src/superseded/eval/runner.py`
- Modify: `src/superseded/cli.py`
- Create: `tests/evals/cases.yaml`
- Test: `tests/test_eval.py`

**Interfaces:**
- Pydantic models `EvalCase`, `ExpectedFinding`, `CaseResult`, `EvalReport`.
- `load_cases(path: Path) -> list[EvalCase]` reads YAML.
- `evaluate_case(case, result) -> CaseResult`; a match requires same file, `abs(line - expected.line) <= 2`, and `expected.title_contains.lower() in finding.title.lower()`. Each finding matches at most one expected finding.
- `run_cases(cases, provider, config) -> EvalReport`; `passed = recall == 1.0 and not forbidden_hit`.
- CLI `superseded eval CASES --dry-run` validates cases without a provider. Without `--dry-run`, it uses `ReviewEngine.select` and prints a table/JSON summary; exits 1 if any case fails.
- `tests/evals/cases.yaml` contains exactly 20 cases spanning security, correctness, performance, style, architecture, plus 2 clean/forbidden-finding cases.

- [ ] **Step 1: Write failing loader/evaluator tests** with one synthetic case and a fake `ReviewResult`.
- [ ] **Step 2: Implement `runner.py` and the Click command.**
- [ ] **Step 3: Add the 20-case YAML corpus with realistic Python/JS diffs and expected findings.**
- [ ] **Step 4: Add a test asserting 20 unique cases load and `eval --dry-run` exits 0.**
- [ ] **Step 5: Run targeted tests and ruff; commit** `feat(eval): add offline reviewer eval corpus and runner`.

### Task 9: Documentation and final integration

**Files:**
- Modify: `AGENTS.md`
- Modify: `src/superseded/review/__init__.py` only if exports are needed
- Test: full suite and ruff

- [ ] **Step 1: Document the new modules, `max_review_rounds`, `REVIEW.md`, `feedback --stats`, and `superseded eval` in `AGENTS.md`.**
- [ ] **Step 2: Run `uv run pytest tests/ -q`, `uv run ruff check src/ tests/`, and `uv run ruff format --check src/ tests/`.**
- [ ] **Step 3: Fix any failures, then commit** `docs: document grounding, convergence, and eval features`.

## Execution Notes

- Each task must be independently reviewable and committed.
- Do not dispatch parallel implementers; tasks share `models.py`, `cli.py`, and `worker.py` across bundles.
- If a task discovers a contract mismatch, update this plan's interface block and note it in the ledger before continuing.
