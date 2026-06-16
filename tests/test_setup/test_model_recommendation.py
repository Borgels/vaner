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


def test_system_ram_does_not_expand_default_local_model() -> None:
    payload = recommend_local_model(
        answers=_answers("mixed"),
        hardware=_profile(
            ram_gb=64,
            memory_total_bytes=64 * 1024**3,
            memory_display_gb=64,
            memory_is_unified=False,
            gpu="none",
            gpu_vram_gb=None,
            gpu_devices=(),
        ),
    )
    assert payload["hardware"]["memory_source"] == "system"
    assert payload["hardware"]["effective_memory_gb"] == 2.0
    assert payload["selected"]["model_id"] == "gemma4:e2b"
    assert payload["selected"]["capability"]["context_window"] == 32768


def test_unknown_high_memory_nvidia_uses_cautious_gpu_class_recommendation() -> None:
    payload = recommend_local_model(
        answers=_answers("mixed"),
        hardware=_profile(
            ram_gb=128,
            memory_total_bytes=128 * 1024**3,
            memory_display_gb=128,
            memory_is_unified=False,
            gpu="nvidia",
            gpu_vram_gb=None,
            gpu_devices=(),
        ),
    )
    assert payload["hardware"]["memory_source"] == "inferred_gpu"
    assert payload["hardware"]["effective_memory_gb"] == 30.0
    assert payload["selected"]["model_id"] != "gemma4:e2b"
    assert payload["selected"]["download_size_gb"] >= 16


def test_unknown_low_memory_nvidia_stays_conservative_without_vram_signal() -> None:
    payload = recommend_local_model(
        answers=_answers("mixed"),
        hardware=_profile(
            ram_gb=32,
            memory_total_bytes=32 * 1024**3,
            memory_display_gb=32,
            memory_is_unified=False,
            gpu="nvidia",
            gpu_vram_gb=None,
            gpu_devices=(),
        ),
    )
    assert payload["hardware"]["memory_source"] == "system"
    assert payload["selected"]["model_id"] == "gemma4:e2b"


def test_dgx_spark_uses_unified_memory_and_gpt_oss_120b() -> None:
    payload = recommend_local_model(
        answers=_answers("coding", "research"),
        hardware=_profile(
            ram_gb=128,
            memory_total_bytes=128 * 1024**3,
            memory_display_gb=128,
            memory_is_unified=False,
            disk_free_gb=500,
            gpu="nvidia",
            gpu_vram_gb=None,
            gpu_devices=(
                GPUDevice(
                    name="NVIDIA DGX Spark GB10",
                    vendor="NVIDIA",
                    kind="nvidia",
                    memory_total_bytes=None,
                    memory_display_gb=None,
                    memory_kind="unknown",
                ),
            ),
        ),
    )
    selected = payload["selected"]
    assert payload["hardware"]["memory_source"] == "unified"
    assert payload["hardware"]["effective_memory_gb"] == 120.0
    assert payload["hardware"]["is_unified_memory"] is True
    assert selected["model_id"] == "gpt-oss:120b"
    assert selected["quantization"] == "MXFP4"
    assert selected["params_b"] >= 100
    assert selected["active_params_b"] == 5.1
    assert selected["capability"]["context_window"] == 131072


def test_recommendation_prefers_installed_compatible_model() -> None:
    # Pick whichever bundled model has a compatible installed match.
    payload = recommend_local_model(
        hardware=_profile(detected_models=(("ollama", "gemma4:e4b", "9GB"),)),
    )
    assert payload["selected"]["model_id"] == "gemma4:e4b"
    assert payload["user"]["needs_model_download"] is False


def test_recommendation_prefers_current_fit_on_32gb_vram_over_legacy_installed_model() -> None:
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
    assert selected["model_id"] == "qwen3.6:35b-a3b-coding-nvfp4"
    assert selected["capability"]["context_window"] == 65536
    assert selected["runtime_params"]["num_ctx"] == 65536
    assert payload["user"]["needs_model_download"] is True


def test_recommendation_uses_current_ollama_moe_on_huge_apple_unified_memory() -> None:
    payload = recommend_local_model(
        answers=_answers("mixed"),
        hardware=_profile(
            os="darwin",
            ram_gb=512,
            memory_total_bytes=512 * 1000**3,
            memory_display_gb=512,
            memory_is_unified=True,
            disk_free_gb=300,
            gpu="apple_silicon",
            gpu_vram_gb=None,
            gpu_devices=(
                GPUDevice(
                    name="Apple M3 Ultra",
                    vendor="Apple",
                    kind="apple_silicon",
                    memory_total_bytes=512 * 1000**3,
                    memory_display_gb=512,
                    memory_kind="unified",
                ),
            ),
            detected_runtimes=("ollama",),
        ),
    )
    selected = payload["selected"]
    assert selected["model_id"] == "qwen3.6:35b-a3b-coding-mxfp8"
    assert selected["runtime"] == "ollama"
    assert selected["architecture"] == "moe"
    assert selected["quantization"] == "MXFP8"
    assert selected["capability"]["context_window"] == 262144
    assert selected["runtime_params"]["num_ctx"] == 262144
    assert payload["user"]["needs_runtime_install"] is False


def test_speed_posture_avoids_oversized_stale_context_model_on_huge_apple() -> None:
    payload = recommend_local_model(
        answers=SetupAnswers(
            work_styles=("mixed",),
            priority="speed",
            compute_posture="balanced",
            cloud_posture="ask_first",
            background_posture="normal",
        ),
        hardware=_profile(
            os="darwin",
            ram_gb=512,
            memory_total_bytes=512 * 1000**3,
            memory_display_gb=512,
            memory_is_unified=True,
            disk_free_gb=300,
            gpu="apple_silicon",
            gpu_vram_gb=None,
            gpu_devices=(
                GPUDevice(
                    name="Apple M3 Ultra",
                    vendor="Apple",
                    kind="apple_silicon",
                    memory_total_bytes=512 * 1000**3,
                    memory_display_gb=512,
                    memory_kind="unified",
                ),
            ),
            detected_runtimes=("ollama",),
        ),
    )
    assert payload["selected"]["model_id"] != "llama4:16x17b"


def test_disk_space_filters_large_downloads() -> None:
    payload = recommend_local_model(
        answers=_answers("mixed"),
        hardware=_profile(
            os="darwin",
            ram_gb=512,
            memory_total_bytes=512 * 1000**3,
            memory_display_gb=512,
            memory_is_unified=True,
            disk_free_gb=25,
            gpu="apple_silicon",
            gpu_vram_gb=None,
            gpu_devices=(
                GPUDevice(
                    name="Apple M3 Ultra",
                    vendor="Apple",
                    kind="apple_silicon",
                    memory_total_bytes=512 * 1000**3,
                    memory_display_gb=512,
                    memory_kind="unified",
                ),
            ),
            detected_runtimes=("ollama",),
        ),
    )
    assert payload["selected"]["model_id"] == "gemma4:e4b"
    assert payload["selected"]["disk"]["status"] in {"enough", "tight"}
    rejected = payload["diagnostics"]["rejected_models"]
    assert any(row["model_id"] == "qwen3.6:35b-a3b-coding-nvfp4" and row["reason"] == "insufficient_disk" for row in rejected)


def test_qwen36_27b_context_keeps_headroom_on_32gb_vram() -> None:
    chosen = compute_effective_context_window(
        max_context_window=262144,
        weights_gb=14.9,
        effective_memory_gb=30.0,
        work_styles=("coding", "research"),
        runtime="ollama",
    )
    assert chosen == 131072


def test_heavy_qwen_context_caps_at_64k_on_32gb_vram() -> None:
    chosen = compute_effective_context_window(
        max_context_window=262144,
        weights_gb=22.0,
        effective_memory_gb=30.0,
        work_styles=("coding", "research"),
        runtime="ollama",
        kv_reference_gb=1.8,
    )
    assert chosen == 65536


def test_heavy_qwen_context_uses_256k_on_48gb_plus_vram() -> None:
    chosen = compute_effective_context_window(
        max_context_window=262144,
        weights_gb=22.0,
        effective_memory_gb=46.0,
        work_styles=("coding", "research"),
        runtime="ollama",
        kv_reference_gb=1.8,
    )
    assert chosen == 262144


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
    assert parsed["backend"]["runtime_options"]["num_ctx"] == payload["selected"]["runtime_params"]["num_ctx"]
    assert parsed["backend"]["sampling_options"] == payload["selected"]["sampling_params"]
    assert parsed["exploration"]["model"] == payload["selected"]["model_id"]
    assert parsed["exploration"]["runtime_options"]["num_ctx"] == payload["selected"]["runtime_params"]["num_ctx"]
    assert parsed["exploration"]["sampling_options"] == payload["selected"]["sampling_params"]
    assert "exploration_model" not in parsed["exploration"]
    assert parsed["compute"]["device"] == "cuda"
    assert parsed["limits"]["max_context_tokens"] == payload["selected"]["capability"]["context_window"] // 3


def test_persist_huge_apple_recommendation_keeps_ollama_backend(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    answers = _answers("mixed")
    persist_setup_and_policy(repo, answers, "local_balanced")
    payload = recommend_local_model(
        answers=answers,
        hardware=_profile(
            os="darwin",
            ram_gb=512,
            memory_total_bytes=512 * 1000**3,
            memory_display_gb=512,
            memory_is_unified=True,
            disk_free_gb=300,
            gpu="apple_silicon",
            gpu_vram_gb=None,
            gpu_devices=(
                GPUDevice(
                    name="Apple M3 Ultra",
                    vendor="Apple",
                    kind="apple_silicon",
                    memory_total_bytes=512 * 1000**3,
                    memory_display_gb=512,
                    memory_kind="unified",
                ),
            ),
            detected_runtimes=("ollama",),
        ),
    )
    persist_runtime_recommendation(repo, payload)
    parsed = tomllib.loads((repo / ".vaner" / "config.toml").read_text(encoding="utf-8"))
    assert parsed["backend"]["name"] == "ollama"
    assert parsed["backend"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert parsed["exploration"]["backend"] == "ollama"
    assert parsed["exploration"]["runtime_options"]["num_ctx"] == payload["selected"]["runtime_params"]["num_ctx"]
    assert parsed["compute"]["device"] == "mps"
    assert parsed["limits"]["max_context_tokens"] == payload["selected"]["capability"]["context_window"] // 3


def test_models_recommended_cli(monkeypatch) -> None:
    monkeypatch.setattr("vaner.cli.commands.setup.detect", _profile)
    result = runner.invoke(setup_app, ["models-recommended", "--work-styles", "coding"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["user"]["selected_model"]["model_id"]
