# Language-Agnostic Context Augmentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `usage_retrieval` extract symbols and find callers for diffs in any programming language, by routing unknown file extensions to the existing generic identifier regex instead of skipping them.

**Architecture:** Two surgical edits in one module (`src/superseded/context/usage_retrieval.py`). `extract_symbols` already has a `_GENERIC_RE` fallback selected by its `else` branch when `lang` is anything other than python/js/ts/go — and it already handles `lang=None`. The bug is purely upstream: `retrieve_usages` skips entries whose extension isn't in `_LANG_MAP` before `extract_symbols` is ever called. Removing that skip guard (plus fixing a hardcoded `"python"` in a fallback path) makes the whole pipeline language-agnostic. No new deps, no changes to curated Python/JS/TS/Go support or to `static_analysis.py` (which already degrades correctly via gitleaks for unknown langs).

**Tech Stack:** Python 3.14+, pytest (asyncio_mode=auto), ruff. Run everything via `uv run`.

**Reference spec:** `docs/superseded/specs/2026-06-26-language-agnostic-review-design.md`

---

## File Structure

- **Modify:** `src/superseded/context/usage_retrieval.py` — remove the `if not lang: continue` skip guard in `retrieve_usages` (~line 127); change the no-entries fallback from `extract_symbols(diff, "python")` to `extract_symbols(diff, None)` (~line 136).
- **Modify (tests):** `tests/test_context_usage.py` — add two tests covering unknown-language symbol extraction and the generic no-entries fallback.

No new files. `extract_symbols` itself is unchanged — its `else` branch already routes `None`/unknown langs to `_GENERIC_RE`.

---

### Task 1: Unknown-language files reach ripgrep via generic symbol extraction

**Files:**
- Test: `tests/test_context_usage.py`
- Modify: `src/superseded/context/usage_retrieval.py:123-129`

**Context:** `retrieve_usages` iterates diff entries and does `lang = _LANG_MAP.get(Path(entry["file"]).suffix)`. For an unknown extension like `.rs`, `lang` is `None`, and `if not lang: continue` skips the entry entirely. When *all* entries are unknown-language, `symbols` stays empty and `retrieve_usages` returns `None` early — ripgrep is never invoked, so the AI gets zero caller-context for Rust/Java/Ruby/etc. diffs. The fix is to drop the skip guard so `lang=None` flows into `extract_symbols`, whose `else` branch already selects `_GENERIC_RE`.

- [ ] **Step 1: Write the failing test (end-to-end via retrieve_usages)**

First, add a direct contract test that locks the pre-existing behavior Task 1 relies on — that `extract_symbols` with `lang=None` selects `_GENERIC_RE`. Add to `tests/test_context_usage.py` near the other `test_extract_symbols_*` tests:

```python
def test_extract_symbols_generic_for_unknown_lang():
    """lang=None (or any unrecognised value) must route to the generic regex,
    returning identifiers of 4+ chars while filtering keywords."""
    diff = "@@ -1,1 +1,3 @@\n+fn process_request() {\n+    self\n+}\n"
    syms = extract_symbols(diff, None)
    assert "process_request" in syms
    assert "self" not in syms  # filtered as a keyword
```

Run: `uv run pytest tests/test_context_usage.py::test_extract_symbols_generic_for_unknown_lang -v`
Expected: PASS already (this is a characterization test for existing behavior — it documents the contract Task 1 depends on). If it fails, `extract_symbols`'s `else` branch is not handling `None` and must be fixed first.

Then add the behavior-change test after `test_multi_file_diff_extracts_symbols_from_all_files`:

```python
def test_unknown_language_uses_generic_symbol_extraction(monkeypatch):
    """A diff with only unknown-language files (e.g. .rs) must still extract
    symbols via the generic regex and reach ripgrep, rather than being skipped."""
    calls = []

    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="lib.rs:10: process_request()\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    diff = (
        "diff --git a/src/lib.rs b/src/lib.rs\n"
        "@@ -1,1 +1,3 @@\n"
        "+fn process_request(input) {\n"
        "+    handle_response()\n"
        "+}\n"
    )
    result = retrieve_usages(diff, Path("/repo"))

    assert calls, "ripgrep was never invoked for unknown-language diff"
    searched_patterns = [cmd[4] for cmd in calls]
    assert any(
        "process_request" in p for p in searched_patterns
    ), "generic symbol was not extracted from the .rs file"
    assert result is not None
    assert "process_request" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_usage.py::test_unknown_language_uses_generic_symbol_extraction -v`
Expected: FAIL — `assert calls` fails because the `.rs` entry is skipped, no symbols are extracted, and `retrieve_usages` returns `None` without calling ripgrep.

- [ ] **Step 3: Remove the skip guard**

In `src/superseded/context/usage_retrieval.py`, find this block inside `retrieve_usages`:

```python
        for entry in entries:
            lang = _LANG_MAP.get(Path(entry["file"]).suffix)
            if not lang:
                continue
            for sym in extract_symbols(entry["diff"], lang):
```

Remove the two guard lines so it becomes:

```python
        for entry in entries:
            lang = _LANG_MAP.get(Path(entry["file"]).suffix)
            for sym in extract_symbols(entry["diff"], lang):
```

Now `lang` is `None` for unknown extensions, and `extract_symbols`'s `else` branch selects `_GENERIC_RE`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_usage.py::test_unknown_language_uses_generic_symbol_extraction -v`
Expected: PASS.

- [ ] **Step 5: Run the full usage-retrieval test suite to confirm no regression**

Run: `uv run pytest tests/test_context_usage.py tests/test_usage_retrieval.py -v`
Expected: all PASS (curated Python/JS/TS/Go behavior is untouched).

- [ ] **Step 6: Commit**

```bash
git add tests/test_context_usage.py src/superseded/context/usage_retrieval.py
git commit -m "feat: extract symbols for unknown languages via generic regex

Unknown file extensions (e.g. .rs, .java, .rb) were skipped before symbol
extraction, so ripgrep usage retrieval never ran for them. Drop the skip
guard so they flow through extract_symbols, which already routes None/unknown
langs to the generic identifier regex."
```

---

### Task 2: No-entries fallback uses generic, not python

**Files:**
- Test: `tests/test_context_usage.py`
- Modify: `src/superseded/context/usage_retrieval.py:136`

**Context:** When `parse_diff_files` returns no entries (a raw hunk with no `diff --git` header), `retrieve_usages` hits an `else` branch that hardcodes `extract_symbols(diff, "python")`. That arbitrarily applies Python's symbol regex (and its `case_insensitive=True` dedup) to non-Python content. Changing it to `extract_symbols(diff, None)` routes it through the same generic fallback as Task 1. This is observable: with `"python"`, `case_insensitive=True` dedupes identifiers that differ only by case (`Widget` and `widget` collapse to one); with `None`, `case_insensitive=False` keeps both, so ripgrep searches for both.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_context_usage.py`:

```python
def test_no_entries_fallback_is_language_agnostic(monkeypatch):
    """When no file entries parse, the fallback symbol extraction must use the
    generic regex (case-sensitive), not Python's (case-insensitive). With
    case-insensitive dedup, `Widget` and `widget` collapse to one symbol; the
    generic path keeps both, so ripgrep should search for both."""
    calls = []

    def fake_run(cmd, **kwargs):
        if "rg" in cmd[0]:
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="f:1: Widget widget\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    # Raw hunk, no diff --git header -> parse_diff_files returns no entries.
    diff = "@@ -1,1 +1,2 @@\n+Widget widget\n"
    retrieve_usages(diff, Path("/repo"))

    assert calls, "ripgrep was never invoked"
    pattern = calls[0][4]  # the rg pattern is the 5th element: rg -n --max-count 4 <pattern> ...
    assert "Widget" in pattern
    assert "widget" in pattern
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_usage.py::test_no_entries_fallback_is_language_agnostic -v`
Expected: FAIL — under the current `"python"` fallback, `case_insensitive=True` dedupes `Widget`/`widget` to a single symbol, so only one of them appears in the rg pattern. The assertion on the missing casing fails.

- [ ] **Step 3: Switch the fallback to generic**

In `src/superseded/context/usage_retrieval.py`, find the `else` branch in `retrieve_usages`:

```python
    else:
        changed_files = []
        symbols = extract_symbols(diff, "python")
```

Change `"python"` to `None`:

```python
    else:
        changed_files = []
        symbols = extract_symbols(diff, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_usage.py::test_no_entries_fallback_is_language_agnostic -v`
Expected: PASS.

- [ ] **Step 5: Run the full test file to confirm no regression**

Run: `uv run pytest tests/test_context_usage.py tests/test_usage_retrieval.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_context_usage.py src/superseded/context/usage_retrieval.py
git commit -m "fix: use generic symbol extraction in no-entries fallback

The raw-hunk fallback hardcoded the Python symbol regex, which
case-insensitively deduped identifiers. Use the generic regex instead so the
fallback is language-agnostic."
```

---

### Task 3: Lint, format, and full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Lint**

Run: `uv run ruff check src/ tests/`
Expected: no errors. (If `usage_retrieval.py` now has an unused name after removing the guard — e.g. a now-unused import — fix it; the only change was deleting two lines, so no new unused imports are expected.)

- [ ] **Step 2: Format check / apply**

Run: `uv run ruff format src/ tests/`
Expected: no changes (or auto-formatted in place).

- [ ] **Step 3: Full test suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 4: (Optional) Manual smoke test**

Run: `uv run superseded review --diff HEAD~1..HEAD --format json`
Expected: runs without error. (Context-augmentation modules are best-effort and failures are logged-and-skipped, not fatal.)
