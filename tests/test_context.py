import tempfile
from pathlib import Path

from superseded.models import Issue, Stage
from superseded.pipeline.context import ContextAssembler


def _make_issue() -> Issue:
    return Issue(
        id="SUP-001",
        title="Add rate limiting",
        filepath=".superseded/issues/SUP-001-add-rate-limiting.md",
    )


def test_context_assembler_base_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        assembler = ContextAssembler(repo_path=tmp)
        prompt = assembler.build(
            stage=Stage.SPEC,
            issue=_make_issue(),
            artifacts_path=str(Path(tmp) / ".superseded" / "artifacts" / "SUP-001"),
        )
    assert "spec" in prompt.lower() or "SPEC" in prompt


def test_context_assembler_includes_agents_md():
    with tempfile.TemporaryDirectory() as tmp:
        agents_md = Path(tmp) / "AGENTS.md"
        agents_md.write_text("# Agent Guide\nThis is the agent map.")
        assembler = ContextAssembler(repo_path=tmp)
        prompt = assembler.build(
            stage=Stage.BUILD,
            issue=_make_issue(),
            artifacts_path=str(Path(tmp) / ".superseded" / "artifacts" / "SUP-001"),
        )
    assert "Agent Guide" in prompt


def test_context_assembler_includes_rules():
    with tempfile.TemporaryDirectory() as tmp:
        rules_dir = Path(tmp) / ".superseded"
        rules_dir.mkdir()
        rules_file = rules_dir / "rules.md"
        rules_file.write_text("# Project Rules\n- Always run tests before committing")
        assembler = ContextAssembler(repo_path=tmp)
        prompt = assembler.build(
            stage=Stage.BUILD,
            issue=_make_issue(),
            artifacts_path=str(Path(tmp) / ".superseded" / "artifacts" / "SUP-001"),
        )
    assert "Always run tests" in prompt


def test_context_assembler_includes_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        artifacts_dir = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "spec.md").write_text("# Spec\nDetailed spec content here.")
        (artifacts_dir / "plan.md").write_text("# Plan\n1. Task one\n2. Task two")
        assembler = ContextAssembler(repo_path=tmp)
        prompt = assembler.build(
            stage=Stage.BUILD,
            issue=_make_issue(),
            artifacts_path=str(artifacts_dir),
        )
    assert "Spec" in prompt
    assert "Plan" in prompt


def test_context_assembler_includes_error_context():
    with tempfile.TemporaryDirectory() as tmp:
        assembler = ContextAssembler(repo_path=tmp)
        prompt = assembler.build(
            stage=Stage.BUILD,
            issue=_make_issue(),
            artifacts_path=str(Path(tmp) / ".superseded" / "artifacts" / "SUP-001"),
            previous_errors=["Build failed: syntax error in main.py"],
            iteration=1,
        )
    assert "Build failed" in prompt
    assert "attempt" in prompt.lower() or "retry" in prompt.lower()


def test_context_assembler_docs_index():
    with tempfile.TemporaryDirectory() as tmp:
        docs_dir = Path(tmp) / "docs"
        docs_dir.mkdir()
        (docs_dir / "ARCHITECTURE.md").write_text(
            "# Architecture\nSystem design overview."
        )
        (docs_dir / "DESIGN.md").write_text("# Design\nKey design decisions.")
        assembler = ContextAssembler(repo_path=tmp)
        prompt = assembler.build(
            stage=Stage.PLAN,
            issue=_make_issue(),
            artifacts_path=str(Path(tmp) / ".superseded" / "artifacts" / "SUP-001"),
        )
    assert "ARCHITECTURE.md" in prompt or "Architecture" in prompt


def test_context_assembler_multi_repo(tmp_path):
    """ContextAssembler can assemble context from multiple repos."""
    # Set up primary repo with AGENTS.md
    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "AGENTS.md").write_text("# Primary guide")
    (primary / ".superseded").mkdir()
    (primary / ".superseded" / "rules.md").write_text("Primary rules")

    # Set up frontend repo with its own AGENTS.md
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "AGENTS.md").write_text("# Frontend guide")

    assembler = ContextAssembler(str(primary))
    assembler.register_repo("frontend", str(frontend))

    issue = Issue(id="SUP-001", title="Test", repos=["frontend"])

    # When building context for a specific repo, include that repo's docs
    context = assembler.build(
        stage=Stage.BUILD,
        issue=issue,
        artifacts_path=str(tmp_path / "artifacts"),
        target_repo="frontend",
    )
    assert "Primary guide" in context
    assert "Frontend guide" in context
    assert "Primary rules" in context


def test_context_assembler_no_target_repo(tmp_path):
    """Without target_repo, only primary context is included."""
    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "AGENTS.md").write_text("# Primary guide")

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "AGENTS.md").write_text("# Frontend guide")

    assembler = ContextAssembler(str(primary))
    assembler.register_repo("frontend", str(frontend))

    issue = Issue(id="SUP-001", title="Test")
    context = assembler.build(
        stage=Stage.BUILD,
        issue=issue,
        artifacts_path=str(tmp_path / "artifacts"),
    )
    assert "Primary guide" in context
    assert "Frontend guide" not in context
