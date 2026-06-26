# Language-Agnostic Context Augmentation

**Date:** 2026-06-26
**Status:** Approved design, pending implementation

## Problem

Superseded reviews diffs by shelling out to AI CLIs (claude-code, opencode, codex)
across five passes (security, correctness, performance, style, architecture). Those
passes are already language-agnostic — they consume the raw diff and produce
findings regardless of source language.

The gap is in **context augmentation**. Two modules add grounding to every pass
prompt and are coupled to a fixed language set (Python, JS/TS, Go):

1. `src/superseded/context/usage_retrieval.py` — extracts symbols from the diff and
   finds their callers via ripgrep.
2. `src/superseded/context/static_analysis.py` — runs linters/formatters.

For any language outside {Python, JS/TS, Go} (e.g. Rust, Java, Ruby, C#, Kotlin),
the app silently produces **zero caller-context**: unknown file extensions are
skipped at the `_LANG_MAP` lookup in `usage_retrieval.py`, so no symbols are
extracted and ripgrep is never invoked for usage retrieval. The AI therefore
reviews the diff with no awareness of how changed symbols are used elsewhere.

## Goal

Make context augmentation degrade gracefully for **any** programming language, with
**no new dependencies**. Curated support for the existing languages is preserved;
unknown languages fall back to generic handling rather than being dropped.

## Non-Goals

- Adding curated linter packs for new languages (Rust/clippy, Java/checkstyle,
  Ruby/rubocop, etc.).
- Introducing a structural parser such as tree-sitter.
- Changing the AI review passes themselves (they already are language-agnostic).

## Design

### Scope: `src/superseded/context/usage_retrieval.py` only

Two changes, both inside one module.

#### Change 1 — Always extract symbols; route unknown extensions to generic

Today, `retrieve_usages` iterates over diff entries and does:

```python
lang = _LANG_MAP.get(Path(entry["file"]).suffix)
if not lang:
    continue
for sym in extract_symbols(entry["diff"], lang):
    ...
```

Unknown extensions (`.rs`, `.java`, `.rb`, `.cs`, …) return `None` and the entry is
skipped, so no symbols are collected and ripgrep usage retrieval is skipped for the
whole diff when every entry is unknown-language.

`extract_symbols` already has a generic fallback (`_GENERIC_RE`, an identifier
regex) selected by its `else` branch whenever `lang` is not python/js/ts/go — and
that branch already fires for `lang=None`. The only reason it is never reached for
unknown languages is that unknown entries are skipped upstream before
`extract_symbols` is ever called.

The fix is therefore a single-line change: drop the `if not lang: continue` guard so
that unknown extensions (which yield `None`) flow through to `extract_symbols`,
which selects `_GENERIC_RE`:

```python
lang = _LANG_MAP.get(Path(entry["file"]).suffix)  # None for unknown extensions
for sym in extract_symbols(entry["diff"], lang):
    ...
```

`extract_symbols` needs no modification — its `else` branch already handles `None`.
The `case_insensitive` flag is already `False` for unknown langs (it keys off
`lang in ("python", "js", "ts")`), so no change is needed there either.

`_GENERIC_RE` matches `\b([A-Za-z_]\w{3,})\b` — identifiers of 4+ chars, with
`_KEYWORDS` filtering out common language keywords across Python/JS/Go. This is
deliberately coarse: it will surface both definitions and references, but
downstream caller-finding via ripgrep only needs symbol *names* to grep for, so
precision of extraction is not critical. Recall matters more than precision here.

#### Change 2 — Fix the no-entries fallback

When no diff entries parse (line ~136), the fallback hardcodes Python:

```python
else:
    changed_files = []
    symbols = extract_symbols(diff, "python")
```

Change `"python"` → `None` so this path uses the same generic fallback:

```python
else:
    changed_files = []
    symbols = extract_symbols(diff, None)
```

### No change to `static_analysis.py`

For unknown languages, `_languages_in_files` returns an empty set, so
`detected_langs` is empty and only tools whose `languages` includes `LANG_ANY`
run. That is exactly `GitleaksTool` (secret scanning), which is genuinely
language-agnostic and still useful. This already degrades correctly; no curated
linter exists for arbitrary languages without the dependencies that were ruled
out, so we accept "secrets scan only" for uncurated languages.

### What stays the same

- The five AI review passes (already language-agnostic).
- Curated linters for Python/JS/TS/Go in `static_analysis.py`.
- Curated symbol regexes (`_PYTHON_SYMBOL_RE`, `_JS_SYMBOL_RE`, `_GO_SYMBOL_RE`)
  in `usage_retrieval.py`. The generic fallback only engages for languages
  outside this set.
- `_KEYWORDS` filter (covers Python/JS/Go keywords; harmless for other languages).

## Testing

`tests/` already mocks ripgrep and `extract_symbols`. Add cases for:

- A diff containing only unknown-language files (e.g. `.rs`, `.java`) produces
  non-empty symbols via the generic regex and reaches the ripgrep path (mocked).
- `extract_symbols` with `lang=None` selects `_GENERIC_RE` and returns identifiers.
- The no-entries fallback path uses the generic regex, not the Python one.

## Risks

- **Generic regex recall over precision.** `_GENERIC_RE` may surface noisy
  identifiers for some languages, increasing the symbol list. Mitigation:
  `MAX_SYMBOLS` (25) caps the list, and the `USAGE_BUDGET` (6000 chars) caps total
  output. Existing behaviour for curated languages is unaffected.
- **No regression risk for curated languages** — the `_LANG_MAP` lookups and
  tailored regexes for Python/JS/TS/Go are untouched.

## Out of Scope / Future Work

- A configurable per-language linter/symbol registry in `.superseded.yaml`
  (config-driven approach) could replace the hardcoded lists later.
- tree-sitter-based structural extraction for richer context on any language.
