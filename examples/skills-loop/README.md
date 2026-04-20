# Vaner Skills Loop Example

This example shows the full closed loop between Agent Skills and Vaner MCP tools.

## 1) Initialize Vaner and managed feedback skill

```bash
vaner init --path .
```

This writes MCP config and installs the managed `vaner-feedback` skill in compatible client skill folders.

## 2) Discover scenarios in your agent

Use MCP tools:

- `list_scenarios`
- `get_scenario`
- `expand_scenario`

Pass the active skill name through the optional `skill` argument where possible.

## 3) Report outcome

After task completion:

```json
{
  "id": "scn_123",
  "result": "useful",
  "note": "included relevant tests",
  "skill": "vaner-feedback"
}
```

Submit through MCP `report_outcome`.

## 4) Distill proven decisions into reusable skills

```bash
vaner why --list --path .
vaner distill-skill <decision-id> --path .
```

The generated `SKILL.md` can be reused in future tasks.
# Skills Loop Example

1. Initialize Vaner and MCP configs:

```bash
vaner init --path .
```

2. Run an agent session using Vaner MCP tools.
3. Review the decision:

```bash
vaner why --path .
```

4. Distill a reusable skill:

```bash
vaner distill-skill --path . --name "repo-playbook"
```

5. In later sessions, the distilled skill is discovered and used as a prediction prior.
# Skills Loop Example

This example shows Vaner's Agent Skills closed loop:

1. Initialize the repo and MCP configs:

```bash
vaner init --path .
```

2. Run an agent session using Vaner MCP tools (`list_scenarios`, `get_scenario`, `report_outcome`).

3. Inspect why Vaner chose a package:

```bash
vaner why --path .
```

4. Distill the latest decision into a reusable skill:

```bash
vaner distill-skill --path . --name "repo-playbook"
```

5. In future sessions, the distilled skill contributes signals and frontier seeds.
