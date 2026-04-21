# Vaner

[![CI](https://github.com/Borgels/vaner/actions/workflows/ci.yml/badge.svg)](https://github.com/Borgels/vaner/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Borgels/vaner/actions/workflows/codeql.yml/badge.svg)](https://github.com/Borgels/vaner/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/Borgels/vaner)](https://github.com/Borgels/vaner/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Borgels/vaner/total?label=Downloads)](https://github.com/Borgels/vaner/releases)
[![License](https://img.shields.io/github/license/Borgels/vaner)](LICENSE)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Borgels/vaner/badge)](https://scorecard.dev/viewer/?uri=github.com/Borgels/vaner)

> Status: alpha (pre-1.0). Interfaces may evolve quickly while we stabilize core behavior.

## What is Vaner?

Vaner is a local-first context engine for coding agents that turns idle compute into useful future context.
Instead of waiting for a prompt and then starting retrieval from cold, Vaner continuously prepares likely
next-context packages, scores them, and serves the best fit quickly when the real question arrives.

Mem0-style systems and Vaner can complement each other, but they solve different core problems. Memory
systems focus on storing and retrieving durable facts across sessions. Vaner focuses on predicting what the
user will likely ask next and preparing evidence-backed context packages in advance. In short: memory systems
help agents remember; Vaner helps agents arrive prepared.

It is built around evidence-backed scenario memory, not chat-log accumulation:

- context is compiled from repo evidence and stored as reusable scenarios
- memory has explicit lifecycle states (`candidate`, `trusted`, `stale`, `demoted`)
- fingerprints tie memory to concrete sources so drift is detected automatically
- conflict handling can abstain instead of blending contradictory evidence

## Install

### One-line installer (Linux/macOS)

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash
```

Common installer flags:

- `--yes` (non-interactive), `--dry-run`, `--verify`
- `--backend uv|pipx`
- `--version <tag>`
- `--with-ollama`
- `--minimal` (legacy minimal extras; pairs with `--no-mcp` when needed)
- `--backend-preset ollama|lmstudio|vllm|openai|anthropic|openrouter|skip`
- `--backend-url <url>`, `--backend-model <model>`, `--backend-api-key-env <env>`
- `--compute-preset background|balanced|dedicated`
- `--max-session-minutes <n>`
- `--no-mcp` (skip MCP extras)

### Manual install (advanced)

```bash
pipx install 'vaner[all]'
# or
uv tool install 'vaner[all]'
```

### Upgrade

```bash
vaner upgrade
vaner version
```

### What Vaner stores and when it expires

Vaner stores local runtime state in your repository under `.vaner/` (for example `store.db`, `scenarios.db`,
`metrics.db`, and `telemetry.db`). Retention is controlled by `limits.max_age_seconds` in `.vaner/config.toml`.
Old signal/replay/query and stale cache entries are purged during engine/precompute activity. There is not yet a
separate `prune` or `compact` command.

Useful commands:

- `vaner config set limits.max_age_seconds 3600 --path .`
- `vaner forget --path .` (remove local Vaner state for this repo)
- `vaner uninstall --path . --keep-state` (remove client wiring while keeping state)

## Configure

Run the setup wizard per repo:

```bash
vaner init --path .
```

Key non-interactive flags:

- `--backend-preset`
- `--backend-model` and `--backend-api-key-env`
- `--compute-preset background|balanced|dedicated`
- `--max-session-minutes <n>`
- `--force`
- `--interactive/--no-interactive`
- `--no-mcp`

What `vaner init` does by default:

- writes repo-local config to `.vaner/config.toml`
- writes repo-local Cursor MCP wiring to `.cursor/mcp.json`
- writes managed feedback skills to `.cursor/skills/vaner/vaner-feedback/SKILL.md` and `~/.claude/skills/vaner/vaner-feedback/SKILL.md`

Use `vaner init --no-mcp --path .` if you want config only and do not want Vaner to wire MCP clients or managed
skills. If Cursor is already open, reload the window after `vaner init` so the new MCP server is picked up.

Start / verify runtime:

```bash
vaner up --path .
vaner status
vaner logs --daemon
vaner down
```

### Supported MCP clients

From the client registry IDs:

`cursor`, `claude-desktop`, `claude-code`, `vscode-copilot`, `codex-cli`, `windsurf`, `zed`, `continue`, `cline`, `roo`

## Quickstart

```bash
vaner init --path .
vaner up --path .
```

## Documentation

Most documentation lives at [docs.vaner.ai](https://docs.vaner.ai):

- Getting started: [docs.vaner.ai/getting-started](https://docs.vaner.ai/getting-started)
- Integrations: [docs.vaner.ai/integrations](https://docs.vaner.ai/integrations)
- Configuration: [docs.vaner.ai/configuration](https://docs.vaner.ai/configuration)
- Architecture: [docs.vaner.ai/architecture](https://docs.vaner.ai/architecture)
- Security: [docs.vaner.ai/security](https://docs.vaner.ai/security)
- CLI reference: [docs.vaner.ai/cli](https://docs.vaner.ai/cli)
- MCP tools: [docs.vaner.ai/mcp](https://docs.vaner.ai/mcp)
- Examples: [docs.vaner.ai/examples](https://docs.vaner.ai/examples)

## MCP (v1.0)

Vaner exposes the following MCP tools:

- `vaner.status`
- `vaner.suggest`
- `vaner.resolve`
- `vaner.expand`
- `vaner.search`
- `vaner.explain`
- `vaner.feedback`
- `vaner.warm`
- `vaner.inspect`
- `vaner.debug.trace`

Memory behavior details: `docs/memory-semantics.md`.

### Minimal agent loop (MCP pseudo)

```python
state = client.call("vaner.status", {})
resolution = client.call("vaner.resolve", {"query": prompt, "budget": {"tokens": 6000}})
reply = llm.respond(resolution["summary"], prompt)
client.call("vaner.feedback", {"resolution_id": resolution["resolution_id"], "rating": "useful"})
```

## Community

- Contributing guide: `CONTRIBUTING.md`
- Security policy: `SECURITY.md`
- Governance model: `GOVERNANCE.md`
- Maintainer roles and sensitive-resource ownership: `MAINTAINERS.md`
- Code of conduct: `CODE_OF_CONDUCT.md`
- Support channels: `SUPPORT.md`
- Examples: `examples/`

## OpenSSF Best Practices

Vaner is preparing an OpenSSF Best Practices submission.

## License

Apache-2.0. Copyright 2026 Borgels Olsen Holding ApS (VAT DK39700425).
