# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
