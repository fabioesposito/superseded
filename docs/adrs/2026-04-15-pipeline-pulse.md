# Pipeline Pulse Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add stage duration tracking, timeline visualization, and ntfy.sh push notifications to Superseded.

**Architecture:** Three features that compose together. Duration timestamps go on `stage_results` rows (migration-safe). Timeline uses those timestamps in a server-rendered HTMX partial. Notifications use `httpx` to POST to ntfy.sh from the executor after stage completion. All config lives in `.superseded/config.yaml` and the settings page.

**Tech Stack:** Python 3.12+, SQLite (aiosqlite), FastAPI + HTMX + Alpine.js + Tailwind CSS, httpx (already a dep), ntfy.sh HTTP API.

---

### Task 1: DB migration — add started_at / finished_at timestamps to stage_results

**Files:**
- Modify: `src/superseded/db.py:95-110` (migration block)
- Test: `tests/test_db.py`

**Step 1: Write the failing test**

In `tests/test_db.py`, add a test that saves a `StageResult` with `started_at` and `finished_at`, then reads it back and verifies the timestamps are preserved.

```python
async def test_db_stage_result_timestamps():
    import datetime
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".superseded" / "state.db"
        db = Database(str(db_path))
        await db.initialize()

        started = datetime.datetime(2026, 4, 15, 10, 0, 0, tzinfo=datetime.timezone.utc)
        finished = datetime.datetime(2026, 4, 15, 10, 2, 34, tzinfo=datetime.timezone.utc)
        result = StageResult(
            stage=Stage.BUILD,
            passed=True,
            output="ok",
            started_at=started,
            finished_at=finished,
        )
        await db.save_stage_result("SUP-001", result)

        results = await db.get_stage_results("SUP-001")
        assert len(results) == 1
        assert results[0]["started_at"] is not None
        assert results[0]["finished_at"] is not None

        await db.close()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_db_stage_result_timestamps -v`
Expected: FAIL — the `started_at` and `finished_at` columns don't exist in the migration yet.

**Step 3: Add migrations for started_at and finished_at**

In `src/superseded/db.py`, in the `initialize` method's migration list (after line 98), add two new migrations:

```python
migrations: list[tuple[str, str, str]] = [
    ("stage_results", "repo", "'primary'"),
    ("harness_iterations", "repo", "'primary'"),
    ("issues", "pause_reason", "''"),
    ("stage_results", "started_at", "NULL"),
    ("stage_results", "finished_at", "NULL"),
]
```

**Step 4: Verify save_stage_result already passes started_at/finished_at**

Check that `db.py:save_stage_result` (line 187-206) already includes `started_at` and `finished_at` in the INSERT. It does — the model has these fields and they're serialized. Also verify `get_stage_results` reads them back. The column values will come through as strings in the dict.

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py::test_db_stage_result_timestamps -v`
Expected: PASS

**Step 6: Run full DB test suite**

Run: `uv run pytest tests/test_db.py -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/superseded/db.py tests/test_db.py
git commit -m "feat: add started_at/finished_at migration to stage_results"
```

---

### Task 2: Record stage start/end timestamps in StageExecutor

**Files:**
- Modify: `src/superseded/pipeline/executor.py:34-70` and `executor.py:72-144`
- Test: `tests/test_executor.py`

**Step 1: Write the failing test**

Add a test that verifies `started_at` and `finished_at` are populated on the `StageResult` returned by `StageExecutor.run_stage()`. This requires mocking the harness runner.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_executor.py -v -k timestamp`
Expected: FAIL — `started_at` will be None since we haven't added timestamp recording yet.

**Step 3: Add timestamp recording in executor**

In `src/superseded/pipeline/executor.py`, in the `run_stage` method, add `import datetime` at the top, then record `started_at` before the stage execution loop and `finished_at` after. Set them on the aggregate `StageResult`:

```python
import datetime

# Add at start of run_stage method, before the loop:
started_at = datetime.datetime.now(datetime.UTC)

# After the loop, when creating the aggregate StageResult:
aggregate = StageResult(
    stage=stage,
    passed=all_passed,
    output="\n".join(combined_output),
    error="" if all_passed else "One or more repos failed",
    started_at=started_at,
    finished_at=datetime.datetime.now(datetime.UTC),
)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_executor.py -v -k timestamp`
Expected: PASS

**Step 5: Run full executor test suite**

Run: `uv run pytest tests/test_executor.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/superseded/pipeline/executor.py tests/test_executor.py
git commit -m "feat: record started_at/finished_at timestamps in StageExecutor"
```

---

### Task 3: DurationMetrics model and DB query

**Files:**
- Modify: `src/superseded/models.py:144-151` (PipelineMetrics)
- Modify: `src/superseded/db.py` (add new method)
- Modify: `src/superseded/routes/pipeline.py:293-332` (_compute_metrics)
- Test: `tests/test_metrics.py`

**Step 1: Write the failing test**

Add a test that creates issues with `started_at`/`finished_at` on their stage results and verifies that `_compute_metrics` returns non-empty `avg_stage_duration_ms` values.

```python
async def test_metrics_includes_stage_durations():
    import datetime

    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        (repo_path / ".superseded" / "issues").mkdir(parents=True)

        db_path = str(repo_path / ".superseded" / "state.db")
        db = Database(db_path)
        await db.initialize()

        issue = Issue(id="SUP-000", title="Test", filepath="")
        await db.upsert_issue(issue)

        started = datetime.datetime(2026, 4, 15, 10, 0, 0, tzinfo=datetime.timezone.utc)
        finished = datetime.datetime(2026, 4, 15, 10, 2, 34, tzinfo=datetime.timezone.utc)
        result = StageResult(
            stage=Stage.BUILD,
            passed=True,
            output="ok",
            started_at=started,
            finished_at=finished,
        )
        await db.save_stage_result("SUP-000", result)

        app = create_app(repo_path=str(repo_path), db=db)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/pipeline/metrics")

        data = resp.json()
        assert "avg_stage_duration_ms" in data
        assert data["avg_stage_duration_ms"]["build"] > 0

        await db.close()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_metrics.py::test_metrics_includes_stage_durations -v`
Expected: FAIL — `avg_stage_duration_ms` is currently always `{}`.

**Step 3: Implement duration computation in _compute_metrics**

In `src/superseded/routes/pipeline.py`, modify `_compute_metrics` to compute avg duration per stage from `started_at` / `finished_at`:

```python
import datetime

# Add inside _compute_metrics, after computing stage_attempts:
stage_durations: dict[str, list[float]] = {}
for r in all_results:
    sa = r.get("started_at")
    fa = r.get("finished_at")
    if sa and fa:
        started = datetime.datetime.fromisoformat(sa) if isinstance(sa, str) else sa
        finished = datetime.datetime.fromisoformat(fa) if isinstance(fa, str) else fa
        duration_ms = (finished - started).total_seconds() * 1000
        if duration_ms > 0:
            stage_durations.setdefault(r["stage"], []).append(duration_ms)

avg_durations = {
    stage: sum(durations) / len(durations)
    for stage, durations in stage_durations.items()
}
```

Then replace `avg_stage_duration_ms={}` with `avg_stage_duration_ms=avg_durations`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_metrics.py::test_metrics_includes_stage_durations -v`
Expected: PASS

**Step 5: Run full metrics test suite**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/superseded/routes/pipeline.py tests/test_metrics.py
git commit -m "feat: compute avg_stage_duration_ms from started_at/finished_at timestamps"
```

---

### Task 4: Show duration in stage results and metrics dashboard

**Files:**
- Modify: `templates/_results.html`
- Modify: `templates/metrics.html`
- Modify: `src/superseded/routes/pipeline.py:293-332` (_compute_metrics)

**Step 1: Add duration display to _results.html**

In `_results.html`, for each stage result, show the duration if `started_at` and `finished_at` are available. The template already has a loop over `stage_results`. Add a duration line using Jinja2:

After the pass/fail label line (~line 10), add:

```html
{% if result.started_at and result.finished_at %}
<span class="text-shell-500 text-xs font-mono">
    {% set dur = (result.finished_at - result.started_at).total_seconds() %}
    {% if dur >= 60 %}{{ (dur / 60)|int }}m {{ (dur % 60)|int }}s{% else %}{{ dur|int }}s{% endif %}
</span>
{% endif %}
```

Note: `started_at` and `finished_at` come from DB as strings. We need to parse them. Update the `issue_detail` route in `routes/issues.py` to convert them to datetimes, or add a Jinja2 filter. The simpler approach: do the computation in the route and pass the formatted duration strings.

**Step 2: Compute formatted durations in the route**

In `src/superseded/routes/issues.py`, in the `issue_detail` function (~line 160-197), after fetching `stage_results`, compute a `durations` dict mapping stage name to formatted string:

```python
import datetime

durations: dict[str, str] = {}
for r in stage_results:
    sa = r.get("started_at")
    fa = r.get("finished_at")
    if sa and fa:
        started = datetime.datetime.fromisoformat(str(sa)) if isinstance(sa, str) else sa
        finished = datetime.datetime.fromisoformat(str(fa)) if isinstance(fa, str) else fa
        dur = (finished - started).total_seconds()
        if dur >= 60:
            durations[r["stage"]] = f"{int(dur // 60)}m {int(dur % 60)}s"
        else:
            durations[r["stage"]] = f"{int(dur)}s"
```

Pass `durations=durations` in the template context.

**Step 3: Use durations in _results.html**

Update the results template to use the pre-computed `durations` dict:

```html
{% if durations.get(result.stage) %}
<span class="text-shell-500 text-xs font-mono ml-2">{{ durations[result.stage] }}</span>
{% endif %}
```

Also update `_render_issue_detail_oob` in `pipeline.py` to pass `durations`.

**Step 4: Add duration chart to metrics dashboard**

In `metrics.html`, add a new chart section after the "Retries by Stage" section for "Avg Duration by Stage" using ApexCharts horizontal bar chart, similar to the success rates chart, using `metrics.avg_stage_duration_ms`.

**Step 5: Add duration card to issue detail**

Show "Took X" or "Still running" on the issue detail page near the pipeline progress bar.

**Step 6: Commit**

```bash
git add templates/_results.html templates/metrics.html src/superseded/routes/issues.py src/superseded/routes/pipeline.py
git commit -m "feat: show stage durations in results, metrics, and issue detail"
```

---

### Task 5: Pipeline timeline visualization

**Files:**
- Create: `templates/_timeline.html`
- Modify: `templates/issue_detail.html`
- Modify: `src/superseded/routes/issues.py` (add timeline data to context)

**Step 1: Create `_timeline.html` partial**

Create a horizontal timeline showing stages as segments. Each segment displays: stage name, start time, duration, status icon. Retry iterations stack under the same label.

```html
<div id="pipeline-timeline" class="mt-6">
    <h2 class="text-sm font-semibold uppercase tracking-widest text-sand-500 mb-3">Timeline</h2>
    <div class="relative flex items-stretch gap-0 h-16">
        {% for stage_name in stage_order %}
        {% set results_for_stage = stage_results | selectattr("stage", "equalto", stage_name) | list %}
        {% set latest_result = results_for_stage | last if results_for_stage else none %}
        {% set is_current = stage_name == issue.stage.value and issue.status.value == 'in-progress' %}
        {% set duration = durations.get(stage_name, "") %}

        <div class="flex-1 relative group {% if not results_for_stage and not is_current %}opacity-40{% endif %}">
            <div class="h-full rounded-lg border-2 flex flex-col items-center justify-center text-xs font-semibold uppercase tracking-wider transition-all
                {% if latest_result and latest_result.passed %}bg-olive-950 border-olive-700/50 text-olive-400
                {% elif latest_result and not latest_result.passed %}bg-coral-950 border-coral-700/50 text-coral-400
                {% elif is_current %}bg-neon-950 border-neon-700/50 text-neon-400 glow-active
                {% else %}bg-shell-900/50 border-shell-800/30 text-shell-600{% endif %}">
                {{ stage_name }}
                {% if duration %}<span class="text-[10px] font-mono mt-0.5">{{ duration }}</span>{% endif %}
                {% if is_current and not latest_result %}
                <span class="w-1.5 h-1.5 rounded-full bg-neon-400 animate-pulse mt-0.5"></span>
                {% endif %}
            </div>
            {% if latest_result and latest_result.started_at %}
            <div class="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-10 w-40 text-xs">
                <div class="bg-shell-800 rounded-md p-2 shadow-lg border border-shell-700">
                    {% if latest_result.passed %}<span class="text-olive-400 font-semibold">PASS</span>{% else %}<span class="text-coral-400 font-semibold">FAIL</span>{% endif %}
                    {% if duration %}<span class="text-shell-400 ml-1">{{ duration }}</span>{% endif %}
                </div>
            </div>
            {% endif %}
        </div>
        {% endfor %}
    </div>
    {% if harness_iterations | length > 0 %}
    <div class="mt-3 flex gap-4 text-xs text-shell-500">
        <span>{{ harness_iterations | length }} iteration{{ 's' if harness_iterations | length != 1 }}</span>
    </div>
    {% endif %}
</div>
```

**Step 2: Include timeline in issue_detail.html**

Add after the "Pipeline Progress" section in `issue_detail.html`, after line 42:

```html
<div class="mb-8">
    {% include "_timeline.html" %}
</div>
```

**Step 3: Pass timeline data from route context**

The route already passes `stage_results`, `stage_order`, `harness_iterations`, and `durations`. No additional data needed.

**Step 4: Update _render_issue_detail_oob to pass durations**

In `src/superseded/routes/pipeline.py`, the `_render_issue_detail_oob` function also needs to compute and pass `durations`. Add duration computation there too.

**Step 5: Commit**

```bash
git add templates/_timeline.html templates/issue_detail.html src/superseded/routes/pipeline.py
git commit -m "feat: add pipeline timeline visualization to issue detail"
```

---

### Task 6: NotificationService with ntfy.sh support

**Files:**
- Create: `src/superseded/notifications.py`
- Modify: `src/superseded/config.py` (add NotificationsConfig)
- Test: `tests/test_notifications.py`

**Step 1: Write the failing test**

```python
import datetime
from unittest.mock import AsyncMock, patch

import pytest

from superseded.models import Issue, Stage, StageResult
from superseded.notifications import NotificationService


async def test_notify_sends_to_ntfy():
    service = NotificationService(topic="superseded-test", enabled=True)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = AsyncMock(status_code=200)
        await service.notify(
            title="SUP-001: BUILD completed",
            message="Build took 2m 34s",
            priority="default",
            tags=["white_check_mark"],
        )
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "superseded-test" in call_args.args[0]
        assert call_args.kwargs["headers"]["Title"] == "SUP-001: BUILD completed"


async def test_notify_disabled_does_not_send():
    service = NotificationService(topic="superseded-test", enabled=False)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        await service.notify(title="test", message="test", priority="default", tags=[])
        mock_post.assert_not_called()


async def test_notify_without_topic_does_not_send():
    service = NotificationService(topic="", enabled=True)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        await service.notify(title="test", message="test", priority="default", tags=[])
        mock_post.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_notifications.py -v`
Expected: FAIL — `superseded.notifications` module doesn't exist yet.

**Step 3: Create NotificationService**

Create `src/superseded/notifications.py`:

```python
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, topic: str, enabled: bool = False) -> None:
        self.topic = topic
        self.enabled = enabled
        self._base_url = "https://ntfy.sh"

    async def notify(
        self,
        title: str,
        message: str = "",
        priority: str = "default",
        tags: list[str] | None = None,
        click_url: str | None = None,
    ) -> None:
        if not self.enabled or not self.topic:
            return
        headers: dict[str, str] = {
            "Title": title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = ",".join(tags)
        if click_url:
            headers["Click"] = click_url
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{self._base_url}/{self.topic}",
                    content=message,
                    headers=headers,
                )
        except httpx.HTTPError:
            logger.warning("Failed to send notification to ntfy.sh")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_notifications.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/superseded/notifications.py tests/test_notifications.py
git commit -m "feat: add NotificationService with ntfy.sh support"
```

---

### Task 7: Add NotificationsConfig to SupersededConfig

**Files:**
- Modify: `src/superseded/config.py`
- Test: `tests/test_config.py`

**Step 1: Add NotificationsConfig model**

In `src/superseded/config.py`, add:

```python
class NotificationsConfig(BaseModel):
    enabled: bool = False
    ntfy_topic: str = ""
```

Then add to `SupersededConfig`:

```python
notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
```

**Step 2: Write a test**

In `tests/test_config.py`, add a test that loads config with a `notifications` section and verifies it's parsed, and that default config has `notifications.enabled == False`.

**Step 3: Run tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add src/superseded/config.py tests/test_config.py
git commit -m "feat: add NotificationsConfig to SupersededConfig"
```

---

### Task 8: Wire notifications into StageExecutor

**Files:**
- Modify: `src/superseded/pipeline/executor.py`
- Modify: `src/superseded/main.py` (_build_pipeline_state)

**Step 1: Add NotificationService to StageExecutor**

Add `notification_service` as a constructor parameter to `StageExecutor`. After each stage completes in `run_stage()` and `_run_single_repo()`, call `notify()` with the result:

```python
if self.notification_service and self.notification_service.enabled:
    duration = ""
    if result.started_at and result.finished_at:
        dur = (result.finished_at - result.started_at).total_seconds()
        if dur >= 60:
            duration = f" ({int(dur // 60)}m {int(dur % 60)}s)"
        else:
            duration = f" ({int(dur)}s)"

    if result.passed:
        await self.notification_service.notify(
            title=f"{issue.id}: {stage.value.upper()} completed",
            message=f"Stage {stage.value} passed{duration}",
            priority="default",
            tags=["white_check_mark"],
        )
    else:
        await self.notification_service.notify(
            title=f"{issue.id}: {stage.value.upper()} failed",
            message=f"Stage {stage.value} failed: {result.error[:200]}",
            priority="high",
            tags=["x"],
        )
```

Also add notification for SHIP success and pause:

In `_run_stage_background` in `pipeline.py`, after the result is processed, add notifications for:
- Pipeline SHIPPED (when `next_stage is None` and `result.passed`)
- Pipeline PAUSED

**Step 2: Wire in main.py**

In `_build_pipeline_state`, create a `NotificationService` from config and pass it to `StageExecutor`:

```python
from superseded.notifications import NotificationService

notification_service = NotificationService(
    topic=config.notifications.ntfy_topic,
    enabled=config.notifications.enabled,
)
executor = StageExecutor(
    runner=runner,
    db=None,
    worktree_manager=worktree_manager,
    notification_service=notification_service,
)
```

**Step 3: Run existing tests to verify no regressions**

Run: `uv run pytest tests/test_executor.py tests/test_integration.py -v`
Expected: PASS (notifications are disabled by default)

**Step 4: Commit**

```bash
git add src/superseded/pipeline/executor.py src/superseded/main.py
git commit -m "feat: wire NotificationService into StageExecutor and pipeline"
```

---

### Task 9: ntfy.sh settings UI

**Files:**
- Modify: `templates/settings.html`
- Create: `templates/_notifications_field.html`
- Modify: `src/superseded/routes/settings.py`

**Step 1: Create `_notifications_field.html` partial**

```html
<div id="notifications-field" class="card rounded-xl p-5">
    <form hx-post="/settings/notifications"
          hx-target="#notifications-field"
          hx-swap="outerHTML"
          class="space-y-4">
        <div class="flex items-center gap-3">
            <label class="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" name="enabled" value="1"
                       {% if notifications.enabled %}checked{% endif %}
                       class="sr-only peer">
                <div class="w-9 h-5 bg-shell-700 peer-focus:outline-none rounded-full peer peer-checked:bg-neon-600
                     after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full
                     after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
            </label>
            <span class="text-sm text-shell-300">Enable push notifications</span>
        </div>
        {% if success %}<p class="text-olive-400 text-xs font-mono">Saved!</p>{% endif %}
        <div>
            <label class="block text-xs font-semibold uppercase tracking-widest text-sand-500 mb-1.5">ntfy.sh Topic</label>
            <input type="text" name="ntfy_topic" value="{{ notifications.ntfy_topic }}"
                   class="w-full bg-shell-900 border border-shell-700 rounded-lg px-3 py-2 text-shell-200 text-sm focus:outline-none focus:border-neon-500 transition-colors font-mono"
                   placeholder="superseded-myproject">
        </div>
        <div class="flex items-center gap-3">
            <button type="submit" class="btn-primary text-white px-4 py-2 rounded-lg text-sm font-semibold">Save Notifications</button>
            {% if notifications.ntfy_topic and notifications.enabled %}
            <span class="text-olive-400 text-xs font-mono">Active</span>
            {% else %}
            <span class="text-shell-600 text-xs font-mono">Inactive</span>
            {% endif %}
        </div>
        <p class="text-shell-600 text-xs">Pushes to ntfy.sh — no account needed. Install the ntfy app or visit <a href="https://ntfy.sh" class="text-neon-500 hover:text-neon-400">ntfy.sh</a>.</p>
    </form>
</div>
```

**Step 2: Add settings route**

In `src/superseded/routes/settings.py`, add a new endpoint:

```python
@router.post("/settings/notifications", response_class=HTMLResponse)
async def update_notifications(request: Request, deps: Deps = Depends(get_deps)):
    form = await _get_form_data(request)
    config = deps.config
    enabled = bool(form.get("enabled"))
    ntfy_topic = str(form.get("ntfy_topic", "")).strip()
    config.notifications.enabled = enabled
    config.notifications.ntfy_topic = ntfy_topic
    save_config(config, Path(config.repo_path))
    _reload_pipeline(request.app, config)
    return get_templates().TemplateResponse(
        request,
        "_notifications_field.html",
        {"notifications": config.notifications, "success": True},
    )
```

**Step 3: Add to settings.html**

After the "Source Root" section (~line 83), add:

```html
<div class="mt-10 mb-3">
    <h2 class="text-lg font-semibold text-shell-100">Notifications</h2>
    <p class="text-shell-500 text-sm mt-1">Push notifications via ntfy.sh for stage events</p>
</div>
{% include "_notifications_field.html" %}
```

**Step 4: Pass notifications to settings template**

In the `settings_page` route, add `notifications=config.notifications` to the template context.

**Step 5: Run tests**

Run: `uv run pytest tests/test_settings_routes.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add templates/settings.html templates/_notifications_field.html src/superseded/routes/settings.py
git commit -m "feat: add ntfy.sh notification settings UI"
```

---

### Task 10: Duration chart on metrics dashboard

**Files:**
- Modify: `templates/metrics.html`
- Modify: `src/superseded/routes/pipeline.py` (_compute_metrics)

**Step 1: Add duration bar chart to metrics.html**

After the retries chart section, add a new section for "Avg Duration by Stage":

```html
<div class="card rounded-xl p-6 mb-8">
    <h3 class="text-xs font-semibold uppercase tracking-widest text-sand-500 mb-4">Avg Duration by Stage</h3>
    <div id="duration-bars"></div>
    <p id="duration-bars-empty" class="text-shell-500 text-sm hidden">No duration data yet</p>
</div>
```

Add JavaScript for the chart (similar pattern to stage bars):

```javascript
var avgDurations = {{ metrics.avg_stage_duration_ms | tojson }};
if (Object.keys(avgDurations).length === 0) {
    document.getElementById('duration-bars').classList.add('hidden');
    document.getElementById('duration-bars-empty').classList.remove('hidden');
} else {
    var durStages = Object.keys(avgDurations);
    var durValues = durStages.map(function(s) { return Math.round(avgDurations[s] / 1000); });
    new ApexCharts(document.querySelector("#duration-bars"), {
        series: [{ name: 'Duration (s)', data: durValues }],
        chart: { type: 'bar', height: 200, background: 'transparent', toolbar: { show: false }, animations: { enabled: true, easing: 'easeinout', speed: 600 } },
        plotOptions: { bar: { columnWidth: '50%', borderRadius: 4, distributed: true, dataLabels: { position: 'top' } } },
        colors: durStages.map(function() { return COLORS.olive; }),
        dataLabels: { enabled: true, formatter: function(v) { return v + 's'; }, style: { fontSize: '0.85rem', fontFamily: 'JetBrains Mono', fontWeight: 600, colors: [COLORS.oliveLight] }, offsetY: -20 },
        xaxis: { categories: durStages.map(function(s) { return s.toUpperCase(); }), labels: { style: { colors: COLORS.shell400, fontFamily: 'JetBrains Mono', fontSize: '0.7rem' } }, axisBorder: { show: false }, axisTicks: { show: false } },
        yaxis: { show: false },
        grid: { show: false },
        legend: { show: false },
        tooltip: { theme: 'dark', y: { formatter: function(v) { return v + ' seconds'; } } },
        theme: { mode: 'dark' }
    }).render();
}
```

**Step 2: Commit**

```bash
git add templates/metrics.html
git commit -m "feat: add avg duration chart to metrics dashboard"
```

---

### Task 11: Integration test — full pipeline pulse flow

**Files:**
- Create: `tests/test_pipeline_pulse.py`

**Step 1: Write integration test**

Test that:
1. Saving a `StageResult` with `started_at`/`finished_at` shows up in metrics duration
2. The timeline renders in the issue detail page
3. `NotificationService.notify()` is called when a stage completes (mock httpx)
4. The settings page shows notifications config

**Step 2: Run test**

Run: `uv run pytest tests/test_pipeline_pulse.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_pipeline_pulse.py
git commit -m "test: add integration tests for pipeline pulse features"
```

---

### Task 12: Lint, format, and verify

**Step 1: Run linter**

Run: `uv run ruff check src/ tests/ --fix`
Expected: No errors

**Step 2: Run formatter**

Run: `uv run ruff format src/ tests/`
Expected: Clean

**Step 3: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "chore: lint and format fixes for pipeline pulse"
```