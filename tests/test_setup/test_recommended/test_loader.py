# SPDX-License-Identifier: Apache-2.0
"""Tests for vaner.setup.recommended.loader."""

from __future__ import annotations

import json
from pathlib import Path

from vaner.setup.recommended.loader import load_registry


def test_missing_file_returns_empty_registry(tmp_path: Path) -> None:
    """A non-existent file is the dev-checkout case; loader returns empty."""
    out = load_registry(tmp_path / "data.json")
    assert out.models == ()
    assert out.generator == "loader.py:empty-fallback"


def test_malformed_json_returns_empty_registry(tmp_path: Path) -> None:
    """Bad JSON does not crash the daemon."""
    bad = tmp_path / "data.json"
    bad.write_text("{not-json}", encoding="utf-8")
    out = load_registry(bad)
    assert out.models == ()


def test_schema_mismatch_returns_empty_registry(tmp_path: Path) -> None:
    """An old schema version with extra fields does not crash."""
    bad = tmp_path / "data.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": 99,
                "generated_at": "2026-04-27T19:00:00Z",
                "generator": "wat",
                "sources": [],
                "models": [{"completely-unknown-shape": True}],
            }
        ),
        encoding="utf-8",
    )
    out = load_registry(bad)
    assert out.models == ()


def test_valid_registry_loads_with_all_fields(tmp_path: Path) -> None:
    payload = {
        "schema_version": 1,
        "generated_at": "2026-04-27T19:00:00Z",
        "generator": "test",
        "sources": [
            {"name": "ollama-library", "snapshot_at": "2026-04-27T18:59:00Z"},
        ],
        "models": [
            {
                "id": "synthetic:7b-instruct",
                "family": "synthetic",
                "params_b": 7.0,
                "min_effective_gb_q4": 5.2,
                "intent_lean": ["coding"],
                "ollama_id": "synthetic:7b-instruct",
                "huggingface_id": None,
                "context_length": 8192,
                "popularity_rank": 1,
                "rank_source": "ollama-library",
            },
        ],
    }
    f = tmp_path / "data.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    out = load_registry(f)
    assert len(out.models) == 1
    assert out.models[0].id == "synthetic:7b-instruct"
    assert out.models[0].intent_lean == ("coding",)
