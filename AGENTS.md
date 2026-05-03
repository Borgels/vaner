<!-- vaner-primer:start v=0.9.0 -->
# Using Vaner

Vaner is a local-first predictive context engine available to you as an MCP server. It prepares evidence-backed context packages in the background and exposes them through the `vaner.*` tool family (`vaner.resolve`, `vaner.search`, `vaner.expand`, `vaner.suggest`, `vaner.feedback`, `vaner.status`, `vaner.explain`, `vaner.warm`, `vaner.inspect`, `vaner.debug.trace`).

Use Vaner when it can reduce uncertainty, prepare likely context, or continue an existing path. Do not call it mechanically when the task is trivial or self-contained.

Operational patterns:

1. **Prepare context early.** Before spelunking the codebase, call `vaner.resolve` with a short description of the task. It returns a ranked package with evidence and provenance. Keep the returned `resolution_id`.
2. **Fallback and branches.** Use `vaner.search` when `vaner.resolve` confidence is weak or the task requires a retrieval style it did not cover. Use `vaner.expand` to explore adjacent scenarios without recomputing everything.
3. **Feedback at the end.** When the task is done (or abandoned), call `vaner.feedback` with the `resolution_id` and one of `useful` / `partial` / `wrong` / `irrelevant`, optionally with `correction`, `preferred_items`, `rejected_items`, and the `skill` label. This reinforces Vaner's scenario ranking for future work.

Treat Vaner as a supplement, not a replacement for reading code. Skip it entirely for one-line changes, pure reformatting, or questions already answered in the open conversation.

Your MCP client may prefix these tool names. For example, Claude Code exposes plugin MCP tools as `mcp__plugin_<plugin>_<server>__<tool>` — so `vaner.resolve` appears as `mcp__plugin_vaner_vaner__vaner.resolve`. The conceptual names in this document map directly to whatever prefix your client uses; no translation is needed when you reason about them, only when you call them.
<!-- vaner-primer:end -->

Release discipline for repository agents:
- Do not create or push a release tag until local release preflight, remote release preflight, and required PR checks are green on the target commit.
- Validate release workflow changes before tagging; tag workflows should publish a verified release, not discover first-run release bugs.
- Do not cancel required PR checks to save time. Fix duplicated CI triggers or stale branch-protection contexts instead.
- Public release notes, reports, and assets must not include private repository names, internal implementation details, local absolute paths, secrets, or raw private prompts.
- Treat targeted reruns as debugging evidence only. Release claims require the current policy's full required benchmark evidence and a stable public report format.

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
