<!-- vaner-primer:start v=2 -->
# Using Vaner

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
