# Contributing to Vaner

Thanks for contributing to Vaner.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

Run checks before opening a PR:

```bash
ruff check .
ruff format --check .
mypy src
pytest
pre-commit run --all-files
```

## Pull Requests

- Keep PRs focused and reviewable.
- Include tests for behavior changes.
- CI must pass before merge.
- Add a clear PR description and test notes.

## Commit style

- Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) when possible.
  - Examples: `feat(router): add fallback ranking`, `fix(cli): handle empty path`
- This keeps release notes and changelogs readable as the project grows.

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
- Do not commit training data, internal eval methodology, or moat-sensitive assets.
  Those belong in the private `vaner-train` repository and are blocked by CI moat guards.


## Documentation

User-facing documentation is published at [docs.vaner.ai](https://docs.vaner.ai).
Keep repository docs minimal and point broad usage guidance there.

## PyPI Trusted Publishing Checklist

Vaner uses PyPI Trusted Publishing for release tags.

Before expecting a tagged release to publish to PyPI:

1. Open `https://pypi.org/manage/project/vaner/settings/publishing/`.
2. Add a trusted publisher for:
   - Owner: `Borgels`
   - Repository: `vaner`
   - Workflow filename: `release.yml`
   - Environment name: leave empty
3. Verify the GitHub release workflow keeps `id-token: write`.

If this is not configured yet, the `Publish to PyPI` step is non-fatal and can be retried after setup.
