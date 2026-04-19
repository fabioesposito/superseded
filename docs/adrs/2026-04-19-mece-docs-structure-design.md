---
title: MECE Docs Structure
category: adrs
summary: Restructure docs/ into MECE categories with YAML frontmatter and categorized ContextAssembler output
tags: [docs, context-assembly, mece]
date: 2026-04-19
---

# MECE Docs Structure Design

## Problem

The `docs/` folder is flat: 3 markdown files + a `plans/` folder with 25 dated design docs.
`ContextAssembler._build_docs_index_layer()` produces a flat bullet list of filenames and
first lines. Agents receive an undifferentiated index — they can't distinguish architecture
docs from guides or decision records. As docs grow, the index becomes noise.

## Approach

Restructure `docs/` using the MECE principle (Mutually Exclusive, Collectively Exhaustive)
with YAML frontmatter on every doc file. Enhance ContextAssembler to produce a categorized
index grouped by doc type.

## Folder Structure

```
docs/
├── architecture/          # System design, component diagrams, data flow
├── guides/                # How-to docs for humans and agents
├── adrs/                  # Architectural Decision Records (renamed from plans/)
└── operations/            # Runbooks, deployment, troubleshooting
```

Each folder is MECE: no doc can belong in two categories.

## YAML Frontmatter

Every doc file:

```yaml
---
title: Human-readable title
category: architecture | guides | adrs | operations
summary: One-line description used in the index
tags: [optional, filtering, tags]
date: YYYY-MM-DD
---
```

- `category` must match the folder the file is in
- `summary` replaces the current first-line extraction heuristic
- `tags` are optional, for future filtering
- `date` is mandatory for ADRs, optional elsewhere

## ContextAssembler Changes

`_build_docs_index_layer()` enhanced to:
1. Read YAML frontmatter from each `.md` file (via PyYAML, already a dependency)
2. Group docs by `category` field
3. Sort within each group by date (descending for ADRs, alphabetical elsewhere)
4. Output a categorized index instead of a flat list
5. Fall back to current first-line extraction for files without frontmatter

### Current output
```
## Documentation Index
- docs/multi-repo.md: How to configure multi-repo pipelines
- docs/tickets.md: Ticket format
```

### New output
```
## Documentation Index

### Architecture
- multi-repo.md: How Superseded fans out pipeline stages across multiple repositories

### Guides
- user-guide.md: Getting started and daily usage
- tickets.md: Ticket format and frontmatter reference

### ADRs
- 2026-04-11-harness-design.md: Harness architecture decisions

### Operations
- setup.md: Development environment setup
```

## Migration

### Files to move
- `docs/multi-repo.md` → `docs/architecture/multi-repo.md`
- `docs/tickets.md` → `docs/guides/tickets.md`
- `docs/user-guide.md` → `docs/guides/user-guide.md`
- `docs/plans/*.md` (25 files) → `docs/adrs/*.md`

### New stubs to create
- `docs/architecture/pipeline.md` — pipeline engine design
- `docs/architecture/agent-harness.md` — harness features and flow
- `docs/operations/setup.md` — development environment setup
- `docs/operations/troubleshooting.md` — common issues

### Frontmatter
Added to every file (existing + stubs) before migration.

## Multi-repo Impact

`ContextAssembler._build_docs_index_layer()` already supports per-repo docs via the
`repo` parameter. The same categorized output applies to target repos that have a `docs/`
folder. Repos without `docs/` are unaffected.

## Testing

- Unit tests for frontmatter parsing (valid, missing, malformed YAML)
- Unit tests for categorized output from ContextAssembler
- Integration test: assembled prompt contains categorized sections
- Verify existing tests still pass after file moves
