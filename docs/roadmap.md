# vaner.ai — Architecture, Roadmap & Execution Plan

_Generated: 2026-03-28. Living document — update as architecture evolves._

See GitHub Issues (milestones: Phase 0–3) for the execution backlog.

---

## What vaner.ai is

A predictive context runtime that sits between a developer's active work surface and their model(s). It observes local state continuously, prepares likely-useful context artifacts in the background, and at prompt time makes a fast, principled decision about what pre-prepared context to inject — and how much of it is still valid.

**The key split:**
- Background preparation engine (LangGraph, durable, async)
- Prompt-time broker (lean Python, <30ms, never LangGraph)

---

## Architecture Principles

1. Local-first. Fresh signals and privacy-sensitive data stay local by default.
2. Separate hot path from background path. Never run LangGraph on the hot path.
3. Prefer artifacts over autonomous actions. The system prepares; the developer decides.
4. Durable execution is a first-class product requirement, not an implementation detail.
5. Measurable value over architectural elegance. Instrument everything from day one.
6. Graceful degradation. Empty store → passthrough. DGX offline → fallback. Never crash the hot path.

---

## System Components

| Component | Responsibility | Hot path? |
|---|---|---|
| Event Collector | inotify, git hooks, editor IPC | No |
| State Engine | current branch, active files, recent diff | No (read: yes) |
| Artifact Store | SQLite KV with staleness metadata and scores | Read: yes |
| Preparation Engine | LangGraph: generate, rank, refresh artifacts | No |
| Prompt-Time Broker | fast reuse decision + context injection | **YES — keep lean** |
| Model Router | route inference to best available backend | Partial |
| Policy Layer | invalidation rules, budgets, privacy filters | Both |
| Eval & Telemetry | async metrics, LangSmith, local SQLite | No (async) |

---

## Machine Roles

| Machine | Role |
|---|---|
| Ubuntu desktop | Daemon, Event Collector, State Engine, Broker, Policy, Artifact Store |
| RTX 5090 | Low-latency local inference, fast summarization |
| DGX Spark (week 10+) | Heavy batch generation, 70B+ models, vLLM, embeddings |
| Remote APIs | Claude (planning), OpenAI (fallback) — budget-controlled |

---

## MVP Scope

**In scope:**
- Event Collector (inotify + git hooks)
- State Engine (branch, active files, recent diff)
- Artifact types: `file_summary`, `diff_summary`, `module_summary`
- Preparation Engine (LangGraph, SqliteSaver, retry/backoff)
- Artifact Store (SQLite)
- Lean prompt-time broker (<30ms hot path)
- Durable execution (checkpointing, retry, dead-letter, cancellation)
- Basic telemetry (local SQLite metrics DB)
- CLI: init, daemon, status, inspect, metrics

**Out of scope for MVP:**
- Editor plugin / IDE integration (Phase 2)
- `symbol_trace`, `impact_slice` artifacts (Phase 2)
- DGX-dependent pipelines (Phase 2)
- Team/shared context (Phase 3)
- Cloud sync, web UI, fine-tuning

---

## 90-Day Execution Sequence

| Weeks | Focus | Key Issues |
|---|---|---|
| 1–2 | Durable substrate | #1 job store, #2 retry, #3 dead-letter, #7 logging, #8 status CLI |
| 3–4 | Observation layer | #9 event collector, #10 state engine, #20 vaner init, #4 crash recovery |
| 5–6 | Artifact preparation | #11 prep engine, #13 SQLite store, #5 cancellation, #6 priority queue |
| 7 | Scoring + diff | #12 artifact scoring, #17 diff_summary |
| 8 | Hot path broker | #14 lean broker, #15 contamination guard, #16 daemon service |
| 9 | Model routing + telemetry | #18 model router, #19 telemetry store |
| 10 | DX + DGX | #21 inspect CLI, #25 DGX integration |
| 11–12 | Eval + Phase 1 close | #23 eval loop, #22 editor integration spike |

**Critical path:** #1 → #4 → #9 → #11 → #14 → #16

---

## Open Architecture Questions

1. What is the prompt interception model? (proxy, editor extension, or explicit CLI?)
2. How does the scorer know what's relevant without the actual prompt?
3. How does contamination guard handle mid-refactor partial state?
4. When is the first real external user test, and with whom?
5. How does vaner handle monorepos / multi-repo setups?

---

## Model Strategy (updated 2026-03-28)

**Principle: local-first, cloud as last resort.**

### Hardware
| Machine | Role | Models |
|---|---|---|
| RTX 5090 (32GB) | Current primary | devstral:14B, qwen2.5-coder:32b |
| DGX Spark (128GB, ~1 week) | Heavy compute | qwen2.5:72b, qwen2.5-coder:72b @ 4-bit, vLLM |
| Cloud | Last resort only | Claude (hard tasks only, budget-gated) |

### Model assignments (target state with DGX)
- File/diff summarization → devstral on RTX 5090
- Code understanding, builder queries → qwen2.5-coder:72b on DGX
- Multi-file architectural planning → qwen2.5:72b on DGX
- Eval judge → qwen2.5:72b on DGX (was Claude Haiku)
- OpenClaw subagents → switch default to ollama/qwen2.5:72b once DGX online

### Cloud spend targets
- Today (RTX 5090 only): Claude for subagent orchestration + eval judge
- With DGX: Claude only for genuinely hard reasoning where local clearly insufficient
- Config: \`cloud_enabled: false\` default in Model Router; explicit opt-in per task type
