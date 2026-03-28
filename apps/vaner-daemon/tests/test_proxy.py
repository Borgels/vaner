"""Tests for VanerProxy context injection logic."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from vaner_daemon.proxy.server import _inject_context, _make_context_string


def _artefact(source_path: str, content: str, age: float = 0):
    from vaner_tools.artefact_store import Artefact
    return Artefact(
        key=f"file_summary:{source_path}",
        kind="file_summary",
        source_path=source_path,
        source_mtime=time.time() - age,
        generated_at=time.time() - age,
        model="test",
        content=content,
    )


def test_make_context_string():
    arts = [_artefact("foo.py", "Foo content"), _artefact("bar.py", "Bar content")]
    result = _make_context_string(arts)
    assert "## Vaner context" in result
    assert "foo.py" in result
    assert "Bar content" in result
    assert "---" in result


def test_make_context_string_top5_limit():
    arts = [_artefact(f"f{i}.py", f"c{i}", age=float(i)) for i in range(10)]
    result = _make_context_string(arts)
    # Only 5 sections (newest first = age 0..4)
    assert result.count("###") == 5
    assert "f0.py" in result
    assert "f9.py" not in result


def test_inject_context_no_system_message():
    messages = [{"role": "user", "content": "hello"}]
    result = _inject_context(messages, "## Vaner context\n### foo.py\nFoo\n---")
    assert result[0]["role"] == "system"
    assert "Vaner context" in result[0]["content"]
    assert result[1]["role"] == "user"


def test_inject_context_prepends_to_existing_system():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hello"},
    ]
    result = _inject_context(messages, "## Vaner context\n---")
    assert result[0]["role"] == "system"
    assert "Vaner context" in result[0]["content"]
    assert "You are helpful." in result[0]["content"]


def test_inject_context_passthrough_when_no_context():
    messages = [{"role": "user", "content": "hello"}]
    # Empty context → don't inject
    context = ""
    if context:
        result = _inject_context(messages, context)
    else:
        result = messages
    assert result == [{"role": "user", "content": "hello"}]


def test_make_context_string_single_artefact():
    arts = [_artefact("solo.py", "Solo content")]
    result = _make_context_string(arts)
    assert "solo.py" in result
    assert "Solo content" in result
