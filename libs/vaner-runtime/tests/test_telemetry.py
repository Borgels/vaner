"""Tests for TelemetryStore."""
from __future__ import annotations

from pathlib import Path

import pytest

from vaner_runtime.telemetry import TelemetryStore


def test_record_and_retrieve(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.db")
    store.record_prep_run("ctx-1", 120.0, 3)
    store.record_prep_run("ctx-2", 80.0, 1)
    store.record_prep_run("ctx-3", 200.0, 5, error="timeout")
    events = store.get_recent(10)
    assert len(events) == 3
    # Newest first
    assert events[0]["context_key"] == "ctx-3"


def test_stats_counts(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.db")
    store.record_prep_run("ctx-a", 100.0, 2)
    store.record_prep_run("ctx-b", 200.0, 4)
    store.record_prep_run("ctx-c", 50.0, 0, error="crashed")
    stats = store.get_stats()
    assert stats["total_runs"] == 3
    assert stats["successful_runs"] == 2
    assert stats["failed_runs"] == 1
    assert stats["avg_duration_ms"] == pytest.approx(116.67, rel=0.01)


def test_empty_store(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.db")
    stats = store.get_stats()
    assert stats["total_runs"] == 0
    assert stats["failed_runs"] == 0
    assert stats["avg_duration_ms"] == 0.0
    assert store.get_recent() == []


def test_artifact_generated(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.db")
    store.record_artifact_generated("file_summary", "src/foo.py", "qwen2.5-coder:32b", 450.0)
    store.record_artifact_generated("diff_summary", ".", "qwen2.5-coder:32b", 300.0)
    stats = store.get_stats()
    assert stats["total_artifacts_generated"] == 2
