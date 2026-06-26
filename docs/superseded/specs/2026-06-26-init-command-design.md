# `superseded init` Command — Design

**Date:** 2026-06-26
**Status:** Approved (brainstormed)
**Scope:** Add a non-interactive `init` command that detects installed AI review CLIs (and `gh`), picks a sensible default agent + model, and writes a `.superseded.yaml` config file.

## Goals

- Let a new user run `superseded init` once and get a working config grounded in what is actually installed on their machine.
- Keep `init` fully non-interactive (scriptable, safe for CI setup snippets).
- Refuse to clobber an existing config unless `--force` is supplied.
- Keep all detection logic isolated and unit-testable, separate from click/IO.

## Non-goals

- Interactive prompts / wizard (explicitly rejected during brainstorming).
- Probing each agent CLI for its currently configured model.
- Editing an existing config in place (no merge mode).
- Detecting `git`, `uv`, or other adjacent tooling.

## Command surface

```
superseded init [--force] [--agent <name>] [--config <path>]
```

- `--force` — required to overwrite an existing `.superseded.yaml` (default: refuse and exit `2`).
- `--agent <name>` — override the detected/preference-picked agent. Must be one of `claude-code`, `opencode`, `codex`, and must report `is_available()`, otherwise exit `2`.
- `--config <path>` — write to a custom path instead of `.superseded.yaml` in the current working directory (mirrors `review --config`). Note: unlike `review --config`, the path need not exist (we are creating it), so the click option uses `dir_okay=False` without `exists=True`.

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Config written successfully. |
| `1`  | No supported AI CLI found on PATH (cannot choose an agent); no file written. |
| `2`  | Misuse: existing file without `--force`, unknown `--agent`, or selected agent not installed. |

### Example output (stderr)

```
Detected agent CLIs: claude-code, opencode
Missing: codex
Selected agent: claude-code (claude-sonnet-4-6)
gh CLI: found
Wrote .superseded.yaml
```

When `gh` is missing:
```
gh CLI: not found (PR features will be disabled)
```

All status text goes to stderr via the existing `_status` helper so stdout stays clean for piping.

## Architecture

Three new/extended pieces:

1. **`src/superseded/detection.py`** (new) — pure detection logic, no click, no filesystem writes.
2. **`src/superseded/config.py`** (extended) — add `write_config(config, path)`.
3. **`src/superseded/cli.py`** (extended) — add the `init` command composing the above.

`review`, `feedback`, and `serve` commands are unchanged.

### `detection.py`

```python
from __future__ import annotations

import shutil
from dataclasses import dataclass

from superseded.review.engine import AGENT_MAP

AGENT_PREFERENCE: tuple[str, ...] = ("claude-code", "opencode", "codex")

# Per the brainstorm: hardcoded default model per agent.
# opencode is intentionally omitted so it resolves to None (lets the
# opencode CLI pick its own default model at runtime).
DEFAULT_MODELS: dict[str, str] = {
    "claude-code": "claude-sonnet-4-6",
    "codex": "gpt-5.4-mini",
}


@dataclass(frozen=True)
class AgentStatus:
    name: str
    available: bool
    binary: str


def detect_agents() -> list[AgentStatus]:
    """Probe all registered agents; return one AgentStatus per agent (any order)."""
    statuses: list[AgentStatus] = []
    for name, cls in AGENT_MAP.items():
        agent = cls(model=None)
        binary = agent.build_command()[0]
        statuses.append(AgentStatus(name=name, available=agent.is_available(), binary=binary))
    return statuses


def detect_gh() -> bool:
    return shutil.which("gh") is not None


def pick_agent(available: list[str]) -> str | None:
    """Return the highest-preference agent name present in `available`, else None."""
    for name in AGENT_PREFERENCE:
        if name in available:
            return name
    return None


def default_model_for(agent: str) -> str | None:
    return DEFAULT_MODELS.get(agent)
```

Notes:
- Reuses `AGENT_MAP` and each agent's own `is_available()` / `build_command()[0]`, so detection never hardcodes binary names out of sync with the agents themselves.
- `AgentStatus` is a plain frozen dataclass (not pydantic) because it is an internal transport type with no validation needs; models stay in `models.py`.
- Importing `AGENT_MAP` from `review.engine` is acceptable (engine already imports agent classes; no new circular dependency, and `detection` has no other downstream importers).

### `config.py` — `write_config`

```python
def write_config(config: Config, path: Path | None = None) -> None:
    if path is None:
        path = Path(".superseded.yaml")
    data = config.model_dump(mode="json")
    text = yaml.safe_dump(data, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
```

- `model_dump(mode="json")` flattens nested `PassConfig` into a plain dict for clean YAML output.
- Atomic write via temp file + `os.replace` prevents half-written configs on interruption.
- `sort_keys=False` preserves the field declaration order (agent, model, passes, ...) which matches the documented structure.
- No comments injected (round-tripping comments through `safe_dump` is fragile; field names are self-documenting and already familiar from `Config`).

### `cli.py` — `init` command

```python
@cli.command()
@click.option("--force", is_flag=True, help="Overwrite an existing .superseded.yaml")
@click.option("--agent", default=None, help="Force a specific agent (claude-code, opencode, codex)")
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to write (default: .superseded.yaml in cwd)",
)
def init(force: bool, agent: str | None, config_path: Path | None) -> None:
    """Detect installed AI CLIs and write a .superseded.yaml config file."""
    _run_init(force=force, agent_override=agent, config_path=config_path)
```

`_run_init` flow:

1. `target = config_path or Path(".superseded.yaml")`.
2. If `target.exists()` and not `force` → `_status(f"Error: {target} already exists. Use --force to overwrite.")`; `sys.exit(2)`.
3. `statuses = detect_agents()`; build `available = [s.name for s in statuses if s.available]` and `missing = [s.name for s in statuses if not s.available]`. Print availability summary to stderr.
4. `gh_ok = detect_gh()`; print found / not-found warning (non-fatal).
5. Determine chosen agent:
   - If `agent_override` is not None:
     - Validate `agent_override in AGENT_MAP`; else exit `2` with the valid list.
     - Verify the corresponding `Agent(model=None).is_available()`; else exit `2` with "selected agent not installed".
     - `chosen = agent_override`.
   - Else: `chosen = pick_agent(available)`; if `None`, print actionable error (no AI CLI found) and exit `1`.
6. `model = default_model_for(chosen)`.
7. `cfg = Config(agent=chosen, model=model)` (all other fields keep their defaults).
8. `write_config(cfg, target)`.
9. `_status(f"Selected agent: {chosen}" + (f" ({model})" if model else ""))`.
10. `_status(f"Wrote {target}")`.

## Testing

### New: `tests/test_init.py`

**Detection unit tests** (mock `shutil.which` via monkeypatch):

- `test_detect_agents_returns_all_three` — all three names present regardless of availability.
- `test_detect_agents_available_flag` — monkeypatch `which` to return `None` for `codex`; assert `codex` status has `available=False`.
- `test_pick_agent_returns_highest_preference` — available `[opencode, codex]` → returns `opencode`; available `[claude-code, codex]` → returns `claude-code`.
- `test_pick_agent_none_when_empty` — returns `None`.
- `test_default_model_for_known_agents` — claude-code → `claude-sonnet-4-6`, codex → `gpt-5.4-mini`.
- `test_default_model_for_opencode_is_none`.
- `test_detect_gh_true` / `test_detect_gh_false` — monkeypatched `which`.

**CLI integration tests** using `click.testing.CliRunner` and monkeypatched detection functions:

- `test_init_happy_path` — monkeypatch `detect_agents`/`detect_gh` to report claude-code available; invoke `init`; assert exit `0`, file exists, `load_config(file).agent == "claude-code"`, all passes enabled.
- `test_init_refuses_overwrite_without_force` — pre-create the target; invoke without `--force`; assert exit `2`, file contents unchanged.
- `test_init_force_overwrites` — same setup with `--force`; assert exit `0`, contents reflect new detection.
- `test_init_no_agents_exit_1` — monkeypatch all unavailable; assert exit `1`, no file written.
- `test_init_agent_override_unknown` — `--agent bogus`; assert exit `2` with valid-list message.
- `test_init_agent_override_not_installed` — `--agent codex` while codex unavailable; assert exit `2`.
- `test_init_agent_override_installed` — `--agent codex` while codex available; assert exit `0`, `model == "gpt-5.4-mini"`.
- `test_init_gh_missing_still_succeeds` — agents available, `gh` not; assert exit `0` with warning on stderr.

### Extended: `tests/test_config.py`

- `test_write_config_round_trip` — `write_config(c, path)` then `load_config(path)` reproduces every field (including nested `passes`).
- `test_write_config_no_temp_lingering` — after success, no `*.tmp` file remains beside the target.
- `test_write_config_nested_passes_block` — raw file text contains a top-level `passes:` key.

All tests follow existing patterns: `from __future__ import annotations`, monkeypatching rather than real subprocess/PATH mutation, no network.

## Documentation updates

- `AGENTS.md` "Architecture notes" gains a one-line mention of `init` and `detection.py`.
- `AGENTS.md` "Commands" block gains the `init` example.
- The `review` command's existing `--config` flag is unchanged; `init --config` is a deliberately separate option (writes instead of reads, no `exists=True`).

## Risk / trade-offs

- **No merge with existing config.** Re-running `init` after manually editing the file requires `--force` and discards edits. Accepted: keeps the command simple and predictable.
- **Hardcoded model strings** can drift from agent class defaults. Mitigation: `DEFAULT_MODELS` lives in `detection.py` next to `AGENT_PREFERENCE`, and the agent classes remain the source of truth for runtime defaults — `init` only seeds the *written* config.
- **No probe for the agent's live-configured model.** Explicitly rejected in brainstorming; would require subprocess calls and per-agent probing, adding latency and failure modes for marginal value.
