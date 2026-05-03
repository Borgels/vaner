from __future__ import annotations

import json
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from vaner.cli.commands.setup import setup_app
from vaner.setup.answers import SetupAnswers
from vaner.setup.config_io import (
    persist_runtime_recommendation,
    persist_setup_and_policy,
)
from vaner.setup.hardware import GPUDevice, HardwareProfile
from vaner.setup.model_recommendation import compute_effective_context_window, load_model_registry, recommend_local_model

runner = CliRunner()


def _profile(**overrides: object) -> HardwareProfile:
    base = {
        "os": "linux",
        "cpu_class": "high",
        "ram_gb": 60,
        "memory_total_bytes": 60 * 1024**3,
        "memory_display_gb": 64,
        "memory_is_unified": False,
        "gpu": "nvidia",
        "gpu_vram_gb": 24,
        "gpu_devices": (
            GPUDevice(
                name="NVIDIA GeForce RTX 4090",
                vendor="nvidia",
                kind="nvidia",
                memory_total_bytes=24 * 1024**3,
                memory_display_gb=26,
                memory_kind="vram",
            ),
        ),
        "is_battery": False,
        "thermal_constrained": False,
        "detected_runtimes": ("ollama",),
        "detected_models": (),
        "tier": "high_performance",
    }
    base.update(overrides)
    return HardwareProfile(**base)  # type: ignore[arg-type]


def _answers(*work_styles: str) -> SetupAnswers:
    return SetupAnswers(
        work_styles=tuple(work_styles or ("mixed",)),
        priority="balanced",
        compute_posture="balanced",
        cloud_posture="local_only",
        background_posture="normal",
    )


def test_model_registry_validates() -> None:
    registry = load_model_registry()
    assert registry.valid is True
    assert registry.schema_version == 1
    assert registry.models


def test_recommendation_user_layer_hides_diagnostics() -> None:
    payload = recommend_local_model(
        answers=_answers("coding"),
        hardware=_profile(),
    )
    assert payload["user"]["detected_accelerator"].startswith("NVIDIA GeForce")
    assert payload["user"]["selected_model"]["model_id"]
    assert "candidate_models" in payload["diagnostics"]
    assert "diagnostics" not in payload["user"]


def test_recommendation_prefers_installed_compatible_model() -> None:
    # Pick whichever bundled model has a compatible installed match.
    payload = recommend_local_model(
        hardware=_profile(detected_models=(("ollama", "qwen3.5:latest", "10GB"),)),
    )
    assert payload["selected"]["model_id"] == "qwen3.5:latest"
    assert payload["user"]["needs_model_download"] is False


def test_recommendation_prefers_qwen36_27b_on_32gb_vram_over_installed_35b() -> None:
    payload = recommend_local_model(
        answers=_answers("coding", "research"),
        hardware=_profile(
            gpu_vram_gb=32,
            gpu_devices=(
                GPUDevice(
                    name="NVIDIA GeForce RTX 5090",
                    vendor="nvidia",
                    kind="nvidia",
                    memory_total_bytes=32 * 1024**3,
                    memory_display_gb=32,
                    memory_kind="vram",
                ),
            ),
            detected_models=(("ollama", "qwen3.5:35b", "22.2GB"),),
        ),
    )
    selected = payload["selected"]
    assert selected["model_id"] == "qwen3.6:27b"
    assert selected["capability"]["context_window"] == 131072
    assert selected["runtime_params"]["num_ctx"] == 131072
    assert payload["user"]["needs_model_download"] is True


def test_qwen36_27b_context_keeps_headroom_on_32gb_vram() -> None:
    chosen = compute_effective_context_window(
        max_context_window=262144,
        weights_gb=14.9,
        effective_memory_gb=30.0,
        work_styles=("coding", "research"),
        runtime="ollama",
    )
    assert chosen == 131072


def test_persist_runtime_recommendation_writes_backend(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    answers = _answers("mixed")
    persist_setup_and_policy(repo, answers, "local_balanced")
    payload = recommend_local_model(answers=answers, hardware=_profile())
    persist_runtime_recommendation(repo, payload)
    parsed = tomllib.loads((repo / ".vaner" / "config.toml").read_text(encoding="utf-8"))
    assert parsed["backend"]["name"] == "ollama"
    assert parsed["backend"]["model"] == payload["selected"]["model_id"]
    assert parsed["exploration"]["exploration_model"] == payload["selected"]["model_id"]
    assert parsed["compute"]["device"] == "cuda"
    assert parsed["limits"]["max_context_tokens"] == payload["selected"]["capability"]["context_window"] // 4


def test_models_recommended_cli(monkeypatch) -> None:
    monkeypatch.setattr("vaner.cli.commands.setup.detect", _profile)
    result = runner.invoke(setup_app, ["models-recommended", "--work-styles", "coding"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["user"]["selected_model"]["model_id"]
