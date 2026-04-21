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

Vaner predicts what the user will likely ask next and prepares evidence-backed context packages in advance.
Instead of reacting from a cold start, it continuously turns idle compute into prepared context that can be
served quickly when the real prompt arrives.

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
- installs per-client usage primers (short guidance about when and how to use Vaner) into each detected client's rules surface: `.claude/CLAUDE.md` (Claude Code), `.cursor/rules/vaner.mdc` (Cursor), `.github/copilot-instructions.md` (Copilot), `AGENTS.md` (Codex CLI), `.clinerules` (Cline), `.continue/rules/vaner.md` (Continue). Primers are non-destructive: existing files get a delimited `<!-- vaner-primer:start v=… -->…<!-- vaner-primer:end -->` block that re-runs replace in place without touching content outside it.

Use `vaner init --no-mcp --path .` if you want config only and do not want Vaner to wire MCP clients or managed
skills. Use `--no-primer` to skip the client-level guidance. Use `--user-primer` to also install the Claude Code
primer at `~/.claude/CLAUDE.md` (always-on across every session) in addition to the repo-level `.claude/CLAUDE.md`.
If Cursor is already open, reload the window after `vaner init` so the new MCP server is picked up.

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

The installer:

- Detects `uv` or `pipx` and installs `vaner[mcp]` (MCP client support included by default; pass `--no-mcp` to skip).
- Falls back to `git+https://github.com/Borgels/vaner.git` if the PyPI package isn't available yet.
- Asks you to pick a model backend (Ollama, LM Studio, vLLM, OpenAI, Anthropic, OpenRouter, or skip).
- Offers a compute budget (`background` / `balanced` / `dedicated`) and an optional wall-clock cap on ponder time (`--max-session-minutes`).

Non-interactive example (CI, Dockerfile):

```bash
VANER_YES=1 \
VANER_BACKEND_PRESET=openai \
VANER_BACKEND_API_KEY_ENV=OPENAI_API_KEY \
VANER_BACKEND_MODEL=gpt-4o \
VANER_COMPUTE_PRESET=background \
VANER_MAX_SESSION_MINUTES=30 \
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash
```

From source (no installer):

```bash
git clone https://github.com/Borgels/vaner.git
cd vaner
pip install '.[mcp]'
```

Installer source for review: [`scripts/install.sh`](scripts/install.sh).

### 2. Connect your AI client

Vaner exposes context to your agent over [MCP](https://modelcontextprotocol.io/). Pick your client and paste the one-liner:

| Client | One command |
| --- | --- |
| Claude Code (plugin, recommended) | `/plugin marketplace add Borgels/Vaner` then `/plugin install vaner@vaner` |
| Claude Code (manual MCP) | `claude mcp add --transport stdio --scope user vaner -- vaner mcp --path .` |
| Codex CLI   | `codex mcp add vaner -- vaner mcp --path .` |
| Cursor / VS Code / Zed / Windsurf / Continue / Claude Desktop / Cline / Roo | see [docs.vaner.ai/mcp](https://docs.vaner.ai/mcp) |

The Claude Code plugin bundles the MCP server, the `vaner-feedback` skill (namespaced as `/vaner:vaner-feedback`), and a SessionStart hook that detects whether the `vaner` CLI is installed. See [docs/claude-plugin.md](docs/claude-plugin.md) for details.

Or let Vaner write the config file for you:

```bash
vaner init --path .                 # initialize this repo + pick backend interactively
vaner mcp --path .                  # smoke-test the MCP server in stdio mode
```

### 3. Run it

```bash
vaner daemon start --no-once --path .
vaner query "where is auth enforced?" --explain --path .
vaner inspect --last --path .
```

Asciinema demo: coming soon.

## Cockpit live pipeline view

Vaner ships a cockpit UI that doubles as a real-time control surface for the
daemon. Open it at `http://127.0.0.1:8473/` (after `vaner daemon start`) or
co-mounted at `/cockpit/` when serving MCP over SSE.

The cockpit view is organised around the actual daemon control flow:

```
Signals → Targets → Model → Artefacts → Scenarios → Decisions
```

- **Pipeline ribbon** at the top shows per-stage activity with animated
  particles flowing left-to-right as the daemon processes each cycle. Model
  lane surfaces a spinner while LLM requests are in flight, plus the last
  latency.
- **Scenario cluster** is the main canvas. Scenarios are laid out by
  kind-bucketed force-directed layout and connected by **shared-path Jaccard
  edges** — so even scenarios without an explicit parent/child form a visible
  constellation when they touch the same files. Drag nodes, scroll to zoom.
- **System vitals** (left rail) tracks mode, current cycle duration, model
  busy state, recent LLM latency EMA, total cycles, artefacts written, and
  error count. All values are derived from the event bus — no polling.
- **Event stream** (right rail) is colour-coded by stage with per-stage
  filter chips, heartbeat collapsing, and an in-flight LLM counter.

Everything is driven by the unified event bus in `src/vaner/events/bus.py`,
which the daemon runner, LLM helpers, proxy, and scenario store publish to.
The SSE endpoint `/events/stream` accepts a `?stages=model,artefacts` filter
for scripted consumers.
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
