import time
from pathlib import Path
import pytest
import vaner_tools.artefact_store as _store
from vaner_tools.artefact_store import write_artefact, read_artefact, list_artefacts, Artefact


@pytest.fixture(autouse=True)
def reset_db_conn():
    """Reset global SQLite connection before/after each test for isolation."""
    orig = _store._db_conn
    _store._db_conn = None
    yield
    _store._db_conn = None


def _make_artefact(**kwargs) -> Artefact:
    defaults = dict(
        key="file_summary:foo.py",
        kind="file_summary",
        source_path="foo.py",
        source_mtime=time.time(),
        generated_at=time.time(),
        model="test",
        content="Test content",
    )
    defaults.update(kwargs)
    return Artefact(**defaults)


def test_write_and_read_artefact(tmp_path):
    a = _make_artefact()
    write_artefact(a, cache_root=tmp_path)
    result = read_artefact(a.kind, a.source_path, cache_root=tmp_path)
    assert result is not None
    assert result.key == a.key
    assert result.content == a.content


def test_list_artefacts_filter_by_kind(tmp_path):
    write_artefact(_make_artefact(key="file_summary:unique_test_a.py", source_path="unique_test_a.py", kind="file_summary"), cache_root=tmp_path)
    write_artefact(_make_artefact(key="module_summary:unique_test_b.py", source_path="unique_test_b.py", kind="module_summary"), cache_root=tmp_path)
    results = list_artefacts(kind="file_summary", cache_root=tmp_path)
    assert all(a.kind == "file_summary" for a in results)
    assert any(a.source_path == "unique_test_a.py" for a in results)


def test_read_missing_returns_none(tmp_path):
    result = read_artefact("file_summary", "nonexistent.py", cache_root=tmp_path)
    assert result is None
