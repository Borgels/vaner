# Performance tuning

Vaner's "ponder" loop runs one scenario at a time by default. Under a real workload on a GPU box you can get ~3–4× the throughput by wiring three knobs together:

1. Raise `compute.exploration_concurrency` in `.vaner/config.toml`.
2. Raise `OLLAMA_NUM_PARALLEL` on the exploration server (if using ollama).
3. Add a pool of exploration endpoints, not just one.

Each of these does a different thing. They stack.

## 1. `compute.exploration_concurrency`

This tells Vaner how many scenarios to explore in parallel per precompute cycle. Default is `4`; the daemon's exploration loop now wraps all LLM calls in an `asyncio.Semaphore(exploration_concurrency)`, so concurrent scenarios fan out up to that bound.

```toml
[compute]
exploration_concurrency = 4
```

Start at 4. Push higher only after confirming the back-end can serve concurrent requests — otherwise you just queue up work inside the LLM server and waste wall-clock.

### Idle-aware ramp

When `compute.idle_only = true` (the default), Vaner now scales concurrency with current host load:

| Host load (max of CPU / GPU) | Effective concurrency (config = 4) |
| ---: | ---: |
| 0.00 | 4 |
| 0.25 | 3 |
| 0.50 | 2 |
| 0.75 | 1 |
| 0.90 (skipped per `idle_only`) | — |

So you get full throughput when the box is idle and degrade gracefully under load, rather than the prior binary "run at full speed or skip entirely" behavior. The hard skip still triggers above `idle_cpu_threshold` / `idle_gpu_threshold`.

Set `idle_only = false` for an always-on compute box where the daemon should never back off.

## 2. ollama server concurrency

Ollama's default is **one** concurrent request per server. Raise this if you're using ollama as the exploration backend:

```bash
# ~/.config/systemd/user/ollama.service or your equivalent
Environment="OLLAMA_NUM_PARALLEL=4"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

Or inline for a test:

```bash
OLLAMA_NUM_PARALLEL=4 ollama serve
```

Match `OLLAMA_NUM_PARALLEL` to `compute.exploration_concurrency`. Anything less on the server side means Vaner will queue at the server; anything more is slack you can't fill unless you also raise `exploration_concurrency`.

## 3. Multi-GPU ollama

Ollama supports multi-GPU via standard CUDA env vars. No Vaner change required:

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 OLLAMA_NUM_PARALLEL=8 ollama serve
```

Ollama will distribute the model across the visible GPUs (VRAM permitting) and serve up to 8 concurrent requests. Raise `compute.exploration_concurrency` to 8 in `.vaner/config.toml` to actually consume that capacity.

This single-server-multi-GPU setup is the cheapest meaningful scale-out: one process, one model load, N GPU workers.

## 4. Multi-endpoint pool

Beyond one server, Vaner can dispatch across a pool of exploration endpoints. Each entry is an OpenAI-compatible URL (vLLM, ollama's `/v1` shim, remote server, …) with its own model and weight. The pool round-robins across entries and tracks per-endpoint health, skipping any endpoint that fails 3+ times in a row until a 60-second cooldown elapses.

```toml
[[exploration.endpoints]]
url = "http://gpu-host-01.local:8000/v1"
model = "Qwen/Qwen2.5-Coder-32B"
weight = 1.0

[[exploration.endpoints]]
url = "http://gpu-host-02.local:8000/v1"
model = "Qwen/Qwen2.5-Coder-32B"
weight = 1.0

[[exploration.endpoints]]
url = "http://gpu-host-03.local:8000/v1"
model = "Qwen/Qwen2.5-Coder-32B"
weight = 2.0   # 2× share of traffic
```

Weight semantics: traffic share is proportional to weight within the pool. `weight = 0` disables the entry entirely. `api_key_env = "NAME_OF_ENV_VAR"` reads a bearer token from the named environment variable (per-entry, so different endpoints can have different keys).

When `exploration.endpoints` is non-empty, Vaner uses the pool. When empty (the default), Vaner falls back to the legacy single-endpoint config (`exploration_endpoint` / `exploration_model` / `exploration_backend`) — existing behaviour, unchanged.

Health tracking is coarse: three consecutive failures → 60-second cooldown. On the next call after cooldown the endpoint is tried half-open and either recovers (counters reset) or re-arms the timer. When *every* endpoint is in cooldown the pool still attempts the least-recently-failed one rather than refusing the call.

## Benchmarking

Before and after each change, measure scenarios-per-minute on a fresh `.vaner/`:

```bash
rm -rf .vaner
vaner init --path . --no-interactive
vaner up --path .
sleep 600  # let the daemon ponder for 10 minutes
vaner status
# Look at the `scenarios:` counter; divide by 10 for per-minute rate.
```

Expected ballpark on a single RTX 4090 with ollama + qwen2.5-coder:7b:

- `exploration_concurrency=1, OLLAMA_NUM_PARALLEL=1` → baseline (≈ X scenarios/min)
- `exploration_concurrency=4, OLLAMA_NUM_PARALLEL=4` → 3–4× baseline
- Multi-GPU ollama, concurrency=8, OLLAMA_NUM_PARALLEL=8 → approaches 8× baseline
- Multi-endpoint pool across N hosts → approaches (N × single-host throughput)

Actual numbers depend heavily on model size, GPU memory bandwidth, and scenario content size. Measure on your hardware rather than extrapolating.

## What's not scaled

Single-daemon architecture still applies: one watcher process, one frontier, one scenario store. Scaling across multiple daemons with a shared frontier and distributed scenario reservation is a deeper architectural change (not yet implemented). With the knobs above you can typically saturate a single GPU box or a small multi-GPU rig without needing multi-daemon.
