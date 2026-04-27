# SPDX-License-Identifier: Apache-2.0
"""Tests for ``GET /models/recommended`` (0.8.8 WS10.4).

The endpoint composes:

- ``HardwareProfile`` (cached on the app via ``_get_hardware_profile_cached``)
- ``MemoryBudget`` (computed via :func:`vaner.setup.memory_budget.memory_budget_for`)
- ``Registry`` (loaded from ``src/vaner/setup/recommended/data.json`` if
  present, empty otherwise)

Tests exercise the empty-registry fallback (the case any dev checkout
will hit before the refresh script runs), the budget plumbing, and the
``work_styles`` query-param path.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from vaner.cli.commands.config import load_config
from vaner.cli.commands.init import init_repo
from vaner.daemon.http import create_daemon_http_app
from vaner.setup import hardware as hw
from vaner.setup.recommended.schema import (
    RecommendedModel,
    Registry,
    RegistrySource,
)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def client(repo_root: Path) -> TestClient:
    init_repo(repo_root)
    config = load_config(repo_root)
    app = create_daemon_http_app(config)
    return TestClient(app)


def _force_hardware(client: TestClient, **profile_kwargs: Any) -> None:
    """Inject a synthetic HardwareProfile into the app's cache."""
    base: dict[str, Any] = {
        "os": "linux",
        "cpu_class": "high",
        "ram_gb": 64,
        "gpu": "nvidia",
        "gpu_vram_gb": 24,
        "is_battery": False,
        "thermal_constrained": False,
        "detected_runtimes": (),
        "detected_models": (),
        "tier": "high_performance",
    }
    base.update(profile_kwargs)
    profile = dataclasses.replace(hw.HardwareProfile(**base))
    # The endpoint reads through _get_hardware_profile_cached → the
    # state dict on the app. We mutate that dict directly so the
    # endpoint sees our synthetic profile without a probe call.
    client.app.state.reset_hardware_cache()  # type: ignore[attr-defined]
    # Now trigger a probe that returns our synthetic value via monkey
    # patching: the cleanest way is to put the value back into the dict
    # the closure consults. We do that via the dunder-state escape
    # hatch the test fixture exposes.
    # The cache lives in a closure-local dict referenced by the reset
    # helper; no public setter exists. Stub by reaching into the
    # registered helper.
    cache: dict[str, Any] = client.app.state.reset_hardware_cache.__closure__[0].cell_contents  # type: ignore[union-attr]
    cache["profile"] = profile


def _patch_registry(monkeypatch: pytest.MonkeyPatch, registry: Registry) -> None:
    """Make load_registry() return the supplied registry."""
    from vaner.setup.recommended import loader as loader_mod

    monkeypatch.setattr(loader_mod, "load_registry", lambda path=None: registry)
    # Also patch the re-export through the package + the daemon's
    # local reference (it imports `load_registry` from the package).
    import vaner.setup.recommended as pkg

    monkeypatch.setattr(pkg, "load_registry", lambda path=None: registry)


def _registry_with(*models: RecommendedModel) -> Registry:
    return Registry(
        schema_version=1,
        generated_at=datetime(2026, 4, 27, 19, 0, tzinfo=UTC),
        generator="test",
        sources=(
            RegistrySource(
                name="ollama-library",
                snapshot_at=datetime(2026, 4, 27, 18, 59, tzinfo=UTC),
            ),
        ),
        models=models,
    )


def _model(**overrides: Any) -> RecommendedModel:
    base: dict[str, Any] = {
        "id": "synthetic:7b-instruct",
        "family": "synthetic",
        "params_b": 7.0,
        "min_effective_gb_q4": 5.2,
        "intent_lean": ("coding", "writing"),
        "ollama_id": "synthetic:7b-instruct",
        "huggingface_id": None,
        "context_length": 8192,
        "popularity_rank": 1,
        "rank_source": "ollama-library",
    }
    base.update(overrides)
    return RecommendedModel.model_validate(base)


# ---------------------------------------------------------------------------
# Empty-registry fallback
# ---------------------------------------------------------------------------


def test_empty_registry_returns_selected_null(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev checkout (no data.json) → endpoint reports empty + selected=null."""
    _patch_registry(monkeypatch, _registry_with())  # 0 models
    _force_hardware(client)

    resp = client.get("/models/recommended")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["registry"]["model_count"] == 0
    assert payload["selected"] is None
    assert payload["alternatives"] == []
    # Budget is still reported so the wizard can show "we sized your machine but have no model to recommend yet."
    assert payload["budget"]["effective_gb_q4"] > 0
    assert payload["budget"]["accelerator"] == "nvidia"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_returns_selected_model_when_registry_has_a_fit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_registry(monkeypatch, _registry_with(_model()))
    _force_hardware(client)

    resp = client.get("/models/recommended")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["selected"] is not None
    assert payload["selected"]["id"] == "synthetic:7b-instruct"
    assert payload["alternatives"][0]["id"] == "synthetic:7b-instruct"
    assert payload["registry"]["model_count"] == 1


def test_budget_reflects_apple_silicon_unified_memory(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_registry(monkeypatch, _registry_with())
    _force_hardware(
        client,
        os="darwin",
        gpu="apple_silicon",
        ram_gb=64,
        gpu_vram_gb=None,
    )
    resp = client.get("/models/recommended")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["budget"]["accelerator"] == "apple_silicon"
    # 64 GB unified × 0.55 ≈ 35; tolerate the calibration band.
    assert 30.0 <= payload["budget"]["effective_gb_q4"] <= 45.0


# ---------------------------------------------------------------------------
# work_styles query parameter
# ---------------------------------------------------------------------------


def test_work_styles_query_param_biases_selection(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coding query → coding-leaning model wins over a same-band general one."""
    coder = _model(
        id="synthetic:7b-coder",
        intent_lean=("coding",),
        popularity_rank=10,
    )
    general = _model(
        id="synthetic:8b-general",
        params_b=8.0,
        min_effective_gb_q4=5.8,
        intent_lean=("mixed",),
        popularity_rank=1,
    )
    _patch_registry(monkeypatch, _registry_with(coder, general))
    _force_hardware(client)

    coding_resp = client.get("/models/recommended?work_styles=coding")
    assert coding_resp.status_code == 200
    assert coding_resp.json()["selected"]["id"] == "synthetic:7b-coder"

    no_intent_resp = client.get("/models/recommended")
    assert no_intent_resp.status_code == 200
    # No intent → larger model wins on params.
    assert no_intent_resp.json()["selected"]["id"] == "synthetic:8b-general"


def test_work_styles_csv_parsed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comma-separated work_styles param is split correctly."""
    _patch_registry(monkeypatch, _registry_with(_model(intent_lean=("research",))))
    _force_hardware(client)

    resp = client.get("/models/recommended?work_styles=writing,research,coding")
    assert resp.status_code == 200
    assert resp.json()["selected"] is not None


def test_blank_work_styles_ignored(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_registry(monkeypatch, _registry_with(_model()))
    _force_hardware(client)

    resp = client.get("/models/recommended?work_styles=")
    assert resp.status_code == 200
    # Empty string → treated as no intent; still returns a pick.
    assert resp.json()["selected"]["id"] == "synthetic:7b-instruct"


# ---------------------------------------------------------------------------
# Hardening (0.8.8): the work_styles query string is length-capped
# ---------------------------------------------------------------------------


def test_work_styles_length_cap_returns_400(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pathological query string is rejected with a 400, not silently truncated."""
    _patch_registry(monkeypatch, _registry_with(_model()))
    _force_hardware(client)

    huge = "a" * 257
    resp = client.get(f"/models/recommended?work_styles={huge}")
    assert resp.status_code == 400
    assert "256 chars" in resp.json()["detail"]


def test_work_styles_at_cap_accepted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly 256 chars is allowed (boundary)."""
    _patch_registry(monkeypatch, _registry_with(_model()))
    _force_hardware(client)

    boundary = "a" * 256
    resp = client.get(f"/models/recommended?work_styles={boundary}")
    assert resp.status_code == 200
