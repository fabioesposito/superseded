# Metrics Dashboard Visual Upgrade — Design

## Context

The metrics page (`/pipeline/metrics/dashboard`) currently shows raw JSON-derived data as simple stat cards and CSS progress bars. The goal is to transform it into a visual dashboard with charts that give immediate pipeline health insight at a glance.

**Constraints:**
- No backend changes — the `PipelineMetrics` model and route already pass all needed data to the template
- Client-side rendering only — charts init from Jinja2 template context
- Must match the existing dark neon aesthetic (shell, neon, coral, petal, olive, sand palette)
- Lightweight chart library via CDN (ApexCharts)

## Approach

**ApexCharts** (~115KB CDN) chosen over Chart.js for:
- Native dark theme mode
- Cleaner donut/gauge defaults
- Built-in responsive handling
- Minimal config for the chart types needed

## Layout (Hero + Details)

### Row 1: Hero — Pipeline Health Gauge

Large **radial bar chart** centered at top showing aggregate success rate:
- Aggregate = weighted average of all `stage_success_rates`
- Color: olive (≥80%), petal (50-80%), coral (<50%)
- Label below: "Healthy" / "Degraded" / "Critical"
- Subtle glow matching existing `.glow-active` animation

Flanked by **4 compact stat cards**: Total Issues, Total Retries, Best Stage, Worst Stage.

### Row 2: Two-Column Detail Charts

**Left — Issues by Status (Donut):**
- Slices colored by status: new = shell-400, in-progress = petal, paused = sand, done = olive, failed = coral
- Tooltip on hover with count + percentage
- Legend below with labels and counts

**Right — Stage Success Rates (Horizontal Bars):**
- One bar per stage (spec → ship)
- Data label showing percentage at bar end
- Color per bar: olive/petal/coral based on thresholds
- Replaces current CSS progress bars

### Row 3: Retries by Stage (Vertical Bars)

- One bar per stage that has retries (skip 0-retry stages)
- Petal (orange) color
- Count label above each bar
- Replaces current plain list

## Technical Details

### Files Changed
1. `templates/base.html` — Add ApexCharts CDN `<script>` tag
2. `templates/metrics.html` — Full rewrite of chart sections

### Files NOT Changed
- `src/superseded/routes/pipeline.py` — No changes, data already in template context
- `src/superseded/models.py` — No changes

### ApexCharts Config
- `theme.mode = 'dark'`
- `chart.background = 'transparent'` (blends into card backgrounds)
- Colors mapped from existing Tailwind config hex values
- `chart.animations.enabled = true`, `easing = 'easeinout'`
- Responsive breakpoints matching Tailwind's md/sm

### Color Mapping (Tailwind → ApexCharts)
```
neon-400:  #f9066f  (primary accent)
coral-400: #e01f2f  (failure, <50%)
petal-400: #d66b29  (warning, retries, 50-80%)
olive-400: #5ca35d  (success, ≥80%)
sand-400:  #a6a359  (labels)
shell-400: #78756e  (neutral, "new" status)
```

### Responsive
- Desktop: gauge + 4 cards on top row, two-column charts below, full-width retries
- Mobile (<768px): everything stacks single column, charts resize via ApexCharts built-in responsive config
