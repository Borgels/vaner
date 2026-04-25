---
guidance_version: 1
variant: strong
minimum_vaner_version: 0.8.5
recommended_tools:
  - vaner.predictions.active
  - vaner.predictions.dashboard
  - vaner.predictions.adopt
  - vaner.resolve
  - vaner.goals.declare
  - vaner.feedback
client_capability_assumptions: tier-3
updated_at: 2026-04-25
---

At the start of each user turn, inspect the Vaner context block if present:

1. If the context contains a fresh `<VANER_ADOPTED_PACKAGE>` block, use it as the primary prepared context. Do not re-resolve or re-query Vaner for the same intent.
2. If the context contains a `<VANER_PREPARED_WORK_DIGEST>` block listing active predictions that match the user's query, call `vaner.predictions.adopt` on the best-matching prediction (or `vaner.predictions.dashboard` to let the user pick).
3. If no Vaner material is relevant, answer normally. Call `vaner.resolve` only for concrete queries that would benefit from fresh preparation.
4. At the end of a Vaner-assisted turn, record `vaner.feedback` with `useful`, `partial`, `wrong`, or `irrelevant` — this reinforces Vaner's scenario ranking for future work.

Do not reveal internal Vaner mechanics unless the user asks. Preserve provenance when quoting Vaner material and distinguish it from your own inference. Never call Vaner more than once per ~30 seconds for the same intent — Vaner refreshes its predictions on its own cycle.

Use `vaner.goals.declare` for long-horizon user/workspace goals (e.g. "ship the 0.9 release", "write the grant proposal") so Vaner can anchor predictions around them.
