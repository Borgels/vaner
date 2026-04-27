# 2026-04 Full-Scale Vaner Benchmark Plan

## Goal

Re-benchmark Vaner 0.8.7+ against the claims that matter now:

1. Vaner improves answer quality when an assistant consumes prepared context.
2. Vaner improves time-to-answer and marginal model cost through ready predictions/adoption.
3. Vaner's Deep-Run maturation loop improves drafts without self-judging or stale overnight output.
4. MCP and cockpit integration remain stable while the benchmark is running.
5. Performance scales predictably across local RTX 5090, single Spark, and dual-Spark deployments.

The old reference points are:

- `docs/benchmarks/README.md`: 0.8.0 session-replay results. Best local qwen3.5:35b Q4 result was aggregate `+1.22` at idle multiplier `0.5`; best Spark Qwen3.5-35B-A3B-FP8 result was `+1.73` at idle multiplier `2.0`.
- `docs/benchmarks/0.8.3-deep-run-validation.md`: Deep-Run gates were defined but real labelled-run numbers were deferred.
- `/home/abo/repos/Vaner-train/eval/benchmark/runs/spark_comparison.md`: earlier Spark run saturated recall but had `mean_prediction_lift=0.0`; Qwen3.6-35B-A3B failed on an older vLLM stack due unsupported `qwen3_5_moe`.

## Current Environment Baseline

- Vaner installed from local checkout as `0.8.7`.
- Cockpit daemon is running at `http://127.0.0.1:8473`.
- Local Ollama models available:
  - `qwen3.5:35b`
  - `qwen2.5-coder:32b`
- Spark primary node:
  - vLLM is serving `Qwen/Qwen3.5-35B-A3B-FP8` at the configured Spark OpenAI-compatible endpoint.
  - 121 GiB unified memory; currently memory-constrained by the active vLLM container.
- Spark secondary node:
  - 121 GiB unified memory; mostly free.
  - Ollama currently has tiny Qwen2.5 models only; use it as the clean host for new model pulls or Spark vLLM recipes.

## Model Matrix

### Required Local Baselines

Run these on the RTX 5090 via Ollama:

| Slot | Model | Purpose |
|---|---|---|
| local-current | `qwen3.5:35b` | Direct continuity against 0.8.0/0.8.3-era runs. |
| local-code | `qwen2.5-coder:32b` | Developer-archetype regression check. |
| local-gemma | latest Gemma model that fits 32 GB VRAM | Non-Qwen family comparison. |

### Required Spark Baselines

Run these through OpenAI-compatible vLLM/Ollama endpoints:

| Slot | Host | Model | Purpose |
|---|---|---|---|
| spark-current | spark01 | `Qwen/Qwen3.5-35B-A3B-FP8` | Existing known-good vLLM control only, not the main Spark benchmark. |
| spark-large-primary | dual Spark | `Intel/Qwen3.5-122B-A10B-int4-AutoRound` | First real Spark target: 120B-class, `tp=2`, more memory headroom than FP8. |
| spark-large-fp8 | dual Spark | `Qwen/Qwen3.5-122B-A10B-FP8` | Quality cross-check if INT4 runs cleanly and memory allows. |
| spark-large-alt | dual Spark | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` or `openai-gpt-oss-120b` | Non-Qwen 120B-class comparison. |
| spark-gemma | spark02 or dual Spark | Gemma4-26B-A4B or latest available Gemma recipe | Smaller non-Qwen family comparison, not a replacement for 120B cells. |

If Qwen3.6 still fails model-type support, record the preflight failure as a benchmark artifact and replace the quality cell with the nearest supported Qwen3.5/Qwen3.6 sibling.

Spark benchmark cells should use 120B-class models wherever possible. The 35B model is retained only as a continuity/control cell because it is already running on `spark01`.

## Benchmark Tracks

### Track A: Answer Quality

Harness: Vaner-train session replay benchmark, or a repaired equivalent if the branch currently lacks `session_replay_bench.py`.

Arms:

- Naked: answer model gets no corpus context.
- RAG: naive top-K retrieval from the same corpus.
- Vaner-live: Vaner `resolve`/MCP prepared briefing.
- Vaner-adopt: adopted ready prediction/package when available.
- Agent-loop optional: same tools for all arms, Vaner only changes starting context.

Datasets:

- Existing session corpus in Vaner-train.
- 8 sessions minimum across developer, researcher, writer, learner.
- Add planner/support if the corpus is ready.

Metrics:

- Blind judge score, 1-10.
- `Vaner - naked`, `Vaner - RAG`, and per-archetype deltas.
- Preference counts.
- Briefing tokens, answer tokens, wall-clock latency.
- Adoption hit rate and adopted-answer quality.

Ship gates:

- Aggregate `Vaner - naked >= +0.5`.
- Aggregate `Vaner - RAG >= 0.0` for the stronger claim.
- No archetype below `-0.3` unless explicitly called out as a known regression.
- Adoption hit rate `>= 50%`, adopted-answer quality above naked, and adopt latency below live Vaner.

### Track B: Deep-Run Maturation

Harness: `/home/abo/repos/Vaner-train/eval/run_deep_run_bench.py`.

Corpus:

- `/home/abo/repos/Vaner-train/tests/fixtures/deep_run/`
- 10 synthetic labelled sessions each for developer, planner, researcher, writer.
- If possible, add a small human-labelled slice before the final run.

Presets:

- conservative
- balanced
- aggressive

Judges:

- Programmatic `reference_match` for smoke and deterministic CI.
- Stronger external LLM judge through vLLM/OpenAI-compatible endpoint for final numbers.

Ship gates from 0.8.3:

- maturation effectiveness `>= +0.30`
- judge agreement kappa `>= 0.70`
- persistence rate in `[0.25, 0.55]`
- rollback rate `<= 0.15`
- stale-by-morning rate `<= 0.15`
- per-archetype mean delta `>= +0.15`

### Track C: Prediction and MCP End-to-End

Harnesses:

- Vaner MCP tools in a real client session.
- Cockpit HTTP/SSE probes.
- `eval/prediction_bench.py`, `eval/next_query_bench.py`, and `eval/scenario_match_bench.py` where they still match the current API.

Metrics:

- MCP tool success/error rate.
- `resolve`, `suggest`, `adopt`, `feedback` latency percentiles.
- Active prediction freshness.
- Prediction top-1/top-3 hit rate where labels exist.
- SSE reconnect count and cockpit HTTP 4xx/5xx count.

Required smoke checks before every long run:

- `vaner doctor --path . --cockpit-url http://127.0.0.1:8473`
- `/status`, `/bootstrap`, `/events/stream`, `/predictions/active`
- `vaner.resolve`, `vaner.feedback`, and at least one dashboard/open-app interaction if the client supports MCP Apps.

### Track D: Performance and Scaling

Sweep dimensions:

- `exploration_concurrency`: 1, 2, 4, 8
- idle multiplier: 0.25, 0.5, 1.0, 2.0
- max context tokens: 2048, 4096, 8192
- backend: local Ollama, spark vLLM single node, spark vLLM dual node

Metrics:

- precompute cycle duration
- LLM call count and failure rate
- tokens generated per cycle
- scenarios spawned/completed
- prediction readiness distribution
- CPU/GPU/memory utilization where available
- cockpit responsiveness during load

Gate:

- No performance cell may silently degrade to heuristic-only summaries.
- For concurrent cells, throughput must improve or the run must explain the backend-side serialization bottleneck.

## Execution Order

1. Freeze versions and environment:
   - record `git rev-parse HEAD` for Vaner and Vaner-train
   - record `vaner --version`, `ollama list`, vLLM `/v1/models`, Docker image tags
   - export `.vaner/config.toml` snapshots for each mode
2. Smoke test Vaner locally:
   - doctor
   - cockpit endpoints
   - one MCP resolve/feedback loop
   - one short Deep-Run start/stop
3. Repair benchmark harness drift:
   - ensure Vaner-train imports current Vaner APIs
   - restore or replace missing session-replay renderer/compare scripts
   - add structured run metadata if missing
4. Run 5-minute smoke cells:
   - local qwen3.5
   - spark01 Qwen3.5 FP8
   - spark02 new model preflight
5. Run Track A full session replay:
   - local qwen3.5
   - local qwen2.5-coder
   - local Gemma
   - spark Qwen3.5
   - spark Qwen3.6/Gemma
6. Run Track B Deep-Run:
   - reference judge first
   - LLM external judge final
7. Run Track C MCP/cockpit soak:
   - 60-120 minutes while Track A or D load is active
   - collect daemon logs and cockpit HTTP error counts
8. Run Track D scaling sweeps:
   - local concurrency sweep
   - spark single-node sweep
   - dual-Spark large-model run if setup is stable
9. Analyze and publish:
   - raw JSON for every run
   - markdown report per run
   - aggregate comparison table
   - failure log with fixes or benchmark-exclusion rationale

## Output Layout

Use a new run root:

```text
eval/runs/full-scale-20260426/
  metadata/
    vaner-git.txt
    vaner-train-git.txt
    local-ollama-models.txt
    spark01-vllm-models.json
    spark02-vllm-models.json
    config-*.toml
  track-a-session/
  track-b-deep-run/
  track-c-mcp-soak/
  track-d-performance/
  aggregate.json
  aggregate.md
```

Mirror final human-readable reports into `docs/benchmarks/`.

## Immediate Next Actions

1. Keep cockpit running from the current checkout and watch `/tmp/vaner-cockpit.log`.
2. Pull or serve the first Gemma model on `spark02`, because it is currently free.
3. Download and serve `Intel/Qwen3.5-122B-A10B-int4-AutoRound` across `spark01` + `spark02` with `max_model_len=65536` for smoke testing.

## Executed Result: Spark 122B Scenario Time Sweep

Run root:

```text
/home/abo/repos/vaner/.vaner/bench-runs/full-scale-20260426T215931Z/track-d-smoke/scenario-time-sweep-spark-qwen122b-final-thematic/
```

Model and endpoint:

- Exploration, answer, and judge model: `Intel/Qwen3.5-122B-A10B-int4-AutoRound`
- Endpoint: configured Spark OpenAI-compatible vLLM endpoint
- Backend mode: OpenAI-compatible vLLM, LLM exploration active

Results:

| Time budget | Human context | Turns | Exact | Partial | Mean score | Mean scenarios | Mean precompute |
|---:|---|---:|---:|---:|---:|---:|---:|
| 30s | short read | 12 | 100% | 0% | 0.9542 | 14.7 | 30.0s |
| 90s | reading a response | 12 | 100% | 0% | 0.9542 | 15.9 | 90.0s |
| 300s | reading + testing code | 12 | 100% | 0% | 0.9500 | 28.9 | 294.0s |

Promotion gate:

- `time_budget_30s_pass`: pass
- `time_budget_90s_pass`: pass
- `time_budget_300s_pass`: pass
- Overall: pass

Regression checks after the run:

```text
uv run pytest tests/test_intent/test_frontier.py tests/test_engine/test_deep_drill.py tests/test_engine/test_token_budget_per_prediction.py tests/test_broker/test_selector.py tests/test_intent/test_adapter_security.py tests/test_clients/test_structured_output.py tests/test_clients/test_reasoning_mode.py tests/test_daemon/test_generator.py
# 91 passed

cd ui/cockpit && npm run build
# passed

cd ui/cockpit && npm run test -- src/components/EventStreamPanel.test.tsx src/components/HardwareProfilePanel.test.tsx src/components/BundleSummaryCard.test.tsx
# 25 passed
```
4. Repair Vaner-train session replay harness drift if `session_replay_bench.py` is intentionally absent on the current branch.
5. Start with short smoke runs before launching overnight-scale sweeps.
