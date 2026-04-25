---
guidance_version: 1
variant: canonical
minimum_vaner_version: 0.8.5
recommended_tools:
  - vaner.predictions.active
  - vaner.predictions.dashboard
  - vaner.predictions.adopt
  - vaner.resolve
  - vaner.goals.declare
  - vaner.feedback
client_capability_assumptions: tier-2
updated_at: 2026-04-25
---

Vaner is a predictive preparation layer available through MCP tools.

Use Vaner when prepared context may improve the answer, especially when:
- the user asks a question that may match recent or ongoing work;
- the task may benefit from previously prepared evidence, drafts, or predictions;
- the user appears to continue a prior thread, goal, document, plan, project, or workflow;
- the answer would otherwise require expensive fresh retrieval or reconstruction.

Prefer an already-adopted Vaner package if one is present in the context. Do not redundantly call Vaner for the same fresh adopted package.

Use:
- `vaner.predictions.active` to inspect current prepared next-step predictions;
- `vaner.predictions.dashboard` to open the interactive predictions card UI (if the client supports MCP Apps — falls back to structured text otherwise);
- `vaner.predictions.adopt` when the user selects or clearly wants a prepared prediction used;
- `vaner.resolve` when answering a concrete query that may benefit from prepared context;
- `vaner.goals.*` when long-horizon user/workspace goals matter;
- `vaner.feedback` at the end of a Vaner-assisted turn (`useful` / `partial` / `wrong` / `irrelevant`).

Do not call Vaner mechanically on every turn. Avoid repeated calls when the current context already contains fresh Vaner material. When using Vaner material, preserve its provenance and distinguish it from your own inference.
