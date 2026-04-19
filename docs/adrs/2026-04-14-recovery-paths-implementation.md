# Pipeline Recovery Paths Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add two recovery mechanisms: agent-to-human questions (agent writes questions.md, pipeline pauses, user answers) and inline ticket editing (user edits ticket body, stage re-runs).

**Architecture:** Add `pause_reason` to Issue model. Detect `questions.md` after agent runs. Add two new endpoints for answering questions and editing ticket body. Update UI to show contextual actions based on pause reason.

**Tech Stack:** Python 3.12+, FastAPI, HTMX, Jinja2, SQLite, frontmatter

---

## Task 1: Add PauseReason model and DB column

**Files:**
- Modify: `src/superseded/models.py`
- Modify: `src/superseded/db.py`

**Step 1: Add PauseReason enum to models.py**

After `IssueStatus` enum, add:

```python
class PauseReason(StrEnum):
    RETRIES_EXHAUSTED = "retries-exhausted"
    AWAITING_INPUT = "awaiting-input"
    USER_EDIT = "user-edit"
```

**Step 2: Add pause_reason field to Issue model**

Add to `Issue` class:
```python
pause_reason: str = ""
```

Update `from_frontmatter()` to read it:
```python
pause_reason=post.get("pause_reason") or "",
```

**Step 3: Add pause_reason column to DB**

In `db.py`, in `initialize()` method, add to the issues table CREATE TABLE:
```sql
pause_reason TEXT DEFAULT ''
```

Also add to the `upsert_issue` INSERT/UPDATE to include `pause_reason`.

Add a method to update pause reason:
```python
async def update_pause_reason(self, issue_id: str, reason: str) -> None:
    await self._conn.execute(
        "UPDATE issues SET pause_reason = ? WHERE id = ?",
        (reason, issue_id),
    )
    await self._conn.commit()
```

**Step 4: Verify**

Run: `uv run pytest tests/ -v`

**Step 5: Commit**

```bash
git add src/superseded/models.py src/superseded/db.py
git commit -m "feat(models): add PauseReason enum and pause_reason field"
```

---

## Task 2: Add questions.md prompt instruction

**Files:**
- Modify: `src/superseded/pipeline/prompts.py`

**Step 1: Add question instruction constant**

At the top of the file, after imports:

```python
QUESTIONS_INSTRUCTION = """
If requirements are ambiguous or you need clarification:
1. Write your questions to `questions.md` in the artifacts directory
2. Format each as: ## Q: [question text]
3. Exit with code 0 (success) — the pipeline will pause for human input
4. Do NOT guess or make assumptions about unclear requirements
"""
```

**Step 2: Append to all stage prompts**

In `PROMPTS` dict, append `QUESTIONS_INSTRUCTION` to each stage's prompt string. For each stage:

```python
Stage.SPEC: PROMPTS[Stage.SPEC] + "\n\n" + QUESTIONS_INSTRUCTION,
```

Do this for all 6 stages.

**Step 3: Commit**

```bash
git add src/superseded/pipeline/prompts.py
git commit -m "feat(prompts): add agent questions instruction to all stages"
```

---

## Task 3: Detect questions.md and set pause_reason

**Files:**
- Modify: `src/superseded/pipeline/harness.py`
- Modify: `src/superseded/pipeline/executor.py`

**Step 1: Check for questions.md in harness.py**

In `run_stage_with_retries()`, after `if passed:` block, before returning the StageResult, add:

```python
if passed:
    # ... existing artifact writing ...

    # Check if agent asked questions
    questions_file = Path(artifacts_path) / "questions.md"
    if questions_file.exists():
        return StageResult(
            stage=stage,
            passed=False,
            output=agent_result.stdout,
            error="awaiting-input",
            artifacts=[],
            started_at=started,
            finished_at=finished,
        )

    return StageResult(...)
```

**Step 2: Set pause_reason in executor.py**

In `_run_single_repo()`, after getting the result, add pause_reason logic:

```python
if not result.passed:
    questions_file = Path(repo_artifacts) / "questions.md"
    if questions_file.exists():
        await self.db.update_pause_reason(issue.id, "awaiting-input")
    else:
        await self.db.update_pause_reason(issue.id, "retries-exhausted")
else:
    await self.db.update_pause_reason(issue.id, "")
```

**Step 3: Verify**

Run: `uv run pytest tests/ -v`

**Step 4: Commit**

```bash
git add src/superseded/pipeline/harness.py src/superseded/pipeline/executor.py
git commit -m "feat(pipeline): detect questions.md and set pause_reason"
```

---

## Task 4: Add answers context layer

**Files:**
- Modify: `src/superseded/pipeline/context.py`

**Step 1: Add `_build_answers_layer` method**

```python
def _build_answers_layer(self, artifacts_path: str) -> str | None:
    answers_file = Path(artifacts_path) / "answers.md"
    if answers_file.exists():
        content = answers_file.read_text(encoding="utf-8")
        return f"## Human Answers to Your Questions\n\n{content}"
    return None
```

**Step 2: Wire into `build()` method**

In the `build()` method, after artifacts layer (around line 162), add:

```python
answers = self._build_answers_layer(artifacts_path)
if answers:
    layers.append(answers)
```

**Step 3: Commit**

```bash
git add src/superseded/pipeline/context.py
git commit -m "feat(context): add answers.md context layer"
```

---

## Task 5: Add answer-questions endpoint

**Files:**
- Modify: `src/superseded/routes/issues.py`

**Step 1: Add POST /issues/{id}/answer-questions endpoint**

```python
@router.post("/{issue_id}/answer-questions", response_class=HTMLResponse)
async def answer_questions(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")

    form = await _get_form_data(request)

    # Build answers.md from form data
    answers_parts = []
    for key, value in form.items():
        if key.startswith("q_"):
            question_num = key[2:]
            answers_parts.append(f"### Answer to Q{question_num}\n\n{value}")
    answers_content = "\n\n".join(answers_parts)

    # Write answers to artifacts
    artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue_id)
    Path(artifacts_path).mkdir(parents=True, exist_ok=True)
    (Path(artifacts_path) / "answers.md").write_text(answers_content, encoding="utf-8")

    # Clear questions.md so agent doesn't ask again
    questions_file = Path(artifacts_path) / "questions.md"
    if questions_file.exists():
        questions_file.unlink()

    # Re-run current stage
    issue = _find_issue_for_pipeline(deps, issue_id)
    if issue is None:
        return HTMLResponse(content="")

    from superseded.routes.pipeline import _run_and_advance
    return await _run_and_advance(deps, issue_id, issue.stage, request)
```

**Step 2: Helper to find issue for pipeline**

Add helper (or import from pipeline routes):
```python
def _find_issue_for_pipeline(deps: Deps, issue_id: str):
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    from superseded.tickets.reader import list_issues
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    return matching[0] if matching else None
```

**Step 3: Commit**

```bash
git add src/superseded/routes/issues.py
git commit -m "feat(issues): add answer-questions endpoint"
```

---

## Task 6: Add update-body endpoint

**Files:**
- Modify: `src/superseded/routes/issues.py`

**Step 1: Add POST /issues/{id}/update-body endpoint**

```python
@router.post("/{issue_id}/update-body", response_class=HTMLResponse)
async def update_issue_body(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")

    form = await _get_form_data(request)
    new_body = str(form.get("body", "")).strip()

    # Read current ticket
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not matching:
        return HTMLResponse(content="")

    issue = matching[0]

    # Read and update the file
    import frontmatter
    with open(issue.filepath, "r") as f:
        post = frontmatter.load(f)
    post.content = new_body
    with open(issue.filepath, "w") as f:
        f.write(frontmatter.dumps(post))

    # Update DB
    await deps.db.upsert_issue(
        Issue(
            id=issue.id,
            title=issue.title,
            filepath=issue.filepath,
            body=new_body,
            stage=issue.stage,
            status=issue.status,
        )
    )

    # Re-run current stage
    from superseded.routes.pipeline import _run_and_advance
    return await _run_and_advance(deps, issue_id, issue.stage, request)
```

**Step 2: Commit**

```bash
git add src/superseded/routes/issues.py
git commit -m "feat(issues): add update-body endpoint for inline ticket editing"
```

---

## Task 7: Update issue detail UI for recovery actions

**Files:**
- Modify: `templates/_actions.html`
- Modify: `templates/issue_detail.html`
- Create: `templates/_questions_form.html`
- Create: `templates/_edit_ticket_form.html`

**Step 1: Create _questions_form.html**

```html
<div id="questions-form" class="card rounded-xl p-6 mb-4">
    <h3 class="text-sm font-semibold text-sand-400 uppercase tracking-widest mb-4">Agent Needs Clarification</h3>
    <div class="mb-4 text-shell-300 text-sm prose prose-invert prose-sm max-w-none">
        {{ questions_html | safe }}
    </div>
    <form hx-post="/issues/{{ issue.id }}/answer-questions"
          hx-target="#issue-detail-content"
          hx-swap="innerHTML">
        {% for q in questions %}
        <div class="mb-3">
            <label class="block text-xs font-semibold text-sand-500 mb-1">{{ q }}</label>
            <textarea name="q_{{ loop.index }}" rows="2"
                      class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500"></textarea>
        </div>
        {% endfor %}
        <button type="submit" class="btn-primary text-white px-4 py-2 rounded-lg text-sm font-semibold">
            Submit Answers & Resume
        </button>
    </form>
</div>
```

**Step 2: Create _edit_ticket_form.html**

```html
<div id="edit-ticket-form" class="card rounded-xl p-6 mb-4" x-data="{ show: false }">
    <button @click="show = !show" class="text-sm text-shell-400 hover:text-shell-200 transition-colors">
        Edit Ticket Body
    </button>
    <div x-show="show" x-cloak class="mt-4">
        <form hx-post="/issues/{{ issue.id }}/update-body"
              hx-target="#issue-detail-content"
              hx-swap="innerHTML">
            <textarea name="body" rows="10"
                      class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm font-mono focus:outline-none focus:border-neon-500">{{ issue.body }}</textarea>
            <div class="flex gap-3 mt-3">
                <button type="submit" class="btn-primary text-white px-4 py-2 rounded-lg text-sm font-semibold">
                    Save & Resume
                </button>
                <button type="button" @click="show = false" class="btn-secondary text-shell-300 px-4 py-2 rounded-lg text-sm">
                    Cancel
                </button>
            </div>
        </form>
    </div>
</div>
```

**Step 3: Update _actions.html**

Replace the current content with conditional rendering based on `issue.pause_reason`:

```html
<div id="issue-actions">
    {% if issue.status.value == 'paused' and issue.pause_reason == 'awaiting-input' %}
        {% include "_questions_form.html" %}
    {% elif issue.status.value == 'paused' %}
        {% include "_edit_ticket_form.html" %}
        <!-- existing retry button -->
    {% elif issue.status.value != 'done' %}
        <!-- existing advance/retry buttons -->
    {% endif %}
</div>
```

**Step 4: Commit**

```bash
git add templates/_actions.html templates/issue_detail.html templates/_questions_form.html templates/_edit_ticket_form.html
git commit -m "feat(ui): add recovery action forms for paused issues"
```

---

## Task 8: Update dashboard to show pause reasons

**Files:**
- Modify: `templates/_dashboard_table.html`

**Step 1: Add pause reason badge**

In the status column, when `issue.status.value == 'paused'`, add a sub-badge:

```html
{% if issue.status.value == 'paused' and issue.pause_reason %}
<span class="ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-medium
    {% if issue.pause_reason == 'awaiting-input' %}bg-sand-900/60 text-sand-400
    {% elif issue.pause_reason == 'retries-exhausted' %}bg-coral-900/60 text-coral-400
    {% else %}bg-shell-800 text-shell-400{% endif %}">
    {% if issue.pause_reason == 'awaiting-input' %}Awaiting Input
    {% elif issue.pause_reason == 'retries-exhausted' %}Retries Exhausted
    {% else %}{{ issue.pause_reason }}{% endif %}
</span>
{% endif %}
```

**Step 2: Commit**

```bash
git add templates/_dashboard_table.html
git commit -m "feat(ui): show pause reason badges in dashboard"
```

---

## Task 9: End-to-end verification

**Step 1: Run all tests**

```bash
uv run pytest tests/ -v
```

**Step 2: Run linter**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

**Step 3: Manual verification**

1. Start server: `uv run superseded --port 8090`
2. Create issue, run build stage
3. If agent writes questions.md → issue should show "Awaiting Input" with questions form
4. Answer questions, click Submit → stage should re-run
5. On paused issue, click "Edit Ticket" → edit body → Save & Resume → stage re-runs

**Step 4: Final commit if needed**

```bash
git add -A
git commit -m "chore: lint and format"
```
