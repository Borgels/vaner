"""Path constants and sandboxed path resolution for Vaner."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path("~/repos/Vaner").expanduser().resolve()
CACHE_DIR: Path = REPO_ROOT / ".vaner" / "cache"


def resolve_repo_path(path: str) -> Path:
    """Resolve a path string, sandboxed to REPO_ROOT.

    Raises ValueError if the resolved path escapes the repo root.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    candidate = candidate.resolve()

    if candidate != REPO_ROOT and REPO_ROOT not in candidate.parents:
        raise ValueError(f"Path escapes repository root: {path}")

    return candidate
