# Configuration

Vaner reads configuration from `.vaner/config.toml`.

## Full Example

```toml
[backend]
name = "openai"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"

[privacy]
allowed_paths = ["src/**", "docs/**"]
excluded_patterns = ["*.env", "*.key", "*.pem", "credentials*", "secrets*"]
redact_patterns = ["sk-[a-zA-Z0-9_-]{20,}", "password\\s*=\\s*\\S+"]
telemetry = "local"

[limits]
max_age_seconds = 3600
max_context_tokens = 4096
```

## Keys

- `backend.name` (`str`, default: `openai`): backend profile name used in diagnostics.
- `backend.base_url` (`str`, default: `https://api.openai.com/v1`): OpenAI-compatible endpoint.
- `backend.model` (`str`, default: `gpt-4o-mini`): model sent to backend requests.
- `backend.api_key_env` (`str`, default: `OPENAI_API_KEY`): environment variable holding API key.
- `privacy.allowed_paths` (`list[str]`, default: `["."]`): include-globs for files considered by planner.
- `privacy.excluded_patterns` (`list[str]`, default shown above): deny-globs applied after allowed paths.
- `privacy.redact_patterns` (`list[str]`, default: `[]`): case-insensitive regex patterns replaced with `[REDACTED]`.
- `privacy.telemetry` (`str`, default: `local`): telemetry mode; v1 supports local storage only.
- `limits.max_age_seconds` (`int`, default: `3600`): stale threshold for artefact age.
- `limits.max_context_tokens` (`int`, default: `4096`): context package token budget.

## Notes

- Missing config file falls back to defaults.
- Invalid `redact_patterns` entries are skipped with a warning.
- `allowed_paths = ["."]` means "allow all paths under the repo."
