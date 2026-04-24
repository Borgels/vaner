---
name: next
description: Show the top candidate next moves Vaner has prepared context for. Use when the user asks "what's next?", "what should I look at?", or wants a list of good next tasks backed by Vaner's predictive context. Renders as numbered cards with label, why-now reasoning, and a confidence/readiness hint.
---

When the user invokes `/vaner:next`, surface the most-ready next candidates Vaner has prepared:

0. **First, check the desktop handoff file.** The Vaner desktop apps (`vaner-desktop-macos`, `vaner-desktop-linux`) drop the full `Resolution` of an adopted prediction at a well-known path when the user clicks **Adopt**. If that's what happened, the user has already picked a candidate — inject it and skip the listing step.

   - Look for the handoff file at:
     - Linux: `$XDG_STATE_HOME/vaner/pending-adopt.json` (fall back to `~/.local/state/vaner/pending-adopt.json` when unset).
     - macOS: `~/Library/Application Support/Vaner/pending-adopt.json`.
     - Windows: `%LOCALAPPDATA%\Vaner\pending-adopt.json`.
   - If the file exists and its top-level `stashed_at` is within the last 10 minutes (`time.time() - stashed_at < 600`), parse it. The body IS a `Resolution` — identical shape to what `mcp__vaner__predictions_adopt` returns, plus `stashed_at` and possibly extra server-side fields the desktop's decoded model didn't know about (the raw payload was preserved on the file-drop).
   - Render a single card using the Resolution's `intent`, `prepared_briefing`, `predicted_response`, and `adopted_from_prediction_id`. Inject the `prepared_briefing` into the next prompt exactly as you would from a freshly-returned adopt result.
   - Delete the file after reading so the same handoff isn't served twice on the next `/vaner:next` call.
   - If the file is absent, older than 10 minutes, or not valid JSON, fall through to step 1.

1. **Next, try `mcp__vaner__predictions_active`** (Phase 4+ engines). It returns first-class `PredictedPrompt` objects — each with a human-readable `label`, `readiness` state, `hypothesis_type`, and budget/progress metrics. Prefer this when available because the labels and readiness states are produced by Vaner's prediction layer, not re-synthesised client-side.

   - If the response includes `"engine_unavailable": true` or an empty predictions list, fall back to step 2.
   - Filter for `readiness in {"drafting", "ready"}` when possible — those are actionable. Queued / grounding predictions are still warming up.

2. **Fallback: `mcp__vaner__suggest`** with a small `limit` (default: 3, cap at 5) for the top intent suggestions. If the user passed arguments to `/vaner:next`, use them as a focus hint (e.g., `/vaner:next auth` → pass `focus: "auth"`).

3. **For the chosen candidate**, adopt or resolve depending on which path you took:
   - If step 1 returned predictions and the user picks one, call `mcp__vaner__predictions_adopt` with its `id`. The returned `Resolution` carries the full prepared package (briefing + draft + evidence) and sets `adopted_from_prediction_id` for provenance. Capture the `resolution_id` for later `mcp__vaner__feedback`.
   - If step 2 was used, call `mcp__vaner__resolve` for the chosen suggestion.

4. **Render the results as numbered candidate cards**, one per candidate, with three pieces:

   - **Label** — the prediction's `label` (step 1) or a short imperative from the suggestion (step 2). Differentiate rendering by `hypothesis_type` when present:
     - `likely_next` → "Next step:"
     - `possible_branch` → "Vaner is exploring:"
     - `long_tail` → dimmed, "Might follow:"
   - **Why now** — one line on what makes this candidate relevant (recent edits, open scenarios, unresolved decisions, signals Vaner is tracking).
   - **Readiness / confidence** — use the real readiness state from step 1 (`queued` / `grounding` / `evidence_gathering` / `drafting` / `ready`) when available; otherwise report suggestion score / cache tier / evidence count from step 2.

   Example format:

   ```
   1. **Next step: Trace the auth middleware chain**
      Why now: edits in src/auth/ today; Vaner has a prepared scenario covering session-token flow.
      Readiness: drafting — confidence 0.82, 7 evidence items, 3/4 scenarios complete.
      Pick: `/vaner:next 1` or ask "help me with #1".
   ```

5. After rendering, ask the user to pick a number (or describe a different task). If they pick:
   - Step-1 path: call `mcp__vaner__predictions_adopt` with the selected `id`. Inject the returned Resolution's `prepared_briefing` into the next prompt.
   - Step-2 path: use the captured `resolution_id` to continue — do not re-call `vaner.resolve` for the same candidate.

Do not render raw prediction dumps or invent readiness information Vaner didn't supply. If both `mcp__vaner__predictions_active` and `mcp__vaner__suggest` return nothing, say so plainly and suggest running `vaner up` or checking `mcp__vaner__status` for daemon readiness rather than fabricating suggestions.

This skill is a structured presenter for predictions; it is not a planner. Skip it entirely if the user is already mid-task or has given a concrete instruction.
