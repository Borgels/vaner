# Vaner System Architecture

Vaner consists of a background preparation engine and a prompt-time broker.

- Background: collect signals, plan targets, generate artefacts, persist in SQLite
- Hot path: select artefacts, enforce policy, compress, assemble context package
- Optional thin proxy: enrich OpenAI-compatible requests and forward

Core rule: no LLM calls on the broker hot path.
