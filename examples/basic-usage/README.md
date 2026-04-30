# Basic Usage

```bash
vaner init --path .
vaner up --path .
vaner query "explain this repo"
vaner inspect --last
```

Minimal config:

```toml
[limits]
max_context_tokens = 2048
```

Expected outcome:

- `vaner query` returns a context block containing selected repo summaries.
- `.vaner/runtime/last_context.md` contains token usage and per-artefact rationale.
- Prepared Work can be inspected through the desktop app, cockpit, or
  `vaner.prepared_work.dashboard` over MCP.
