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

## vaner.resolve — optional briefing + draft

The resolve tool returns `evidence` pointers and a 400-char `summary` by default.
That's shape-compatible with naive RAG responses. To receive the richer output
Vaner actually assembles internally, pass one or both of these flags:

- `include_briefing: bool` (default `false`) — adds `prepared_briefing` to the
  response: the full formatted markdown of pre-compiled artefact summaries.
  Accompanied by `briefing_token_used` + `briefing_token_budget` for sizing the
  downstream prompt.
- `include_predicted_response: bool` (default `false`) — adds `predicted_response`
  when a draft answer was speculatively cached during precompute (null when
  none is available).
- `include_metrics: bool` (default `false`) — adds a `metrics` object to the
  response carrying runtime economics for this call:
  `briefing_tokens`, `evidence_tokens`, `total_context_tokens`, `cache_tier`,
  `freshness`, `elapsed_ms`, `estimated_cost_per_1k_tokens`, `estimated_cost_usd`.
  Pair with the optional `estimated_cost_per_1k_tokens` request field (e.g.
  `2.50` for gpt-4o input pricing) to get a dollar estimate per resolve call.

All three flags are additive; default callers see the legacy shape unchanged.
