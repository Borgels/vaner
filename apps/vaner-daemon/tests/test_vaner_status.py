"""Tests for daemon_status() and artifact count helpers."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from vaner_daemon.daemon import daemon_status


def test_daemon_status_not_running(tmp_path):
    """daemon_status on a path with no PID file → running=False."""
    status = daemon_status(tmp_path)
    assert status["running"] is False
    assert status["pid"] is None
    assert status["uptime_seconds"] is None
    assert status["branch"] == ""
    assert status["active_files"] == []
    assert "proxy_port" in status
    assert "proxy_running" in status


def test_daemon_status_stale_pid(tmp_path):
    """Stale PID file (process dead) → running=False, file cleaned up."""
    vaner_dir = tmp_path / ".vaner"
    vaner_dir.mkdir()
    pid_file = vaner_dir / "daemon.pid"
    pid_file.write_text("999999999")  # non-existent PID
    status = daemon_status(tmp_path)
    assert status["running"] is False
    assert not pid_file.exists()


def test_count_artefacts_empty(tmp_path):
    """Fresh SQLite store returns 0."""
    from vaner_tools.artefact_store import count_artefacts, _get_conn
    # Force new connection for tmp_path
    conn = _get_conn(tmp_path / "cache")
    count = conn.execute("SELECT COUNT(*) FROM artefacts").fetchone()[0]
    assert count == 0


def test_count_artefacts_after_write(tmp_path):
    """Writing 3 artefacts → count_artefacts returns at least 3."""
    from vaner_tools.artefact_store import Artefact, write_artefact, count_artefacts, _get_conn
    cache = tmp_path / "cache"
    # Reset module-level connection for isolation
    import vaner_tools.artefact_store as store
    orig = store._db_conn
    store._db_conn = None
    try:
        for i in range(3):
            a = Artefact(
                key=f"file_summary:file{i}.py",
                kind="file_summary",
                source_path=f"file{i}.py",
                source_mtime=time.time(),
                generated_at=time.time(),
                model="test",
                content=f"content {i}",
            )
            write_artefact(a, cache)
        n = count_artefacts(cache)
        assert n == 3
    finally:
        if store._db_conn:
            store._db_conn.close()
        store._db_conn = orig


def test_inspect_list_artefacts(tmp_path):
    """list_artefacts returns written artefacts."""
    from vaner_tools.artefact_store import Artefact, write_artefact, list_artefacts
    import vaner_tools.artefact_store as store
    orig = store._db_conn
    store._db_conn = None
    cache = tmp_path / "cache"
    try:
        for i in range(2):
            a = Artefact(
                key=f"file_summary:src/f{i}.py",
                kind="file_summary",
                source_path=f"src/f{i}.py",
                source_mtime=time.time(),
                generated_at=time.time(),
                model="test",
                content=f"content {i}",
            )
            write_artefact(a, cache)
        arts = list_artefacts(kind="file_summary", cache_root=cache)
        assert len(arts) == 2
        paths = {a.source_path for a in arts}
        assert "src/f0.py" in paths
        assert "src/f1.py" in paths
    finally:
        if store._db_conn:
            store._db_conn.close()
        store._db_conn = orig


def test_inspect_by_path(tmp_path):
    """read_artefact returns correct artefact by kind+source_path."""
    from vaner_tools.artefact_store import Artefact, write_artefact, read_artefact
    import vaner_tools.artefact_store as store
    orig = store._db_conn
    store._db_conn = None
    cache = tmp_path / "cache"
    try:
        a = Artefact(
            key="file_summary:foo.py",
            kind="file_summary",
            source_path="foo.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="Foo module.",
        )
        write_artefact(a, cache)
        result = read_artefact("file_summary", "foo.py", cache)
        assert result is not None
        assert result.content == "Foo module."
    finally:
        if store._db_conn:
            store._db_conn.close()
        store._db_conn = orig
