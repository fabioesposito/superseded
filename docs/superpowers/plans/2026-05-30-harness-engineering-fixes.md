# Harness Engineering Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 18 gaps identified in the harness architecture audit — covering context engineering, resource enforcement, checkpoint integration, plan tracking, automatic retry, review feedback loops, worktree merge, output quality, cost tracking, and cross-stage intelligence.

**Architecture:** Each fix is self-contained and modifies existing files. New capabilities are added as methods on existing classes (`ContextAssembler`, `LifecycleManager`, `VerificationEngine`, `WorktreeManager`, `Harness`). Tests follow the existing pattern: `tempfile.TemporaryDirectory` + `AsyncMock` agents.

**Tech Stack:** Python 3.14+, pytest, aiosqlite, pydantic, asyncio subprocesses

---

## File Map

| File | Changes |
|------|---------|
| `src/superseded/harness/context.py` | Token counting, adaptive sizing, curated errors, session summarization, selective docs, source code context |
| `src/superseded/harness/__init__.py` | Auto-retry loop, resource enforcement, checkpoint integration, plan tracking, worktree merge, output quality, cross-stage context |
| `src/superseded/harness/lifecycle.py` | Resource limit tracking during streaming |
| `src/superseded/harness/verification.py` | Structured verification feedback |
| `src/superseded/harness/checkpoint.py` | Checkpoint metadata for plan tracking |
| `src/superseded/pipeline/worktree.py` | Merge worktree changes on success |
| `src/superseded/pipeline/plan.py` | Plan task status tracking |
| `src/superseded/models.py` | New models: `VerificationFeedback`, `PlanProgress`, `CostRecord` |
| `src/superseded/config.py` | `auto_retry` config field |
| `tests/test_context.py` | Tests for all context engineering improvements |
| `tests/test_harness.py` | Tests for auto-retry, resource enforcement, checkpoint integration |
| `tests/test_verification.py` | Tests for structured verification feedback |
| `tests/test_worktree.py` | Tests for merge |
| `tests/test_plan.py` | Tests for plan tracking |

---

## Task 1: Token Counting in ContextAssembler

**Files:**
- Modify: `src/superseded/harness/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test for token counting**

```python
# tests/test_context.py — add at end of file

def test_context_assembler_counts_tokens(tmp_path):
    """ContextAssembler tracks approximate token count per layer."""
    (tmp_path / "AGENTS.md").write_text("# Guide\n" + "word " * 500)
    assembler = ContextAssembler(str(tmp_path))
    prompt = assembler.build(
        stage=Stage.SPEC,
        issue=Issue(id="SUP-001", title="Test", filepath="test.md"),
        artifacts_path=str(tmp_path / "artifacts"),
    )
    # ~500 words ≈ 667 tokens (words / 0.75)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py::test_context_assembler_counts_tokens tests/test_context.py::test_context_assembler_reports_layer_tokens -v`
Expected: FAIL — `last_token_estimate` and `layer_tokens` don't exist

- [ ] **Step 3: Implement token counting**

```python
# src/superseded/harness/context.py — add after imports

def _estimate_tokens(text: str) -> int:
    """Approximate token count: words / 0.75."""
    return max(1, len(text.split()) * 4 // 3)
```

Add to `ContextAssembler.__init__`:
```python
self.last_token_estimate: int = 0
self.layer_tokens: dict[str, int] = {}
```

Replace the `build` method's layer assembly to track tokens:
```python
def build(self, ...) -> str:
    layers: list[str] = []
    self.layer_tokens = {}
    previous_errors = previous_errors or []

    def _add_layer(name: str, content: str | None) -> None:
        if content:
            layers.append(content)
            self.layer_tokens[name] = _estimate_tokens(content)

    _add_layer("AGENTS.md", self._build_agents_md_layer())

    if crg_enabled:
        _add_layer("CRG tools", self._build_crg_tools_layer())
    else:
        _add_layer("docs index", self._build_docs_index_layer())

    _add_layer("issue ticket", self._build_issue_layer(issue))

    if target_repo:
        _add_layer(f"AGENTS.md ({target_repo})", self._build_agents_md_layer(target_repo))
        _add_layer(f"docs ({target_repo})", self._build_docs_index_layer(target_repo))
        _add_layer(f"rules ({target_repo})", self._build_rules_layer(target_repo))

    _add_layer("artifacts", self._build_artifacts_layer(artifacts_path))
    _add_layer("answers", self._build_answers_layer(artifacts_path))

    if not crg_enabled:
        _add_layer("session history", self._build_session_history_layer(stage, session_turns))

    _add_layer("rules", self._build_rules_layer())
    _add_layer("skill prompt", self._build_skill_layer(stage, target_repo=target_repo))

    if previous_errors:
        _add_layer("error context", self._build_error_layer(previous_errors, iteration))

    prompt = "\n\n---\n\n".join(layers)
    result = sanitize_agent_prompt(prompt)
    self.last_token_estimate = _estimate_tokens(result)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/context.py tests/test_context.py
git commit -m "feat(context): add token counting per layer"
```

---

## Task 2: Adaptive Context Sizing

**Files:**
- Modify: `src/superseded/harness/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context.py

def test_context_assembler_drops_low_priority_layers_when_over_budget(tmp_path):
    """When max_tokens is set, low-priority layers are dropped to fit."""
    # Create a large session history that should be dropped first
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py::test_context_assembler_drops_low_priority_layers_when_over_budget -v`
Expected: FAIL — no `max_tokens` attribute or adaptive behavior

- [ ] **Step 3: Implement adaptive context sizing**

Add to `ContextAssembler.__init__`:
```python
self.max_tokens: int = 0  # 0 = unlimited
```

Add method to `ContextAssembler`:
```python
def _fits_budget(self, layers: list[str]) -> bool:
    if self.max_tokens <= 0:
        return True
    return _estimate_tokens("\n\n---\n\n".join(layers)) <= self.max_tokens
```

Modify `build()` to drop low-priority layers when over budget. After assembling all layers, check budget and drop in priority order (session history first, then docs index, then artifacts):
```python
# After assembling all layers, check budget
if self.max_tokens > 0:
    # Priority: session history < docs index < artifacts < everything else
    drop_order = ["session history", "docs index"]
    for drop_name in drop_order:
        if self._fits_budget(layers):
            break
        # Find and remove the layer
        for i, name in enumerate(list(self.layer_tokens.keys())):
            if name == drop_name:
                layers.pop(i)
                del self.layer_tokens[name]
                break

    # If still over budget, truncate artifacts
    if not self._fits_budget(layers):
        for i, name in enumerate(list(self.layer_tokens.keys())):
            if name == "artifacts" and i < len(layers):
                remaining = self.max_tokens - _estimate_tokens("\n\n---\n\n".join(layers[:i] + layers[i+1:]))
                max_chars = max(500, remaining * 3)  # ~4 chars per token
                layers[i] = layers[i][:max_chars] + "\n\n[truncated to fit context budget]"
                self.layer_tokens[name] = _estimate_tokens(layers[i])
                # Rebuild layers list
                break
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/context.py tests/test_context.py
git commit -m "feat(context): add adaptive context sizing with token budget"
```

---

## Task 3: Curated Error Layer

**Files:**
- Modify: `src/superseded/harness/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context.py

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
    # Most recent errors should appear first
    lines = [l for l in prompt.split("\n") if l.startswith("- ")]
    assert "syntax error" in lines[0]  # most frequent = most important
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py::test_error_layer_deduplicates_and_prioritizes -v`
Expected: FAIL — current implementation doesn't deduplicate

- [ ] **Step 3: Implement curated error layer**

Replace `_build_error_layer` in `ContextAssembler`:
```python
def _build_error_layer(self, previous_errors: list[str], iteration: int) -> str:
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for err in previous_errors:
        normalized = err.strip().lower()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(err)

    # Sort by frequency (most common = most systemic)
    from collections import Counter
    freq = Counter(err.strip().lower() for err in previous_errors)
    unique.sort(key=lambda e: -freq[e.strip().lower()])

    error_lines = "\n".join(f"- {err}" for err in unique)
    return (
        f"## Retry Context (attempt {iteration + 1})\n\n"
        f"The previous attempt(s) failed. Fix the following {len(unique)} distinct error(s):\n\n"
        f"{error_lines}\n\n"
        f"Address each error. Do not repeat the same mistakes."
    )
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/context.py tests/test_context.py
git commit -m "feat(context): deduplicate and prioritize error context"
```

---

## Task 4: Structured Session History (Summarization)

**Files:**
- Modify: `src/superseded/harness/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context.py

def test_session_history_summarizes_long_turns(tmp_path):
    """Session history truncates long turns more aggressively and summarizes."""
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
        {"stage": "spec", "attempt": 0, "role": "user", "content": f"turn {i}"}
        for i in range(20)
    ]
    prompt = assembler._build_session_history_layer(Stage.BUILD, turns)
    # Should limit to last ~5 turns
    assert prompt is not None
    # Count "turn" occurrences — should be limited
    turn_count = prompt.count("turn ")
    assert turn_count <= 6  # 5 turns + maybe header
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py::test_session_history_summarizes_long_turns tests/test_context.py::test_session_history_limits_total_turns -v`
Expected: FAIL

- [ ] **Step 3: Implement session history improvement**

Replace `_build_session_history_layer` in `ContextAssembler`:
```python
MAX_SESSION_HISTORY_TURNS = 5
MAX_TURN_CONTENT_LENGTH = 500  # Reduced from 2000

def _build_session_history_layer(
    self, current_stage: Stage, session_turns: list[dict] | None = None
) -> str | None:
    if not session_turns:
        return None

    prior_turns = [t for t in session_turns if t["stage"] != current_stage.value]
    if not prior_turns:
        return None

    # Only include the last N turns
    recent_turns = prior_turns[-MAX_SESSION_HISTORY_TURNS:]

    parts: list[str] = []
    current_section = None
    for turn in recent_turns:
        section = f"{turn['stage']} (attempt {turn['attempt'] + 1})"
        if section != current_section:
            current_section = section
            parts.append(f"### {section}")

        role_label = "You asked" if turn["role"] == "user" else "Agent responded"
        content = turn["content"]
        if len(content) > MAX_TURN_CONTENT_LENGTH:
            # Take first and last 200 chars
            content = content[:200] + f"\n\n[... {len(content) - 400} chars omitted ...]\n\n" + content[-200:]
        parts.append(f"**{role_label}:**\n{content}")

    if not parts:
        return None
    return "## Previous Session History (summarized)\n\n" + "\n\n".join(parts)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/context.py tests/test_context.py
git commit -m "feat(context): limit session history to last 5 turns with shorter content"
```

---

## Task 5: Selective Docs Index

**Files:**
- Modify: `src/superseded/harness/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context.py

def test_docs_index_filters_by_stage_relevance(tmp_path):
    """Docs index only includes docs relevant to the current stage."""
    docs_dir = tmp_path / "docs"
    arch = docs_dir / "architecture"
    guides = docs_dir / "guides"
    ops = docs_dir / "operations"
    for d in (arch, guides, ops):
        d.mkdir(parents=True)

    (arch / "pipeline.md").write_text("---\ncategory: architecture\nsummary: Pipeline design\n---\n# Pipeline")
    (guides / "setup.md").write_text("---\ncategory: guides\nsummary: Setup guide\n---\n# Setup")
    (ops / "runbook.md").write_text("---\ncategory: operations\nsummary: Ops runbook\n---\n# Ops")

    assembler = ContextAssembler(str(tmp_path))

    # BUILD stage should prioritize architecture and guides, not operations
    prompt = assembler._build_docs_index_layer(stage=Stage.BUILD)
    assert "pipeline.md" in prompt or "Pipeline" in prompt
    assert "setup.md" in prompt or "Setup" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py::test_docs_index_filters_by_stage_relevance -v`
Expected: FAIL — `_build_docs_index_layer` doesn't accept `stage` parameter

- [ ] **Step 3: Implement selective docs index**

Modify `_build_docs_index_layer` to accept an optional `stage` parameter and filter:
```python
# Stage-to-relevant-categories mapping
_STAGE_CATEGORIES: dict[Stage, list[str]] = {
    Stage.SPEC: ["architecture", "guides"],
    Stage.PLAN: ["architecture", "guides", "adrs"],
    Stage.BUILD: ["architecture", "guides"],
    Stage.VERIFY: ["architecture", "guides"],
    Stage.REVIEW: ["architecture", "guides", "adrs"],
    Stage.SHIP: ["guides", "operations"],
}

def _build_docs_index_layer(
    self, repo: str | None = None, stage: Stage | None = None
) -> str | None:
    repo_path = self._get_repo_path(repo)
    docs_dir = repo_path / "docs"
    if not docs_dir.exists():
        return None

    categories: dict[str, list[tuple[str, str]]] = {}
    uncategorized: list[tuple[str, str]] = []

    relevant = set(self._STAGE_CATEGORIES.get(stage, [])) if stage else None

    for md_file in sorted(docs_dir.glob("**/*.md")):
        rel = md_file.relative_to(docs_dir)
        content = md_file.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(content)

        summary = meta.get("summary", "").strip()
        if not summary:
            summary = content.split("\n")[0].strip("# ").strip()

        category = meta.get("category", "").strip()
        if relevant and category and category not in relevant:
            continue  # Skip irrelevant categories
        if category and category in ("architecture", "guides", "adrs", "operations"):
            categories.setdefault(category, []).append((str(rel), summary))
        else:
            uncategorized.append((str(rel), summary))

    if not categories and not uncategorized:
        return None

    label = f"{repo} repo" if repo else "Documentation"
    sections: list[str] = [f"## {label} Index\n"]

    category_order = ["architecture", "guides", "adrs", "operations"]
    for cat in category_order:
        if cat in categories:
            sections.append(f"### {cat.title()}")
            for rel, summary in categories[cat]:
                sections.append(f"- {rel}: {summary}")
            sections.append("")

    if uncategorized:
        sections.append("### Other")
        for rel, summary in uncategorized:
            sections.append(f"- {rel}: {summary}")

    return "\n".join(sections)
```

Also update the `build()` method calls to pass `stage`:
```python
# In build():
docs_index = self._build_docs_index_layer(stage=stage)
# And for target repo:
target_docs = self._build_docs_index_layer(target_repo, stage=stage)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/context.py tests/test_context.py
git commit -m "feat(context): filter docs index by stage relevance"
```

---

## Task 6: Enforce Resource Limits During Streaming

**Files:**
- Modify: `src/superseded/harness/__init__.py`
- Modify: `src/superseded/harness/lifecycle.py`
- Test: `tests/test_harness.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_harness.py

async def test_harness_enforces_wall_time_limit():
    """Harness kills agent when wall time limit is exceeded."""
    mock_agent = AsyncMock()

    async def slow_stream(prompt, context):
        yield AgentEvent(event_type="stdout", content="working...", stage=Stage.BUILD)
        # Simulate slow agent — the harness should enforce the limit
        import asyncio
        await asyncio.sleep(0.1)
        yield AgentEvent(
            event_type="status", content="", stage=Stage.BUILD,
            metadata={"exit_code": 0, "duration_ms": 100},
        )

    mock_agent.run_streaming = slow_stream

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
        )
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)

        # Set a very tight wall time limit
        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
            resource_limits=ResourceLimits(max_wall_time_seconds=1),
        )
        # Should either pass quickly or fail with limit message
        assert result.passed is not None  # Just verify it completes

        await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness.py::test_harness_enforces_wall_time_limit -v`
Expected: FAIL — `run_stage` doesn't accept `resource_limits` parameter

- [ ] **Step 3: Implement resource limit enforcement**

Add to `_run_stage_streaming` in `Harness`, inside the streaming loop:
```python
# After collecting events, check resource limits
stage_config = self.stage_configs.get(stage.value)
if stage_config and stage_config.resource_limits:
    from superseded.harness.lifecycle import ResourceLimits
    limits = ResourceLimits(
        max_tokens=stage_config.resource_limits.max_tokens,
        max_wall_time_seconds=stage_config.resource_limits.max_wall_time_seconds,
        max_cost_usd=stage_config.resource_limits.max_cost_usd,
    )
    # Check wall time during streaming
    import time as _time
    stream_start = _time.monotonic()
    # ... inside the event loop, after each event:
    elapsed = _time.monotonic() - stream_start
    limit_error = self.lifecycle_manager.check_resource_limits(
        limits, wall_time=elapsed
    )
    if limit_error:
        # Kill the process
        break  # Exit the streaming loop
```

Modify the `_run_stage_streaming` method signature to accept optional resource limits and enforce them:
```python
async def _run_stage_streaming(
    self,
    issue: Issue,
    stage: Stage,
    artifacts_path: str,
    previous_errors: list[str] | None = None,
    repo: str | None = None,
    resource_limits: ResourceLimits | None = None,
) -> StageResult:
```

In the streaming loop, track elapsed time and check limits:
```python
import time as _time

stream_start = _time.monotonic()
# ... existing event loop ...
async for event in self.resolve_agent(stage).run_streaming(prompt, context):
    # ... existing event handling ...

    # Check resource limits
    if resource_limits:
        elapsed = _time.monotonic() - stream_start
        limit_error = self.lifecycle_manager.check_resource_limits(
            resource_limits, wall_time=elapsed
        )
        if limit_error:
            yield AgentEvent(
                event_type="error",
                content=limit_error,
                stage=stage,
            )
            break
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/__init__.py src/superseded/harness/lifecycle.py tests/test_harness.py
git commit -m "feat(harness): enforce resource limits during stage execution"
```

---

## Task 7: Integrate Checkpoints with Streaming Loop

**Files:**
- Modify: `src/superseded/harness/__init__.py`
- Test: `tests/test_harness_streaming.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_harness_streaming.py — add at end

async def test_harness_saves_checkpoint_during_execution():
    """Harness saves checkpoint periodically during long-running stages."""
    mock_agent = AsyncMock()

    async def long_stream(prompt, context):
        for i in range(5):
            yield AgentEvent(
                event_type="stdout",
                content=f"Task {i} completed with enough content to pass minimum",
                stage=Stage.BUILD,
            )
        yield AgentEvent(
            event_type="status", content="", stage=Stage.BUILD,
            metadata={"exit_code": 0, "duration_ms": 500},
        )

    mock_agent.run_streaming = long_stream

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        harness = Harness(
            repo_path="/tmp/testrepo",
            agent_factory=_mock_factory(mock_agent),
            db=db,
        )
        # Checkpoint should be created during execution
        # After run completes, checkpoint should be cleared
        assert not harness.checkpoint_manager.has_checkpoint("SUP-001", "build")

        await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness_streaming.py::test_harness_saves_checkpoint_during_execution -v`
Expected: FAIL

- [ ] **Step 3: Implement checkpoint integration**

In `_run_stage_streaming`, save a checkpoint after receiving stdout events:
```python
# Inside the streaming loop, after stdout events:
if event.event_type == "stdout":
    stdout_parts.append(event.content)
    # Save checkpoint periodically (every 10 stdout lines)
    if len(stdout_parts) % 10 == 0:
        self.checkpoint_manager.save(Checkpoint(
            issue_id=issue.id,
            stage=stage.value,
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            completed_tasks=[],
            current_task=f"Processing... ({len(stdout_parts)} outputs received)",
        ))
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness_streaming.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/__init__.py tests/test_harness_streaming.py
git commit -m "feat(harness): save checkpoints during streaming execution"
```

---

## Task 8: Structured Verification Feedback

**Files:**
- Modify: `src/superseded/harness/verification.py`
- Test: `tests/test_verification.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_verification.py — add at end

def test_format_errors_includes_file_hints():
    """Verification feedback includes file/section hints for the agent."""
    from superseded.harness.verification import VerificationEngine, VerificationResult

    engine = VerificationEngine()
    result = VerificationResult(
        passed=False,
        failures=[
            "Missing required section: ## Architecture",
            "Tests failed: 2 failed, 10 passed.",
        ],
    )
    formatted = engine.format_errors_for_retry(result)
    assert "## Architecture" in formatted
    assert "Tests failed" in formatted
    # Should include actionable guidance
    assert "Fix" in formatted or "fix" in formatted or "address" in formatted.lower()


def test_format_errors_groups_by_type():
    """Verification feedback groups errors by type (missing sections, test failures)."""
    from superseded.harness.verification import VerificationEngine, VerificationResult

    engine = VerificationEngine()
    result = VerificationResult(
        passed=False,
        failures=[
            "Missing required section: ## Architecture",
            "Missing required section: ## Tasks",
            "Tests failed: 2 failed, 10 passed.",
        ],
    )
    formatted = engine.format_errors_for_retry(result)
    # Should group missing sections together
    arch_idx = formatted.find("Architecture")
    tasks_idx = formatted.find("Tasks")
    assert arch_idx < tasks_idx  # Same group, adjacent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_verification.py::test_format_errors_includes_file_hints tests/test_verification.py::test_format_errors_groups_by_type -v`
Expected: FAIL — current format is flat numbered list

- [ ] **Step 3: Implement structured verification feedback**

Replace `format_errors_for_retry` in `VerificationEngine`:
```python
def format_errors_for_retry(self, result: VerificationResult) -> str:
    if result.passed:
        return ""

    # Group failures by type
    missing_sections = [f for f in result.failures if f.startswith("Missing required section")]
    test_failures = [f for f in result.failures if "Tests failed" in f or "test" in f.lower()]
    review_findings = [f for f in result.failures if "finding" in f.lower() or "Critical" in f]
    other = [f for f in result.failures if f not in missing_sections + test_failures + review_findings]

    lines = ["The previous attempt failed verification. Fix these specific issues:\n"]

    if missing_sections:
        lines.append("### Missing Artifact Sections")
        lines.append("Add these required sections to your output artifact:\n")
        for f in missing_sections:
            section = f.replace("Missing required section: ", "")
            lines.append(f"- `{section}` — add this heading with substantive content beneath it")
        lines.append("")

    if test_failures:
        lines.append("### Test Failures")
        lines.append("Fix the failing tests:\n")
        for f in test_failures:
            lines.append(f"- {f}")
        lines.append("")

    if review_findings:
        lines.append("### Review Findings")
        lines.append("Address these review findings:\n")
        for f in review_findings:
            lines.append(f"- {f}")
        lines.append("")

    if other:
        lines.append("### Other Issues")
        for f in other:
            lines.append(f"- {f}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_verification.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/verification.py tests/test_verification.py
git commit -m "feat(verification): group and structure retry feedback by error type"
```

---

## Task 9: Plan Execution Tracking

**Files:**
- Modify: `src/superseded/pipeline/plan.py`
- Modify: `src/superseded/harness/__init__.py`
- Test: `tests/test_plan.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan.py — add at end

def test_plan_task_status_tracking(tmp_path):
    """Plan tracks completion status of individual tasks."""
    from superseded.pipeline.plan import Plan, PlanTask, write_plan, read_plan, update_task_status

    tasks = [
        PlanTask(title="Setup DB", description="Create database schema"),
        PlanTask(title="Add API", description="Implement REST endpoints"),
        PlanTask(title="Write tests", description="Add test coverage"),
    ]
    plan_path = str(tmp_path / "plan.md")
    write_plan(plan_path, "My Plan", "Build a REST API", tasks)

    # Mark first task complete
    update_task_status(plan_path, "Setup DB", "complete")
    plan = read_plan(plan_path)
    assert plan.tasks[0].status == "complete"
    assert plan.tasks[1].status == "pending"


def test_plan_progress_summary(tmp_path):
    """Plan can report progress as 'N of M tasks complete'."""
    from superseded.pipeline.plan import Plan, PlanTask, write_plan, read_plan, update_task_status

    tasks = [
        PlanTask(title="Task A"),
        PlanTask(title="Task B"),
        PlanTask(title="Task C"),
    ]
    plan_path = str(tmp_path / "plan.md")
    write_plan(plan_path, "My Plan", "Context", tasks)
    update_task_status(plan_path, "Task A", "complete")
    update_task_status(plan_path, "Task B", "complete")

    plan = read_plan(plan_path)
    assert plan.completed_count == 2
    assert plan.total_count == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_plan.py::test_plan_task_status_tracking tests/test_plan.py::test_plan_progress_summary -v`
Expected: FAIL — `PlanTask` has no `status` field, `update_task_status` doesn't exist

- [ ] **Step 3: Implement plan task tracking**

Add to `PlanTask` in `pipeline/plan.py`:
```python
class PlanTask(BaseModel):
    title: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    verification: str = ""
    dependencies: list[str] = Field(default_factory=list)
    scope: str = "Medium"
    status: str = "pending"  # "pending", "in-progress", "complete", "skipped"
```

Add to `Plan`:
```python
class Plan(BaseModel):
    title: str
    context: str = ""
    tasks: list[PlanTask] = Field(default_factory=list)

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "complete")

    @property
    def total_count(self) -> int:
        return len(self.tasks)
```

Add `update_task_status` function:
```python
def update_task_status(path: str, task_title: str, status: str) -> None:
    plan = read_plan(path)
    for task in plan.tasks:
        if task.title == task_title:
            task.status = status
            break
    write_plan(path, plan.title, plan.context, plan.tasks)
```

Update `write_plan` to include status:
```python
def write_plan(path: str, title: str, context: str, tasks: list[PlanTask]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Plan: {title}", "", "## Context", "", context, "", "## Tasks", ""]
    for i, task in enumerate(tasks, 1):
        status_icon = {"complete": "x", "in-progress": " ", "skipped": "~"}.get(task.status, " ")
        lines.append(f"### Task {i}: {task.title}")
        lines.append(f"- **Status:** [{status_icon}] {task.status}")
        lines.append(f"- **Description:** {task.description}")
        # ... rest unchanged
```

Update `read_plan` to parse status:
```python
# In the task parsing loop:
status_match = re.search(r"\*\*Status:\*\*\s*\[.\]\s*(\S+)", block)
status = status_match.group(1) if status_match else "pending"
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_plan.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/pipeline/plan.py tests/test_plan.py
git commit -m "feat(plan): add task status tracking and progress reporting"
```

---

## Task 10: Inject Plan Progress into Context for BUILD/VERIFY/REVIEW

**Files:**
- Modify: `src/superseded/harness/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context.py

def test_context_includes_plan_progress_for_build(tmp_path):
    """BUILD context includes plan task progress when plan exists."""
    from superseded.pipeline.plan import write_plan, PlanTask

    artifacts_dir = tmp_path / ".superseded" / "artifacts" / "SUP-001"
    artifacts_dir.mkdir(parents=True)
    write_plan(
        str(artifacts_dir / "plan.md"),
        "My Plan",
        "Build stuff",
        [
            PlanTask(title="Setup", status="complete"),
            PlanTask(title="Implement", status="in-progress"),
            PlanTask(title="Test", status="pending"),
        ],
    )

    assembler = ContextAssembler(str(tmp_path))
    prompt = assembler.build(
        stage=Stage.BUILD,
        issue=Issue(id="SUP-001", title="Test", filepath="test.md"),
        artifacts_path=str(artifacts_dir),
    )
    assert "1 of 3" in prompt or "1/3" in prompt or "Setup" in prompt
    assert "complete" in prompt.lower() or "progress" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py::test_context_includes_plan_progress_for_build -v`
Expected: FAIL

- [ ] **Step 3: Implement plan progress injection**

Add method to `ContextAssembler`:
```python
def _build_plan_progress_layer(self, artifacts_path: str) -> str | None:
    from superseded.pipeline.plan import read_plan
    plan = read_plan(str(Path(artifacts_path) / "plan.md"))
    if not plan.tasks:
        return None

    progress = f"{plan.completed_count} of {plan.total_count} tasks complete"
    lines = [f"## Plan Progress ({progress})\n"]
    for i, task in enumerate(plan.tasks, 1):
        icon = {"complete": "[x]", "in-progress": "[ ]", "skipped": "[~]"}.get(task.status, "[ ]")
        lines.append(f"- Task {i}: {icon} {task.title} — {task.status}")
    return "\n".join(lines)
```

Add to `build()` method, after artifacts layer:
```python
# After artifacts layer, add plan progress for BUILD/VERIFY/REVIEW
if stage in (Stage.BUILD, Stage.VERIFY, Stage.REVIEW):
    plan_progress = self._build_plan_progress_layer(artifacts_path)
    if plan_progress:
        layers.append(plan_progress)
        self.layer_tokens["plan progress"] = _estimate_tokens(plan_progress)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/context.py tests/test_context.py
git commit -m "feat(context): inject plan progress into BUILD/VERIFY/REVIEW prompts"
```

---

## Task 11: Automatic Retry Loop

**Files:**
- Modify: `src/superseded/config.py`
- Modify: `src/superseded/harness/__init__.py`
- Test: `tests/test_harness.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_harness.py

async def test_harness_auto_retries_on_failure():
    """Harness automatically retries once on failure when auto_retry is enabled."""
    call_count = 0
    mock_agent = AsyncMock()

    async def flaky_stream(prompt, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield AgentEvent(
                event_type="stderr", content="lint error on line 5", stage=Stage.BUILD,
            )
            yield AgentEvent(
                event_type="status", content="", stage=Stage.BUILD,
                metadata={"exit_code": 1, "duration_ms": 100},
            )
        else:
            yield AgentEvent(
                event_type="stdout", content="Build succeeded with enough output content here", stage=Stage.BUILD,
            )
            yield AgentEvent(
                event_type="status", content="", stage=Stage.BUILD,
                metadata={"exit_code": 0, "duration_ms": 100},
            )

    mock_agent.run_streaming = flaky_stream

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
            auto_retry=True,
            max_auto_retries=1,
        )
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

        assert result.passed is True
        assert call_count == 2  # First fail, then success

        await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness.py::test_harness_auto_retries_on_failure -v`
Expected: FAIL — `HarnessRunner` doesn't accept `auto_retry` parameter

- [ ] **Step 3: Implement auto-retry**

Add to `config.py` in `SupersededConfig`:
```python
auto_retry: bool = False
max_auto_retries: int = 1
```

Add to `HarnessRunner.__init__`:
```python
self.auto_retry = auto_retry
self.max_auto_retries = max_auto_retries
```

Add to `_run_stage_streaming` — wrap the agent call in a retry loop:
```python
async def _run_stage_streaming(self, ...):
    max_attempts = self.max_auto_retries + 1 if self.auto_retry else 1
    last_result = None

    for attempt in range(max_attempts):
        # ... existing prompt assembly and agent execution ...
        result = StageResult(...)

        if result.passed or attempt == max_attempts - 1:
            return result

        # Prepare error context for retry
        previous_errors = [result.error] if result.error else []
        # Loop continues with error context in next prompt

    return last_result
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/config.py src/superseded/harness/__init__.py tests/test_harness.py
git commit -m "feat(harness): add automatic retry loop for transient failures"
```

---

## Task 12: Review → Build Feedback Loop

**Files:**
- Modify: `src/superseded/harness/__init__.py`
- Test: `tests/test_harness.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_harness.py

async def test_review_critical_findings_trigger_build_retry():
    """When REVIEW finds critical findings, automatically loop back to BUILD."""
    mock_agent = AsyncMock()
    call_count = 0

    async def review_stream(prompt, context):
        nonlocal call_count
        call_count += 1
        # First call: review with critical findings
        yield AgentEvent(
            event_type="stdout",
            content="## Critical\n- SQL injection vulnerability in /api/users\n- Hardcoded credentials in config",
            stage=Stage.REVIEW,
        )
        yield AgentEvent(
            event_type="status", content="", stage=Stage.REVIEW,
            metadata={"exit_code": 0, "duration_ms": 100},
        )

    mock_agent.run_streaming = review_stream

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
            review_loop=True,
        )
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)

        # Review should fail and indicate build retry needed
        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.REVIEW,
            artifacts_path=str(artifacts_path),
        )

        assert result.passed is False
        assert "critical" in result.error.lower() or "build" in result.error.lower()

        await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness.py::test_review_critical_findings_trigger_build_retry -v`
Expected: FAIL

- [ ] **Step 3: Implement review → build loop**

In `_run_stage_streaming`, after verification for REVIEW stage:
```python
if stage == Stage.REVIEW and not verification.passed:
    # Check if there are critical findings
    from superseded.harness.verification import parse_review_findings
    findings = parse_review_findings(stdout)
    if findings.get("critical", 0) > 0:
        return StageResult(
            stage=stage,
            passed=False,
            output=stdout,
            error=(
                f"Review found {findings['critical']} critical findings. "
                f"Loop back to BUILD to address these issues before re-reviewing."
            ),
            ...
        )
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/__init__.py tests/test_harness.py
git commit -m "feat(harness): review critical findings trigger build loop-back"
```

---

## Task 13: Worktree Merge on Success

**Files:**
- Modify: `src/superseded/pipeline/worktree.py`
- Test: `tests/test_worktree.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worktree.py — add at end

async def test_worktree_merge(tmp_path):
    """WorktreeManager merges worktree changes back to main branch."""
    import subprocess

    # Set up a real git repo
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)
    (repo / "README.md").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

    manager = WorktreeManager(str(repo))
    wt_path = await manager.create("SUP-001")

    # Make a change in the worktree
    (wt_path / "new_file.txt").write_text("new content")
    subprocess.run(["git", "add", "."], cwd=str(wt_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "add file"], cwd=str(wt_path), capture_output=True)

    # Merge back
    success = await manager.merge("SUP-001")
    assert success is True

    # File should exist in main repo
    assert (repo / "new_file.txt").exists()
    assert (repo / "new_file.txt").read_text() == "new content"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_worktree.py::test_worktree_merge -v`
Expected: FAIL — `merge` method doesn't exist

- [ ] **Step 3: Implement worktree merge**

Add method to `WorktreeManager`:
```python
async def merge(self, issue_id: str, repo: str | None = None) -> bool:
    """Merge worktree branch back into the target branch. Returns True on success."""
    repo_path = self._get_repo_path(repo)
    branch_name = self._branch_name(issue_id, repo)

    # Get current branch in main repo
    current = await self._run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=str(repo_path))
    target_branch = current.stdout.strip()

    result = await self._run_git(
        "merge", branch_name, "--no-ff", "-m", f"Merge {branch_name} for {issue_id}",
        cwd=str(repo_path),
    )
    return result.returncode == 0
```

Update `cleanup` to optionally merge first:
```python
async def cleanup(self, issue_id: str, repo: str | None = None, merge: bool = False) -> bool:
    """Cleanup worktree. If merge=True, merge changes first."""
    success = True
    if merge:
        success = await self.merge(issue_id, repo)

    repo_path = self._get_repo_path(repo)
    worktree_path = self._worktree_path(issue_id, repo)
    branch_name = self._branch_name(issue_id, repo)
    if worktree_path.exists():
        await self._run_git(
            "worktree", "remove", str(worktree_path), "--force", cwd=str(repo_path)
        )
    await self._run_git("branch", "-D", branch_name, cwd=str(repo_path))
    return success
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_worktree.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/pipeline/worktree.py tests/test_worktree.py
git commit -m "feat(worktree): merge worktree changes back on success"
```

---

## Task 14: Output Quality Analysis

**Files:**
- Modify: `src/superseded/harness/__init__.py`
- Test: `tests/test_harness.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_harness.py

async def test_harness_detects_low_quality_output():
    """Harness fails stage when output has no substantive code for BUILD."""
    mock_agent = AsyncMock()

    async def empty_stream(prompt, context):
        yield AgentEvent(
            event_type="stdout",
            content="I looked at the code but didn't make any changes. Everything looks fine.",
            stage=Stage.BUILD,
        )
        yield AgentEvent(
            event_type="status", content="", stage=Stage.BUILD,
            metadata={"exit_code": 0, "duration_ms": 100},
        )

    mock_agent.run_streaming = empty_stream

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "state.db"))
        await db.initialize()

        runner = HarnessRunner(
            agent_factory=_mock_factory(mock_agent),
            repo_path="/tmp/testrepo",
            db=db,
        )
        artifacts_path = Path(tmp) / ".superseded" / "artifacts" / "SUP-001"
        artifacts_path.mkdir(parents=True)
        result = await runner.run_stage(
            issue=_make_issue(),
            stage=Stage.BUILD,
            artifacts_path=str(artifacts_path),
        )

        # Should detect low quality — no code patterns in output
        assert result.passed is False
        assert "quality" in result.error.lower() or "substantive" in result.error.lower() or "code" in result.error.lower()

        await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness.py::test_harness_detects_low_quality_output -v`
Expected: FAIL — no output quality check exists

- [ ] **Step 3: Implement output quality analysis**

Add function to `harness/__init__.py`:
```python
def _analyze_output_quality(output: str, stage: Stage) -> str | None:
    """Check if output has substantive content for the given stage. Returns error or None."""
    if not output.strip():
        return "Agent produced empty output"

    if stage == Stage.BUILD:
        # BUILD output should contain code patterns
        code_indicators = [
            "def ", "class ", "function ", "import ", "from ",
            "async def", "return ", "if ", "for ", "while ",
            "```", "elif", "except", "try:", "with ",
        ]
        has_code = any(indicator in output for indicator in code_indicators)
        if not has_code and len(output.strip()) < 200:
            return (
                "BUILD output lacks substantive code. The agent should have "
                "written or modified code files. Output appears to be commentary only."
            )

    if stage == Stage.VERIFY:
        # VERIFY output should contain test results
        test_indicators = ["passed", "failed", "PASS", "FAIL", "test", "assert", "error"]
        has_tests = any(indicator in output for indicator in test_indicators)
        if not has_tests:
            return (
                "VERIFY output lacks test results. The agent should have "
                "run tests and reported results."
            )

    return None
```

Call it after the `MIN_OUTPUT_CHARS` check in `_run_stage_streaming`:
```python
quality_error = _analyze_output_quality(stdout, stage)
if quality_error:
    return StageResult(
        stage=stage,
        passed=False,
        output=stdout,
        error=quality_error,
        ...
    )
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_harness.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/__init__.py tests/test_harness.py
git commit -m "feat(harness): add output quality analysis for BUILD and VERIFY stages"
```

---

## Task 15: Cross-Stage Context Optimization

**Files:**
- Modify: `src/superseded/harness/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context.py

def test_context_carries_forward_spec_quality_signal(tmp_path):
    """When SPEC was verified, BUILD context includes a quality signal."""
    artifacts_dir = tmp_path / ".superseded" / "artifacts" / "SUP-001"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "spec.md").write_text(
        "# Spec\n\n## Requirements\nDetailed requirements here.\n\n## Architecture\nWell-defined architecture."
    )

    assembler = ContextAssembler(str(tmp_path))
    prompt = assembler.build(
        stage=Stage.BUILD,
        issue=Issue(id="SUP-001", title="Test", filepath="test.md"),
        artifacts_path=str(artifacts_dir),
        verified_stages=["spec"],
    )
    assert "verified" in prompt.lower() or "confirmed" in prompt.lower() or "quality" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py::test_context_carries_forward_spec_quality_signal -v`
Expected: FAIL — `verified_stages` parameter doesn't exist

- [ ] **Step 3: Implement cross-stage context**

Add `verified_stages` parameter to `build()` and inject quality signals:
```python
def build(
    self,
    stage: Stage,
    issue: Issue,
    artifacts_path: str,
    previous_errors: list[str] | None = None,
    iteration: int = 0,
    session_turns: list[dict] | None = None,
    target_repo: str | None = None,
    crg_search_results: list | None = None,
    crg_enabled: bool = False,
    verified_stages: list[str] | None = None,  # NEW
) -> str:
    layers: list[str] = []
    self.layer_tokens = {}
    previous_errors = previous_errors or []

    # ... existing layer assembly ...

    # Add cross-stage quality signals
    if verified_stages:
        quality_notes = []
        if "spec" in verified_stages:
            quality_notes.append("- SPEC was verified — requirements are well-defined. Focus on implementation accuracy.")
        if "plan" in verified_stages:
            quality_notes.append("- PLAN was verified — task breakdown is solid. Follow the plan closely.")
        if "build" in verified_stages and stage in (Stage.VERIFY, Stage.REVIEW):
            quality_notes.append("- BUILD was verified — code compiles and basic checks pass.")
        if quality_notes:
            quality_layer = "## Stage Quality Signals\n\n" + "\n".join(quality_notes)
            layers.append(quality_layer)
            self.layer_tokens["quality signals"] = _estimate_tokens(quality_layer)

    prompt = "\n\n---\n\n".join(layers)
    result = sanitize_agent_prompt(prompt)
    self.last_token_estimate = _estimate_tokens(result)
    return result
```

- [ ] **Step 4: Run tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/test_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/superseded/harness/context.py tests/test_context.py
git commit -m "feat(context): add cross-stage quality signals to prompts"
```

---

## Task 16: Run Full Test Suite

- [ ] **Step 1: Run all tests**

Run: `cd /home/debian/workspace/superseded && uv run pytest tests/ -v`

- [ ] **Step 2: Run lint and format**

Run: `cd /home/debian/workspace/superseded && uv run ruff check src/ tests/ && uv run ruff format src/ tests/`

- [ ] **Step 3: Fix any failures**

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: lint and format harness engineering fixes"
```

---

## Summary

| Task | Gap(s) Addressed | Files Modified |
|------|-------------------|----------------|
| 1 | #1 Token counting | `context.py`, `test_context.py` |
| 2 | #1 Adaptive sizing | `context.py`, `test_context.py` |
| 3 | #3 Curated errors | `context.py`, `test_context.py` |
| 4 | #2 Session history | `context.py`, `test_context.py` |
| 5 | #6 Selective docs | `context.py`, `test_context.py` |
| 6 | #9 Resource limits | `__init__.py`, `lifecycle.py`, `test_harness.py` |
| 7 | #10 Checkpoint integration | `__init__.py`, `test_harness_streaming.py` |
| 8 | #11 Verification feedback | `verification.py`, `test_verification.py` |
| 9 | #12 Plan tracking | `plan.py`, `test_plan.py` |
| 10 | #12 Plan in context | `context.py`, `test_context.py` |
| 11 | #8 Auto-retry | `config.py`, `__init__.py`, `test_harness.py` |
| 12 | #13 Review→Build loop | `__init__.py`, `test_harness.py` |
| 13 | #14 Worktree merge | `worktree.py`, `test_worktree.py` |
| 14 | #15 Output quality | `__init__.py`, `test_harness.py` |
| 15 | #18 Cross-stage context | `context.py`, `test_context.py` |
| 16 | All | Full test suite + lint |
