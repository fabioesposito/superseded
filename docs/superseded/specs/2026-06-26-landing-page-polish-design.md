# Landing Page Polish — Design Spec

## Overview

Polish the Superseded landing page (`index.html`) to match the clean, content-focused quality of simplepixelart.com while keeping the existing dark navy + neon accent color palette.

## Goals

- Reduce cognitive load: fewer sections, clearer hierarchy
- Remove game metaphors ("Stage Select", "Character Select", "Continue?", "Bonus Stage")
- Remove heavy visual effects (CRT scanlines, pixel star animations)
- Improve breathing room and spacing
- Keep the retro color palette and pixel font (used sparingly)

## Constraints

- Single-file implementation (`index.html` with inline `<style>`)
- No external dependencies beyond Google Fonts
- Mobile-responsive (current breakpoints: 900px, 768px, 480px)
- Keep the SVG logo as-is

## Page Structure

### 1. Navigation

**Current:** Fixed nav with 4 links (STAGES, POWER-UPS, AGENTS, SERVER) + GitHub + START buttons, mobile hamburger menu.

**New:** Fixed nav with 3 links (How it works, Features, Docs) + GitHub button. Cleaner, less busy. Docs link points to the GitHub repo README.

- Remove "SERVER" and "AGENTS" links
- Rename remaining links to plain language
- Keep mobile hamburger menu
- Remove the "START" CTA from nav (redundant with hero)

### 2. Hero Section

**Current:** Badge, large SVG logo, headline, subtext, 2 CTA buttons, install command. 160px top padding.

**New:** Keep the same elements but increase breathing room. Remove the SVG logo (it's large and adds visual noise — the headline is the focal point). Keep badge, headline, subtext, CTAs, install command.

- Increase top padding to ~180px
- Headline: pixel font, keep neon text-shadow glow
- Subtext: monospace font, muted color
- CTAs: "Get Started" (primary) + "View on GitHub" (ghost)
- Install command: terminal-style box with copy button
- Use `pip install superseded` as the primary install command (simpler than git clone)

### 3. How It Works

**Current:** 7 numbered stages in a vertical timeline with dashed connector line.

**New:** 4 steps in a horizontal grid (responsive: 4 cols → 2 cols → 1 col). Each step has a number badge, title, and one-line description.

Steps:
1. **Fetch** — Pull diff from a GitHub PR or local git range
2. **Context** — Load feedback memory, run static analysis, gather cross-file usages
3. **Review** — 5 AI passes run in parallel (security, correctness, performance, style, architecture)
4. **Output** — Terminal table, JSON, markdown, or inline PR comments

The pass tags (SECURITY, CORRECTNESS, etc.) appear under step 3 as small colored badges.

### 4. Features

**Current:** 10 items in an auto-fit grid with suit icons.

**New:** 6 items in a 3-column grid (responsive: 3 → 2 → 1 col). Each card has an icon, title, and one-line description. Cards have subtle border and hover state.

Features to keep (the best 6):
1. Multi-pass review — 5 specialized passes with domain-specific prompts
2. Feedback memory — dismiss a finding once, it won't appear again
3. GitHub integration — inline PR review comments, critical issues request changes
4. Pluggable agents — Claude Code, OpenCode, or Codex
5. Structured output — JSON, markdown, or terminal table
6. CI-native — Docker-based GitHub Action, runs on every PR

Removed: Server mode, Static analysis pre-pass, Cross-file usage retrieval, Reasoning trail (these move to docs).

### 5. CTA Section

**Current:** "INSERT COIN TO START" box with install commands and action buttons.

**New:** Clean centered section: "Get started in 30 seconds" heading, two install commands (pip + first review), two action buttons (Read the Docs + Star on GitHub).

### 6. Footer

**Current:** Logo + copyright + GitHub/Issues links.

**New:** Simplified: copyright line + links (GitHub, Docs, Issues). No logo in footer (redundant with nav).

## Visual Design

### Colors (unchanged)

Keep the existing CSS custom properties:
- `--bg: #080810` (dark navy)
- `--bg-card: #12122a`
- `--cyan: #00f0ff`, `--green: #00ff41`, `--magenta: #ff5eff`, `--yellow: #ffd700`, `--red: #ff4466`, `--orange: #ff8833`

### Typography

- **Headings:** 'Press Start 2P' (pixel font) — used for h1 and section titles only, at smaller sizes
- **Body:** 'Fira Code' (monospace) — used for all body text, descriptions, nav links
- **Terminal:** 'VT323' — used for install commands and code snippets only
- Remove 'Press Start 2P' from nav links, buttons, badges, labels — use 'Fira Code' instead

### Effects to Remove

- CRT scanline overlay (`body::after`)
- Pixel star animations (`.bg-stars`)
- Button glow animation (`btn-glow`)
- Shield pulse animation (`shield-pulse`)
- CRT terminal demo section entirely

### Effects to Keep (subtle)

- Background dot grid (`.bg-grid`) — reduce opacity to 0.15
- Scroll reveal (`.reveal`) — keep as-is
- Hover states on cards and buttons — simpler, no glow effects

### Spacing

- Section padding: 100px top/bottom (desktop), 60px (mobile)
- Max width: 1040px container
- Generous gaps between grid items (16-20px)

## Responsive Behavior

Keep existing breakpoints but simplify:
- **Desktop (>900px):** Full layouts
- **Tablet (768-900px):** 2-column grids
- **Mobile (<768px):** Single column, hamburger nav
- **Small mobile (<480px):** Tighter padding

## What Moves to Docs

These sections are removed from the landing page and should exist in the GitHub README or a dedicated docs page:
- Agent details (Claude Code, OpenCode, Codex with auth info)
- Server mode setup (GitHub App, webhooks, concurrency)
- Static analysis pre-pass details
- Cross-file usage retrieval details
- Reasoning trail details

## Implementation Notes

- Single file: edit `index.html` in place
- Keep the `<script>` block for copy-to-clipboard, smooth scroll, and scroll reveal
- No new JS needed
- The SVG logo is kept in the nav but removed from the hero
