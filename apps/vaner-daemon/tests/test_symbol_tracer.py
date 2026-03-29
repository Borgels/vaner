"""Tests for symbol tracer."""
from __future__ import annotations

from pathlib import Path

from vaner_daemon.preparation_engine.symbol_tracer import (
    _extract_symbols,
    _format_symbols,
    generate_symbol_trace,
)


SAMPLE_PY = '''
class Foo:
    def bar(self): pass
    def baz(self, x): pass

class Empty:
    pass

def top_level(a, b, c):
    pass

def another():
    pass
'''


def test_extract_classes():
    symbols = _extract_symbols(SAMPLE_PY)
    classes = [s for s in symbols if s["kind"] == "class"]
    assert len(classes) == 2
    foo = next(s for s in classes if s["name"] == "Foo")
    assert "bar" in foo["methods"]
    assert "baz" in foo["methods"]


def test_extract_functions():
    symbols = _extract_symbols(SAMPLE_PY)
    funcs = [s for s in symbols if s["kind"] == "function"]
    names = [f["name"] for f in funcs]
    assert "top_level" in names
    assert "another" in names


def test_extract_function_args():
    symbols = _extract_symbols(SAMPLE_PY)
    top = next(s for s in symbols if s.get("name") == "top_level")
    assert top["args"] == ["a", "b", "c"]


def test_format_symbols():
    symbols = _extract_symbols(SAMPLE_PY)
    formatted = _format_symbols(symbols)
    assert "class Foo" in formatted
    assert "def top_level" in formatted


def test_syntax_error_returns_empty():
    symbols = _extract_symbols("def broken(: pass")
    assert symbols == []


def test_generate_symbol_trace(tmp_path):
    import vaner_tools.artefact_store as store
    orig = store._db_conn
    store._db_conn = None
    try:
        src = tmp_path / "mymod.py"
        src.write_text("class Alpha:\n    def run(self): pass\n\ndef helper(x): pass\n")
        result = generate_symbol_trace(src, tmp_path)
        assert result is not None
        assert result.kind == "symbol_trace"
        assert result.model == "ast"
        assert "Alpha" in result.content
        assert "helper" in result.content
    finally:
        if store._db_conn:
            store._db_conn.close()
        store._db_conn = orig


def test_generate_symbol_trace_non_python(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("key: value\n")
    result = generate_symbol_trace(f, tmp_path)
    assert result is None


def test_generate_symbol_trace_empty_file(tmp_path):
    src = tmp_path / "empty.py"
    src.write_text("")
    result = generate_symbol_trace(src, tmp_path)
    assert result is None  # no symbols → no artefact
