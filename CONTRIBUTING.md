# Contributing to Vaner

## Architecture

Vaner has two stacks:

**Builder stack** (tools for building Vaner itself):
- `apps/vaner-builder/` — coding agent (qwen2.5-coder:32b via Ollama)
- `apps/repo-analyzer/` — pre-computes artifact summaries into `.vaner/cache/`
- `apps/supervisor/` — routes requests through the builder stack

**Product stack** (what runs on developer machines):
- `apps/vaner-daemon/` — background daemon, event collector, state engine, preparation engine, proxy
- `libs/vaner-runtime/` — job store, retry, queue, telemetry
- `libs/vaner-tools/` — artifact store (SQLite), TF-IDF scoring, repo tools

## Development workflow

```bash
# Setup
cd ~/repos/Vaner
python vaner.py init        # installs git hooks, creates config
python vaner.py daemon start

# Run all tests
apps/vaner-daemon/.venv/bin/pytest apps/vaner-daemon/tests/ -q
libs/vaner-runtime/.venv/bin/pytest libs/vaner-runtime/tests/ -q

# Lint
apps/vaner-daemon/.venv/bin/python -m ruff check apps/vaner-daemon/src/ libs/ --ignore E501,D,T201,ANN

# Status
python vaner.py status
python vaner.py inspect
```

## Branch workflow

- Work on `develop` branch
- PRs merge to `main` via auto-merge (CI required, 0 human approvals)
- Never commit `.env` files (API keys)

## Building with local agents

```bash
# Write a task plan in tasks/my_feature.md (one task per line)
# Run with the supervised builder:
apps/vaner-builder/.venv/bin/python work.py --plan tasks/my_feature.md

# Builder uses qwen2.5-coder:32b locally (RTX 5090 / Ollama)
# Validator runs pytest + ruff after each task
# --yes flag runs all tasks unattended
```

## Task plan format

```markdown
# tasks/my_feature.md
# One task per line, # = comment

Read src/foo.py and src/bar.py. Add method do_thing() to Foo class that...
Write tests in tests/test_foo.py covering: test_do_thing_happy, test_do_thing_error.
Run pytest and ruff. Fix all failures. Commit with message "feat: add do_thing to Foo".
```

## Model strategy

| Task | Model | Where |
|---|---|---|
| File summarization | devstral / qwen2.5-coder:32b | RTX 5090 (Ollama) |
| Code generation | qwen2.5-coder:32b | RTX 5090 (Ollama) |
| Architecture (DGX) | qwen2.5-coder:72b | DGX Spark (vLLM) |
| Cloud | Claude (last resort) | Anthropic API |

## Key files

- `vaner.py` — main CLI entry point
- `docs/roadmap.md` — phase plan and architectural decisions
- `eval/` — A/B evaluation framework
- `.vaner/config.json` — runtime configuration
- `.vaner/monitor.log` — overnight build monitor log
