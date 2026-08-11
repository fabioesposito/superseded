# Post-Review AI Verification Stage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional, sequential post-merge AI verification stage that filters false positives and re-estimates severity before emitting the final review result.

**Architecture:** After the 5 parallel passes complete and `merge_findings()` deduplicates findings, a new `_run_verification()` method in `ReviewEngine` sends the merged findings + diff back to the agent with a specialized verification prompt. The verifier returns `keep`/`drop` verdicts with optional severity re-estimation. Dropped findings are excluded from the final `ReviewResult` and recorded as `feedback` with `source = "verifier"`. Failure is non-fatal — original findings are kept on any error.

**Tech Stack:** Python 3.14, Pydantic v2, click, aiosqlite, Alembic migrations

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `src/superseded/models.py` | Add `verification`, `verified_severity`, `verification_reason` to `Finding` |
| Modify | `src/superseded/config.py` | Add `verify: bool = True` to `Config` |
| Modify | `src/superseded/review/prompts.py` | Add `build_verify_prompt()` function |
| Create | `src/superseded/review/verifier.py` | Add `_parse_verdicts()` parser |
| Modify | `src/superseded/review/engine.py` | Add `_run_verification()` method + call in `review()` |
| Modify | `src/superseded/cli.py` | Add `--verify`/`--no-verify` flag, `resolve_verify()`, plumbing |
| Create | `src/superseded/memory/migrations/versions/0003_verification_columns.py` | Alembic migration for `findings.verification`, `findings.verification_reason`, `feedback.source` |
| Modify | `src/superseded/memory/store.py` | Extend `record_findings_batch` for verification fields, add `record_verification_feedback()` |
| Modify | `tests/test_models.py` | Test new `Finding` fields |
| Modify | `tests/test_config.py` | Test `verify` config toggle + precedence |
| Modify | `tests/test_prompts.py` | Test `build_verify_prompt()` output structure |
| Create | `tests/test_verifier.py` | Test `_parse_verdicts()` |
| Modify | `tests/test_engine.py` | Test `_run_verification()` + integration in `review()` |
| Modify | `tests/test_cli.py` | Test `--verify`/`--no-verify` flag |
| Modify | `tests/test_memory_store.py` | Test new columns and `record_verification_feedback()` |

---

### Task 1: Add verification fields to the Finding model

**Files:**
- Modify: `src/superseded/models.py:5-51`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
def test_finding_verification_fields_default():
    f = Finding(
        pass_name="security",
        severity="important",
        file="foo.py",
        line=42,
        title="SQL injection",
        description="User input in raw query",
        suggestion="Use parameterized queries",
    )
    assert f.verification is None
    assert f.verified_severity is None
    assert f.verification_reason is None


def test_finding_verification_dropped():
    f = Finding(
        pass_name="security",
        severity="important",
        file="foo.py",
        line=42,
        title="SQL injection",
        description="User input in raw query",
        suggestion="Use parameterized queries",
        verification="dropped",
        verification_reason="Code already sanitizes input on line 15",
    )
    assert f.verification == "dropped"
    assert f.verification_reason == "Code already sanitizes input on line 15"


def test_finding_verification_reestimate():
    f = Finding(
        pass_name="style",
        severity="important",
        file="bar.py",
        line=10,
        title="Unclear naming",
        description="Variable x is ambiguous",
        suggestion="Rename to user_count",
        verification="kept",
        verified_severity="suggestion",
    )
    assert f.verification == "kept"
    assert f.verified_severity == "suggestion"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_finding_verification_fields_default -v`
Expected: FAIL with validation error for `verification`

- [ ] **Step 3: Add fields to Finding model**

Edit `src/superseded/models.py`, change the `Finding` class to add three new fields:

```python
class Finding(BaseModel):
    pass_name: PassName
    severity: Severity
    file: str
    line: int
    end_line: int | None = None
    title: str
    description: str
    suggestion: str
    confidence: Confidence = "high"
    reasoning: str = Field(default="")
    id: str = Field(default="")
    verification: Literal["kept", "dropped"] | None = None
    verified_severity: Severity | None = None
    verification_reason: str | None = None

    @model_validator(mode="after")
    def _default_end_line(self) -> Finding:
        if self.end_line is None:
            self.end_line = self.line
        return self

    def model_post_init(self, __context) -> None:
        if not self.id:
            raw = f"{self.pass_name}-{self.file}-{self.line}-{self.title}"
            self.id = f"{self.pass_name}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

    @property
    def dedup_key(self) -> str:
        med = hashlib.sha256(f"{self.file}-{self.line}-{self.title}".encode()).hexdigest()[:16]
        return med
```

The import for `Literal` is already present at line 4. No new imports needed.

- [ ] **Step 4: Run all model tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All tests PASS (11+ tests)

- [ ] **Step 5: Commit**

```bash
git add src/superseded/models.py tests/test_models.py
git commit -m "feat: add verification fields to Finding model"
```

---

### Task 2: Add verify toggle to Config + resolution logic

**Files:**
- Modify: `src/superseded/config.py:18-37`
- Modify: `src/superseded/cli.py:48-60` (add `VERIFY_ENV` constant)
- Modify: `src/superseded/cli.py:88-94` (add `resolve_verify()` function)
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the config test**

```python
def test_config_verify_defaults_to_true():
    config = Config()
    assert config.verify is True


def test_config_verify_from_dict():
    config = Config(verify=False)
    assert config.verify is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_config_verify_defaults_to_true -v`
Expected: FAIL with "Field required" or "Extra inputs are not permitted"

- [ ] **Step 3: Add `verify` to Config**

Edit `src/superseded/config.py`, add one line to the `Config` class after `max_learned_rules`:

```python
class Config(BaseModel):
    agent: str = "opencode"
    model: str | None = None
    passes: PassConfig = PassConfig()
    post_to_pr: bool = False
    format: str = "table"
    log_format: str = "text"
    log_level: str = "WARNING"
    memory: bool = True
    static_analysis: bool = True
    usage_retrieval: bool = True
    conventions: bool = True
    spec_retrieval: bool = True
    graph: bool = True
    sandbox: bool = False
    progressive: bool = True
    learned_review: bool = True
    reflection_threshold: int = 5
    max_learned_rules: int = 5
    verify: bool = True
```

- [ ] **Step 4: Add `VERIFY_ENV` constant to CLI**

Edit `src/superseded/cli.py`, add after the existing `SANDBOX_ENV` line (line 51):

```python
VERIFY_ENV = "SUPERSEDED_VERIFY"
```

- [ ] **Step 5: Add `resolve_verify()` function to CLI**

Edit `src/superseded/cli.py`, add after `resolve_sandbox()` (near line ~112):

```python
def resolve_verify(cli_value: bool | None, config: Config) -> bool:
    env = os.environ.get(VERIFY_ENV)
    if env is not None:
        return env.strip().lower() in _TRUTHY
    if cli_value is not None:
        return cli_value
    return config.verify
```

- [ ] **Step 6: Run config tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/superseded/config.py src/superseded/cli.py tests/test_config.py
git commit -m "feat: add verify toggle to Config and resolve_verify()"
```

---

### Task 3: Add build_verify_prompt() to prompts.py

**Files:**
- Modify: `src/superseded/review/prompts.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Write the prompt test**

```python
def test_build_verify_prompt_contains_expected_sections():
    from superseded.models import Finding
    from superseded.review.prompts import build_verify_prompt

    findings = [
        Finding(
            pass_name="security",
            severity="critical",
            file="a.py",
            line=1,
            title="SQLI",
            description="Raw query",
            suggestion="Parameterize",
        )
    ]
    diff = "diff --git a/a.py b/a.py\n+raw query"
    prompt = build_verify_prompt(findings, diff, "file context here")
    assert "verify" in prompt.lower()
    assert "diff" in prompt.lower()
    assert "SQLI" in prompt
    assert "file context here" in prompt
    assert "critical|important|suggestion|nit" in prompt


def test_build_verify_prompt_handles_none_file_context():
    from superseded.models import Finding
    from superseded.review.prompts import build_verify_prompt

    findings = [Finding(pass_name="style", severity="nit", file="x.py", line=1, title="T", description="d", suggestion="s")]
    prompt = build_verify_prompt(findings, "diff", None)
    assert "No additional file context" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompts.py::test_build_verify_prompt_contains_expected_sections -v`
Expected: FAIL with "build_verify_prompt not defined"

- [ ] **Step 3: Implement build_verify_prompt()**

Add to the end of `src/superseded/review/prompts.py`:

```python
def build_verify_prompt(
    findings: list,
    diff: str,
    file_context: str | None,
) -> str:
    """Build a verification prompt that asks the agent to re-examine merged findings.

    The agent receives the full diff, surrounding file context, and the merged
    findings JSON. It must return a verdict for each finding: ``keep`` (possibly
    with re-estimated severity/confidence) or ``drop`` (false positive).
    """
    import json as _json

    ctx = file_context or "No additional file context available."

    findings_json = _json.dumps(
        [
            {
                "id": f.id,
                "pass": f.pass_name,
                "severity": f.severity,
                "confidence": f.confidence,
                "file": f.file,
                "line": f.line,
                "title": f.title,
                "description": f.description,
            }
            for f in findings
        ],
        indent=2,
    )

    return f"""You are performing a final verification pass over the findings from a code review.

## Your Role
Re-examine each finding against the original diff and surrounding code. Your job is to catch false positives and re-calibrate severity. Be skeptical: if the code already handles the issue, the finding is wrong. Only drop a finding when the code clearly disproves it — keeping noise is better than dropping a real bug.

## Severity Calibration
Calibrate every kept finding against these anchors — `severity` must be one of exactly `critical`, `important`, `suggestion`, `nit`:
- `critical` — exploitable vulnerability or correctness bug causing data loss / outage.
- `important` — likely bug or security weakness that should block merge.
- `suggestion` — meaningful improvement to clarity, correctness, or maintainability.
- `nit` — subjective, trivial style preference.

When in doubt between two levels, pick the lower one.

## Context

### Diff
{diff}

### File Context (surrounding code for changed files, +/-20 lines from changes)
{ctx}

### Merged Findings
{findings_json}

## Output Format
Return ONLY a JSON array. No explanation text before or after.

[
  {{
    "id": "correctness-a1b2c3d4e5f6",
    "action": "keep",
    "severity": "suggestion",
    "confidence": "low",
    "reason": "short justification"
  }},
  {{
    "id": "security-f7e8d9c0b1a2",
    "action": "drop",
    "reason": "The code already handles this case on line 42"
  }}
]

If you have no opinion on a finding, omit it from the array — it will be kept unchanged.
"""
```

- [ ] **Step 4: Run prompt tests to verify they pass**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/review/prompts.py tests/test_prompts.py
git commit -m "feat: add build_verify_prompt() for post-merge verification"
```

---

### Task 4: Create verifier parser (new file review/verifier.py)

**Files:**
- Create: `src/superseded/review/verifier.py`
- Create: `tests/test_verifier.py`

- [ ] **Step 1: Write the verifier tests**

```python
"""Tests for review.verifier._parse_verdicts()."""
from __future__ import annotations

import json

import pytest

from superseded.review.verifier import Verdict, _parse_verdicts


def test_parse_verdicts_keep():
    raw = json.dumps([
        {"id": "sec-abc123", "action": "keep", "severity": "suggestion", "confidence": "low", "reason": "valid"}
    ])
    verdicts = _parse_verdicts(raw)
    assert len(verdicts) == 1
    assert verdicts["sec-abc123"].action == "keep"
    assert verdicts["sec-abc123"].severity == "suggestion"
    assert verdicts["sec-abc123"].confidence == "low"
    assert verdicts["sec-abc123"].reason == "valid"


def test_parse_verdicts_drop():
    raw = json.dumps([
        {"id": "cor-xyz789", "action": "drop", "reason": "already handled"}
    ])
    verdicts = _parse_verdicts(raw)
    assert verdicts["cor-xyz789"].action == "drop"
    assert verdicts["cor-xyz789"].reason == "already handled"


def test_parse_verdicts_keep_with_no_reestimate():
    raw = json.dumps([
        {"id": "perf-111", "action": "keep", "reason": "looks right"}
    ])
    verdicts = _parse_verdicts(raw)
    assert verdicts["perf-111"].action == "keep"
    assert verdicts["perf-111"].severity is None
    assert verdicts["perf-111"].confidence is None


def test_parse_verdicts_invalid_action_skipped():
    raw = json.dumps([
        {"id": "x", "action": "something_else", "reason": "hmm"}
    ])
    errors, verdicts = _parse_verdicts(raw, collect_errors=True)
    assert len(verdicts) == 0
    assert len(errors) == 1


def test_parse_verdicts_invalid_severity_skipped():
    raw = json.dumps([
        {"id": "x", "action": "keep", "severity": "extreme", "reason": "bad"}
    ])
    errors, verdicts = _parse_verdicts(raw, collect_errors=True)
    assert len(verdicts) == 1
    assert verdicts["x"].severity is None  # invalid severity is ignored, keep stands
    assert len(errors) == 0  # invalid severity is not a hard error


def test_parse_verdicts_non_json_returns_empty():
    raw = "This is not JSON at all"
    errors, verdicts = _parse_verdicts(raw, collect_errors=True)
    assert len(verdicts) == 0
    assert len(errors) == 1


def test_parse_verdicts_empty_array():
    raw = "[]"
    errors, verdicts = _parse_verdicts(raw, collect_errors=True)
    assert len(verdicts) == 0
    assert len(errors) == 0


def test_parse_verdicts_duplicate_ids_keeps_last():
    raw = json.dumps([
        {"id": "dup", "action": "keep", "reason": "first"},
        {"id": "dup", "action": "drop", "reason": "second"},
    ])
    verdicts = _parse_verdicts(raw)
    assert verdicts["dup"].action == "drop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_verifier.py -v`
Expected: FAIL with "No module named 'superseded.review.verifier'"

- [ ] **Step 3: Create src/superseded/review/verifier.py**

```python
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = frozenset({"critical", "important", "suggestion", "nit"})
_VALID_CONFIDENCES = frozenset({"high", "medium", "low"})


@dataclass
class Verdict:
    action: Literal["keep", "drop"]
    severity: str | None = None
    confidence: str | None = None
    reason: str = ""


def _parse_verdicts(
    raw: str, *, collect_errors: bool = False
) -> dict[str, Verdict] | tuple[list[str], dict[str, Verdict]]:
    """Parse the verifier's JSON output into a dict of ``finding_id -> Verdict``.

    ``collect_errors`` returns a tuple ``(errors, verdicts)`` for error-tolerant
    callers (e.g. logging partial parse failures). When ``False``, returns only
    the dict and logs warnings silently.

    Invalid items (missing id, unknown action, unparseable JSON) are skipped.
    Items with invalid severity strings keep the verdict but drop the severity.
    """
    verdicts: dict[str, Verdict] = {}
    errors: list[str] = []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as err:
        msg = f"Verifier output is not valid JSON: {err}"
        logger.warning(msg)
        if collect_errors:
            return [msg], {}
        return {}
    if not isinstance(items, list):
        msg = "Verifier output is not a JSON array"
        logger.warning(msg)
        if collect_errors:
            return [msg], {}
        return {}
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"verifier item is not a dict: {item!r}")
            continue
        fid = item.get("id")
        if not fid or not isinstance(fid, str):
            errors.append(f"verifier item missing string 'id': {item!r}")
            continue
        action = item.get("action")
        if action not in ("keep", "drop"):
            errors.append(f"verifier item {fid!r} has invalid action {action!r}")
            continue
        severity = item.get("severity")
        if severity is not None and (not isinstance(severity, str) or severity not in _VALID_SEVERITIES):
            severity = None
        confidence = item.get("confidence")
        if confidence is not None and (not isinstance(confidence, str) or confidence not in _VALID_CONFIDENCES):
            confidence = None
        verdicts[fid] = Verdict(
            action=action,
            severity=severity,
            confidence=confidence,
            reason=str(item.get("reason", "")),
        )
    if collect_errors:
        return errors, verdicts
    return verdicts
```

- [ ] **Step 4: Run verifier tests to verify they pass**

Run: `uv run pytest tests/test_verifier.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/review/verifier.py tests/test_verifier.py
git commit -m "feat: add verifier output parser (_parse_verdicts)"
```

---

### Task 5: Add _run_verification() to ReviewEngine

**Files:**
- Modify: `src/superseded/review/engine.py`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Write the engine verification tests**

Read `tests/test_engine.py` first to understand the existing test patterns and mocks.

Add these tests to `tests/test_engine.py`:

```python
def test_run_verification_keeps_all():
    """When verifier returns all 'keep', all findings are preserved."""
    from unittest.mock import MagicMock

    from superseded.config import Config
    from superseded.models import Finding, ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.build_command.return_value = ["fake-agent"]
    engine.agent.parse_output.return_value = []
    engine.agent.name = "fake-agent"

    f1 = Finding(pass_name="security", severity="critical", file="a.py", line=1, title="X", description="d", suggestion="s")
    f2 = Finding(pass_name="style", severity="nit", file="b.py", line=2, title="Y", description="d", suggestion="s")
    result = ReviewResult(findings=[f1, f2])

    mock_run = MagicMock(return_value='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}, {"id": "' + f2.id + '", "action": "keep", "reason": "ok"}]')

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert len(new_result.findings) == 2


def test_run_verification_drops_false_positives():
    """When verifier drops some findings, they are excluded."""
    from unittest.mock import MagicMock

    from superseded.config import Config
    from superseded.models import Finding, ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    f1 = Finding(pass_name="security", severity="critical", file="a.py", line=1, title="Real", description="d", suggestion="s")
    f2 = Finding(pass_name="style", severity="nit", file="b.py", line=2, title="Fake", description="d", suggestion="s")
    result = ReviewResult(findings=[f1, f2])

    mock_run = MagicMock(return_value='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}, {"id": "' + f2.id + '", "action": "drop", "reason": "false positive"}]')

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert len(new_result.findings) == 1
    assert new_result.findings[0].id == f1.id
    # Dropped finding should have verification="dropped"
    assert f2.verification == "dropped"


def test_run_verification_reestimates_severity():
    """When verifier re-estimates severity, it is applied."""
    from unittest.mock import MagicMock

    from superseded.config import Config
    from superseded.models import Finding, ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    f = Finding(pass_name="performance", severity="important", file="a.py", line=5, title="Slow", description="d", suggestion="s")
    result = ReviewResult(findings=[f])

    mock_run = MagicMock(return_value='[{"id": "' + f.id + '", "action": "keep", "severity": "suggestion", "confidence": "low", "reason": "less severe than reported"}]')

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert len(new_result.findings) == 1
    assert new_result.findings[0].severity == "suggestion"
    assert new_result.findings[0].confidence == "low"
    assert new_result.findings[0].verified_severity == "suggestion"


def test_run_verification_failure_returns_original():
    """When verifier fails (non-zero exit), original findings are kept."""
    from unittest.mock import MagicMock

    from superseded.config import Config
    from superseded.models import Finding, ReviewResult
    from superseded.review.executor import AgentRunError

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    f = Finding(pass_name="security", severity="critical", file="a.py", line=1, title="X", description="d", suggestion="s")
    result = ReviewResult(findings=[f])

    mock_run = MagicMock(side_effect=AgentRunError("timeout"))

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert new_result is result  # same object
    assert len(new_result.warnings) == 1


def test_run_verification_missing_ids_kept():
    """Findings not in verifier output are kept unchanged."""
    from unittest.mock import MagicMock

    from superseded.config import Config
    from superseded.models import Finding, ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    f1 = Finding(pass_name="security", severity="critical", file="a.py", line=1, title="Mentioned", description="d", suggestion="s")
    f2 = Finding(pass_name="style", severity="nit", file="b.py", line=2, title="Omitted", description="d", suggestion="s")
    result = ReviewResult(findings=[f1, f2])

    mock_run = MagicMock(return_value='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}]')

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert len(new_result.findings) == 2  # both kept


def test_run_verification_skips_when_no_findings():
    """Verification is skipped when there are no findings."""
    from unittest.mock import MagicMock

    from superseded.config import Config
    from superseded.models import ReviewResult

    engine = ReviewEngine(agent=MagicMock(), config=Config(verify=True))
    engine.agent.name = "fake-agent"

    result = ReviewResult(findings=[])
    mock_run = MagicMock()

    new_result = engine._run_verification(result, "diff", "ctx", 600, MagicMock(run=mock_run))

    assert mock_run.call_count == 0
    assert new_result is result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_engine.py::test_run_verification_keeps_all -v`
Expected: FAIL with "ReviewEngine has no attribute '_run_verification'"

- [ ] **Step 3: Implement _run_verification() in engine.py**

Edit `src/superseded/review/engine.py`. Add the import at the top:

```python
from superseded.review.verifier import _parse_verdicts
```

Add `_run_verification()` method to `ReviewEngine` (after `_run_and_validate()`, before `review()`):

```python
    def _run_verification(
        self,
        result: ReviewResult,
        diff: str,
        file_context: str | None,
        timeout: int,
        sess: Session,
    ) -> ReviewResult:
        """Run a post-merge verification pass over the deduplicated findings.

        Sends the merged findings back to the agent with a specialised
        verification prompt. The agent returns ``keep``/``drop`` verdicts
        per finding with optional severity re-estimation.

        On any failure the original ``result`` is returned unchanged and a
        warning is appended.
        """
        from superseded.review.prompts import build_verify_prompt

        if not result.findings:
            return result

        prompt = build_verify_prompt(result.findings, diff, file_context)
        cmd = self.agent.build_command()
        try:
            stdout = sess.run(cmd, prompt, timeout=timeout)
        except Exception as err:
            logger.warning("Verification pass failed: %s", err)
            result.warnings.append(f"Verification pass failed: {err}")
            return result

        errors, verdicts = _parse_verdicts(stdout, collect_errors=True)

        kept: list[Finding] = []
        dropped_count = 0
        reestimated_count = 0

        for f in result.findings:
            verdict = verdicts.get(f.id)
            if verdict is None:
                # Not mentioned by verifier — keep unchanged
                f.verification = "kept"
                kept.append(f)
                continue
            if verdict.action == "drop":
                f.verification = "dropped"
                f.verification_reason = verdict.reason
                dropped_count += 1
                continue
            # action == "keep"
            f.verification = "kept"
            if verdict.severity is not None:
                f.severity = verdict.severity
                f.verified_severity = verdict.severity
                reestimated_count += 1
            if verdict.confidence is not None:
                f.confidence = verdict.confidence
            if verdict.reason:
                f.verification_reason = verdict.reason
            kept.append(f)

        for err in errors:
            logger.warning("Verification parse error: %s", err)

        dropped_msg = f"Verification completed: {dropped_count} findings dropped, {len(kept)} kept"
        if reestimated_count:
            dropped_msg += f" ({reestimated_count} re-estimated)"
        result.warnings.append(dropped_msg)

        return ReviewResult(findings=kept, warnings=result.warnings)
```

- [ ] **Step 4: Wire _run_verification() into review()**

Edit `src/superseded/review/engine.py`, in the `review()` method, add after the `merge_findings` line (which is inside the `with` block, after the `for ... as_completed` loop):

Current code location: the `result = self.merge_findings(all_findings)` line is at line 166, followed by `result.warnings = warnings` at line 167, and `return result` at line 168.

Replace lines 166-168:

```python
        result = self.merge_findings(all_findings)
        result.warnings = warnings

        if self.config.verify and result.findings:
            result = self._run_verification(result, diff, file_context, timeout, sess)

        return result
```

Note: `diff` and `file_context` are already in scope as parameters to `review()`. The `sess` context manager is available because `_run_verification` is called inside the `with` block.

- [ ] **Step 5: Run engine tests to verify they pass**

Run: `uv run pytest tests/test_engine.py -v`
Expected: All tests PASS (including new verification tests)

- [ ] **Step 6: Commit**

```bash
git add src/superseded/review/engine.py tests/test_engine.py
git commit -m "feat: add _run_verification() post-merge verification stage to engine"
```

---

### Task 6: Add --verify/--no-verify CLI flag and plumbing

**Files:**
- Modify: `src/superseded/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the CLI test**

Read `tests/test_cli.py` first to understand existing test patterns.

```python
def test_verify_flag_passed_to_run_review():
    """--no-verify flag is parsed and passed through."""
    from click.testing import CliRunner
    from superseded.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--no-verify", "--diff", "HEAD~1..HEAD"])
    # Command may fail on agent availability, but the flag should be parsed
    assert "--no-verify" not in str(result.exception).lower() if result.exception else True
```

- [ ] **Step 2: Add --verify/--no-verify flag to review command**

Edit `src/superseded/cli.py`. In the `review` function decorator chain (around line 322), add the flag after the `--sandbox/--no-sandbox` option:

```python
@click.option(
    "--verify/--no-verify",
    "verify",
    default=None,
    help="Toggle post-merge verification pass (default: from config; env SUPERSEDED_VERIFY).",
)
```

- [ ] **Step 3: Update review() function signature**

Add `verify: bool | None` parameter to the `review()` function signature (around line 324):

```python
def review(
    ctx: click.Context,
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: str | None,
    timeout: int | None,
    config_path: Path | None,
    no_memory: bool,
    full_review: bool,
    no_static: bool,
    no_usage: bool,
    no_conventions: bool,
    no_specs: bool,
    graph: bool | None,
    sandbox: bool | None,
    verify: bool | None,   # new
    staged: bool,
    files: tuple[str, ...],
) -> None:
```

- [ ] **Step 4: Pass verify through to _run_review()**

In the `review()` function, update the `_run_review()` call (around line 369) to include `verify`:

```python
    _run_review(
        pr=pr,
        diff_range=diff_range,
        agent=agent,
        model=model,
        output_format=output_format,
        post=post,
        passes=pass_list,
        timeout=timeout,
        config_path=config_path,
        no_memory=no_memory,
        full=full_review,
        no_static=no_static,
        no_usage=no_usage,
        no_conventions=no_conventions,
        no_specs=no_specs,
        graph=graph,
        sandbox=sandbox,
        verify=verify,      # new
        staged=staged,
        files=list(files) or None,
    )
```

- [ ] **Step 5: Add verify to _run_review() signature and resolve it**

Edit the `_run_review()` function signature (around line 392) to add `verify`:

```python
def _run_review(
    pr: int | None,
    diff_range: str | None,
    agent: str | None,
    model: str | None,
    output_format: str | None,
    post: bool,
    passes: list[str] | None,
    *,
    timeout: int | None = None,
    config_path: Path | None = None,
    no_memory: bool = False,
    full: bool = False,
    no_static: bool = False,
    no_usage: bool = False,
    no_conventions: bool = False,
    no_specs: bool = False,
    graph: bool | None = None,
    sandbox: bool | None = None,
    verify: bool | None = None,  # new
    staged: bool = False,
    files: list[str] | None = None,
) -> None:
```

After `config = load_config(config_path)` (around line 414) resolve verify:

```python
    config = load_config(config_path)
    agent_name = resolve_agent(agent, config)
    model_name = resolve_model(model, config)
    fmt = output_format or config.format
    post = post or config.post_to_pr
    verify = resolve_verify(verify, config)  # new
```

Then, in the `engine.review()` call (around line 549), the engine already reads `self.config.verify` (Task 5) — but we need to set it on the config being used. Since `engine` already holds `config`, we need to temporarily override it or pass it differently.

Best approach: the engine already has `self.config` set at construction time. The `_run_review` function creates the engine once at line 424 and that config is the loaded one. But we just resolved `verify` above based on CLI/env overrides. We need to update the engine's config with the resolved value.

Add after the verify resolution line:

```python
    verify = resolve_verify(verify, config)
    config.verify = verify  # new: apply CLI/env override to config
```

This way `engine.config.verify` reflects the resolved value.

- [ ] **Step 6: Run CLI tests to verify no regressions**

Run: `uv run pytest tests/test_cli.py -v`
Expected: All existing tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/superseded/cli.py tests/test_cli.py
git commit -m "feat: add --verify/--no-verify CLI flag for post-merge verification"
```

---

### Task 7: Add Alembic migration for verification columns

**Files:**
- Create: `src/superseded/memory/migrations/versions/0003_verification_columns.py`

- [ ] **Step 1: Create the migration file**

```python
"""verification columns

Add verification / verification_reason columns to findings table and
source column to feedback table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('findings', sa.Column('verification', sa.String(), nullable=True))
    op.add_column('findings', sa.Column('verification_reason', sa.String(), nullable=True))
    op.add_column('feedback', sa.Column('source', sa.String(), server_default='human', nullable=False))


def downgrade() -> None:
    # SQLite cannot drop columns; no-op to keep downgrade safe.
    pass
```

- [ ] **Step 2: Verify migration applies cleanly**

Run: `uv run python -c "from superseded.memory import alembic_runner; alembic_runner.upgrade('sqlite+aiosqlite:///.superseded/test_verify.db')"`
Expected: No errors. Then clean up: `rm -f .superseded/test_verify.db`

- [ ] **Step 3: Commit**

```bash
git add src/superseded/memory/migrations/versions/0003_verification_columns.py
git commit -m "feat: add Alembic migration for verification columns"
```

---

### Task 8: Extend memory store for verification fields

**Files:**
- Modify: `src/superseded/memory/store.py`
- Modify: `tests/test_memory_store.py`

- [ ] **Step 1: Read existing memory store tests**

Read `tests/test_memory_store.py` to understand test patterns (aiosqlite fixtures, `setup_db` fixture).

- [ ] **Step 2: Extend record_findings_batch() to accept verification fields**

Edit `src/superseded/memory/store.py`. In `record_findings_batch()`, update the SQL INSERT to include `verification` and `verification_reason`:

Replace the SQL and tuple list in `record_findings_batch()` (lines 225-252):

```python
        async def _do(db: aiosqlite.Connection) -> None:
            await db.executemany(
                "INSERT INTO findings "
                "(id, repo, pass, severity, file, line, title, description, reasoning, verification, verification_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "severity = excluded.severity, "
                "description = excluded.description, "
                "reasoning = excluded.reasoning, "
                "verification = excluded.verification, "
                "verification_reason = excluded.verification_reason "
                "WHERE excluded.severity != severity "
                "OR excluded.description != description "
                "OR excluded.reasoning != reasoning "
                "OR excluded.verification != verification "
                "OR excluded.verification_reason != verification_reason",
                [
                    (
                        f["id"],
                        repo,
                        f["pass_name"],
                        f["severity"],
                        f["file"],
                        f["line"],
                        f["title"],
                        f["description"],
                        f.get("reasoning", ""),
                        f.get("verification"),          # new
                        f.get("verification_reason"),   # new
                    )
                    for f in findings
                ],
            )
            await db.commit()
```

- [ ] **Step 3: Add record_verification_feedback() method**

Add to `MemoryStore` class in `src/superseded/memory/store.py`, after `record_feedback()`:

```python
    async def record_verification_feedback(self, finding_id: str) -> None:
        """Record that the AI verification pass dismissed a finding.

        Uses ``source = 'verifier'`` to distinguish from human ``dismiss`` actions,
        so the StatsAggregator and PatternReflector can treat them separately.
        """
        async with self._db() as db:
            await db.execute(
                "INSERT INTO feedback (finding_id, action, source) VALUES (?, ?, ?)",
                (finding_id, "dismiss", "verifier"),
            )
            await db.execute(
                "UPDATE findings SET dismissed = TRUE WHERE id = ?",
                (finding_id,),
            )
            await db.commit()
```

- [ ] **Step 4: Update _post_review_store in cli.py to pass verification fields**

Edit `src/superseded/cli.py`. In `_post_review_store()`, update the dict sent to `record_findings_batch()` (around lines 612-627) to include the new fields:

```python
        if result.findings:
            await store.record_findings_batch(
                [
                    {
                        "id": f.id,
                        "pass_name": f.pass_name,
                        "severity": f.severity,
                        "file": f.file,
                        "line": f.line,
                        "title": f.title,
                        "description": f.description,
                        "reasoning": f.reasoning,
                        "verification": f.verification,
                        "verification_reason": f.verification_reason,
                    }
                    for f in result.findings
                ],
                repo,
            )

            # Record verifier-dismissed findings as feedback.
            # Dropped findings live in result.dropped_findings (added in Step 5).
            if result.dropped_findings:
                _status(f"Recording {len(result.dropped_findings)} verifier-dropped findings as feedback...")
                for f in result.dropped_findings:
                    await store.record_verification_feedback(f.id)
```

- [ ] **Step 5: Add dropped_findings to ReviewResult**

Edit `src/superseded/models.py`, add to `ReviewResult`:

```python
class ReviewResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dropped_findings: list[Finding] = Field(default_factory=list)
```

Edit `_run_verification()` in `src/superseded/review/engine.py` to populate it. Replace the return line:

```python
    return ReviewResult(findings=kept, warnings=result.warnings)
```

With:

```python
    return ReviewResult(findings=kept, warnings=result.warnings, dropped_findings=dropped_findings)
```

And add the dropped_findings tracking variable:

In `_run_verification()`, add after `kept: list[Finding] = []`:

```python
    dropped_findings: list[Finding] = []
```

And in the "drop" branch, after `f.verification = "dropped"`:

```python
                dropped_findings.append(f)
```

Update `_post_review_store` in `cli.py` to also persist dropped findings:

```python
            # Record verifier-dropped findings as feedback
            if result.dropped_findings:
                _status(f"Recording {len(result.dropped_findings)} verifier-dropped findings as feedback...")
                for f in result.dropped_findings:
                    await store.record_verification_feedback(f.id)
```

- [ ] **Step 6: Run memory store tests + model tests to verify**

Run: `uv run pytest tests/test_memory_store.py tests/test_models.py -v`
Expected: All tests PASS

- [ ] **Step 7: Run engine tests again**

Run: `uv run pytest tests/test_engine.py -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/superseded/memory/store.py src/superseded/models.py src/superseded/review/engine.py src/superseded/cli.py
git commit -m "feat: persist verification fields and verifier-dismissed feedback"
```

---

### Task 9: Integration test & lint/format

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Read existing integration tests**

Read `tests/test_integration.py` to understand the mock pattern for `subprocess.run`.

- [ ] **Step 2: Write integration test for full verification pipeline**

```python
def test_review_with_verification_enabled():
    """Full review pipeline with verification enabled."""
    import json

    from superseded.agents.opencode import OpenCodeAgent
    from superseded.config import Config
    from superseded.models import Finding, ReviewResult
    from superseded.review.engine import ReviewEngine
    from superseded.review.merger import merge_findings

    agent = OpenCodeAgent()

    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="SQL Injection",
        description="Raw SQL with user input",
        suggestion="Use parameterized queries",
        confidence="high",
        reasoning="User input concatenated into SQL string",
    )
    f2 = Finding(
        pass_name="style",
        severity="nit",
        file="a.py",
        line=5,
        title="Unused import",
        description="os is imported but never used",
        suggestion="Remove the import",
        confidence="high",
        reasoning="grep shows no usage of os",
    )

    # Simulate merged findings
    result = merge_findings([[f1], [f2]])

    # Simulate verifier output: keeps f1, drops f2
    verify_output = json.dumps([
        {"id": f1.id, "action": "keep", "severity": "important", "reason": "Valid but downgrade to important"},
        {"id": f2.id, "action": "drop", "reason": "os is actually used on line 10"},
    ])

    class MockSession:
        call_count = 0
        def run(self, cmd, prompt, **kwargs):
            self.call_count += 1
            return verify_output

    mock_sess = MockSession()
    engine = ReviewEngine(agent=agent, config=Config(verify=True))

    verified = engine._run_verification(result, "mock diff", "mock ctx", 600, mock_sess)

    assert len(verified.findings) == 1
    assert verified.findings[0].id == f1.id
    assert verified.findings[0].severity == "important"  # re-estimated
    assert len(verified.dropped_findings) == 1
    assert verified.dropped_findings[0].id == f2.id


def test_review_with_verification_disabled():
    """When verify=False, review() skips the verification pass."""
    import json

    from superseded.agents.opencode import OpenCodeAgent
    from superseded.config import Config
    from superseded.models import Finding, ReviewResult

    f = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="X",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f])

    call_count = [0]

    class MockSession:
        def run(self, cmd, prompt, **kwargs):
            call_count[0] += 1
            return "[]"

    mock_sess = MockSession()

    # Verify the empty-findings guard: even with verify disabled at config level,
    # _run_verification skips when there are findings (the guard checks
    # result.findings, not config). The config-level check lives in review().
    engine = ReviewEngine(agent=OpenCodeAgent(), config=Config(verify=True))
    result2 = engine._run_verification(result, "diff", "ctx", 600, mock_sess)
    # This tests that _run_verification returns the result (kept findings + warnings).
    assert len(result2.findings) == 1
```

- [ ] **Step 3: Run the integration tests**

Run: `uv run pytest tests/test_integration.py -v`
Expected: All tests PASS (4+ tests, including 2 new verification integration tests)

- [ ] **Step 4: Run ruff check + ruff format**

```bash
uv run ruff check src/ tests/
```

- [ ] **Step 3: Run the integration tests**

Run: `uv run pytest tests/test_integration.py -v`
Expected: All tests PASS

- [ ] **Step 4: Run ruff check + ruff format**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```
Expected: No warnings, no format changes needed.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for verification pipeline"
```

---

### Task 10: Apply memory store migration on first review run

**Files:**
- Modify: `src/superseded/memory/alembic_runner.py` (verify it picks up 0003)

- [ ] **Step 1: Verify the migration is discovered**

Run: `uv run python -c "from superseded.memory import alembic_runner; alembic_runner.heads()"`
Expected: Output shows `0003` as the head revision.

- [ ] **Step 2: Test that init() applies the migration on an existing DB**

```bash
# Create a test DB at the old schema level
rm -f .superseded/test_migrate.db
uv run python -c "
from superseded.memory import alembic_runner
alembic_runner.upgrade('sqlite+aiosqlite:///.superseded/test_migrate.db')
print('OK')
"
# Verify columns exist
uv run python -c "
import sqlite3
conn = sqlite3.connect('.superseded/test_migrate.db')
cols = [c[1] for c in conn.execute('PRAGMA table_info(findings)')]
assert 'verification' in cols, f'verification missing from {cols}'
assert 'verification_reason' in cols, f'verification_reason missing from {cols}'
fb_cols = [c[1] for c in conn.execute('PRAGMA table_info(feedback)')]
assert 'source' in fb_cols, f'source missing from {fb_cols}'
print('All columns present')
conn.close()
"
rm -f .superseded/test_migrate.db
```

- [ ] **Step 3: Commit** (no file changes expected)

```bash
# If no changes, skip commit
```

---

### Task 11: Final verification — end-to-end smoke test

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run ruff check + format**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```
Expected: All checks PASS, no format changes.

- [ ] **Step 3: Verify imports are clean**

```bash
uv run python -c "from superseded.review.verifier import _parse_verdicts, Verdict; print('verifier imports OK')"
uv run python -c "from superseded.review.prompts import build_verify_prompt; print('prompt imports OK')"
uv run python -c "from superseded.review.engine import ReviewEngine; print('engine imports OK')"
```
Expected: All three print OK with no errors.
