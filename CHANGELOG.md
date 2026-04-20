# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-04-20

### Added

- Added `vaner up` and `vaner down` to run daemon and cockpit as one supervised flow with a single startup command.
- Added preflight safeguards for unsafe repo roots, inotify headroom checks, and proactive port selection.
- Added runtime log tailing for daemon and cockpit via `vaner logs`.
- Added fallback from inotify to polling watchers when Linux watch limits are exhausted.
- Added runtime snapshot checks that power both `vaner status` and `vaner doctor` consistently.
- Added new diagnostics for `repo_root_sensible`, `inotify_headroom`, and `cli_up_to_date`.

### Changed

- Hardened background daemon startup with dead-on-arrival detection and startup error surfacing.
- Hardened cockpit startup with explicit busy-port remediation guidance and fallback port suggestions.
- Updated onboarding docs, troubleshooting docs, README, and installer/landing callouts to make `vaner up` the primary post-install flow.

## [0.3.0] - 2026-04-20

### Added

- Initial public project scaffolding and documentation split to `docs.vaner.ai`.
- Agent Skills closed loop integration:
  - skill discovery and intent seeding
  - MCP skill attribution on scenario tools and outcome reporting
  - scenario feedback queue for adaptive frontier weighting
  - managed bundled `vaner-feedback` skill installation in `vaner init`
  - `vaner distill-skill` for converting decision records into reusable `SKILL.md`
- Added hardening checks for MCP command wiring, boot probing, config drift detection, and optional release update probing.

### Changed

- Cockpit now serves an interactive dashboard at `/` (scenario cards, outcome actions, compute device switching), and `/ui` redirects to `/` for backwards compatibility.
- CLI UX polish:
  - added `vaner --version`, grouped help panels, and command aliases (`ls`, `ps`, `logs`)
  - added `config get`, `config keys`, and `config edit`
  - improved `config show` and `doctor` terminal output with richer formatting
  - added `vaner upgrade` helper
  - `vaner init` now applies detected compute defaults and offers shell-completion install
  - MCP config scaffolding now prefers the absolute `vaner` binary path and only falls back to `uvx`
- Core eval/query test paths now run without requiring `sentence-transformers`, enabling hermetic default CI coverage.
- GitHub workflow hardening improved by pinning `actions/attest-sbom` and reducing unnecessary workflow token permissions.

### Breaking changes

- `exploration.exploration_model` renamed to `exploration.model`.
- `exploration.exploration_endpoint` renamed to `exploration.endpoint`.
- `exploration.exploration_backend` renamed to `exploration.backend`.
- `exploration.exploration_api_key` renamed to `exploration.api_key`.
- `exploration.embedding_device` removed; `compute.embedding_device` is now authoritative.
- `intent.skills_loop.enabled` defaults to `true`.

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
