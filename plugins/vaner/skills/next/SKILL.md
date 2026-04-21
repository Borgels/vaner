---
name: next
description: Show the top candidate next moves Vaner has prepared context for. Use when the user asks "what's next?", "what should I look at?", or wants a list of good next tasks backed by Vaner's predictive context. Renders as numbered cards with label, why-now reasoning, and a confidence/readiness hint.
---

When the user invokes `/vaner:next`, surface the most-ready next candidates Vaner has prepared:

1. Call `mcp__vaner__suggest` with a small `limit` (default: 3, cap at 5) to fetch the top intent suggestions. If the user passed arguments to `/vaner:next`, use them as a focus hint (e.g., `/vaner:next auth` → pass `focus: "auth"`).
2. If a top suggestion looks actionable, also call `mcp__vaner__resolve` for it to pull the prepared context package (capture the `resolution_id` for later `mcp__vaner__feedback`).
3. Render the results as **numbered candidate cards**, one per suggestion, with three pieces:

   - **Label** — short, imperative phrasing of the candidate (e.g., "Trace the auth middleware chain", "Review the cockpit scenario cluster").
   - **Why now** — one line on what makes this candidate relevant in the current state (recent edits, open scenarios, unresolved decisions, signals Vaner is tracking).
   - **Readiness / confidence** — one line on how prepared Vaner is for it: confidence score, cache tier, or evidence count if `mcp__vaner__resolve` was called; otherwise the `vaner.suggest` score alone.

   Example format:

   ```
   1. **Trace the auth middleware chain**
      Why now: edits in src/auth/ today; Vaner has a prepared scenario covering session-token flow.
      Readiness: confidence 0.82, 7 evidence items, cache tier: warm.
      Pick: `/vaner:next 1` or ask "help me with #1".
   ```

4. After rendering, ask the user to pick a number (or describe a different task). If they pick, use the captured `resolution_id` to continue — do not re-call `vaner.resolve` for the same candidate.

Do not render raw prediction dumps or invent readiness information Vaner didn't supply. If `mcp__vaner__suggest` returns no candidates, say so plainly and suggest running `vaner up` or checking `mcp__vaner__status` for daemon readiness rather than fabricating suggestions.

This skill is a structured presenter for predictions; it is not a planner. Skip it entirely if the user is already mid-task or has given a concrete instruction.
