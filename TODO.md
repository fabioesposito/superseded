# TODO

Outstanding issues from the code review (commit `9e494c9`). Criticals and Important functional/spec gaps are resolved; what remains is larger features, self-contained optimizations, and low-stakes polish.

## Functional gaps

- [x] **Resolved-thread detection via GraphQL.** `memory/feedback.py` removed the bogus `._resolved` REST field (it never existed on PR review comments), so the "check if past review comments have been resolved" requirement (`docs/superseded/specs/2026-06-24-code-review-tool-design.md:292`) is still unmet. Implement via GraphQL `PullRequestReviewThread.isResolved` — needs a GraphQL client path, auth reuse, and pagination. The `-1` reaction branch still works, so learn-back isn't broken; this adds the second intended signal.
- [ ] **Batch ripgrep symbol lookups.** `context/usage_retrieval.py:153` runs one `rg` per symbol (worst case 25 × 15s = 375s, exceeding the 300s agent budget). Collapse into a single `rg` with alternation regex `\b(sym1|sym2|…)\b`, or parallelize. Changes output grouping (one block vs. per-symbol) and budget logic — do as a focused change, not a drive-by.
- [ ] **Reasoning-trail design spec is missing.** The grounded spec references `docs/superseded/specs/2026-06-24-reasoning-trail-design.md` but the file doesn't exist. The feature (Finding.reasoning, DB column + migration, dismissed-findings learn-back, collapsible rendering) shipped with no spec. Write it retroactively.

## Spec compliance (low-stakes)

- [x] **`installation_config` table.** `server-mode-design.md:321-330` marks it "optional, for future use" — not a gap, but document the omission in the plan if not built.
- [ ] **Static budget truncation drops whole tool blocks.** `context/static_analysis.py:276-287` drops an entire tool's block if it alone exceeds `STATIC_BUDGET`, losing all its findings. Spec wording says "N more findings omitted"; impl says "N tool output(s) omitted". Switch to per-finding truncation and match the spec tail string.
- [ ] **`TOOLS` not sorted alphabetically.** `context/static_analysis.py:210-220`; spec testing plan calls out "aggregate block ordering (alphabetical by name)". Add the sort + a test.
- [ ] **Symbol cap is first-added-first.** `context/usage_retrieval.py` keeps the first `MAX_SYMBOLS` symbols; spec wants "most-recently-added first" so the focal change is retained. Reverse the retention.
- [ ] **Case-sensitive dedupe for all languages.** Spec wants case-insensitive dedupe for Python/JS/TS (`MyClass`/`myclass`). Branch the `seen` set on language.
- [ ] **Keyword blocklist is hardcoded.** `context/usage_retrieval.py:40-91` misses `match`, `case`, `del`, `nonlocal`, `assert`, `global`. Use `keyword.kwlist` as the spec specifies.

## Optimizations / polish

- [ ] **`_sign_jwt` caching.** `server/github.py:32-39` re-signs the JWT on every installation-token call. JWTs are valid 10 min; a small TTL cache cuts signing overhead.
- [ ] **`ServerConfig` strictness.** `server/config.py:20-30` lets `app_id=0` (default) bypass required-field checks so `ServerConfig()` works without a key file. Decide whether "unset" should be a distinct state, then tighten (breaks `test_server_config_defaults` + the fixture pattern).
- [ ] **Semaphore acquired around `get_installation_token`.** `server/worker.py:66-69` — a network call holds a concurrency slot. Acquire after token fetch to maximize parallelism.
- [ ] **`record_finding` uses `INSERT OR IGNORE`.** `memory/store.py:75` — re-reviewing the same code never updates severity/description/reasoning for an existing finding id. Decide whether stability or freshness wins.
- [x] **GitHub review comments can fail silently** if `f.line`/`f.end_line` fall outside the PR diff hunk (`output/github_pr.py:16-23`). Catch `CalledProcessError` and retry out-of-range comments as top-level review body.
- [ ] **`test_review_continues_when_one_pass_fails`** is marked `@pytest.mark.asyncio` (`tests/test_engine.py:73`) but the body is synchronous. Harmless under `asyncio_mode=auto` but misleading.
- [ ] **`checkout.py:8-14` accepts `base_ref` but never uses it** (dead parameter). Either use it for a local `git diff base..head` (drops the `fetch_pr_diff` API call) or drop it from the signature + spec.
