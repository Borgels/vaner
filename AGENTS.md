<!-- vaner-primer:start v=1 -->
# Using Vaner

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
