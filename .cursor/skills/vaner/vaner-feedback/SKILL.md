---
name: vaner-feedback
description: Report scenario outcomes back to Vaner after completing a task.
tags: [vaner, feedback]
vaner:
  kind: research
  feedback: auto
x-vaner-managed: true
---

Use this skill when finishing a task that used Vaner MCP scenarios.

1. Keep the `resolution_id` returned by `vaner.resolve`, and note any item ids you want to praise or reject from `vaner.expand` / `vaner.search`.
2. Call `vaner.feedback` with `rating` (`useful` | `partial` | `wrong` | `irrelevant`) and include `resolution_id` plus any optional `correction`, `preferred_items`, or `rejected_items`.
3. Set `skill` to `vaner-feedback` so Vaner can attribute telemetry and improve future scenario ranking.
