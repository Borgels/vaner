# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### Activity-timing aware cycle budget

- Added `ActivityTimingModel` (`src/vaner/intent/timing.py`) — an EMA-based estimator of inter-prompt cadence seeded from `query_history` timestamps and live-updated every time `VanerEngine.query()` is called. The model distinguishes "active session" cadence from session boundaries (gaps longer than `active_session_gap_seconds`, default 3 min, are treated as AFK and excluded from the EMA so one overnight break doesn't poison the estimate).
- Wired the timing model into `VanerEngine.precompute_cycle`: when `compute.adaptive_cycle_budget = true` (default), the cycle deadline now shrinks to roughly `adaptive_cycle_utilisation × estimated_seconds_until_next_prompt` during active sessions. When the user is idle the budget expands back up to `compute.max_cycle_seconds`. The static `max_cycle_seconds` cap remains the hard upper bound — the adaptive model only ever *shortens* the cycle, so existing operators retain their safety net.
- New config knobs: `ComputeConfig.adaptive_cycle_budget`, `adaptive_cycle_min_seconds`, `adaptive_cycle_utilisation`.

#### Unused-prediction decay

- Added access tracking to `prediction_cache` (schema v7): new `access_count` + `last_accessed_at` columns updated by `ArtefactStore.touch_prediction_cache()` whenever `TieredPredictionCache.match()` returns an entry above cold-miss tier.
- Added `ArtefactStore.purge_unused_prediction_cache(max_age_seconds_without_access, min_access_count_to_protect)` and wired it into the end-of-cycle cleanup. Entries that Vaner precomputed but the developer never consumed within `exploration.unused_cache_max_age_seconds` (default 30 min) are now removed independently of TTL. Entries with at least one real cache hit are protected regardless of age. Set the config field to `0.0` to fall back to TTL-only behavior.

#### Predicted-response precompute (opt-in)

- Added `VanerEngine._precompute_predicted_responses()` and an opt-in gate (`exploration.predicted_response_enabled`, default `false`). When enabled and the cycle has budget remaining, Vaner picks the top validated prompt macros (`use_count ≥ exploration.predicted_response_min_macro_use_count`, default 3) and spends dedicated LLM calls to draft a short response per macro. The draft is stashed in the cache enrichment as `predicted_response` alongside the normal context package, so agents consuming Vaner can surface a ready-made answer the moment the expected prompt arrives.
- New config knobs: `exploration.predicted_response_enabled`, `predicted_response_min_macro_use_count`, `predicted_response_max_per_cycle` (default 1) — the per-cycle cap keeps the feature from starving the exploration loop.

## [0.7.0] - 2026-04-22

### Added

#### Claude Code plugin surface (supersedes manual MCP wiring for Claude Code)

- Added a supported Claude Code plugin (`plugins/vaner/`) distributed via a same-repo marketplace at `.claude-plugin/marketplace.json`. Claude Code users can now install with `/plugin marketplace add Borgels/Vaner` followed by `/plugin install vaner@vaner`. Bundles the Vaner MCP server, the `vaner-feedback` skill (now namespaced as `/vaner:vaner-feedback`), a `/vaner:install` skill that wraps the canonical installer behind a Bash-tool permission prompt, and a SessionStart hook that reports when the `vaner` CLI is missing from PATH — otherwise injects the canonical Vaner usage primer on every session.
- Added a `/vaner:next` skill in the plugin that calls `mcp__vaner__suggest` and renders top-N candidate next moves as structured numbered cards (label, why-now, confidence/readiness hint) rather than raw predictions. When Vaner's daemon is live the SessionStart hook also surfaces the cockpit URL (`http://127.0.0.1:8473/`) so the model can point the user at the live pipeline view.
- Added a plugin monitor that tails `.vaner/memory/log.md` on first `/vaner:next` invocation, so the model stays aware of scenario events (resolve, feedback, promotion) mid-session.

#### Canonical usage primer

- Added per-client usage primers to `vaner init`. MCP wiring alone does not teach a model when and how to use Vaner; `init` now also installs a short guidance block into each detected client's native rules surface: `.claude/CLAUDE.md` (Claude Code), `.cursor/rules/vaner.mdc` (Cursor), `.github/copilot-instructions.md` (VS Code Copilot), `AGENTS.md` (Codex CLI), `.clinerules` (Cline), and `.continue/rules/vaner.md` (Continue). The primer is sourced from a single canonical file at `src/vaner/defaults/prompts/agent-primer.md` and wrapped client-specifically. Non-destructive: existing files get the primer appended in a delimited `<!-- vaner-primer:start … -->…<!-- vaner-primer:end -->` block that re-runs replace in place without touching surrounding content. Opt-out via `--no-primer`; `--user-primer` additionally writes the Claude Code primer at `~/.claude/CLAUDE.md` for always-on global guidance. Clients without a well-defined primer surface (Claude Desktop, Windsurf, Zed, Roo) are left as-is for this release.

#### Ponder parallelism

- Wired `compute.exploration_concurrency` (default 4) into the daemon's exploration loop. Previously a dead config — exposed by the cockpit UI and HTTP API but ignored by the engine. The scenario loop now runs up to `exploration_concurrency` LLM calls in parallel via an `asyncio.Semaphore`, with an `asyncio.Lock` guarding frontier mutations, follow-on branch pushes, and the covered-paths accumulator. Live benchmark on a single-GPU box with ollama 0.20.7 + `qwen2.5-coder:7b`: 9 scenarios in **140s at `exploration_concurrency=1` → 77s at `exploration_concurrency=4` (1.8× speedup)**. Server-side concurrency (vLLM natively, ollama with `OLLAMA_NUM_PARALLEL≥4`) determines the ceiling; a server that truly serializes requests will *degrade* below serial — a direct curl test against a single-slot backend measured 8 parallel calls as ~3× slower than 8 serial calls. The daemon emits a one-time `WARNING` log line whenever `exploration_concurrency > 1` reminding the operator to tune the server side. Individual scenario LLM failures no longer kill the whole cycle — they're logged and skipped. See `docs/performance.md` for the tuning ladder.
- Added an idle-aware concurrency ramp. When `compute.idle_only = true`, the effective per-cycle concurrency now scales smoothly with current load (`max(1, int(exploration_concurrency × (1 − load)))`), so Vaner shares the machine gracefully under light foreground load instead of the previous binary "run at full speed or skip the cycle" behaviour. The hard idle-skip cutoff still applies above `idle_cpu_threshold` / `idle_gpu_threshold`.
- Added multi-endpoint exploration routing. Set `exploration.endpoints = [...]` in `.vaner/config.toml` to dispatch exploration LLM calls across a pool of OpenAI-compatible endpoints (vLLM, remote ollama, LM Studio, etc.) via weighted round-robin. The pool tracks per-endpoint health (consecutive failures, total calls) and skips endpoints that have failed three or more times in a row for a 60-second cooldown before trying them half-open again. When `exploration.endpoints` is empty, behaviour is unchanged and Vaner uses the existing single-endpoint path.

### Fixed

- Fixed primer grammar: step 1 of the canonical primer now reads "Prepare context early" (imperative, parallel with steps 2 and 3) instead of "Prepared context early" — caught by Vaner's own `/vaner:next` card-pick flow during live testing.
- Fixed the plugin's SessionStart hook preview of missing-binary tool names: now advertises the real Claude Code namespacing (`mcp__plugin_vaner_vaner__vaner.resolve`, etc.) instead of the pre-namespace form.
- Fixed dogfood skill drift: `.cursor/skills/vaner/vaner-feedback/SKILL.md` in this repo was stuck on the v0.2.0 tool names (`list_scenarios`, `report_outcome`); now matches the canonical v0.6.x template.

### Docs

- Added `docs/performance.md` with the full ponder-throughput tuning ladder (`exploration_concurrency` → `OLLAMA_NUM_PARALLEL` → `CUDA_VISIBLE_DEVICES` multi-GPU → multi-endpoint pool) and an emphatic warning about raising concurrency without a concurrent backend.
- Added a scripted / non-interactive mode section to `docs/claude-plugin.md` covering `--permission-mode bypassPermissions`, `--allowedTools`, the MCP tool naming convention, and the minimum CLI version (v0.6.0) required for the primer's tool references to resolve.
- Added a CLI-version parity note to `CONTRIBUTING.md` for the plugin distribution.

### CI

- Added `.github/workflows/validate-claude-plugin.yml`: parity scripts (skill, primer, version), JSON well-formedness, hook smoke tests (both missing-binary and vaner-present branches), and `claude plugin validate`.
- Added `.github/workflows/creds-tripwire.yml`: narrow `git grep` for known test-only credentials and private-infrastructure hostnames on every PR and push.

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
