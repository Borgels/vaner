# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added a supported Claude Code plugin (`plugins/vaner/`) distributed via a same-repo marketplace at `.claude-plugin/marketplace.json`. Claude Code users can now install Vaner with `/plugin marketplace add Borgels/Vaner` followed by `/plugin install vaner@vaner`. The plugin bundles the Vaner MCP server, the `vaner-feedback` skill (now namespaced as `/vaner:vaner-feedback`), a `/vaner:install` skill that wraps the canonical installer behind a Bash-tool permission prompt, and a SessionStart hook that reports when the `vaner` CLI is missing from PATH.

## [0.6.2] - 2026-04-21

### Fixed

- Fixed version-pinned installer fallback so `VANER_VERSION=<tag>` can fall back to the matching GitHub tag when PyPI does not have that release yet.
- Fixed the default install/query path to degrade gracefully when `sentence-transformers` is unavailable instead of crashing at first query-time embedding use.
- Fixed `vaner uninstall` so repo-local Cursor MCP wiring is removed correctly when `vaner init` created `.cursor/mcp.json`.
- Fixed managed feedback skill content drift by shipping a single current `vaner.feedback`-based skill template instead of duplicated legacy tool instructions.
- Improved MCP v1 `suggest` / `search` ranking in fresh repos so Vaner-managed config/skill files are downranked unless the query explicitly targets them.

### Added

- Added regression tests for pinned installer fallback, uninstall symmetry, managed skill content, graceful missing-embedding fallback, and MCP ranking around Vaner-managed files.

## [0.6.1] - 2026-04-21

### Fixed

- Fixed MCP server startup for both stdio and SSE by passing explicit `NotificationOptions` during capability initialization (resolves `tools_changed` crash on connect).
- Fixed `vaner config show` / `vaner config keys` crashes by restoring `intent` settings in `VanerConfig` and wiring `[intent]` + `[intent.skills_loop]` config loading.
- Fixed repeated MCP client wiring from creating unbounded `*.vaner-backup-*` files by skipping backups on no-op merges and rotating backups to keep only the latest 3.
- Hardened installer behavior for uv by retrying with explicit MCP dependencies (`mcp[cli]`, `starlette`) when extras resolution fails.

### Added

- Added `vaner uninstall` command to remove managed MCP wiring + managed skill files, with `--keep-state` support for preserving local `.vaner` state.
- Added regression tests for MCP boot/session readiness, config command intent keys, and MCP backup idempotence/rotation.

## [0.6.0] - 2026-04-20

### Changed (BREAKING)

- Replaced the legacy 5-tool MCP surface with the v1.0 tool set: `vaner.status`, `vaner.suggest`, `vaner.resolve`, `vaner.expand`, `vaner.search`, `vaner.explain`, `vaner.feedback`, `vaner.warm`, `vaner.inspect`, and `vaner.debug.trace`. See `docs/mcp-migration.md`.
- Scenario storage now uses memory semantics (`memory_state`, `memory_confidence`, `memory_evidence_hashes_json`) and keeps `pinned` only as a compatibility alias for `memory_state == 'trusted'`.
- `vaner.feedback` no longer auto-promotes on one `useful` signal; promotion is gated by memory policy rules documented in `docs/memory-semantics.md`.

### Added

- Added first-class memory policy rules in `src/vaner/memory/policy.py` for promotion gating, evidence invalidation, contradiction-aware conflict detection, and decision reuse.
- Added memory quality metrics surfaced by `vaner.status` and `vaner.debug.trace` (`predictive_hit_rate`, `stale_hit_rate`, `promotion_precision`, `contradiction_rate`, `correction_survival_rate`, `demotion_recovery_rate`, `trusted_evidence_avg`, `abstain_rate`).
- Added inspectability traces at `.vaner/memory/log.md` and `.vaner/memory/index.md` (explicitly not the semantic memory layer).

## [0.5.0] - 2026-04-20

### Added

- Added a full `vaner init` onboarding wizard with backend/compute prompts, multi-client MCP selection, safe config merges, and backup files.
- Added new init controls: `--clients auto|all|none|other|csv`, `--dry-run`, and stronger `--force` handling for malformed files.
- Added an explicit escape hatch for unsupported clients that prints a generic MCP snippet, docs links, and a support issue URL.
- Added CLI tests for MCP client registry/merge behavior and the new onboarding wizard interaction flows.

### Changed

- Updated onboarding docs and landing flows to promote `curl | bash` followed by `vaner init` as the default path.
- Switched client config writes to a single MCP client registry implementation that supports Cursor, Claude Desktop/Code, VS Code, Codex CLI, Windsurf, Zed, Continue, Cline, and Roo.

### Removed

- Removed the legacy `write_mcp_configs(repo_root)` path from init in favor of the new wizard + registry flow.

## [0.2.0] - 2026-04-19

### Removed

- Removed deprecated MCP compatibility tools: `legacy_get_context`, `legacy_precompute`, and `legacy_get_metrics`.
- Removed deprecated `ExplorationPolicy`; `ScoringPolicy` is now the sole exploration policy surface.

### Changed

- Hardened MCP tests with explicit tool-matrix assertions, per-tool error-path checks, telemetry checks, and an in-memory protocol round-trip test.
- Added CLI MCP smoke tests for `stdio`/`sse` wiring and non-loopback SSE safety checks.
- Added docs drift tests to prevent reintroducing removed MCP tool names in docs examples.
- Hardened release CI with strict provenance enforcement, keyless Sigstore signing for artifacts, SBOM attestation, and release verification gates.
- Added container provenance/SBOM output plus keyless SBOM attestation for Docker releases.
- Added VSIX provenance attestation and Sigstore bundle upload to GitHub releases.
