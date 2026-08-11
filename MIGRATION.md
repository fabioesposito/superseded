# Migrating to superseded v0.6.0

v0.6.0 replaces the external-AI-CLI harness (claude-code, codex, opencode) with
a direct DeepSeek API call. The review engine, prompts, dedup, verification,
context gathering, memory store, and output formats are unchanged — only the
model-calling layer and the sandbox machinery around it changed.

## Required: set the DeepSeek API key

```bash
export SUPERSEDED_DEEPSEEK_API_KEY=sk-...
```

Get a key at <https://platform.deepseek.com>. The same env var is read by
`superseded review`, `superseded serve`, and the GitHub Action.

## `.superseded.yaml`

Rename `agent:` → `provider:`:

```yaml
# Before
agent: opencode
model: null

# After
provider: deepseek
model: deepseek-v4-flash   # or null to use the provider default
```

If your existing YAML has `agent: opencode`, `agent: claude-code`, or
`agent: codex`, superseded will refuse to start with a hard error. Set
`provider: deepseek` and configure the env var above.

The `sandbox:` key (if present) is ignored (a `DeprecationWarning` is emitted)
— direct-API calls have no subprocess to isolate.

## Removed CLI flags

- `--sandbox` / `--no-sandbox`
- `--agent` (replaced by `--provider`)

## Removed CLI commands

- `superseded skill install`
- `superseded skill print`

(The skill command existed only to install a `SKILL.md` into other AI CLIs'
config dirs. With those CLIs no longer used, the command has no purpose.)

## Removed / renamed environment variables

| Old | New |
|---|---|
| `SUPERSEDED_AGENT` | `SUPERSEDED_PROVIDER` (the old name still works but emits a `DeprecationWarning`) |
| `SUPERSEDED_SANDBOX`, `SUPERSEDED_SANDBOX_KIND`, `SUPERSEDED_SANDBOX_TIMEOUT`, `SUPERSEDED_SANDBOX_KEEP_ON_ERROR`, `SUPERSEDED_SANDBOX_IO_MODE` | (removed — no sandbox) |
| `SUPERSEDED_SMOLVM_BINARY`, `SUPERSEDED_SMOLVM_IMAGE`, `SUPERSEDED_SMOLVM_IMAGE_CLAUDE`, `SUPERSEDED_SMOLVM_IMAGE_OPENCODE`, `SUPERSEDED_SMOLVM_IMAGE_CODEX` | (removed) |
| `SUPERSEDED_ALLOW_NO_SANDBOX` | (removed — no sandbox) |
| (none) | `SUPERSEDED_DEEPSEEK_API_KEY` (new, required) |

## Server operators

The server no longer needs KVM, `docker-sbx`, OCI images, or `smolmachines`.
It now runs anywhere Python 3.14+ runs. Required env at startup:

- `SUPERSEDED_DEEPSEEK_API_KEY` (refuses to start if missing)
- Existing GitHub App / port / database-url config unchanged.

The server's `ServerConfig` no longer has `sandbox_*` or `smolvm_*` fields.
The only new field is `deepseek_api_key`.

## Defaults

- Provider: `deepseek`
- Model: `deepseek-v4-flash` (override with `--model` / `SUPERSEDED_MODEL` / `.superseded.yaml`)
