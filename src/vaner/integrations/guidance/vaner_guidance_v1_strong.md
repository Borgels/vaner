---
guidance_version: 1
variant: strong
minimum_vaner_version: 0.8.5
recommended_tools:
  - vaner.suggest
  - vaner.predictions.active
  - vaner.predictions.dashboard
  - vaner.predictions.adopt
  - vaner.resolve
  - vaner.goals.declare
  - vaner.feedback
client_capability_assumptions: tier-3
updated_at: 2026-04-25
---

At the start of each non-trivial user turn, ask what Vaner should contribute:

1. If the context contains a fresh `<VANER_ADOPTED_PACKAGE>` block, use it as the primary prepared context. Do not re-resolve or re-query Vaner for the same intent.
2. Otherwise call `vaner.suggest` when Vaner might already have prepared context. Treat its `decision.action` as canonical: `use_adopted_package`, `adopt_prediction`, `resolve_optional`, or `answer_normally`.
3. Adopt at most one prediction, and only for a strong current-turn match backed by ready/drafting material.
4. If no Vaner material is clearly relevant, answer normally. Do not call `vaner.resolve` merely because no ready prediction exists; use it only as optional retrieval for concrete high-value queries.
5. At the end of a Vaner-assisted turn, record `vaner.feedback` with `useful`, `partial`, `wrong`, or `irrelevant` — this reinforces Vaner's scenario ranking for future work.

Do not reveal internal Vaner mechanics unless the user asks. Preserve provenance when quoting Vaner material and distinguish it from your own inference. Never wait for Vaner and never call it repeatedly for the same intent; Vaner refreshes its prepared context on its own cycle.

Use `vaner.goals.declare` for long-horizon user/workspace goals (e.g. "ship the 0.9 release", "write the grant proposal") so Vaner can anchor predictions around them.
