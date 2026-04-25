# SPDX-License-Identifier: Apache-2.0
"""Generate the ComposerAdapter JSON Schema artifact.

Pydantic models in :mod:`vaner.signals.composer.contract` are the source
of truth. The committed JSON Schema at
``docs/specs/composer-adapter.schema.json`` is regenerated from them and
serves as the cross-language contract for non-Python adapter authors.

Run via the ``vaner-composer-schema`` console script (see pyproject) or
``python -m vaner.signals.composer.schema``. CI guards drift by
re-running the script and ``git diff --exit-code`` on the artifact.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from vaner.signals.composer.contract import DraftIntentSnapshot

ARTIFACT_RELATIVE = Path("docs/specs/composer-adapter.schema.json")


def _repo_root() -> Path:
    """Walk up from this file to the repo root.

    The artifact path is anchored at the repo root so the script works
    from any cwd (CI, devs running from a worktree, etc.).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("could not locate repo root from " + str(here))


def render_schema() -> str:
    schema = DraftIntentSnapshot.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    out_path = _repo_root() / ARTIFACT_RELATIVE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_schema(), encoding="utf-8")
    if "--quiet" not in argv:
        sys.stdout.write(f"wrote {out_path.relative_to(_repo_root())}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
