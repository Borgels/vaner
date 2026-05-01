# Vaner integration testbed

A reproducible Ubuntu container with Vaner installed from this repo's
working tree plus the supported AI clients. The point is to exercise
`vaner launch <client>` end-to-end against real client tooling
without polluting the host machine.

## Phase D-1 — CLI clients (this image)

Two clients are pre-installed:

| Client       | How             |
|--------------|-----------------|
| Claude Code  | `npm i -g @anthropic-ai/claude-code` |
| Codex CLI    | `npm i -g @openai/codex` |

The smoke script (`test-launch-clients.sh`) runs `vaner launch` for
each and asserts all four leverage layers landed:

- MCP entry — `~/.claude/mcp.json`, `~/.codex/config.toml`
- Primer — `.claude/CLAUDE.md`, `AGENTS.md`
- Skill — `~/.claude/skills/vaner/vaner-feedback/SKILL.md`,
  `~/.codex/skills/vaner-feedback/SKILL.md`
- Plugin / hooks — `~/.claude/plugins/vaner/` (Codex's plugin gate is
  still flag-locked upstream so we don't assert it here)

## Phase D-2 — GUI clients (future)

Cursor, Zed, VS Code (Copilot), Continue, Cline, Windsurf, Roo,
Claude Desktop need a desktop environment. The plan is a separate
image that adds:

- xvfb + xfce4 (lightweight enough not to mask install-time bugs)
- noVNC on a known port for human verification
- Per-client install steps (.deb where possible, AppImage where not)

GUI assertions don't fit a one-shot CI run — they're for manual
verification. The CLI smoke script is what gates merges.

## Build + run

```bash
# From the repo root:
docker build -f tests/integration/testbed/Dockerfile -t vaner-testbed .

# One-shot smoke:
docker run --rm vaner-testbed bash /opt/testbed/test-launch-clients.sh

# Interactive shell:
docker run --rm -it vaner-testbed
```

Or via compose:

```bash
docker compose -f tests/integration/testbed/compose.yml run --rm smoke
docker compose -f tests/integration/testbed/compose.yml run --rm testbed
```

## What's not in scope

- Networking against external MCP servers (the launcher only writes
  config; nothing connects).
- Backend models — Vaner's daemon isn't started.
- Real model traffic — the smoke is purely "do the right files
  appear in the right places".
