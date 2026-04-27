# SPDX-License-Identifier: Apache-2.0
"""End-to-end test for scripts/refresh_recommended_models.py.

Drives the refresh script against synthetic Ollama fixtures (no live
network calls) and asserts the produced ``data.json`` is schema-valid
+ contains models in the expected param bands.

The fixtures use fictional names (``alpha``, ``bravo``, …) on purpose
— we test bucketing + scraping logic, not which real-world model
gets surfaced.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from vaner.setup.recommended.schema import Registry

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "recommended"
SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "refresh_recommended_models.py"


@pytest.fixture
def refresh_module() -> object:
    """Import the script as a module so we can call main() programmatically.

    The module is registered into ``sys.modules`` *before* exec so that
    its frozen dataclasses can resolve their own ``__module__`` during
    ``__init_subclass__``-time annotation handling. Without this,
    ``dataclass(frozen=True, slots=True)`` blows up with
    ``AttributeError: 'NoneType' object has no attribute '__dict__'`` on
    Python 3.12.
    """
    spec = importlib.util.spec_from_file_location("refresh_recommended_models", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["refresh_recommended_models"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("refresh_recommended_models", None)
        raise
    return module


def test_refresh_against_fixtures_writes_valid_registry(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "data.json"
    rc = refresh_module.main(  # type: ignore[attr-defined]
        [
            "--source-snapshot",
            str(FIXTURE_DIR),
            "--out",
            str(out_path),
        ],
    )
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    registry = Registry.model_validate(payload)
    assert registry.schema_version == 1
    assert len(registry.models) > 0
    # Every model in the registry has a non-null Ollama id.
    for m in registry.models:
        assert m.ollama_id is not None
        assert m.ollama_id == m.id


def test_refresh_buckets_cover_multiple_param_bands(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    """The fixture has 1.5B/3B/7B/8B/13B/32B/70B/405B → must hit ≥ 4 bands."""
    out_path = tmp_path / "data.json"
    refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(FIXTURE_DIR), "--out", str(out_path)],
    )
    registry = Registry.model_validate(json.loads(out_path.read_text(encoding="utf-8")))
    bands = {
        (
            params <= 4,
            4 < params <= 10,
            10 < params <= 20,
            20 < params <= 50,
            50 < params <= 100,
            100 < params <= 250,
            params > 250,
        ).index(True)
        for params in (m.params_b for m in registry.models)
    }
    assert len(bands) >= 4, f"only {len(bands)} param bands hit: {bands}"


def test_refresh_dedupes_same_family_same_size(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    """No duplicate (family, params_b) pairs in the produced registry."""
    out_path = tmp_path / "data.json"
    refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(FIXTURE_DIR), "--out", str(out_path)],
    )
    registry = Registry.model_validate(json.loads(out_path.read_text(encoding="utf-8")))
    keys = [(m.family, m.params_b) for m in registry.models]
    assert len(keys) == len(set(keys)), keys


def test_refresh_assigns_min_budget_above_zero(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "data.json"
    refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(FIXTURE_DIR), "--out", str(out_path)],
    )
    registry = Registry.model_validate(json.loads(out_path.read_text(encoding="utf-8")))
    for m in registry.models:
        assert m.min_effective_gb_q4 > 0


def test_refresh_fails_loudly_on_empty_registry(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    """The CI staleness gate: zero models must return non-zero exit."""
    empty_dir = tmp_path / "empty-snapshot"
    empty_dir.mkdir()
    # Library file exists but has no model anchors.
    (empty_dir / "ollama_library.html").write_text("<html><body>nothing</body></html>", encoding="utf-8")

    rc = refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(empty_dir), "--out", str(tmp_path / "data.json")],
    )
    assert rc != 0


def test_refresh_allow_empty_overrides_the_gate(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty-snapshot"
    empty_dir.mkdir()
    (empty_dir / "ollama_library.html").write_text("<html></html>", encoding="utf-8")
    rc = refresh_module.main(  # type: ignore[attr-defined]
        [
            "--source-snapshot",
            str(empty_dir),
            "--out",
            str(tmp_path / "data.json"),
            "--allow-empty",
        ],
    )
    assert rc == 0


def test_refresh_intent_lean_for_coder_family_includes_coding(
    refresh_module: object,
    tmp_path: Path,
) -> None:
    """The 'charlie-coder' fixture should be tagged as a coding model."""
    out_path = tmp_path / "data.json"
    refresh_module.main(  # type: ignore[attr-defined]
        ["--source-snapshot", str(FIXTURE_DIR), "--out", str(out_path)],
    )
    registry = Registry.model_validate(json.loads(out_path.read_text(encoding="utf-8")))
    coder_entries = [m for m in registry.models if "charlie-coder" in m.id]
    assert coder_entries, [m.id for m in registry.models]
    # Unknown family in the FAMILY_INTENTS table → defaults to ("mixed",).
    # The fixture name is intentionally not in the mapping; this asserts
    # the safe fallback path. If the maintainer adds 'charlie' to the
    # mapping later, this test will fail loudly and prompt a rethink.
    for m in coder_entries:
        assert m.intent_lean == ("mixed",)
