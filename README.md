<p align="center">
  <img src="docs/assets/vaner-lockup-animated.svg" alt="Vaner" width="260">
</p>

<p align="center">
  <strong>Local-first preparation for AI coding agents.</strong><br>
  Vaner turns idle compute into evidence-backed Prepared Work before your next prompt lands.
</p>

<p align="center">
  <a href="https://github.com/Borgels/vaner/actions/workflows/ci.yml"><img src="https://github.com/Borgels/vaner/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Borgels/vaner/actions/workflows/codeql.yml"><img src="https://github.com/Borgels/vaner/actions/workflows/codeql.yml/badge.svg" alt="CodeQL"></a>
  <a href="https://github.com/Borgels/vaner/releases/latest"><img src="https://img.shields.io/github/v/release/Borgels/vaner" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Borgels/vaner" alt="License"></a>
  <a href="https://scorecard.dev/viewer/?uri=github.com/Borgels/vaner"><img src="https://api.scorecard.dev/projects/github.com/Borgels/vaner/badge" alt="OpenSSF Scorecard"></a>
</p>

<p align="center">
  <a href="https://vaner.ai"><strong>Download Desktop</strong></a>
  ·
  <a href="https://docs.vaner.ai">Docs</a>
  ·
  <a href="https://docs.vaner.ai/integrations">Integrations</a>
  ·
  <a href="https://github.com/Borgels/vaner/releases/latest">Latest release</a>
</p>

> Status: alpha (pre-1.0). Interfaces may evolve while core behavior stabilizes.

## What Vaner Does

Vaner runs beside your editor as a local engine. It watches the repository you
scope it to, prepares useful context in the background, and serves the best fit
to your AI client when the real question arrives.

Prepared Work can include review notes, bug hypotheses, docs drift, research
briefs, virtual diffs, and prediction-backed drafts. It is non-mutating by
default: Vaner can prepare a diff, but applying or exporting work is always an
explicit user action.

## Recommended Install

Most users should start with **[Vaner Desktop](https://vaner.ai)**.

Desktop starts the local engine, picks an Ollama model for your hardware, and
wires Vaner into supported AI clients it detects on your machine. You normally
do not install Vaner inside the AI client first; the AI client is where the
agent uses Vaner after Desktop or `vaner init` configures the integration.

## How You Interact With Vaner

Vaner is one local engine/daemon with several surfaces around it. MCP is the
primary interface for AI agents. Desktop and Companion are the primary
human-facing controls.

| Surface | Primary user | Purpose | Default? | Where documented |
| --- | --- | --- | --- | --- |
| AI client via MCP | AI agent | Pull Prepared Work and call `vaner.*` tools from Cursor, Claude Code, Codex, Zed, and similar clients. | Yes, for agents | [MCP mode](https://docs.vaner.ai/integrations/mcp) |
| Desktop tray/taskbar window | Human user | Install, start/stop Vaner, see status, and wire detected clients. | Yes, for humans | [Getting started](https://docs.vaner.ai/getting-started) |
| Companion/settings thin client | Human user | Manage common settings, integrations, backend/runtime choices, and privacy posture. | Yes, through Desktop | [Configuration](https://docs.vaner.ai/configuration) |
| Cockpit / Web UI | Advanced user | Inspect engine state, priorities, Prepared Work, diagnostics, and live activity. | No, advanced | [Prepared Work](https://docs.vaner.ai/prepared-work#inspecting-prepared-work) |
| CLI | Power users / CI | Script setup, daemon control, status, doctor, logs, and config. | No | [CLI reference](https://docs.vaner.ai/cli) |
| Primer/rules, skills, plugins/hooks | AI agent integration | Make MCP usage reliable and client-specific. | Yes, where supported | [Client integration depth](https://docs.vaner.ai/integrations/client-capabilities) |
| Proxy/gateway | Compatibility users | OpenAI-compatible fallback for tools that cannot call MCP directly. | No | [Proxy mode](https://docs.vaner.ai/integrations/proxy) |

## Power Users / CI

Use the CLI path for CI, Docker, SSH-only machines, or scripted setup.

```bash
# Linux/macOS one-line installer
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash

# Or install with a Python tool runner
pipx install 'vaner[mcp]'
uv tool install 'vaner[mcp]'
```

First run:

```bash
vaner init --path .   # detect hardware, pick a model, wire detected MCP clients
vaner up --path .     # start the daemon and Cockpit
```

Cockpit opens at `http://127.0.0.1:8473/` with live engine state, Prepared
Work, predictions, goals, diagnostics, and feedback.

## AI Client Setup

Vaner exposes context to agents over [MCP](https://modelcontextprotocol.io/).
Desktop and `vaner init` configure supported clients automatically. Manual
setup is still available:

| Client | One command |
| --- | --- |
| Claude Code plugin | `/plugin marketplace add Borgels/vaner` then `/plugin install vaner@vaner` |
| Claude Code MCP | `claude mcp add --transport stdio --scope user vaner -- vaner mcp --path .` |
| Codex CLI | `codex mcp add vaner -- vaner mcp --path .` |
| Cursor, VS Code, Zed, Windsurf, Continue, Claude Desktop, Cline, Roo | See [Integrations](https://docs.vaner.ai/integrations) |

## Documentation

- [Getting started](https://docs.vaner.ai/getting-started): Desktop install and first run
- [Architecture](https://docs.vaner.ai/architecture): how the local engine works
- [Integrations](https://docs.vaner.ai/integrations): supported MCP clients
- [MCP mode](https://docs.vaner.ai/integrations/mcp): the `vaner.*` agent tool surface
- [Prepared Work](https://docs.vaner.ai/prepared-work): what Vaner prepares and how to inspect it
- [Configuration](https://docs.vaner.ai/configuration): backends, compute, retention, privacy posture
- [CLI reference](https://docs.vaner.ai/cli): setup, daemon control, status, doctor, logs, config
- [Security](https://docs.vaner.ai/security): local-first guarantees and threat model

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, CI,
Claude Code plugin parity rules, and DCO sign-off.

```bash
git clone https://github.com/Borgels/vaner.git
cd vaner
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pytest tests -m "not slow and not integration"
```

## Project

- Security policy: [SECURITY.md](SECURITY.md)
- Governance: [GOVERNANCE.md](GOVERNANCE.md)
- Maintainers: [MAINTAINERS.md](MAINTAINERS.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Support: [SUPPORT.md](SUPPORT.md)
- Examples: [examples/](examples/)

## License

Apache-2.0. Copyright 2026 Borgels Olsen Holding ApS (VAT DK39700425).
