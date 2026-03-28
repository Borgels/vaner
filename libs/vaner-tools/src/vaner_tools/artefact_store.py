"""Artefact store — read/write/check-staleness for .vaner/cache/.

Primary storage: SQLite at .vaner/cache/artefacts.db
Legacy: JSON files at .vaner/cache/{kind}/{source_path_urlencoded}.json

Public API is unchanged — all callers work without modification.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
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
# SQLite connection (module-level, thread-safe)
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()
_db_conn: sqlite3.Connection | None = None


def _get_conn(cache_root: Path | None = None) -> sqlite3.Connection:
    global _db_conn
    root = cache_root or CACHE_DIR
    db_path = root / "artefacts.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if _db_conn is None:
        _db_conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
        _db_conn.execute("""
            CREATE TABLE IF NOT EXISTS artefacts (
                key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_mtime REAL NOT NULL,
                generated_at REAL NOT NULL,
                model TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
        """)
        _db_conn.commit()
    return _db_conn


def _row_to_artefact(row: sqlite3.Row) -> Artefact:
    return Artefact(
        key=row["key"],
        kind=row["kind"],
        source_path=row["source_path"],
        source_mtime=row["source_mtime"],
        generated_at=row["generated_at"],
        model=row["model"],
        content=row["content"],
        metadata=json.loads(row["metadata"] or "{}"),
    )


# ---------------------------------------------------------------------------
# Legacy path helper (kept for migrate_from_json)
# ---------------------------------------------------------------------------


def _legacy_path(kind: str, source_path: str, cache_root: Path | None = None) -> Path:
    root = cache_root or CACHE_DIR
    encoded = urllib.parse.quote(source_path, safe="")
    return root / kind / f"{encoded}.json"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def write_artefact(artefact: Artefact, cache_root: Path | None = None) -> None:
    """Write artefact to SQLite. Thread-safe."""
    conn = _get_conn(cache_root)
    meta = json.dumps(artefact.metadata or {})
    with _db_lock:
        conn.execute(
            """INSERT OR REPLACE INTO artefacts
               (key, kind, source_path, source_mtime, generated_at, model, content, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (artefact.key, artefact.kind, artefact.source_path,
             artefact.source_mtime, artefact.generated_at,
             artefact.model, artefact.content, meta),
        )
        conn.commit()


def read_artefact(kind: str, source_path: str, cache_root: Path | None = None) -> Artefact | None:
    """Read artefact by kind+source_path. Returns None if missing."""
    key = _make_key(kind, source_path)
    conn = _get_conn(cache_root)
    row = conn.execute("SELECT * FROM artefacts WHERE key = ?", (key,)).fetchone()
    if row:
        return _row_to_artefact(row)
    # Fallback: try legacy JSON
    path = _legacy_path(kind, source_path, cache_root)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            a = Artefact(**data)
            write_artefact(a, cache_root)  # migrate on read
            return a
        except Exception:
            pass
    return None


def _make_key(kind: str, source_path: str) -> str:
    return f"{kind}:{urllib.parse.quote(source_path, safe='')}"


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


def is_stale(artefact: Artefact, max_age_seconds: float = 3600) -> bool:
    """Return True if the artefact should be regenerated."""
    age = time.time() - artefact.generated_at
    if age > max_age_seconds:
        return True
    source_abs = REPO_ROOT / artefact.source_path
    if source_abs.exists():
        if source_abs.stat().st_mtime > artefact.source_mtime:
            return True
    return False


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_artefacts(kind: str | None = None, cache_root: Path | None = None) -> list[Artefact]:
    """Return all artefacts from SQLite, optionally filtered by kind."""
    conn = _get_conn(cache_root)
    if kind:
        rows = conn.execute("SELECT * FROM artefacts WHERE kind = ?", (kind,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM artefacts").fetchall()
    results = [_row_to_artefact(r) for r in rows]
    # Also sweep legacy JSON files not yet in DB
    if not results:
        results = _sweep_legacy(kind, cache_root)
    return results


def _sweep_legacy(kind: str | None, cache_root: Path | None) -> list[Artefact]:
    """Read legacy JSON files and import them into SQLite."""
    root = cache_root or CACHE_DIR
    if not root.exists():
        return []
    search = root / kind if kind else root
    if not search.exists():
        return []
    results = []
    for path in search.rglob("*.json"):
        if "repo_index" in str(path):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            a = Artefact(**data)
            write_artefact(a, cache_root)
            results.append(a)
        except Exception:
            continue
    return results


# ---------------------------------------------------------------------------
# Migration helper
# ---------------------------------------------------------------------------


def migrate_from_json(cache_root: Path | None = None) -> int:
    """Import all legacy JSON artefacts into SQLite. Returns count migrated."""
    root = cache_root or CACHE_DIR
    count = 0
    if not root.exists():
        return 0
    for path in root.rglob("*.json"):
        if "repo_index" in str(path) or "artefacts.db" in str(path):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            a = Artefact(**data)
            write_artefact(a, cache_root)
            count += 1
        except Exception:
            continue
    return count


# ---------------------------------------------------------------------------
# Repo index helper
# ---------------------------------------------------------------------------


def read_repo_index(cache_root: Path | None = None) -> dict | None:
    """Read repo index: first from SQLite file_summary artefacts, then legacy JSON."""
    conn = _get_conn(cache_root)
    rows = conn.execute(
        "SELECT source_path, content FROM artefacts WHERE kind = 'file_summary'"
    ).fetchall()
    if rows:
        return {r["source_path"]: r["content"] for r in rows}
    # Legacy fallback
    root = cache_root or CACHE_DIR
    index_path = root / "repo_index" / "root.json"
    if index_path.exists():
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Count helper
# ---------------------------------------------------------------------------


def count_artefacts(cache_root: Path | None = None) -> int:
    """Return total number of artefacts in store."""
    conn = _get_conn(cache_root)
    row = conn.execute("SELECT COUNT(*) FROM artefacts").fetchone()
    return row[0] if row else 0
