---
guidance_version: 1
variant: weak
minimum_vaner_version: 0.8.5
recommended_tools:
  - vaner.suggest
  - vaner.predictions.active
  - vaner.predictions.adopt
  - vaner.resolve
client_capability_assumptions: tier-1
updated_at: 2026-04-25
---

When relevant, use `vaner.suggest` as the non-blocking turn-start decision. Prefer fresh adopted Vaner context already in the prompt. Adopt at most one strong matching prepared context package. If Vaner has nothing clearly useful, answer normally; do not call `vaner.resolve` merely because no ready prediction exists.
