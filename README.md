# Vaner

[![CI](https://github.com/Borgels/vaner/actions/workflows/ci.yml/badge.svg)](https://github.com/Borgels/vaner/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Borgels/vaner/actions/workflows/codeql.yml/badge.svg)](https://github.com/Borgels/vaner/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/Borgels/vaner)](https://github.com/Borgels/vaner/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Borgels/vaner/total?label=Downloads)](https://github.com/Borgels/vaner/releases)
[![License](https://img.shields.io/github/license/Borgels/vaner)](LICENSE)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Borgels/vaner/badge)](https://scorecard.dev/viewer/?uri=github.com/Borgels/vaner)

> Status: alpha (pre-1.0). Interfaces may evolve quickly while we stabilize core behavior.

Vaner is a local-first predictive context engine for AI coding workflows. It
uses idle time to anticipate likely next prompts, pre-build useful context, and
serve the best context package quickly when the real prompt arrives.

## Quickstart

### 1. Install

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash
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
| Claude Code | `claude mcp add --transport stdio --scope user vaner -- vaner mcp --path .` |
| Codex CLI   | `codex mcp add vaner -- vaner mcp --path .` |
| Cursor / VS Code / Zed / Windsurf / Continue / Claude Desktop / Cline / Roo | see [docs.vaner.ai/mcp](https://docs.vaner.ai/mcp) |

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
- Examples: [docs.vaner.ai/examples](https://docs.vaner.ai/examples)

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
