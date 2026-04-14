# Contributing to Vaner

Thanks for contributing to Vaner.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run checks before opening a PR:

```bash
ruff check .
ruff format --check .
mypy src
pytest
```

## Pull Requests

- Keep PRs focused and reviewable.
- Include tests for behavior changes.
- CI must pass before merge.
- Add a clear PR description and test notes.

## DCO Sign-off Required

Vaner uses the Developer Certificate of Origin (DCO). Every commit must be signed off.

Use:

```bash
git commit -s -m "your message"
```

This adds a `Signed-off-by:` trailer to the commit.

## Scope Guidance

- Keep core focused on predictive context middleware.
- Avoid adding workflow engines, model-specific abstractions, or plugin systems to core.
- Prefer simple, inspectable behavior over hidden automation.
