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
        (docs_dir / "ARCHITECTURE.md").write_text("# Architecture\nSystem design overview.")
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


def test_context_assembler_categorized_docs_index(tmp_path):
    """Docs index groups files by category from frontmatter."""
    docs_dir = tmp_path / "docs"
    arch_dir = docs_dir / "architecture"
    guides_dir = docs_dir / "guides"
    arch_dir.mkdir(parents=True)
    guides_dir.mkdir(parents=True)

    (arch_dir / "pipeline.md").write_text(
        "---\ntitle: Pipeline\ncategory: architecture\nsummary: Pipeline design\n---\n# Pipeline"
    )
    (guides_dir / "setup.md").write_text(
        "---\ntitle: Setup Guide\ncategory: guides\nsummary: How to set up\n---\n# Setup"
    )

    assembler = ContextAssembler(repo_path=str(tmp_path))
    prompt = assembler.build(
        stage=Stage.PLAN,
        issue=_make_issue(),
        artifacts_path=str(tmp_path / ".superseded" / "artifacts" / "SUP-001"),
    )

    assert "### Architecture" in prompt
    assert "### Guides" in prompt
    assert "pipeline.md" in prompt
    assert "Pipeline design" in prompt
    assert "setup.md" in prompt
    assert "How to set up" in prompt


def test_context_assembler_docs_fallback_no_frontmatter(tmp_path):
    """Docs without frontmatter fall back to first-line extraction."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "legacy.md").write_text("# Legacy Doc\nOld style content.")

    assembler = ContextAssembler(repo_path=str(tmp_path))
    prompt = assembler.build(
        stage=Stage.PLAN,
        issue=_make_issue(),
        artifacts_path=str(tmp_path / ".superseded" / "artifacts" / "SUP-001"),
    )

    assert "legacy.md" in prompt
    assert "Legacy Doc" in prompt


def test_context_skill_layer_includes_repo_info(tmp_path):
    """Skill layer includes repo-specific instructions when target_repo is set."""
    primary = tmp_path / "primary"
    primary.mkdir()

    frontend = tmp_path / "frontend"
    frontend.mkdir()

    assembler = ContextAssembler(str(primary))
    assembler.register_repo("frontend", str(frontend))

    # Without target_repo
    prompt_no_repo = assembler._build_skill_layer(Stage.SHIP)
    assert "Target Repository" not in prompt_no_repo

    # With target_repo
    prompt_with_repo = assembler._build_skill_layer(Stage.SHIP, target_repo="frontend")
    assert "Target Repository: frontend" in prompt_with_repo
    assert str(frontend) in prompt_with_repo
    assert "gh pr create" in prompt_with_repo


def test_context_assembler_counts_tokens(tmp_path):
    """ContextAssembler tracks approximate token count per layer."""
    (tmp_path / "AGENTS.md").write_text("# Guide\n" + "word " * 500)
    assembler = ContextAssembler(str(tmp_path))
    assembler.build(
        stage=Stage.SPEC,
        issue=Issue(id="SUP-001", title="Test", filepath="test.md"),
        artifacts_path=str(tmp_path / "artifacts"),
    )
    assert assembler.last_token_estimate > 0
    assert assembler.last_token_estimate > 400


def test_context_assembler_reports_layer_tokens(tmp_path):
    """ContextAssembler exposes per-layer token breakdown."""
    (tmp_path / "AGENTS.md").write_text("# Guide\n" + "word " * 200)
    (tmp_path / ".superseded").mkdir()
    (tmp_path / ".superseded" / "rules.md").write_text("Rules\n" + "rule " * 100)
    assembler = ContextAssembler(str(tmp_path))
    assembler.build(
        stage=Stage.BUILD,
        issue=Issue(id="SUP-001", title="Test", filepath="test.md"),
        artifacts_path=str(tmp_path / "artifacts"),
    )
    assert len(assembler.layer_tokens) >= 2
    assert any("AGENTS.md" in k for k in assembler.layer_tokens)
    assert any("rules" in k.lower() for k in assembler.layer_tokens)


def test_context_assembler_drops_low_priority_layers_when_over_budget(tmp_path):
    """When max_tokens is set, low-priority layers are dropped to fit."""
    (tmp_path / "AGENTS.md").write_text("# Guide\nEssential content here.")
    assembler = ContextAssembler(str(tmp_path))
    assembler.max_tokens = 500  # Tight budget
    prompt = assembler.build(
        stage=Stage.BUILD,
        issue=Issue(id="SUP-001", title="Test", filepath="test.md"),
        artifacts_path=str(tmp_path / "artifacts"),
        session_turns=[
            {"stage": "spec", "attempt": 0, "role": "assistant", "content": "x " * 2000},
            {"stage": "plan", "attempt": 0, "role": "assistant", "content": "y " * 2000},
        ],
    )
    # Should still contain essential layers
    assert "Guide" in prompt
    # Session history should be dropped or heavily truncated
    assert assembler.last_token_estimate <= 600  # Some slack for truncation


def test_error_layer_deduplicates_and_prioritizes(tmp_path):
    """Error layer deduplicates similar errors and puts most recent first."""
    assembler = ContextAssembler(str(tmp_path))
    errors = [
        "Build failed: syntax error in main.py",
        "Build failed: syntax error in main.py",  # duplicate
        "Tests failed: 2 assertions failed",
        "Build failed: syntax error in main.py",  # duplicate
    ]
    prompt = assembler._build_error_layer(errors, iteration=2)
    # Should deduplicate
    assert prompt.count("syntax error in main.py") == 1
    # Should indicate attempt number
    assert "attempt 3" in prompt
    # Most frequent errors should appear first
    lines = [l for l in prompt.split("\n") if l.startswith("- ")]
    assert "syntax error" in lines[0]  # most frequent = most important


def test_session_history_summarizes_long_turns(tmp_path):
    """Session history truncates long turns more aggressively."""
    assembler = ContextAssembler(str(tmp_path))
    long_content = "This is a detailed response. " * 500  # ~1500 words
    turns = [
        {"stage": "spec", "attempt": 0, "role": "assistant", "content": long_content},
    ]
    prompt = assembler._build_session_history_layer(Stage.BUILD, turns)
    # Should be summarized, not raw 2000-char truncation
    assert len(prompt) < len(long_content)
    assert "spec" in prompt.lower()


def test_session_history_limits_total_turns(tmp_path):
    """Session history includes at most the last N turns across all stages."""
    assembler = ContextAssembler(str(tmp_path))
    turns = [
        {"stage": "spec", "attempt": 0, "role": "user", "content": f"turn {i}"} for i in range(20)
    ]
    prompt = assembler._build_session_history_layer(Stage.BUILD, turns)
    assert prompt is not None
    turn_count = prompt.count("turn ")
    assert turn_count <= 6  # 5 turns + maybe header
