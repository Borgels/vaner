"""Tests for module summarizer."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaner_daemon.preparation_engine.module_summarizer import (
    _collect_file_summaries,
    discover_modules,
)
from vaner_tools.artefact_store import Artefact


def _artefact(source_path: str, kind: str = "file_summary") -> Artefact:
    return Artefact(
        key=f"{kind}:{source_path}",
        kind=kind,
        source_path=source_path,
        source_mtime=time.time(),
        generated_at=time.time(),
        model="test",
        content=f"Summary of {source_path}",
    )


def test_collect_file_summaries_matches_prefix():
    arts = [
        _artefact("src/vaner_daemon/daemon.py"),
        _artefact("src/vaner_daemon/config.py"),
        _artefact("src/other/foo.py"),
    ]
    result = _collect_file_summaries("src/vaner_daemon", arts)
    assert "daemon.py" in result
    assert "config.py" in result
    assert "foo.py" not in result


def test_collect_file_summaries_empty():
    arts = [_artefact("src/foo.py")]
    result = _collect_file_summaries("src/bar", arts)
    assert result == ""


def test_collect_file_summaries_caps_at_10():
    arts = [_artefact(f"src/mod/f{i}.py") for i in range(15)]
    result = _collect_file_summaries("src/mod", arts)
    # Max 10 sections
    assert result.count("###") == 10


def test_discover_modules(tmp_path):
    # Create a module with 3 py files
    mod = tmp_path / "src" / "mymod"
    mod.mkdir(parents=True)
    (mod / "__init__.py").write_text("")
    (mod / "a.py").write_text("")
    (mod / "b.py").write_text("")
    # Single-file module — should be excluded (min_files=2, but __init__ counts)
    small = tmp_path / "src" / "tiny"
    small.mkdir(parents=True)
    (small / "__init__.py").write_text("")
    # Only 1 .py file → excluded by min_files=2
    modules = discover_modules(tmp_path, min_files=2)
    # mymod has __init__ + a + b = 3 files ≥ 2
    assert any("mymod" in m for m in modules)


@pytest.mark.asyncio
async def test_generate_module_summary_mocked(tmp_path):
    from vaner_daemon.preparation_engine.module_summarizer import generate_module_summary

    fake_result = MagicMock()
    fake_result.content = "MyMod: handles X and Y."

    with patch("vaner_daemon.preparation_engine.module_summarizer.list_artefacts") as mock_list, \
         patch("vaner_daemon.preparation_engine.module_summarizer.ChatOllama") as MockModel, \
         patch("vaner_daemon.preparation_engine.module_summarizer.write_artefact") as mock_write:
        mock_list.return_value = [_artefact("src/mymod/foo.py"), _artefact("src/mymod/bar.py")]
        mock_instance = MagicMock()
        mock_instance.ainvoke = AsyncMock(return_value=fake_result)
        MockModel.return_value = mock_instance
        mock_write.return_value = None

        result = await generate_module_summary("src/mymod", tmp_path)

    assert result is not None
    assert result.kind == "module_summary"
    assert result.source_path == "src/mymod"
    assert "MyMod" in result.content


@pytest.mark.asyncio
async def test_generate_module_summary_no_files(tmp_path):
    from vaner_daemon.preparation_engine.module_summarizer import generate_module_summary

    with patch("vaner_daemon.preparation_engine.module_summarizer.list_artefacts") as mock_list:
        mock_list.return_value = []
        result = await generate_module_summary("src/empty", tmp_path)

    assert result is None
