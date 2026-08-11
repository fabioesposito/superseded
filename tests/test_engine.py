from __future__ import annotations

from unittest.mock import MagicMock

from superseded.models import Finding, ReviewResult, ReviewUsage
from superseded.providers import ProviderResponse
from superseded.review.engine import ReviewEngine


def make_finding(
    pass_name="security", severity="critical", file="a.py", line=1, title="test issue"
):
    return Finding(
        pass_name=pass_name,
        severity=severity,
        file=file,
        line=line,
        end_line=line + 1,
        title=title,
        description="desc",
        suggestion="fix",
    )


class FakeProvider:
    """A test double matching the Provider protocol."""

    name = "fake"

    def __init__(self, content_by_prompt: dict[str, str] | None = None, default="[]"):
        self._by_prompt = content_by_prompt or {}
        self._default = default
        self.calls: list[str] = []

    def complete(self, prompt, *, model=None, timeout=600.0, temperature=0.0):
        self.calls.append(prompt)
        content = self._by_prompt.get(prompt, self._default)
        return ProviderResponse(
            content=content, prompt_tokens=10, completion_tokens=5, model="fake"
        )


def test_engine_deduplicates():
    f1 = make_finding()
    f2 = make_finding()
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[f1], [f2]])
    assert len(result.findings) == 1


def test_engine_deduplicates_across_passes():
    security = make_finding(pass_name="security", file="a.py", line=10, title="same bug")
    correctness = make_finding(pass_name="correctness", file="a.py", line=10, title="same bug")
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[security], [correctness]])
    assert len(result.findings) == 1
    assert result.findings[0].file == "a.py"
    assert result.findings[0].line == 10
    assert result.findings[0].title == "same bug"


def test_engine_dedup_keeps_highest_severity():
    low = make_finding(severity="suggestion", file="a.py", line=10, title="same bug")
    high = make_finding(severity="critical", file="a.py", line=10, title="same bug")
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[low], [high]])
    assert len(result.findings) == 1
    assert result.findings[0].severity == "critical"


def test_engine_dedup_keeps_highest_severity_regardless_of_order():
    high = make_finding(severity="critical", file="a.py", line=10, title="same bug")
    low = make_finding(severity="suggestion", file="a.py", line=10, title="same bug")
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[high], [low]])
    assert len(result.findings) == 1
    assert result.findings[0].severity == "critical"


def test_engine_sorts_by_severity():
    f1 = make_finding(severity="nit", line=1)
    f2 = make_finding(severity="critical", line=2)
    f3 = make_finding(severity="suggestion", line=3)
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    result = engine.merge_findings([[f1, f2, f3]])
    assert result.findings[0].severity == "critical"
    assert result.findings[-1].severity == "nit"


def test_engine_selects_provider(monkeypatch):
    from superseded.providers import DeepSeekProvider

    monkeypatch.setenv("SUPERSEDED_DEEPSEEK_API_KEY", "test-key")
    engine = ReviewEngine.select("deepseek", model=None)
    assert isinstance(engine.provider, DeepSeekProvider)


def test_engine_select_rejects_unknown_provider():
    import pytest

    with pytest.raises(ValueError, match="Unknown provider"):
        ReviewEngine.select("bogus", model=None)


def test_review_continues_when_one_pass_fails():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=False))

    good_finding = make_finding(severity="critical", line=5)

    def fake_run_pass(pass_name, prompt, timeout=300, progress=None):
        if pass_name == "correctness":
            raise RuntimeError("boom")
        return [good_finding], ReviewUsage()

    engine.run_pass = fake_run_pass  # type: ignore[method-assign]
    result = engine.review(diff="diff", passes=["security", "correctness"])
    assert isinstance(result, ReviewResult)
    assert len(result.findings) == 1
    assert result.findings[0] is good_finding


def test_run_pass_skips_and_logs_malformed_findings(caplog):
    import logging

    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    raw_items = [
        '{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "t", "description": "d", "suggestion": "s"}',
        '{"severity": "not-a-severity", "file": "b.py", "line": 1, "end_line": 1, "title": "bad", "description": "d", "suggestion": "s"}',
    ]
    # First call returns one valid + one malformed; retry returns nothing valid.
    engine.provider.complete = MagicMock(
        side_effect=[
            ProviderResponse(content="[" + raw_items[0] + ", " + raw_items[1] + "]"),
            ProviderResponse(content="[" + raw_items[0] + "]"),
        ]
    )
    with caplog.at_level(logging.WARNING, logger="superseded.review.engine"):
        findings, _ = engine.run_pass("security", "prompt")
    assert len(findings) == 1
    assert findings[0].file == "a.py"
    assert "malformed" in caplog.text.lower() or "not-a-severity" in caplog.text
    assert engine.provider.complete.call_count == 2
    second_prompt = engine.provider.complete.call_args_list[1].args[0]
    assert "Correction" in second_prompt


def test_run_pass_retries_once_on_malformed_and_recovers():
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    valid = '{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "ok", "description": "d", "suggestion": "s"}'
    bad = '{"severity": "minor", "file": "b.py", "line": 1, "end_line": 1, "title": "bad", "description": "d", "suggestion": "s"}'
    recovered = '{"severity": "important", "file": "b.py", "line": 1, "end_line": 1, "title": "fixed", "description": "d", "suggestion": "s"}'
    engine.provider.complete = MagicMock(
        side_effect=[
            ProviderResponse(content="[" + valid + ", " + bad + "]"),
            ProviderResponse(content="[" + recovered + "]"),
        ]
    )
    findings, _ = engine.run_pass("security", "prompt")
    assert engine.provider.complete.call_count == 2
    assert len(findings) == 1
    assert findings[0].title == "fixed"
    second_prompt = engine.provider.complete.call_args_list[1].args[0]
    assert "Correction" in second_prompt or "correction" in second_prompt


def test_run_pass_retries_at_most_once():
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    valid = '{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "ok", "description": "d", "suggestion": "s"}'
    bad = '{"severity": "minor", "file": "b.py", "line": 1, "end_line": 1, "title": "bad", "description": "d", "suggestion": "s"}'
    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(content="[" + valid + ", " + bad + "]")
    )
    engine.run_pass("security", "prompt")
    assert engine.provider.complete.call_count == 2


def test_run_pass_does_not_retry_when_all_valid():
    engine = ReviewEngine(provider=MagicMock(), config=MagicMock())
    valid = '{"severity": "critical", "file": "a.py", "line": 1, "end_line": 2, "title": "ok", "description": "d", "suggestion": "s"}'
    engine.provider.complete = MagicMock(return_value=ProviderResponse(content="[" + valid + "]"))
    engine.run_pass("security", "prompt")
    assert engine.provider.complete.call_count == 1


def test_review_accumulates_usage():
    engine = ReviewEngine(
        provider=FakeProvider(default="[]"), config=MagicMock(is_pass_enabled=lambda n: True)
    )
    result = engine.review(diff="d", passes=["security", "correctness"])
    # Two passes, each FakeProvider call returns prompt_tokens=10, completion_tokens=5.
    assert result.usage.prompt_tokens == 20
    assert result.usage.completion_tokens == 10
    assert set(result.usage.per_pass.keys()) == {"security", "correctness"}


def test_run_verification_keeps_all():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="X",
        description="d",
        suggestion="s",
    )
    f2 = Finding(
        pass_name="style",
        severity="nit",
        file="b.py",
        line=2,
        title="Y",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f1, f2])

    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(
            content='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}, '
            '{"id": "' + f2.id + '", "action": "keep", "reason": "ok"}]'
        )
    )
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert len(new_result.findings) == 2


def test_run_verification_drops_false_positives():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="Real",
        description="d",
        suggestion="s",
    )
    f2 = Finding(
        pass_name="style",
        severity="nit",
        file="b.py",
        line=2,
        title="Fake",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f1, f2])

    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(
            content='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}, '
            '{"id": "' + f2.id + '", "action": "drop", "reason": "false positive"}]'
        )
    )
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert len(new_result.findings) == 1
    assert new_result.findings[0].id == f1.id
    assert f2.verification == "dropped"


def test_run_verification_reestimates_severity():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    f = Finding(
        pass_name="performance",
        severity="important",
        file="a.py",
        line=5,
        title="Slow",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f])
    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(
            content='[{"id": "'
            + f.id
            + '", "action": "keep", "severity": "suggestion", "confidence": "low", "reason": "less severe"}]'
        )
    )
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert new_result.findings[0].severity == "suggestion"
    assert new_result.findings[0].confidence == "low"
    assert new_result.findings[0].verified_severity == "suggestion"


def test_run_verification_failure_returns_original():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
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
    engine.provider.complete = MagicMock(side_effect=RuntimeError("timeout"))
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert new_result is result
    assert len(new_result.warnings) == 1


def test_run_verification_missing_ids_kept():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    f1 = Finding(
        pass_name="security",
        severity="critical",
        file="a.py",
        line=1,
        title="Mentioned",
        description="d",
        suggestion="s",
    )
    f2 = Finding(
        pass_name="style",
        severity="nit",
        file="b.py",
        line=2,
        title="Omitted",
        description="d",
        suggestion="s",
    )
    result = ReviewResult(findings=[f1, f2])
    engine.provider.complete = MagicMock(
        return_value=ProviderResponse(
            content='[{"id": "' + f1.id + '", "action": "keep", "reason": "ok"}]'
        )
    )
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert len(new_result.findings) == 2


def test_run_verification_skips_when_no_findings():
    from superseded.config import Config

    engine = ReviewEngine(provider=MagicMock(), config=Config(verify=True))
    result = ReviewResult(findings=[])
    engine.provider.complete = MagicMock()
    new_result = engine._run_verification(result, "diff", "ctx", 600)
    assert engine.provider.complete.call_count == 0
    assert new_result is result
