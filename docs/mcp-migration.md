# MCP v1.0 Migration

Vaner MCP v1.0 started as a breaking rewrite from the legacy 5-tool scenario API
to a 10-tool predictive context surface with explicit confidence, provenance,
gaps, and memory metadata. The live tool surface has grown since then; use
`tools/list` from `src/vaner/mcp/server.py` as the source of truth for current
names.

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

- `include_briefing: bool` (default `true`) — adds `prepared_briefing` to the
  response: the full formatted markdown of pre-compiled artefact summaries.
  Accompanied by `briefing_token_used` + `briefing_token_budget` for sizing the
  downstream prompt.
- `include_predicted_response: bool` (default `true`) — adds `predicted_response`
  when a draft answer was speculatively cached during precompute (null when
  none is available).
- `include_metrics: bool` (default `false`) — adds a `metrics` object to the
  response carrying runtime economics for this call:
  `briefing_tokens`, `evidence_tokens`, `total_context_tokens`, `cache_tier`,
  `freshness`, `elapsed_ms`, `estimated_cost_per_1k_tokens`, `estimated_cost_usd`.
  Pair with the optional `estimated_cost_per_1k_tokens` request field (e.g.
  `2.50` for gpt-4o input pricing) to get a dollar estimate per resolve call.

`include_metrics` is additive. Briefing and predicted-response fields are now
included by default for parity with the daemon HTTP `/resolve` surface; callers
that need the lean legacy shape can pass either include flag as `false`.

## Full v1 tool surface

The complete `vaner.*` MCP tool family at v1.0 (see [docs.vaner.ai/integrations/mcp](https://docs.vaner.ai/integrations/mcp) for full schemas):

- `vaner.status` — engine health, model, compute config, prediction metrics.
- `vaner.suggest` — intent-priming suggestions for a draft prompt.
- `vaner.resolve` — unified context resolution; returns evidence + briefing + predicted response.
- `vaner.expand` — explore adjacent scenarios from an existing resolution.
- `vaner.search` — retrieval-style fallback when `vaner.resolve` confidence is weak.
- `vaner.explain` — rationale for a scenario's score and selection.
- `vaner.feedback` — record `useful` / `partial` / `wrong` / `irrelevant` against a `resolution_id`.
- `vaner.warm` — explicit precompute trigger for a scoped path or anchor.
- `vaner.inspect` — full scenario detail (evidence, score components, prepared context).
- `vaner.debug.trace` — diagnostic trace for integration debugging.

The advanced families (`vaner.predictions.*`, `vaner.goals.*`, `vaner.artefacts.*`, `vaner.work_products.*`, `vaner.prepared_work.dashboard`, `vaner.deep_run.*`, `vaner.setup.*`, `vaner.policy.show`, `vaner.sources.status`) are documented per-page on docs.vaner.ai.
