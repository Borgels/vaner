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
/plugin marketplace add Borgels/Vaner
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
