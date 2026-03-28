# Vaner

A predictive context platform. Local-first execution, platform-backed coordination.

The core idea: instead of only reacting at prompt time, the system works in the background to prepare likely-useful context ahead of time. At prompt time, a broker decides how much of the prepared material is still relevant and safe to use.

See [`docs/vaner_report.docx`](docs/vaner_report.docx) for the full strategic report.

## Structure

```
apps/
  studio-agent/    LangGraph proof of concept — a single coding/planning agent
                   that can read and navigate the Vaner repo

docs/
  vaner_report.docx        Strategic report (product, architecture, business model)
  agent-architecture.md    Current agent design + proposed two-agent split
```

## Status

Working proof of concept in `apps/vaner-broker`. The agent can answer questions about the repo using read-only tools (list files, read file, find files, grep). Runs on Devstral via Ollama + LangGraph Studio.

Next step: split into a `repo-analyzer` (background preparation) and a `broker` (prompt-time routing). See [`docs/agent-architecture.md`](docs/agent-architecture.md).

## Getting Started

```bash
cd apps/vaner-broker
cp .env.example .env
pip install -e . "langgraph-cli[inmem]"
langgraph dev
```
