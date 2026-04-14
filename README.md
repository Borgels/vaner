# Vaner.ai

Vaner.ai is a predictive context middleware layer that prepares likely-useful context before you ask for it.

At prompt time, Vaner selects, compresses, and injects the right context package so the model receives better input and returns better answers.

## What Vaner is

- A local-first predictive context engine
- A background preparation runtime + fast prompt-time broker
- A transparent layer between developer workflows and model backends

## What Vaner is not

- Not a model
- Not just memory
- Not just static RAG
- Not an agent framework

## v1 Product Loop

1. `vaner init` in a repo
2. `vaner daemon start` to collect signals and generate artefacts
3. `vaner query "..."` to ask a repo question
4. `vaner inspect --last` to see selected artefacts, scores, freshness, and token budget

## Trust and Privacy

- Local-first defaults
- No content logging
- Explicit scope boundaries
- Inspectable context decisions

See `docs/security.md` for details.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Community

- Contributing guide: `CONTRIBUTING.md`
- Security policy: `SECURITY.md`
- Code of conduct: `CODE_OF_CONDUCT.md`
- Architecture docs: `docs/architecture.md`
- Configuration reference: `docs/configuration.md`

## License

Apache-2.0. Copyright 2026 Borgels Olsen Holding ApS (VAT DK39700425).
