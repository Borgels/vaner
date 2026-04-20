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

1. Keep scenario ids returned by list/get/expand calls.
2. Call report_outcome with id, result (useful|partial|irrelevant), optional note.
3. Set skill to vaner-feedback for attribution.
