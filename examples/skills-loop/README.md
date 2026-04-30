# Vaner Skills Loop Example

This example shows the closed loop between Agent Skills, Prepared Work, and
Vaner MCP tools.

## 1. Initialize Vaner and managed feedback skill

```bash
vaner init --path .
vaner up --path .
```

This writes MCP config and installs the managed `vaner-feedback` skill in
compatible client skill folders.

## 2. Use Vaner from your agent

Use current MCP tools:

- `vaner.prepared_work.dashboard`
- `vaner.resolve`
- `vaner.inspect`
- `vaner.feedback`
- `vaner.work_products.feedback`

Pass the active skill name through the optional `skill` argument where possible.

## 3. Report outcome

After task completion, submit feedback through MCP:

```json
{
  "resolution_id": "res_123",
  "rating": "useful",
  "note": "included relevant tests",
  "skill": "vaner-feedback"
}
```

For Prepared Work artifacts, use `vaner.work_products.feedback` with
`feedback_state` set to `useful`, `partial`, `irrelevant`, or `not_useful`.

## 4. Distill proven decisions into reusable skills

```bash
vaner why --list --path .
vaner distill-skill <decision-id> --path .
```

The generated `SKILL.md` can be reused in future tasks.
