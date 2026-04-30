# Agent Skills Integration

Vaner supports a closed loop with agent `SKILL.md` files:

1. **Skills as prior**: Vaner discovers workspace skills and emits
   `skill_loaded` signals.
2. **Skill-aware exploration**: the exploration frontier seeds extra scenarios
   from skill triggers.
3. **Feedback closure**: MCP `vaner.feedback` and
   `vaner.work_products.feedback` accept optional skill attribution.
4. **Distillation**: `vaner distill-skill <decision-id>` converts successful
   decision records into reusable skills.

## Discovery roots

By default, Vaner scans:

- `.cursor/skills/**/SKILL.md`
- `.claude/skills/**/SKILL.md`
- `skills/**/SKILL.md`

Only repo-local roots are persisted by default. Set
`intent.include_global_skills = true` to include absolute/global roots.

## Skill frontmatter

Vaner consumes optional frontmatter keys:

- `name`
- `description`
- `tags`
- `triggers`
- `vaner.kind`

`triggers` help Vaner map skills to likely file sets and seed tactical frontier
scenarios.

```yaml
---
name: vaner-predictive-debug
description: Use when diagnosing failing tests.
tags: [debug, tests]
triggers:
  - "tests/**"
  - "pytest"
vaner:
  kind: debug
  expand_depth: 2
  feedback: auto
---
```

## Feedback loop

When agents call `vaner.feedback` or `vaner.work_products.feedback` with skill
attribution, Vaner records:

- result (`useful`, `partial`, `irrelevant`, or `not_useful` where supported)
- source attribution (`skill` or fallback)
- skill label

The exploration policy consumes this feedback in future precompute cycles.

## Distill a skill from a decision

```bash
vaner distill-skill --path . --name "repo-debug-playbook"
```

If no decision id is provided, Vaner uses the latest decision record.
