"""Artefact store — read/write/check-staleness for .vaner/cache/.

File layout:
    .vaner/cache/{kind}/{source_path_urlencoded}.json

The repo_index is a special case stored at:
    .vaner/cache/repo_index/root.json
"""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .paths import CACHE_DIR, REPO_ROOT

# ---------------------------------------------------------------------------
# Artefact dataclass
# ---------------------------------------------------------------------------


@dataclass
class Artefact:
    """A single cached artefact."""

    key: str
    kind: str
    source_path: str
    source_mtime: float
    generated_at: float
    model: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def artefact_path(kind: str, source_path: str) -> Path:
    """Return the filesystem path where an artefact is stored.

    Uses URL-encoding so that slashes in source_path become safe filename chars.
    """
    encoded = urllib.parse.quote(source_path, safe="")
    return CACHE_DIR / kind / f"{encoded}.json"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def write_artefact(artefact: Artefact) -> None:
    """Serialise and write an artefact to disk, creating parent dirs."""
    path = artefact_path(artefact.kind, artefact.source_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(artefact), indent=2), encoding="utf-8")


def read_artefact(kind: str, source_path: str) -> Artefact | None:
    """Read an artefact from disk. Returns None if missing or corrupt."""
    path = artefact_path(kind, source_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Artefact(**data)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


def is_stale(artefact: Artefact, max_age_seconds: float = 3600) -> bool:
    """Return True if the artefact should be regenerated.

    An artefact is stale when:
    - The source file's current mtime is newer than artefact.source_mtime, OR
    - The artefact is older than max_age_seconds.
    """
    # Age check
    age = time.time() - artefact.generated_at
    if age > max_age_seconds:
        return True

    # Source mtime check
    source_abs = REPO_ROOT / artefact.source_path
    if source_abs.exists():
        current_mtime = source_abs.stat().st_mtime
        if current_mtime > artefact.source_mtime:
            return True

    return False


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_artefacts(kind: str | None = None) -> list[Artefact]:
    """Walk CACHE_DIR and return all artefacts, optionally filtered by kind."""
    results: list[Artefact] = []
    if not CACHE_DIR.exists():
        return results

    search_root = CACHE_DIR / kind if kind else CACHE_DIR
    if not search_root.exists():
        return results

    for path in search_root.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(Artefact(**data))
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Repo index helper
# ---------------------------------------------------------------------------


def read_repo_index() -> dict | None:
    """Read the flat repo index from .vaner/cache/repo_index/root.json.

    Returns the parsed dict, or None if missing.
    """
    index_path = CACHE_DIR / "repo_index" / "root.json"
    if not index_path.exists():
        return None
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return None
