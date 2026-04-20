# Memory Semantics

Vaner v1.0 keeps memory on scenarios instead of a separate wiki layer.

## States

- `candidate`
- `trusted`
- `stale`
- `demoted`

## Promotion Gate

Memory does not auto-promote on every `useful` feedback. Promotion to `trusted` requires one of:

- explicit pin intent (`preferred_items`)
- repeated useful outcomes (`>= 2` streak)
- high confidence + multi-evidence + low contradiction
- correction later confirmed by useful feedback

`partial` keeps candidate memory, `wrong`/`irrelevant` drive demotion or staleness.

## Evidence Fingerprints and Invalidation

Compiled memory is evidence-backed through fingerprints derived from source path, locator, content hash surrogate, and weight.

When fingerprints drift:

- `trusted -> stale`
- `candidate -> stale` (or demoted when confidence is very low)

`resolve` never treats stale memory as a normal predictive hit.

## Conflict and Abstention

If compiled memory conflicts with fresh evidence:

- add `memory_conflict` to `gaps`
- downgrade freshness
- abstain when conflict is strong

## Decision Reuse

Payload reuse is allowed only when:

- evidence is still fresh
- context envelope is materially similar
- no contradiction has appeared since validation

Otherwise previous decisions are only reranking hints or ignored.

## Inspectability Traces

`.vaner/memory/log.md` and `.vaner/memory/index.md` are inspectability traces over evolving scenario memory.

They are not the semantic memory layer. The semantic memory layer is scenario state (`memory_state`, `memory_confidence`, `memory_evidence_hashes_json`, compiled memory sections).
