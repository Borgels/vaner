# Using Vaner

Vaner is a local-first predictive context engine available to you as an MCP server. It prepares evidence-backed context packages in the background and exposes them through the `vaner.*` tool family (`vaner.resolve`, `vaner.search`, `vaner.expand`, `vaner.suggest`, `vaner.feedback`, `vaner.status`, `vaner.explain`, `vaner.warm`, `vaner.inspect`, `vaner.debug.trace`).

Use Vaner when it can reduce uncertainty, prepare likely context, or continue an existing path. Do not call it mechanically when the task is trivial or self-contained.

Operational patterns:

1. **Prepared context early.** Before spelunking the codebase, call `vaner.resolve` with a short description of the task. It returns a ranked package with evidence and provenance. Keep the returned `resolution_id`.
2. **Fallback and branches.** Use `vaner.search` when `vaner.resolve` confidence is weak or the task requires a retrieval style it did not cover. Use `vaner.expand` to explore adjacent scenarios without recomputing everything.
3. **Feedback at the end.** When the task is done (or abandoned), call `vaner.feedback` with the `resolution_id` and one of `useful` / `partial` / `wrong` / `irrelevant`, optionally with `correction`, `preferred_items`, `rejected_items`, and the `skill` label. This reinforces Vaner's scenario ranking for future work.

Treat Vaner as a supplement, not a replacement for reading code. Skip it entirely for one-line changes, pure reformatting, or questions already answered in the open conversation.
