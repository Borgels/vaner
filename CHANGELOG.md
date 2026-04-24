# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.3] - 2026-04-24

### Added

#### Overnight / Deep-Run Mode (WS1–WS3)
- **Policy layer** distinguishing intent ("user is away for the night") from incidental idleness. Introduces a persisted `DeepRunSession` record (`src/vaner/intent/deep_run.py`, `src/vaner/store/deep_run.py`) with single-active-session enforced by a UNIQUE partial index on `status='active'` plus a defensive store-layer check. Sessions resume on daemon restart; expired sessions auto-close with `cancelled_reason="expired_on_restart"`.
- **Three presets** (Conservative / Balanced / Aggressive) compose the existing engine knobs (exploit/invest/no-regret ratio biases, drafter thresholds, frontier weights, `idle_curve_multiplier`, per-cycle utilisation). `src/vaner/intent/deep_run_policy.py` ships the immutable preset table + four horizon biases (`likely_next` / `long_horizon` / `finish_partials` / `balanced`) + the focus admission gate (`active_goals` / `current_workspace` / `all_recent`).
- **`PredictionGovernor.Mode.DEEP_RUN`** added alongside BACKGROUND / DEDICATED / BUDGET. Continues unless explicitly stopped; pause/resume gating is delegated to the engine via gate probes.
- **Resource / cost / locality gates** (`src/vaner/intent/deep_run_gates.py`):
  - `ResourceGateProbe` Protocol + `NoOpResourceGateProbe` default for tests; `evaluate_resource_gates()` returns the active pause-reason set (battery / thermal / user-input / engine-error-rate).
  - `try_consume_cost()` — thread-safe in-memory cumulative spend counter, gates remote calls when `cost_cap_usd > 0`. `cost_cap_usd = 0` (the default) means *no remote spend permitted* — a hard router-layer block, not a budget warning.
  - `is_remote_call_allowed()` — `local_only` blocks remote URLs.
  - Routing-state singleton (`set_active_session_for_routing()` / `get_active_session_for_routing()`) lets the router consult the active session without parameter-passing through every LLM call.
- **Router-layer enforcement** (`src/vaner/router/backends.py`): new `_enforce_deep_run_gates_for_remote()` helper plus `DeepRunRemoteCallBlockedError` raised when locality or cost gates fire. Wired into the four remote-call paths (sync + streaming, primary + fallback). Zero behaviour change when no Deep-Run session is active.
- **Maturation revisiting** (`src/vaner/intent/deep_run_maturation.py`) — the central new mechanism. A maturation pass re-enters the drafter on already-`READY` predictions to deepen evidence, refine drafts, and resolve contradictions. Critical defenses against the well-known same-model self-judging anti-pattern (per Anthropic Engineering's *Harness design for long-running apps*):
  - **Generator/judge role separation.** Drafter and judge are distinct callables with distinct prompts. Default `JudgeCallable` is a programmatic, skeptical, rubric-based judge — `kept=False` unless concrete contract clauses are satisfied.
  - **Per-pass `MaturationContract`** built from the prediction's current weakness signal *before* the drafter runs. Universal forbidden clauses (no length-only growth, no silent evidence-ref removal, anchor preserved) plus weakness-specific must-clauses (e.g. "≥2 new evidence_refs not in the prior set").
  - **Probationary persistence + diminishing-returns thresholds.** Kept maturations are probationary for N cycles; subsequent reconciliation contradictions roll them back via `rollback_kept_maturation()`. Persistence threshold tightens with each `revision`.
  - `select_maturation_candidates()` ranks READY predictions by goal-confidence × evidence-room × revision decay × state factor, respecting per-prediction failure caps and probation windows.
- **Honest 4-counter discipline** (spec §9.2 / §14.1): `DeepRunSession` and `DeepRunSummary` carry `matured_kept`, `matured_discarded`, `matured_rolled_back`, `matured_failed` as four separate values. Every surface (CLI, MCP, HTTP, cockpit) renders all four; never collapses into a single inflated "matured" total.
- **`PredictionRun` extension** with `revision`, `last_matured_cycle`, `probationary_until_cycle`, `failed_revisits`, `maturation_eligible`.

#### Surfaces (WS4)
- **CLI** — `vaner deep-run start | stop | status | list | show` (`src/vaner/cli/commands/deep_run.py`). `--until` accepts duration (`8h`, `45m`), time-of-day (`07:00`), or ISO-8601. Dual output: human Rich rendering + `--json` for machine consumers.
- **MCP** — five new tools (`vaner.deep_run.start`, `.stop`, `.status`, `.list`, `.show`); `vaner.status` extended with a `deep_run` field carrying the active session record.
- **Daemon HTTP** — `POST /deep-run/start`, `POST /deep-run/stop`, `GET /deep-run/status`, `GET /deep-run/sessions`, `GET /deep-run/sessions/{id}` (`src/vaner/daemon/http.py`).
- **Cockpit (React)** — `ui/cockpit/src/types/deepRun.ts` (TS schema mirrors), `ui/cockpit/src/api/deepRun.ts` (fetch helpers), `ui/cockpit/src/components/DeepRunPanel.tsx` (status pill + start card + history table). All four maturation counters surfaced separately.
- **Desktop hand-off** — `docs/0.8.3/desktop-hand-off.md` documents the wire format, SwiftUI scope, and integration points for the separate `vaner-desktop` repo (popover quick action, menu-bar indicator, preferences card).
- **Single canonical record across surfaces.** `DeepRunSession` row is the one source of truth; CLI / MCP / HTTP / cockpit all read it. Stable-schema serializers (`_session_to_dict` / `_summary_to_dict`) shared between CLI and MCP and HTTP.

#### Bench primitives + ship gates (WS5)
- `Vaner-train/eval/deep_run_bench.py` — `MaturationBenchOutcome`, `MaturationBenchMetrics`, `compute_maturation_metrics()`, `evaluate_ship_gates()`. Five binding ship gates encoded in `SHIP_GATES`:
  - `maturation_effectiveness` — external-judged mean improvement Δ ≥ +0.30 per kept pass.
  - `judge_external_agreement` — Cohen's κ ≥ 0.70 between in-engine and external judges. The anti-self-judging gate.
  - `persistence_rate_in_band` — kept fraction in [0.25, 0.55].
  - `probationary_rollback_rate` — ≤ 0.15.
  - `stale_by_morning_rate` — ≤ 0.15.
- Per-archetype floor: no archetype's mean Δ may be below +0.15; all four of writer/researcher/developer/planner must have ≥1 session in the fixture.
- Validation report at `docs/benchmarks/0.8.3-deep-run-validation.md` documents the gates + lists the remaining follow-up work (labelled fixture corpus, external judge wiring, bench run).

### Internal
- Tests: +211 across WS1–WS5. Full Vaner suite: 1020 passing, 14 skipped (zero new skips).
- Pre-existing tool-list assertions in `tests/test_mcp/test_protocol_roundtrip.py`, `test_scenario_tools.py`, `test_server_boot.py` updated to include the five new `vaner.deep_run.*` tools (26 tools total, up from 21).
- Hard safety gates verified in code + tests: cost-cap compliance (no overshoot under 100-thread concurrency), local-only fidelity (zero remote calls when `local_only`), single-active session enforcement, four-counter integrity, resume-on-restart, reconciliation rollback inside probation.

### Why this is different from idle mode
Idle mode answers a *resource* question ("is the machine free right now?") and must hedge against the user returning at any moment. Deep-Run answers a *policy* question ("is the user telling me they will be away for a long, predictable window and want me to use it well?") and adopts a different stance: longer per-cycle utilisation, broader frontier, deeper drafting bars, **maturation passes on already-ready predictions**, accumulated provenance, post-session summary. Deep-Run never *infers* itself from idleness alone — it requires explicit user opt-in via CLI / MCP / cockpit / desktop.

### Why this does not turn Vaner into an autonomous agent harness
Deep-Run only ever does *more* prepare and *more* promote work. The **prepare ≠ promote ≠ adopt ≠ execute** boundary is preserved: adoption requires an explicit MCP/HTTP/CLI call from a user or authorised agent; execution endpoints with `side_effects ∈ {"read", "mutate"}` are out of scope. Deep-Run lets Vaner *think* harder while you sleep; it does not let Vaner *do* anything you did not already authorise it to do during the day.

## [0.8.0] - 2026-04-23

### Added

#### Persistent prediction pool (WS6)
- **Registry survives across cycles.** `VanerEngine._merge_prediction_specs` now merges new specs into a long-lived `PredictionRegistry` instead of rebuilding one per cycle. Existing predictions keep their accumulated `evidence_score`, `scenarios_complete`, `prepared_briefing`, `draft_answer`, `thinking_traces`, and `file_content_hashes`.
- **Signal-driven invalidation.** New `src/vaner/intent/invalidation.py` emits `file_change`, `commit`, `category_shift`, and `adoption` signals from git + observation state. `PredictionRegistry.apply_invalidation_signals()` applies them: file-change halves weight and clears the briefing, commit stales phase-anchored predictions, category-shift demotes anchored predictions.
- **Per-path content hashes.** `src/vaner/daemon/signals/git_reader.py` gains `read_head_sha`, `read_commit_subjects`, and `read_content_hashes` (git `hash-object` with SHA-256 fallback). Briefing-attach sites capture hashes so invalidation can compare against disk state.

#### BriefingAssembler (WS9)
- `src/vaner/intent/briefing.py` — new `Briefing` / `BriefingSection` / `BriefingAssembler` with `from_prediction` / `from_paths` / `from_artefacts` builders. Single canonical briefing assembler; approximation warning latched so operators know when heuristic token counts are in play. Wired into the engine's evidence-threshold path (replaces the deleted `_synthesise_briefing_from_scenarios`) and MCP `_build_adopt_resolution`.

#### Drafter (WS10)
- `src/vaner/intent/drafter.py` — single `Drafter` class owning the rewrite + draft LLM templates and gate arithmetic. `VanerEngine._precompute_predicted_responses` routes through it; arc / pattern / history / goal-sourced predictions all drive the same path.

#### Workspace Goals (WS7)
- New `src/vaner/intent/goals.py` (`WorkspaceGoal`, `GoalEvidence`, `GoalSource`, `GoalStatus`) and `src/vaner/intent/branch_parser.py` (heuristic goal extractor from branch names like `feat/jwt-migration`).
- New `workspace_goals` SQLite table with indexes on `status` and `created_at`; `ArtefactStore.upsert_workspace_goal`, `list_workspace_goals`, `update_workspace_goal_status`, `delete_workspace_goal`.
- `VanerEngine._merge_prediction_specs` seeds `PredictionSpec(source="goal")` from active goals; goal-anchored predictions surface in `get_active_predictions()` and flow through the full readiness lifecycle.
- Four new MCP tools: `vaner.goals.list`, `vaner.goals.declare`, `vaner.goals.update_status`, `vaner.goals.delete`.

#### Unified resolution path (WS8)
- `VanerEngine.resolve_query(query, *, context, include_briefing, include_predicted_response) -> Resolution` — single canonical `query → Resolution` entry. Consults the prediction registry for a label-match (populating `predicted_response` from the cached draft and `alternatives_considered` from runners-up) and falls back to the heuristic `query()` path, building the Resolution from the returned ContextPackage via `BriefingAssembler.from_artefacts`.
- `Resolution.predicted_response`, `alternatives_considered`, and `briefing_token_used` / `briefing_token_budget` are now populated honestly on the adopt path; briefing rendering comes from `BriefingAssembler`.

#### Calibrated predictions
- `IsotonicCalibrator` in `src/vaner/intent/calibration.py` — pure-Python isotonic curve consumer, no scikit-learn at inference. Loaded from `calibration_curve.json` in the defaults bundle, applied after `IntentScorer._predict()` to convert raw GBDT scores into calibrated probabilities.
- Fail-closed: malformed curve JSON → uncalibrated fallback (same as pre-0.8.0 bundles).

#### Bundle integrity enforcement
- `DefaultsIntegrityError` — SHA256 mismatches in the defaults manifest now **raise** instead of silently returning a sentinel path. Set `VANER_DEFAULTS_ALLOW_MISMATCH=1` for permissive mode with a telemetry log accessible via `drain_checksum_mismatches()`.
- `DefaultsVersionError` — manifests can now declare `min_reader_version`; loader refuses bundles that require a newer vaner than the current runtime.

#### Event stream
- `scenarios` stage is now emitted by default alongside `prediction`, `calibration`, `draft`, `budget`. Operators can opt stages out with `VANER_EVENT_STAGES` env var or the `?stages=` query param.

### Changed
- Manifests in `src/vaner/defaults/*/manifest.json` are regenerated to match shipped file contents; checksum drift is now a hard error rather than a silent skip.
- `_version_tuple` in the defaults loader parses version strings robustly (stops at first non-digit per segment).

### Internal
- Tests: +28 across `test_defaults/test_loader_checksum.py` and `test_intent/test_calibration.py`.
- **0.8.0 architectural cleanup tests (+~100 across WS6–WS10 + WS7 + WS8):** `tests/test_intent/test_prediction_invalidation.py` (17), `tests/test_intent/test_briefing.py` (9), `tests/test_intent/test_drafter.py` (15), `tests/test_intent/test_goals.py` (28), `tests/test_engine/test_goal_seeded_predictions.py` (2), `tests/test_engine/test_resolve_query.py` (4), `tests/test_engine/test_file_change_invalidates_persistent_briefing.py` (2, WS6 end-to-end file-edit invalidation), extended `tests/test_daemon/test_signals.py` (+3), plus MCP-surface updates in `test_mcp/test_protocol_roundtrip.py`, `test_mcp/test_scenario_tools.py`, `test_mcp/test_server_boot.py`, and `tests/test_mcp_v2/test_goals_tool.py` (8). Full suite: 780 passing.

### Evaluation — three delivery modes, three validation tracks

Vaner has three independent delivery modes. The 0.8.0 evaluation validates each one on its own terms rather than asking a single metric to underwrite all three.

- **(A) Context augmentation for a backend LLM.** User prompts their frontier LLM → LLM calls Vaner via MCP → Vaner returns a prepared briefing → LLM answers. *Quality* claim.
- **(B) Instant-answer delivery via the predictive cache.** User sees ready predictions, clicks one, Vaner's prepared package is served directly — the frontier may not be called at all. *UX + performance + marginal-cost* claim.
- **(C) Persistent preparation engine.** Vaner accumulates evidence across cycles and invalidates only on real signals (file edits, commits, category shifts, adoption). *Architecture correctness* claim.

The framing is **not** "Vaner beats frontier models." It is: Vaner improves frontier-model responses through precomputed context (mode A), and sometimes bypasses the frontier call entirely by serving prepared work (mode B), with persistence (mode C) as the foundation that makes A and B compound across successive user turns.

#### Track A — answer-quality uplift (MCP-assisted flow)

Harness: `Vaner-train/eval/session_replay_bench.py` with `--judge-answer-quality --include-rag`. Naked (no context) vs RAG (naive top-K embedding retrieval) vs Vaner (prepared briefing). Blind shuffled judge, 1–10 absolute scores + preference.

Config: `qwen3.5:35b` ponder on local RTX 5090 via Ollama; `gpt-5.3-chat-latest` answer via OpenAI; `gpt-5` judge via OpenAI. 4 archetype sessions (developer / researcher / writer / learner), realistic trace idle (15-min cap, ranges 90s–780s per turn, total precompute ~3h wall-clock).

**Results (bench: `ws4-realdeploy-3way-20260423T210832Z.json`):**

| archetype | naked | RAG | Vaner | Δ (V−R) | wins: V / R / naked / tie |
|---|---|---|---|---|---|
| developer | 2.50 | **6.38** | 5.62 | −0.76 | 1 / 4 / 1 / 2 |
| researcher | 1.00 | 4.25 | **6.75** | **+2.50** | 5 / 1 / 0 / 2 |
| writer | 1.00 | 3.86 | **5.29** | **+1.43** | 4 / 1 / 0 / 2 |
| learner | 1.00 | **5.88** | 2.38 | −3.50 | 0 / 5 / 0 / 3 |
| **overall** | **1.38** | **5.09** | **5.01** | **−0.08** | 10 / 11 / 1 / 9 |

Vaner beats naked on every archetype (so context value is real) and roughly matches RAG overall, but **archetype variance is large**. Vaner wins clearly on researcher (+2.50) and writer (+1.43) — the archetypes where multi-turn semantic intent and cross-file context matter. Vaner loses on developer (−0.76, small) and learner (−3.50, large). Interpretation: naive embedding retrieval is hard to beat when the user's question has a clearly-matching passage in the corpus (algorithms textbook; Flask API docs in the dev's memory), and Vaner's strength is in synthesizing *across* evidence for narrative/research work.

**0.8.0 Track A ship-posture:** *do not* claim a blanket "Vaner beats RAG." Instead: "Vaner is competitive with RAG overall and wins clearly on long-horizon document and narrative work. On algorithms-study and code-reference queries, naive RAG retrieval is a strong baseline Vaner does not yet beat — WS8 unified resolve is the 0.8.1 focus that addresses this."

#### Track B — instant-adopt UX/perf (predictive cache)

Harness: same bench run, `--test-adopt` fields aggregated by `Vaner-train/eval/aggregate_track_b.py`. Measures adoption hit-rate (fraction of turns with a matching ready/drafting prediction), adopted-answer quality vs naked + vs Vaner-heuristic, and latency ratio (adopt path vs live heuristic path, both under the bench's judge-a-fresh-answer constraint).

**Results:**

| archetype | hit-rate | adopt | naked (adopted turns) | Vaner-heuristic (adopted turns) | latency adopt / Vaner |
|---|---|---|---|---|---|
| developer | 0% | — | — | — | — |
| researcher | 87.5% | 3.71 | 1.00 | 6.29 | 1701ms / 2351ms |
| writer | 50% | 3.00 | 1.00 | 4.00 | 2527ms / 2420ms |
| learner | 62.5% | 1.40 | 1.00 | 3.20 | 1672ms / 2386ms |
| **overall** | **50%** | **2.81** | **1.00** | **4.75** | **1898ms / 2379ms (ratio 0.80)** |

Hit-rate is above the 40% ship-gate floor — the adopt surface has real content half the time. Adopt quality is above naked (+1.81) but meaningfully below Vaner-heuristic (−1.94) — meaning the label-match heuristic in `VanerEngine.resolve_query` is often selecting a prediction whose prepared draft is close but not quite the user's actual prompt. WS8.1's unified resolve (semantic matching, not just label overlap) is designed to close this gap.

Bench-mode latency ratio 0.80 → adopt is ~20% faster than Vaner-heuristic because the briefing is already built; **production** latency ratio with a verbatim-draft-served adopt (no frontier call at all) would be ~0.01 (milliseconds vs seconds). The bench can't measure that directly because it always calls the answer model so the judge can score.

**0.8.0 Track B ship-posture:** adopt UX available for ~50% of prompts, produces better-than-naked answers, and is a latency improvement over the live pipeline even when still calling the frontier. Production prepared-draft-serving is a UX-side 0.8.1 follow-up.

Developer 0% hit-rate is a surprise worth investigation — likely a mismatch between the developer session's prompt vocabulary (Flask-specific verbs like "implement", "add", "debug") and the prediction labels synthesised by `_merge_prediction_specs`. Filed as WS8.1 investigation.

#### Track C — persistent preparation correctness (architecture)

Test-driven. 27 unit tests across `test_prediction_invalidation.py` + `test_prediction_persistence.py` cover the WS6 invariants: merge preserves accumulated state across cycles; file_change demotes + clears briefing; commit stales phase-anchored; category-shift demotes anchored; adoption marks spent; no-hash predictions untouched.

End-to-end integration smoke: `test_file_change_invalidates_persistent_briefing.py` runs two consecutive `precompute_cycle` calls on a real engine with a file edit in between and asserts the captured briefing is cleared and the invalidation reason recorded — plus the inverse that no edits means no clearing.

**Track C ship-gate:** all unit + integration tests green (status: 29 tests, all passing).

### Known limitations / deferred to 0.8.1

- **True agent-with-search baseline.** The Track A "naked" arm is zero-context, not "frontier model with its own search tool." Deferred: a harness that runs multi-call agent loops so Vaner's uplift can be measured against a realistic code-assistant control.
- **Same-family judge.** Track A uses `gpt-5` to judge `gpt-5.3-chat-latest` answers. Claude-as-judge (or another family) independence check is an 0.8.1 follow-up.
- **Workspace Goals.** Ships with branch-name inference and MCP declaration; commit-subject clustering and query-embedding clustering deferred.
- **MCP resolve convergence.** MCP `vaner.resolve` still uses its own scenario-store path; `VanerEngine.resolve_query` is the canonical Python API and both will converge in 0.8.1 via a thin daemon-HTTP wrapper.
- **SWE-Bench Verified.** Task-completion metric is 0.8.1+ — requires an Agentless-style harness replacing localization with Vaner, not yet built.

## [0.7.1] - 2026-04-22

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

#### Deep-drill on high-priority predictions

- Added a high-priority deep-drill pathway to the exploration frontier. Scenarios whose *effective* priority clears `exploration.deep_drill_priority_threshold` (default `0.60`) are now treated as high-confidence next-prompt predictions and get more compute invested:
  - `_explore_scenario_with_llm()` receives a `high_priority` flag and widens the LLM prompt to accept up to `exploration.deep_drill_max_followons` follow-on branches (default `5` vs. the original hard-coded `3`). The prompt is also tagged `[HIGH-PRIORITY]` so the model knows to invest effort in surfacing second-order context (callers, callees, tests, configuration) rather than stopping at breadth.
  - Children of high-priority parents inherit a decrementing `depth_bonus` budget (default `2`). The frontier's admission gate now allows `depth > max_exploration_depth` by up to `depth_bonus` hops, so a promising lineage can drill past the base depth cap without raising the cap for the whole frontier. The bonus shrinks by 1 per LLM hop so the drill-down is bounded.
  - High-priority branches use a softer `deep_drill_branch_decay` (default `0.88`) instead of the general `branch_priority_decay` (default `0.70`), so the deep line keeps ranking near the top of the heap instead of getting crowded out by fresh shallow seeds.
- Fixed a latent bug in `_explore_scenario_with_llm`: the `if not callable(self.llm)` guard previously returned a 2-tuple where the declared return type expected a 4-tuple, which would have raised at the call site. Now returns `([], [], "", 0.0)`.

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
