# MCP v1.0 Migration

Vaner MCP v1.0 is a breaking rewrite from the legacy 5-tool scenario API to a 10-tool predictive context surface with explicit confidence, provenance, gaps, and memory metadata.

- `list_scenarios` -> `vaner.status` plus `vaner.resolve`
- `get_scenario` -> `vaner.resolve` (query) or `vaner.inspect` (by id)
- `expand_scenario` -> `vaner.expand`
- `compare_scenarios` -> removed (use two `vaner.resolve` calls and compare in client)
- `report_outcome` -> `vaner.feedback`

## Provenance Semantics

- `predictive_hit`: trusted reuse path chosen
- `cached_result`: prior memory reranked as a strong hint
- `fresh_resolution`: newly computed package this turn
- `retrieval_fallback`: predictive path weak; retrieval fallback used

Freshness can downgrade from `fresh` to `recent`/`stale` when memory conflict is detected.

## Minimal Agent Loop

```text
1) vaner.status
2) vaner.suggest (when ambiguous)
3) vaner.resolve
4) vaner.expand (if deeper inspection needed)
5) vaner.feedback
```
