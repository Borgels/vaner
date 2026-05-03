<!-- vaner-primer:start v=0.9.0 -->
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
<!-- vaner-primer:end -->

<!-- vaner-benchmark-principles:start v=1 -->
# Vaner Benchmark Principles

Future Vaner benchmark and optimization work should measure the user-facing product architecture, not a same-model shortcut:
- Vaner preparation runs on its own local or cheaper background model.
- The user's primary AI is a separate model, usually a cloud model accessed through the user's client.
- The judge should be separate from the primary model when possible.
- Public claims must compare naked primary AI, naive RAG plus primary AI, raw Vaner briefing plus primary AI, answerable Vaner briefing plus primary AI, and adopted/prepared Vaner packages when available.
- Final public release claims must come from a versioned release benchmark report schema. Do not treat one-off slices, smoke tests, or changing canvas formats as release-readiness evidence.

Optimize for the golden trifecta from the user's perspective:
- Higher answer quality than naked and naive RAG, judged against public datasets and reproducible artifacts.
- Faster user-visible answers. Background preparation cost/time counts as system work, but not as user-visible latency once it was done before the turn.
- Lower or better-justified primary-model cost. Track local prep tokens, cloud prep tokens, injected context tokens, primary answer tokens, judge tokens, and cost per quality point.

Do not headline raw Vaner briefing as the whole product. Report these separately:
- raw Vaner briefing uplift vs best(naked, RAG);
- answerable Vaner briefing uplift vs best(naked, RAG);
- adoption/prepared package uplift when applicable;
- best Vaner product-path uplift, using the best valid Vaner path for the turn.

Keep predictive readiness claims strict:
- Prepared-only readiness counts only artifacts created before the user prompt was revealed.
- Floor-assisted and answerable-briefing wins are reliability/product wins, not prediction-readiness wins.
- Use intent-readiness wording in public copy; avoid claiming exact next-prompt prediction unless the artifact proves it.

Composer/pre-submit signals are first-class Vaner signals. When a client can stream what the user is typing before submission, benchmark it as a separate "typed-signal" condition:
- snapshot when partial text becomes available;
- let Vaner prepare from that signal before the submitted prompt;
- compare against no-signal Vaner, naked, and naive RAG;
- report lead time, typed-prefix length, intent match, evidence readiness, answerable uplift, latency, and cost.
<!-- vaner-benchmark-principles:end -->
