---
guidance_version: 2
variant: canonical
minimum_vaner_version: 0.8.5
recommended_tools:
  - vaner.suggest
  - vaner.predictions.active
  - vaner.predictions.dashboard
  - vaner.predictions.adopt
  - vaner.resolve
  - vaner.goals.declare
  - vaner.feedback
client_capability_assumptions: tier-2
updated_at: 2026-04-30
---

Vaner is a predictive preparation layer available through MCP tools.

At the start of a non-trivial turn, the product question is:

> What, if anything, should Vaner contribute to this user turn?

Use `vaner.suggest` as the canonical turn-start API. It returns exactly one decision:
- `use_adopted_package`
- `adopt_prediction`
- `resolve_optional`
- `answer_normally`

Prefer an already-adopted Vaner package if one is present in the context. Do not redundantly call Vaner for the same fresh adopted package.

Use:
- `vaner.suggest` to decide immediately and non-blockingly whether Vaner should contribute;
- `vaner.predictions.active` to inspect current prepared context for diagnostics;
- `vaner.predictions.dashboard` to open the interactive predictions card UI (if the client supports MCP Apps — falls back to structured text otherwise);
- `vaner.predictions.adopt` only when `vaner.suggest` returns `adopt_prediction` or shared relevance fields show a strong current-turn match;
- `vaner.resolve` only when `vaner.suggest` returns `resolve_optional` or a concrete high-value query is likely to benefit from retrieval;
- `vaner.goals.*` when long-horizon user/workspace goals matter;
- `vaner.feedback` at the end of a Vaner-assisted turn (`useful` / `partial` / `wrong` / `irrelevant`).

Do not call Vaner mechanically on every turn. Never wait for Vaner; if no clearly relevant prepared context is ready, answer normally. Never call `vaner.resolve` merely because no ready prediction exists. Adopt at most one prediction. Avoid repeated calls when the current context already contains fresh Vaner material. When using Vaner material, preserve its provenance and distinguish it from your own inference.

Release discipline for repository agents:
- Do not create or push a release tag until local release preflight, remote release preflight, and required PR checks are green on the target commit.
- Validate release workflow changes before tagging; tag workflows should publish a verified release, not discover first-run release bugs.
- Do not cancel required PR checks to save time. Fix duplicated CI triggers or stale branch-protection contexts instead.
- Public release notes, reports, and assets must not include private repository names, internal implementation details, local absolute paths, secrets, or raw private prompts.
- Treat targeted reruns as debugging evidence only. Release claims require the current policy's full required benchmark evidence and a stable public report format.
