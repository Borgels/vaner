# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the curated local-model picker."""

from __future__ import annotations

from vaner.setup.hardware import GPUDevice, HardwareProfile
from vaner.setup.models_registry import recommend


def _profile(*, vram_gb: float, gpu: str = "nvidia", name: str = "GPU") -> HardwareProfile:
    devices = (
        GPUDevice(
            name=name,
            vendor=gpu,
            kind="discrete",
            memory_total_bytes=int(vram_gb * (1024 ** 3)),
            memory_display_gb=vram_gb,
            memory_kind="vram",
        ),
    )
    return HardwareProfile(
        os="linux",
        cpu_class="high",
        ram_gb=64,
        gpu=gpu,
        gpu_vram_gb=vram_gb,
        is_battery=False,
        thermal_constrained=False,
        detected_runtimes=(),
        detected_models=(),
        tier="high_performance" if vram_gb >= 24 else ("capable" if vram_gb >= 12 else "light"),
        gpu_devices=devices,
    )


def test_rtx_5090_picks_qwen3_32b() -> None:
    """The user's actual setup: RTX 5090, mixed work_style → qwen3:32b."""
    payload = recommend(_profile(vram_gb=32, name="NVIDIA GeForce RTX 5090"))
    assert payload["selected"]["id"] == "qwen3:32b"
    assert payload["selected"]["family"] == "qwen3"
    assert payload["budget"]["accelerator"] == "nvidia"
    assert payload["budget"]["effective_gb_q4"] == 32.0
    assert "RTX 5090" in payload["budget"]["accelerator_label"]


def test_coding_workstyle_keeps_qwen3_when_it_fits() -> None:
    """qwen3 carries a 'general' tag; for coding work_style on a 32 GB
    card, qwen3:32b ties qwen2.5-coder:32b on params_b and wins by
    being earlier in the curated registry. Both are 32B-class — the
    tie-break is intentional."""
    payload = recommend(_profile(vram_gb=32), work_styles=("coding",))
    assert payload["selected"]["id"] == "qwen3:32b"


def test_modest_card_drops_to_14b() -> None:
    """16 GB VRAM with 15% headroom (~13.6 GB) drops the picker to the
    14B class, not the 32B."""
    payload = recommend(_profile(vram_gb=16))
    assert payload["selected"]["id"] == "qwen3:14b"


def test_8gb_card_drops_to_8b() -> None:
    payload = recommend(_profile(vram_gb=8))
    assert payload["selected"]["id"] == "qwen3:8b"


def test_already_installed_is_marked() -> None:
    payload = recommend(
        _profile(vram_gb=32),
        installed_models=("qwen3:32b",),
    )
    assert payload["selected"]["already_installed"] is True
    assert payload["user"]["needs_model_download"] is False
    assert payload["user"]["next_actions"] == []


def test_alternatives_exclude_selected() -> None:
    payload = recommend(_profile(vram_gb=32))
    selected_id = payload["selected"]["id"]
    alt_ids = [a["id"] for a in payload["alternatives"]]
    assert selected_id not in alt_ids
    assert len(alt_ids) >= 1
