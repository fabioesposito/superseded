# Landing Page Redesign — Enterprise SaaS

**Date:** 2026-07-01

## Context

The existing `index.html` uses a retro terminal/cyberpunk aesthetic (pixel fonts, neon colors, dark background). Stakeholders want an enterprise-grade landing page that positions Superseded as a premium developer tool.

## Design Direction

**Aesthetic:** Clean, premium SaaS (Stripe/Vercel DNA)
- Light theme, warm white backgrounds, generous whitespace
- Deep navy primary, electric blue accent, warm grays
- Refined serif for display headlines, clean geometric sans for body
- Scroll-triggered animations, subtle hover interactions
- No terminal/retro vibes — belongs next to Stripe, Vercel, Linear

## Narrative

The hero message: **"Code review that learns from your team."**

The feedback loop is the centerpiece. Every dismissed finding trains the system, and code quality compounds over time.

## Sections

### 1. Hero
- Headline: "Code review that learns from your team."
- Sub: "5 parallel AI review passes. Every dismissed finding makes the next review sharper. Code quality compounds."
- CTAs: "Get Started" (primary) + "View on GitHub" (secondary)
- Install command in a clean code pill
- Visual: animated quality curve (SVG line chart rising over time)

### 2. The Problem
- "Most AI code review says the same thing twice."
- Three pain points: repetitive findings, no memory, generic prompts
- Icon + text cards

### 3. The Feedback Loop (Centerpiece)
- Headline: "Every review raises the bar."
- 4-step horizontal flow: Review → Dismiss → Learn → Improve
- Visual emphasis: most designed section, subtle background gradient
- Supporting stat about false positive reduction

### 4. How It Works
- "5 specialized passes. One parallel run."
- Cards: Security, Correctness, Performance, Style, Architecture
- Color-coded badges

### 5. Features Grid
- GitHub integration, Pluggable agents, CI-native, Server mode, Structured output, Static analysis
- 2×3 grid, clean hover cards

### 6. CTA
- "Start reviewing in 30 seconds"
- Install commands
- Docs + GitHub links

### 7. Footer
- Minimal: copyright, GitHub, Docs, Issues

## Technical

- Single HTML file, no build step (replaces existing `index.html`)
- All CSS inline in `<style>` block
- Vanilla JS for scroll animations and interactions
- Responsive: mobile-first breakpoints at 768px and 480px
- Google Fonts for typography (serif display + geometric sans)
