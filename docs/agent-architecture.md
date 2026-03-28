# Agent Architecture

## Current: Single-Agent Flow (`apps/studio-agent`)

This is the working LangGraph proof of concept. It lives in `apps/studio-agent` and is intentionally simple — one agent, one loop, a handful of read-only tools.

### What it does

The agent receives a free-text user request and has access to four tools that let it read the repository:

| Tool | Purpose |
|---|---|
| `list_files(path)` | List directory contents |
| `read_file(path)` | Read a text file |
| `find_files(pattern, path)` | Glob-style file search |
| `grep_text(query, path, ...)` | Text search across files |

All paths are sandboxed to the repo root (`~/repos/Vaner`). The agent cannot write anything.

### Flow

```
user input
    │
    ▼
first_model_call
    │
    ├─ no tool calls → END (direct answer)
    │
    └─ tool calls detected
           │
           ▼
       run_tools  (executes all tool calls in sequence)
           │
           ▼
    second_model_call  (answers the user using tool results)
           │
           ▼
          END
```

The model is `devstral` via Ollama (`ChatOllama`, temperature=0). Tool calls are parsed from raw JSON in the model output — one call per line, or a single JSON object.

### State

```python
@dataclass
class State:
    user_input: str
    response: str
    tool_requests: list[dict]   # parsed tool calls from first model turn
    tool_results: list[dict]    # results after execution
```

Runtime context (`Context`) supports a single configurable param (`my_configurable_param`) that gets appended to the system prompt.

### What it's good for right now

- Answering questions about the repo (navigation, code search, reading files)
- Quick one-shot planning tasks
- Debugging and exploring the codebase

### What it can't do yet

- Write or modify files
- Work across multiple turns (stateless between requests)
- Run anything autonomously in the background
- Handle large repos efficiently (no chunking, no indexing)

---

## Next: Two-Agent Split

This is the natural next step based on the Vaner thesis: **separate preparation from brokerage**.

The report describes the core system principle as:

> *"A background preparation engine that observes, predicts, and prepares artefacts — and a prompt-time broker that decides, at each prompt, how much of the already-prepared material is still useful and safe to use."*

Applied to the current agent, that means splitting into two:

### `repo-analyzer`

**Responsibility:** Understand the repository and produce structured context artefacts.

- Triggered on demand or on file change
- Walks the repo, reads files, builds a lightweight index (symbols, file summaries, dependency graph)
- Outputs structured artefacts: file summaries, key entry points, recent change context
- Stateless between runs — writes to a local cache/store
- Tools: current read-only set + a `write_artefact` tool targeting the cache only

This agent does the expensive, slow work *ahead of time*, not at prompt time.

### `broker`

**Responsibility:** Handle the user request and decide what prepared context to use.

- Receives the user request
- Reads available artefacts from the analyzer's cache
- Decides which artefacts are still fresh and relevant (or none)
- Assembles the final context and routes to the right model
- Returns the answer

This agent stays fast. It doesn't re-read the whole repo on every request — it trusts (or validates) what the analyzer already prepared.

### Why this split matters

The current agent re-reads everything from scratch on every request. For small repos that's fine. For anything real-sized, it becomes slow and expensive. The split also makes it possible to pre-compute context during idle time — which is the core Vaner thesis.

### What to build first

1. **Extract the read tools** into a shared utility module so both agents can use them
2. **Add a simple file-based artefact store** (JSON or markdown files under `.vaner/cache/`)
3. **Build `repo-analyzer`** as a second LangGraph graph that writes summaries to the cache
4. **Update `broker`** (rename the current agent) to check the cache before falling back to direct reads
5. **Add a freshness check** — the broker should know if a cached artefact is stale (based on file mtime vs cache timestamp)

That's enough to prove the core thesis without over-engineering it.

---

## Repo Layout (current + proposed)

```
apps/
  studio-agent/          ← current PoC (keep as-is, rename to broker later)
    src/agent/graph.py   ← single-agent flow
  repo-analyzer/         ← new: background preparation agent (not built yet)

docs/
  vaner_report.docx      ← strategic report
  agent-architecture.md  ← this file

.vaner/
  cache/                 ← artefact store (not built yet)
```

---

## Agent Roadmap

```
Phase 1 (now)       broker only — current PoC, direct reads, no cache

Phase 2 (next)      repo-analyzer → broker
                    Analyzer pre-computes file/dir summaries into .vaner/cache/
                    Broker checks cache before doing direct reads
                    → see build-spec-two-agent.md

Phase 3             indexer + synthesizer → broker
                    Split analyzer in two:
                    - indexer: fast, deterministic (symbol maps, dependency graph)
                              runs on RTX 5090 / small models
                    - synthesizer: slow, LLM-heavy (patch candidates, design sketches)
                              runs on DGX Spark / 70B+ models
                    Broker picks from both artefact pools

Phase 4             + eval agent
                    Scores artefact quality, feeds back into ranker
                    Only worth building once artefact volume is real
```

The two-agent split is the hard conceptual move. Phases 3 and 4 are just clean subdivisions once the cache contract and broker routing are solid.

---

## Design Principles

From the strategic report:

> *"Use AI to generate and generalize. Use ML to prioritize. Use system design to control."*

Applied here:
- The **analyzer** generates and generalizes (LLM-driven summarization)
- The **broker** prioritizes (what's fresh, what's relevant, what to skip)
- The **artefact store + freshness rules** are system design — deterministic, auditable, controllable

Don't add a third agent until the two-agent split is working and proven useful.
