# Metrics Dashboard Visual Upgrade — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the metrics page from plain stat cards + CSS bars into a visual dashboard using ApexCharts donut, radial gauge, and bar charts.

**Architecture:** Client-side chart rendering via ApexCharts CDN. No backend changes — charts init from existing Jinja2 template context (`metrics` object). Template-only changes to `base.html` (CDN script) and `metrics.html` (full rewrite).

**Tech Stack:** ApexCharts (CDN), Jinja2, Tailwind CSS, existing dark neon palette

---

## Task 1: Add ApexCharts CDN to base template

**Files:**
- Modify: `templates/base.html:10` (after htmx-ext-sse script tag)

**Step 1: Add ApexCharts CDN script tag**

Add this line after the htmx-ext-sse script (line 10):

```html
<script src="https://cdn.jsdelivr.net/npm/apexcharts"></script>
```

**Step 2: Verify the page still loads**

Start the server and visit `/` to confirm no JS errors from the CDN addition.

```bash
uv run superseded
# Visit http://localhost:8000/ — check browser console for errors
```

**Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat: add ApexCharts CDN to base template"
```

---

## Task 2: Rewrite metrics template — hero section

**Files:**
- Modify: `templates/metrics.html` (full rewrite)

**Step 1: Write the hero gauge and stat cards**

Replace the entire `metrics.html` content block with the hero section. The hero has:
- A centered radial bar chart showing aggregate success rate
- 4 stat cards below it: Total Issues, Total Retries, Best Stage, Worst Stage
- The aggregate is computed client-side from `stage_success_rates`

```html
{% extends "base.html" %}
{% block title %}Pipeline Metrics - Superseded{% endblock %}
{% block content %}
<div class="animate-fade-in">
    <div class="flex items-center justify-between mb-8">
        <h1 class="text-3xl font-bold text-shell-50 tracking-tight">Pipeline Metrics</h1>
        <a href="/" class="text-shell-500 hover:text-shell-300 text-sm transition-colors">&larr; Dashboard</a>
    </div>

    <!-- Hero: Health Gauge + Stat Cards -->
    <div class="grid grid-cols-1 lg:grid-cols-5 gap-4 mb-8">
        <!-- Gauge takes 3/5 width -->
        <div class="lg:col-span-3 card rounded-xl p-6 flex flex-col items-center justify-center">
            <h3 class="text-xs font-semibold uppercase tracking-widest text-sand-500 mb-4 self-start">Pipeline Health</h3>
            <div id="health-gauge" class="w-full max-w-xs"></div>
            <p id="health-label" class="text-sm font-semibold mt-2"></p>
        </div>
        <!-- 4 stat cards stacked 2x2 in 2/5 width -->
        <div class="lg:col-span-2 grid grid-cols-2 gap-4">
            <div class="card card-accent rounded-xl p-5">
                <h3 class="text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Total Issues</h3>
                <p class="text-3xl font-bold text-shell-50">{{ metrics.total_issues }}</p>
            </div>
            <div class="card card-accent-petal rounded-xl p-5">
                <h3 class="text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Total Retries</h3>
                <p class="text-3xl font-bold text-petal-400">{{ metrics.total_retries }}</p>
            </div>
            <div class="card card-accent-olive rounded-xl p-5">
                <h3 class="text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Best Stage</h3>
                <p id="best-stage" class="text-lg font-bold text-olive-400"></p>
            </div>
            <div class="card card-accent-petal rounded-xl p-5">
                <h3 class="text-xs font-semibold uppercase tracking-widest text-sand-500 mb-2">Worst Stage</h3>
                <p id="worst-stage" class="text-lg font-bold text-coral-400"></p>
            </div>
        </div>
    </div>

    <!-- Charts row: donut + stage bars -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-8">
        <div class="card rounded-xl p-6">
            <h3 class="text-xs font-semibold uppercase tracking-widest text-sand-500 mb-4">Issues by Status</h3>
            <div id="issues-donut"></div>
        </div>
        <div class="card rounded-xl p-6">
            <h3 class="text-xs font-semibold uppercase tracking-widest text-sand-500 mb-4">Stage Success Rates</h3>
            <div id="stage-bars"></div>
        </div>
    </div>

    <!-- Retries chart -->
    <div class="card rounded-xl p-6 mb-8">
        <h3 class="text-xs font-semibold uppercase tracking-widest text-sand-500 mb-4">Retries by Stage</h3>
        <div id="retries-bars"></div>
    </div>
</div>

<script>
(function() {
    var stageRates = {{ metrics.stage_success_rates | tojson }};
    var issuesByStatus = {{ metrics.issues_by_status | tojson }};
    var retriesByStage = {{ metrics.retries_by_stage | tojson }};

    var COLORS = {
        neon: '#f9066f',
        coral: '#e01f2f',
        coralLight: '#e74b58',
        petal: '#d66b29',
        petalLight: '#de8954',
        olive: '#5ca35d',
        oliveLight: '#7cb67d',
        sand: '#a6a359',
        shell400: '#969389',
        shell700: '#43413e',
        shell800: '#2d2c2a',
        shell900: '#1c1b19',
    };

    var stages = ['spec', 'plan', 'build', 'verify', 'review', 'ship'];

    // --- Health Gauge ---
    var rateValues = stages.map(function(s) { return stageRates[s] || 0; });
    var avgRate = rateValues.reduce(function(a, b) { return a + b; }, 0) / rateValues.length;
    var avgPct = Math.round(avgRate * 100);

    var gaugeColor;
    var healthText;
    if (avgRate >= 0.8) { gaugeColor = COLORS.olive; healthText = 'Healthy'; }
    else if (avgRate >= 0.5) { gaugeColor = COLORS.petal; healthText = 'Degraded'; }
    else { gaugeColor = COLORS.coral; healthText = 'Critical'; }

    document.getElementById('health-label').textContent = healthText;
    document.getElementById('health-label').style.color = gaugeColor;

    new ApexCharts(document.querySelector("#health-gauge"), {
        series: [avgPct],
        chart: { height: 220, type: 'radialBar', background: 'transparent', animations: { enabled: true, easing: 'easeinout', speed: 800 } },
        plotOptions: {
            radialBar: {
                hollow: { size: '65%', margin: 0 },
                track: { background: COLORS.shell800, strokeWidth: '100%' },
                dataLabels: {
                    name: { show: false },
                    value: { fontSize: '2.5rem', fontWeight: 700, fontFamily: 'Outfit', color: gaugeColor, offsetY: 8, formatter: function(v) { return v + '%'; } }
                },
                startAngle: -135, endAngle: 135
            }
        },
        colors: [gaugeColor],
        stroke: { lineCap: 'round' },
        labels: ['Health'],
        theme: { mode: 'dark' }
    }).render();

    // --- Best/Worst Stage ---
    var best = { name: '', rate: -1 };
    var worst = { name: '', rate: 2 };
    stages.forEach(function(s) {
        var r = stageRates[s] || 0;
        if (r > best.rate) best = { name: s, rate: r };
        if (r < worst.rate) worst = { name: s, rate: r };
    });
    document.getElementById('best-stage').textContent = best.name + ' ' + Math.round(best.rate * 100) + '%';
    document.getElementById('worst-stage').textContent = worst.name + ' ' + Math.round(worst.rate * 100) + '%';

    // --- Issues Donut ---
    var statusColors = {
        'new': COLORS.shell400,
        'in-progress': COLORS.petal,
        'paused': COLORS.sand,
        'done': COLORS.olive,
        'failed': COLORS.coral
    };
    var statusLabels = Object.keys(issuesByStatus);
    var statusValues = statusLabels.map(function(k) { return issuesByStatus[k]; });
    var statusColorList = statusLabels.map(function(k) { return statusColors[k] || COLORS.shell400; });

    if (statusLabels.length > 0) {
        new ApexCharts(document.querySelector("#issues-donut"), {
            series: statusValues,
            chart: { type: 'donut', height: 280, background: 'transparent', animations: { enabled: true, easing: 'easeinout', speed: 600 } },
            labels: statusLabels,
            colors: statusColorList,
            stroke: { width: 0 },
            plotOptions: { pie: { donut: { size: '70%', labels: { show: true, total: { show: true, label: 'Total', color: COLORS.shell400, fontFamily: 'Outfit', fontSize: '0.75rem', fontWeight: 600, formatter: function(w) { return w.globals.seriesTotals.reduce(function(a, b) { return a + b; }, 0); } } } } } },
            dataLabels: { enabled: false },
            legend: { position: 'bottom', fontFamily: 'Outfit', fontSize: '0.8rem', labels: { colors: COLORS.shell400 }, markers: { width: 8, height: 8, radius: 2 } },
            tooltip: { theme: 'dark' },
            theme: { mode: 'dark' }
        }).render();
    }

    // --- Stage Success Bars ---
    var barValues = stages.map(function(s) { return Math.round((stageRates[s] || 0) * 100); });
    var barColors = barValues.map(function(v) {
        if (v >= 80) return COLORS.olive;
        if (v >= 50) return COLORS.petal;
        return COLORS.coral;
    });

    new ApexCharts(document.querySelector("#stage-bars"), {
        series: [{ name: 'Success Rate', data: barValues }],
        chart: { type: 'bar', height: 280, background: 'transparent', toolbar: { show: false }, animations: { enabled: true, easing: 'easeinout', speed: 600 } },
        plotOptions: { bar: { horizontal: true, barHeight: '60%', borderRadius: 4, distributed: true, dataLabels: { position: 'right' } } },
        colors: barColors,
        dataLabels: { enabled: true, formatter: function(v) { return v + '%'; }, style: { fontSize: '0.8rem', fontFamily: 'JetBrains Mono', fontWeight: 600, colors: ['#fff'] }, offsetX: 10 },
        xaxis: { categories: stages.map(function(s) { return s.toUpperCase(); }), max: 100, labels: { show: false }, axisBorder: { show: false }, axisTicks: { show: false } },
        yaxis: { labels: { style: { colors: COLORS.shell400, fontFamily: 'JetBrains Mono', fontSize: '0.7rem', fontWeight: 500, cssClass: 'uppercase' } } },
        grid: { show: false },
        legend: { show: false },
        tooltip: { theme: 'dark', y: { formatter: function(v) { return v + '% success'; } } },
        theme: { mode: 'dark' }
    }).render();

    // --- Retries Bars ---
    var retryStages = Object.keys(retriesByStage);
    var retryValues = retryStages.map(function(k) { return retriesByStage[k]; });

    if (retryStages.length > 0) {
        new ApexCharts(document.querySelector("#retries-bars"), {
            series: [{ name: 'Retries', data: retryValues }],
            chart: { type: 'bar', height: 200, background: 'transparent', toolbar: { show: false }, animations: { enabled: true, easing: 'easeinout', speed: 600 } },
            plotOptions: { bar: { columnWidth: '50%', borderRadius: 4, distributed: true, dataLabels: { position: 'top' } } },
            colors: retryStages.map(function() { return COLORS.petal; }),
            dataLabels: { enabled: true, style: { fontSize: '0.85rem', fontFamily: 'JetBrains Mono', fontWeight: 600, colors: [COLORS.petalLight] }, offsetY: -20 },
            xaxis: { categories: retryStages.map(function(s) { return s.toUpperCase(); }), labels: { style: { colors: COLORS.shell400, fontFamily: 'JetBrains Mono', fontSize: '0.7rem' } }, axisBorder: { show: false }, axisTicks: { show: false } },
            yaxis: { show: false },
            grid: { show: false },
            legend: { show: false },
            tooltip: { theme: 'dark' },
            theme: { mode: 'dark' }
        }).render();
    }
})();
</script>
{% endblock %}
```

**Step 2: Verify the page renders**

```bash
uv run superseded
# Visit http://localhost:8000/pipeline/metrics/dashboard
# Check: gauge renders with percentage, donut shows statuses, bars show rates, retries chart shows
# Check: no console errors
# Check: responsive at mobile width (stacks to single column)
```

**Step 3: Run existing tests**

```bash
uv run pytest tests/test_metrics.py -v
```

All 4 existing metrics tests should pass (we didn't change the route or model).

**Step 4: Commit**

```bash
git add templates/metrics.html
git commit -m "feat: visual metrics dashboard with ApexCharts"
```

---

## Task 3: Verify empty state gracefully

**Files:**
- Modify: `templates/metrics.html` (conditional rendering for empty data)

**Step 1: Test with empty database**

Delete `.superseded/state.db` (or test with fresh DB), restart server, visit metrics page.

Check that:
- Gauge shows 0% without errors
- Donut chart section hides or shows "No data" gracefully
- Retries section hides (already has `{% if metrics.retries_by_stage %}` in old template, but new JS handles empty arrays via the `if (statusLabels.length > 0)` guards)

The template already has `if` guards in the JS for empty donut and empty retries. Verify these work.

```bash
uv run superseded
# Visit http://localhost:8000/pipeline/metrics/dashboard with empty DB
```

**Step 2: Run all tests**

```bash
uv run pytest tests/test_metrics.py -v
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

**Step 3: Commit if fixes needed**

```bash
git add templates/metrics.html
git commit -m "fix: handle empty metrics state gracefully"
```

---

## Task 4: Playwright validation

**Files:**
- Create/Modify: Playwright test (if project has existing Playwright tests)

**Step 1: Run Playwright tests**

```bash
uv run superseded &
npx playwright test
```

Check that existing UI tests pass and the metrics page is accessible.

**Step 2: Manual browser verification checklist**

Visit `http://localhost:8000/pipeline/metrics/dashboard` and verify:
- [ ] Radial gauge renders with correct percentage and color
- [ ] Health label text matches threshold (Healthy/Degraded/Critical)
- [ ] Stat cards show correct numbers
- [ ] Best/Worst stage labels are populated
- [ ] Donut chart renders with correct slices and legend
- [ ] Horizontal bars show all 6 stages with correct percentages and colors
- [ ] Retries bar chart shows (if retries exist) or is hidden (if none)
- [ ] Hover tooltips work on all charts
- [ ] Page is responsive at 375px, 768px, 1280px widths
- [ ] No console errors

**Step 3: Final commit**

```bash
git add -A
git commit -m "chore: metrics dashboard verification complete"
```
