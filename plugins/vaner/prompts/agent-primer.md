# Using Vaner

Vaner is a local-first predictive context engine available to you as an MCP server. It prepares evidence-backed context packages in the background and exposes them through the `vaner.*` tool family (`vaner.resolve`, `vaner.search`, `vaner.expand`, `vaner.suggest`, `vaner.feedback`, `vaner.status`, `vaner.explain`, `vaner.warm`, `vaner.inspect`, `vaner.debug.trace`).

Use Vaner when it already has clearly relevant, fresh, prepared context for the current turn. Do not call it mechanically when the task is trivial, self-contained, or already answered by the open conversation.

Operational patterns:

1. **Turn decision first.** At the start of a non-trivial turn, call `vaner.suggest` if Vaner might already have prepared context. It returns exactly one decision: `use_adopted_package`, `adopt_prediction`, `resolve_optional`, or `answer_normally`.
2. **Never wait.** Use a ready adopted package or adopt at most one strong matching prediction. If Vaner has nothing clearly useful, answer normally. Do not call `vaner.resolve` merely because no ready prediction exists.
3. **Optional retrieval.** Use `vaner.resolve` only for concrete, high-value current-turn queries where retrieval is likely to materially improve the answer and where you can continue if Vaner is unavailable or slow.
4. **Feedback at the end.** When the task is done (or abandoned), call `vaner.feedback` with the `resolution_id` or adopted prediction id and one of `useful` / `partial` / `wrong` / `irrelevant`, optionally with `correction`, `preferred_items`, `rejected_items`, and the `skill` label. This reinforces Vaner's scenario ranking for future work.

Treat Vaner as a supplement, not a replacement for reading code. Skip it entirely for one-line changes, pure reformatting, or questions already answered in the open conversation.

Your MCP client may prefix these tool names. For example, Claude Code exposes plugin MCP tools as `mcp__plugin_<plugin>_<server>__<tool>` — so `vaner.resolve` appears as `mcp__plugin_vaner_vaner__vaner.resolve`. The conceptual names in this document map directly to whatever prefix your client uses; no translation is needed when you reason about them, only when you call them.
