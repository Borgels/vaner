# Vaner Claude Code plugin

Vaner ships a supported [Claude Code plugin](https://code.claude.com/docs/en/plugins) that bundles the Vaner MCP server, the `vaner-feedback` skill, and a bootstrap hook into one installable unit.

## What's in the plugin

| Component | Location in the plugin | Effect in Claude Code |
| --- | --- | --- |
| MCP server | `plugins/vaner/.mcp.json` | Registers an MCP server named `vaner`. Tools appear as `mcp__vaner__search`, `mcp__vaner__resolve`, `mcp__vaner__expand`, `mcp__vaner__suggest`, `mcp__vaner__feedback`, `mcp__vaner__status`, `mcp__vaner__explain`, `mcp__vaner__warm`, `mcp__vaner__inspect`, `mcp__vaner__debug__trace`. |
| Feedback skill | `plugins/vaner/skills/vaner-feedback/SKILL.md` | Invokable as `/vaner:vaner-feedback`. Guides the agent through reporting scenario outcomes via `vaner.feedback`. |
| Install skill | `plugins/vaner/skills/install/SKILL.md` | Invokable as `/vaner:install`. Runs the canonical installer under the Bash tool's permission prompt when the `vaner` CLI is not yet installed. |
| SessionStart hook | `plugins/vaner/hooks/hooks.json` + `scripts/check-vaner.sh` | On each new session, detects whether `vaner` is on PATH. If missing, injects an `additionalContext` message directing the user at the installer; otherwise silent. |

## Install

```text
/plugin marketplace add Borgels/vaner
/plugin install vaner@vaner
```

That's it — the plugin is cached to `~/.claude/plugins/cache/vaner/` and enabled for the chosen scope. If the `vaner` CLI is not on PATH, the next session will tell you how to install it (or you can run `/vaner:install`).

### Prerequisite: the `vaner` CLI

The plugin wires Claude Code *to* the Vaner CLI; it does not bundle it. Install the CLI once with the canonical installer:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash -s -- --yes
```

Or invoke `/vaner:install` from inside Claude Code — the skill wraps the same command behind a permission prompt.

## Update

```text
/plugin update vaner@vaner
```

Update propagation is driven by `plugins/vaner/.claude-plugin/plugin.json::version`. Vaner's repo keeps that field in lockstep with `pyproject.toml` via `scripts/bump-plugin-version.sh`, so every tagged Vaner release becomes a plugin update.

## Uninstall

```text
/plugin uninstall vaner@vaner
```

If you previously ran `vaner init`, you may also want to clean up the pre-plugin managed skill copy:

```bash
rm -rf ~/.claude/skills/vaner
```

## Relationship with `vaner init`

`vaner init` still works and still writes a managed feedback skill to `~/.claude/skills/vaner/vaner-feedback/SKILL.md` plus a per-repo `.cursor/mcp.json`. For Claude Code users the plugin supersedes both — the plugin-shipped skill resolves as `/vaner:vaner-feedback` (namespaced), and the plugin's `.mcp.json` registers the MCP server at the user scope. The standalone skill can coexist harmlessly (contents are byte-identical and CI-enforced) or be removed.

For Cursor, Codex CLI, and other clients that don't support Claude Code plugins, continue using `vaner init` / the per-client instructions at [docs.vaner.ai/mcp](https://docs.vaner.ai/mcp).

## Running in scripted / non-interactive mode

`claude --print` mode defaults to denying MCP tool calls that would require a permission prompt. Plugin MCP tools fall into that category. When scripting — benchmarks, CI checks, shell automations — grant the tools explicitly:

```bash
# Fastest, trust-everything approach (local shell scripts):
claude --plugin-dir ./plugins/vaner --permission-mode bypassPermissions --print "…"

# Surgical — only allow Vaner's MCP tools (recommended for CI):
claude --plugin-dir ./plugins/vaner \
  --allowedTools "mcp__plugin_vaner_vaner__*" \
  --print "/vaner:next"
```

### MCP tool naming

The canonical primer (`src/vaner/defaults/prompts/agent-primer.md`) and the `/vaner:next` skill refer to tools by their conceptual names: `vaner.status`, `vaner.resolve`, `vaner.search`, `vaner.suggest`, `vaner.expand`, `vaner.feedback`, `vaner.explain`, `vaner.warm`, `vaner.inspect`, `vaner.debug.trace`.

Claude Code exposes plugin MCP tools with a namespaced prefix: `mcp__plugin_<plugin-name>_<server-name>__<tool>`. For the Vaner plugin that resolves to `mcp__plugin_vaner_vaner__vaner.status`, `mcp__plugin_vaner_vaner__vaner.resolve`, and so on. Confirm the exact names in a session with:

```bash
claude --plugin-dir ./plugins/vaner mcp list
```

(Expect `plugin:vaner:vaner: vaner mcp - ✓ Connected`.)

The model generally maps the conceptual names to the prefixed names without help, but if you're writing automation that invokes tools by name, use the prefixed form directly.

### Required CLI version

The plugin targets the v1.0 MCP tool surface introduced in Vaner **0.6.0**. Older installs (0.5.x and below) expose a legacy 5-tool surface (`list_scenarios`, `get_scenario`, `expand_scenario`, `compare_scenarios`, `report_outcome`) — the primer's tool references will not match and the `/vaner:next` skill will not find the tools it expects. Check with `vaner --help` (look for `suggest`, `resolve`, and `feedback` subcommands) and upgrade via the canonical installer if needed:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://vaner.ai/install.sh | bash -s -- --yes
```

## Ponder throughput

The daemon's exploration loop runs scenarios in parallel up to `compute.exploration_concurrency` (default `4`). For guidance on tuning ponder throughput — ollama `OLLAMA_NUM_PARALLEL`, multi-GPU setups, multi-endpoint pools — see [docs/performance.md](performance.md).

## Troubleshooting

- **`/mcp` does not show a `vaner` server.** Confirm `command -v vaner` works in the same shell you launched Claude Code from. The plugin's `.mcp.json` calls `vaner` directly; if it isn't on PATH, the server cannot start and the SessionStart hook will have told you so at session start.
- **MCP tools run against the wrong repo.** The plugin launches `vaner mcp` with Claude Code's session working directory, and Vaner resolves its repo root from `$PWD`. If you started Claude Code from outside your project, `cd` into it first and open a new session.
- **Skill does not appear as `/vaner:vaner-feedback`.** Run `/plugin list` to confirm `vaner@vaner` is installed and enabled. If you had a pre-plugin `~/.claude/skills/vaner/vaner-feedback/SKILL.md`, the plugin takes precedence.

## For plugin developers working on Vaner itself

- Test locally without a marketplace roundtrip:
  ```bash
  claude --plugin-dir ./plugins/vaner
  ```
- Validate:
  ```bash
  claude plugin validate ./plugins/vaner
  ```
- CI enforces skill parity (`scripts/sync-plugin-skill.sh --check`) and version parity (`scripts/bump-plugin-version.sh --check`). Update both via the `--write` mode after editing the canonical skill or bumping `pyproject.toml`.
