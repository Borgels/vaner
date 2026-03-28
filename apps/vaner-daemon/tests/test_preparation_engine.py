"""Tests for the preparation engine components."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaner_daemon.preparation_engine.triggers import Debouncer, PrepTrigger
from vaner_daemon.preparation_engine.planner import (
    ArtifactJob,
    PreparationPlanner,
    WATCHED_EXTENSIONS,
)
from vaner_runtime.job_queue import Priority


# ---------------------------------------------------------------------------
# Debouncer tests
# ---------------------------------------------------------------------------

def _trigger(context_key: str = "abc", reason: str = "file_changed") -> PrepTrigger:
    return PrepTrigger(
        context_key=context_key,
        active_files=[],
        branch="develop",
        reason=reason,
    )


def test_debouncer_blocks_rapid_triggers():
    d = Debouncer(min_interval_seconds=10.0)
    t = _trigger("key1")
    assert d.should_trigger(t) is True
    assert d.should_trigger(t) is False


def test_debouncer_allows_after_interval():
    d = Debouncer(min_interval_seconds=0.05)
    t = _trigger("key2")
    assert d.should_trigger(t) is True
    assert d.should_trigger(t) is False  # blocked
    time.sleep(0.1)
    assert d.should_trigger(t) is True   # interval elapsed


def test_debouncer_independent_keys():
    d = Debouncer(min_interval_seconds=10.0)
    t1 = _trigger("keyA")
    t2 = _trigger("keyB")
    assert d.should_trigger(t1) is True
    assert d.should_trigger(t2) is True
    assert d.should_trigger(t1) is False
    assert d.should_trigger(t2) is False


# ---------------------------------------------------------------------------
# Planner tests
# ---------------------------------------------------------------------------

def _make_artefact(source_path: str, kind: str = "file_summary", age: float = 0):
    from vaner_tools.artefact_store import Artefact
    return Artefact(
        key=source_path,
        source_path=source_path,
        kind=kind,
        content="summary",
        generated_at=time.time() - age,
        source_mtime=time.time() - age - 1,
        model="test",
    )


def test_planner_file_summary_for_missing(tmp_path):
    planner = PreparationPlanner(tmp_path)
    trigger = PrepTrigger(
        context_key="ctx",
        active_files=["src/main.py", "src/utils.py"],
        branch="develop",
        reason="file_changed",
    )
    jobs = planner.plan(trigger, existing_artifacts=[])
    kinds = [j.artifact_kind for j in jobs]
    assert "file_summary" in kinds
    assert len(jobs) == 2


def test_planner_skip_non_watched_extension(tmp_path):
    planner = PreparationPlanner(tmp_path)
    trigger = PrepTrigger(
        context_key="ctx",
        active_files=["build/output.pyc", "cache/data.log"],
        branch="develop",
        reason="file_changed",
    )
    jobs = planner.plan(trigger, existing_artifacts=[])
    assert jobs == []


def test_planner_diff_on_git_commit(tmp_path):
    planner = PreparationPlanner(tmp_path)
    trigger = PrepTrigger(
        context_key="ctx",
        active_files=[],
        branch="develop",
        reason="git_commit",
    )
    jobs = planner.plan(trigger, existing_artifacts=[])
    assert len(jobs) == 1
    assert jobs[0].artifact_kind == "diff_summary"
    assert jobs[0].priority == Priority.CRITICAL


def test_planner_caps_at_max_jobs(tmp_path):
    planner = PreparationPlanner(tmp_path, max_jobs=5)
    files = [f"src/file_{i}.py" for i in range(20)]
    trigger = PrepTrigger(
        context_key="ctx",
        active_files=files,
        branch="develop",
        reason="file_changed",
    )
    jobs = planner.plan(trigger, existing_artifacts=[])
    assert len(jobs) <= 5


def test_planner_skips_fresh_artifacts(tmp_path):
    planner = PreparationPlanner(tmp_path)
    existing = [_make_artefact("src/main.py", age=10)]  # recent, not stale
    trigger = PrepTrigger(
        context_key="ctx",
        active_files=["src/main.py"],
        branch="develop",
        reason="file_changed",
    )
    # Fresh artifact: is_stale should be False → no job
    with patch("vaner_daemon.preparation_engine.planner.is_stale", return_value=False):
        jobs = planner.plan(trigger, existing_artifacts=existing)
    assert jobs == []


# ---------------------------------------------------------------------------
# Generator tests (mocked model)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generator_file_summary_mocked(tmp_path):
    from vaner_daemon.preparation_engine.generator import generate_file_summary

    src = tmp_path / "hello.py"
    src.write_text("def hello(): pass\n")

    fake_result = MagicMock()
    fake_result.content = "Defines hello() function."

    with patch("vaner_daemon.preparation_engine.generator.ChatOllama") as MockModel, \
         patch("vaner_daemon.preparation_engine.generator.write_artefact") as mock_write:
        mock_instance = MagicMock()
        mock_instance.ainvoke = AsyncMock(return_value=fake_result)
        MockModel.return_value = mock_instance

        from vaner_tools.artefact_store import Artefact
        mock_write.return_value = Artefact(
            key=str(src),
            source_path=str(src),
            kind="file_summary",
            content="Defines hello() function.",
            generated_at=time.time(),
            source_mtime=time.time(),
            model="qwen2.5-coder:32b",
        )

        result = await generate_file_summary(src, tmp_path)

    assert result is not None
    assert result.kind == "file_summary"
    mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_generator_diff_summary_mocked(tmp_path):
    from vaner_daemon.preparation_engine.generator import generate_diff_summary

    fake_result = MagicMock()
    fake_result.content = "Added retry logic to job_store."

    with patch("vaner_daemon.preparation_engine.generator.subprocess.check_output", return_value="diff output"), \
         patch("vaner_daemon.preparation_engine.generator.ChatOllama") as MockModel, \
         patch("vaner_daemon.preparation_engine.generator.write_artefact") as mock_write:
        mock_instance = MagicMock()
        mock_instance.ainvoke = AsyncMock(return_value=fake_result)
        MockModel.return_value = mock_instance

        from vaner_tools.artefact_store import Artefact
        mock_write.return_value = Artefact(
            key=str(tmp_path / "diff_summary_sentinel"),
            source_path=str(tmp_path / "diff_summary_sentinel"),
            kind="diff_summary",
            content="Added retry logic to job_store.",
            generated_at=time.time(),
            source_mtime=time.time(),
            model="qwen2.5-coder:32b",
        )

        result = await generate_diff_summary(tmp_path)

    assert result is not None
    assert result.kind == "diff_summary"
