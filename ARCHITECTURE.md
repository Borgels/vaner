# Architecture

Vaner is a local-first predictive context engine for coding assistants.

## High-level flow

1. Signal collectors observe repository and editor activity.
2. The daemon prepares artefacts and relation edges in a local store.
3. The intent layer predicts likely next prompts and context targets.
4. The broker ranks and assembles context packages under policy controls.
5. CLI/API/proxy layers expose the same engine for different client surfaces.

## Core packages

- `src/vaner/engine/`: orchestration and runtime loop
- `src/vaner/intent/`: prediction, scoring, and frontier state
- `src/vaner/store/`: persistence and retrieval for artefacts/signals
- `src/vaner/broker/`: context selection and package assembly
- `src/vaner/router/`: OpenAI-compatible proxy and translation paths
- `src/vaner/cli/`: command-line controls and maintenance workflows

## Data boundaries

- Default mode is local-first with explicit repository scope.
- Safety and privacy policy modules gate what leaves local storage.
- Training and moat-sensitive workflows are intentionally isolated to private repos.

## Agent skills loop

- Vaner discovers workspace `SKILL.md` files and emits `skill_loaded` intent signals.
- Skills seed tactical frontier candidates through trigger/path matching and optional `vaner.kind`.
- MCP tools accept an optional `skill` label so outcomes are attributed to the active skill.
- Feedback is fed back into source multipliers, reinforcing successful frontier sources.
- `vaner distill-skill` converts successful decision records into managed reusable skills.

## Decision transparency

- Every `query` writes a structured decision record to `.vaner/runtime/decisions/`.
- The record captures package-level metadata (`cache_tier`, `partial_similarity`, token usage), per-selection scoring factors, and prediction links when the package came from precompute/frontier exploration.
- CLI surfaces share this record:
  - `vaner inspect --last [--verbose|--json]`
  - `vaner why [decision-id] [--list|--verbose|--json]`
  - `vaner query --explain [--verbose|--json]`

## More details

Detailed architecture docs live at `https://docs.vaner.ai/architecture`.
