# Agent Skills Integration

Vaner supports a closed loop with agent `SKILL.md` files:

1. **Skills as prior**: Vaner discovers workspace skills and emits `skill_loaded` signals.
2. **Skill-aware exploration**: the exploration frontier seeds extra scenarios from skill triggers.
3. **Feedback closure**: MCP `report_outcome` accepts optional `skill`, which is persisted for attribution.
4. **Distillation**: `vaner distill-skill <decision-id>` converts successful decision records into reusable skills.

## Discovery roots

By default, Vaner scans:

- `.cursor/skills`
- `.claude/skills`
- `skills`

These can be configured through `.vaner/config.toml` under `[intent]`.

## Skill frontmatter

Vaner consumes optional frontmatter keys:

- `name`
- `description`
- `tags`
- `triggers`
- `vaner.kind`

`triggers` are used to match likely file paths and seed tactical frontier scenarios.

## Feedback loop

When agents call `report_outcome` with `skill`, Vaner records:

- scenario result (`useful` / `partial` / `irrelevant`)
- source attribution (`skill` or fallback)
- skill label

The exploration policy consumes this feedback in future precompute cycles.
# Agent Skills Integration

Vaner supports a closed loop between skill files and predictive context:

1. Discover skill files (`SKILL.md`) from configured roots.
2. Feed those skills into intent features and frontier seeding.
3. Attribute scenario outcomes with a skill name.
4. Apply feedback to future scenario ranking.
5. Distill successful decision records into managed skills.

## Supported roots

- `.cursor/skills/**/SKILL.md`
- `.claude/skills/**/SKILL.md`
- `skills/**/SKILL.md`

By default, only repo-local roots are persisted. Set `intent.include_global_skills = true` to include absolute/global roots.

## Frontmatter

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

## Distill from decisions

```bash
vaner distill-skill --path . --name "repo-debug-playbook"
```

Without an explicit id, the latest decision record is used.
# Agent Skills Integration

Vaner can read and produce `SKILL.md` files to create a closed feedback loop for predictive context.

## How the loop works

1. Skill files are discovered from configured roots (`intent.skill_roots`).
2. Discovery emits `skill_loaded` signal events.
3. Intent features and frontier seeding use those signals as a prior.
4. Agents call `report_outcome` with optional `skill` attribution.
5. Feedback updates future frontier multipliers.
6. `vaner distill-skill` can generate reusable SKILL.md files from successful decisions.

## Supported roots

- `.cursor/skills/**/SKILL.md`
- `.claude/skills/**/SKILL.md`
- `skills/**/SKILL.md`

By default, Vaner only scans roots inside the repository. Set `intent.include_global_skills = true` to include absolute/global roots.

## Frontmatter schema

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
x-vaner-managed: true
x-vaner-source-decision: d_abc123
---
```

## CLI

Generate a managed skill from a decision:

```bash
vaner distill-skill --path . --name "repo-debug-playbook"
```

If no decision id is provided, Vaner uses the latest decision record.

## MCP outcome attribution

When calling `report_outcome`, include `skill` to attribute outcomes:

```json
{
  "id": "scn_123",
  "result": "useful",
  "skill": "vaner-feedback",
  "note": "helped narrow failing module"
}
```
