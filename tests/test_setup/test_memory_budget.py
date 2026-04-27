# SPDX-License-Identifier: Apache-2.0
"""Tests for :mod:`vaner.setup.memory_budget`.

The test parameters mirror the calibration table in the 0.8.8 plan
addendum (WS10.1). Each row asserts the budget lands within ±25% of an
expected value — the absolute number doesn't matter, only that the
estimator produces a sensible *ordering* across silhouettes (a Mac
Studio Ultra must score higher than a laptop, a 4090 desktop must
score higher than an integrated-graphics box, etc.).
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from vaner.setup import hardware as hw
from vaner.setup.memory_budget import MemoryBudget, memory_budget_for


def _profile(**overrides: Any) -> hw.HardwareProfile:
    base: dict[str, Any] = {
        "os": "linux",
        "cpu_class": "mid",
        "ram_gb": 16,
        "gpu": "integrated",
        "gpu_vram_gb": None,
        "is_battery": False,
        "thermal_constrained": False,
        "detected_runtimes": (),
        "detected_models": (),
        "tier": "unknown",
    }
    base.update(overrides)
    return dataclasses.replace(hw.HardwareProfile(**base))


# ---------------------------------------------------------------------------
# Calibration table — silhouette -> expected budget (with ±25% tolerance)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,profile_kwargs,expected_min,expected_max",
    [
        # Laptop with no discrete GPU. Plan target 4 GB; realistic
        # range allows for both the battery-throttled path (3.6) and
        # the no-battery integrated path (6.4).
        (
            "laptop-16gb-no-dgpu-battery",
            {"os": "linux", "cpu_class": "low", "ram_gb": 16, "gpu": "none", "is_battery": True},
            3.0,
            7.0,
        ),
        # Laptop with dGPU. Discrete branch at 8 GB VRAM.
        (
            "laptop-32gb-rtx4060-battery",
            {"os": "linux", "cpu_class": "mid", "ram_gb": 32, "gpu": "nvidia", "gpu_vram_gb": 8, "is_battery": True},
            4.0,
            7.0,
        ),
        (
            "desktop-32gb-rtx4060",
            {"os": "linux", "cpu_class": "mid", "ram_gb": 32, "gpu": "nvidia", "gpu_vram_gb": 8, "is_battery": False},
            6.0,
            8.0,
        ),
        # Apple Silicon M-series 32 GB.
        (
            "apple-silicon-m3-32gb-desktop",
            {"os": "darwin", "cpu_class": "high", "ram_gb": 32, "gpu": "apple_silicon", "is_battery": False},
            14.0,
            22.0,
        ),
        (
            "apple-silicon-m3-pro-laptop-32gb",
            {"os": "darwin", "cpu_class": "high", "ram_gb": 32, "gpu": "apple_silicon", "is_battery": True},
            10.0,
            16.0,
        ),
        # Apple Silicon Pro/Max 64 GB.
        (
            "apple-silicon-max-64gb",
            {"os": "darwin", "cpu_class": "high", "ram_gb": 64, "gpu": "apple_silicon", "is_battery": False},
            30.0,
            45.0,
        ),
        # Apple Silicon Ultra 192 GB.
        (
            "apple-silicon-ultra-192gb",
            {"os": "darwin", "cpu_class": "high", "ram_gb": 192, "gpu": "apple_silicon", "is_battery": False},
            85.0,
            130.0,
        ),
        # Apple Silicon Ultra 512 GB.
        (
            "apple-silicon-ultra-512gb",
            {"os": "darwin", "cpu_class": "high", "ram_gb": 512, "gpu": "apple_silicon", "is_battery": False},
            220.0,
            340.0,
        ),
        # Desktop with RTX 4090 24 GB.
        (
            "desktop-64gb-rtx4090",
            {"os": "linux", "cpu_class": "high", "ram_gb": 64, "gpu": "nvidia", "gpu_vram_gb": 24, "is_battery": False},
            17.0,
            25.0,
        ),
        # Single RTX PRO 6000 96 GB (workstation, but until WS10.5
        # multi-GPU detection lands the budget reflects one card).
        (
            "workstation-128gb-rtx-pro-6000-single",
            {"os": "linux", "cpu_class": "high", "ram_gb": 128, "gpu": "nvidia", "gpu_vram_gb": 96, "is_battery": False},
            70.0,
            100.0,
        ),
        # NVIDIA GPU detected, VRAM unknown (Linux lspci-only path).
        (
            "linux-lspci-nvidia-vram-unknown",
            {"os": "linux", "cpu_class": "mid", "ram_gb": 32, "gpu": "nvidia", "gpu_vram_gb": None, "is_battery": False},
            5.0,
            8.0,
        ),
        # Integrated graphics laptop (e.g., AMD Ryzen with iGPU).
        (
            "integrated-laptop-16gb",
            {"os": "linux", "cpu_class": "mid", "ram_gb": 16, "gpu": "integrated", "is_battery": True},
            3.5,
            7.5,
        ),
    ],
)
def test_calibration_silhouettes(
    label: str,
    profile_kwargs: dict[str, Any],
    expected_min: float,
    expected_max: float,
) -> None:
    """Each calibration silhouette produces a budget in the expected band."""
    profile = _profile(**profile_kwargs)
    budget = memory_budget_for(profile)
    assert isinstance(budget, MemoryBudget), label
    assert expected_min <= budget.effective_gb_q4 <= expected_max, (
        f"{label}: budget {budget.effective_gb_q4} not in [{expected_min}, {expected_max}]"
    )


# ---------------------------------------------------------------------------
# Ordering invariants — the absolute numbers can drift, the *ordering*
# across silhouettes must not.
# ---------------------------------------------------------------------------


def test_ordering_apple_silicon_grows_with_unified_memory() -> None:
    """An M-series with more unified memory has a strictly larger budget."""
    sizes = (16, 32, 64, 128, 192, 512)
    budgets = [memory_budget_for(_profile(os="darwin", ram_gb=ram, gpu="apple_silicon")) for ram in sizes]
    values = [b.effective_gb_q4 for b in budgets]
    assert values == sorted(values), values


def test_ordering_4090_beats_4060() -> None:
    """A 24 GB GPU produces a larger budget than an 8 GB GPU on the same box."""
    weak = memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=8, ram_gb=64))
    strong = memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=24, ram_gb=64))
    assert strong.effective_gb_q4 > weak.effective_gb_q4


def test_ordering_apple_ultra_beats_consumer_4090() -> None:
    """A Mac Studio Ultra 192 GB outranks a consumer RTX 4090 desktop."""
    apple = memory_budget_for(_profile(os="darwin", gpu="apple_silicon", ram_gb=192))
    nvidia = memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=24, ram_gb=64))
    assert apple.effective_gb_q4 > nvidia.effective_gb_q4


def test_ordering_battery_penalty() -> None:
    """A battery-bound machine scores lower than the same hardware on AC."""
    on_ac = memory_budget_for(_profile(os="darwin", gpu="apple_silicon", ram_gb=64, is_battery=False))
    on_battery = memory_budget_for(_profile(os="darwin", gpu="apple_silicon", ram_gb=64, is_battery=True))
    assert on_battery.effective_gb_q4 < on_ac.effective_gb_q4


def test_ordering_thermal_penalty() -> None:
    """Thermal-constrained CPU-only is hit harder than a cool box."""
    cool = memory_budget_for(_profile(gpu="none", ram_gb=64, thermal_constrained=False))
    hot = memory_budget_for(_profile(gpu="none", ram_gb=64, thermal_constrained=True))
    assert hot.effective_gb_q4 < cool.effective_gb_q4


# ---------------------------------------------------------------------------
# Edge / failure cases
# ---------------------------------------------------------------------------


def test_zero_ram_returns_empty_budget() -> None:
    """A failed RAM probe produces a 0 GB budget the resolver can fall back from."""
    budget = memory_budget_for(_profile(ram_gb=0))
    assert budget.effective_gb_q4 == 0.0
    assert budget.accelerator == "cpu_only"
    assert "RAM probe returned 0; budget unknown" in budget.notes


def test_extreme_hardware_caps_at_600() -> None:
    """A wildly large machine clamps to the 600 GB cap, not infinity."""
    # Synthetic "single GPU" with 1 TB VRAM; cap kicks in.
    budget = memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=1024, ram_gb=2048))
    assert budget.effective_gb_q4 == 600.0


def test_accelerator_propagates_to_budget() -> None:
    """The accelerator field follows the GPU type."""
    assert memory_budget_for(_profile(gpu="apple_silicon", ram_gb=32)).accelerator == "apple_silicon"
    assert memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=8, ram_gb=32)).accelerator == "nvidia"
    assert memory_budget_for(_profile(gpu="amd", gpu_vram_gb=16, ram_gb=32)).accelerator == "amd"
    assert memory_budget_for(_profile(gpu="none", ram_gb=32)).accelerator == "cpu_only"
    assert memory_budget_for(_profile(gpu="integrated", ram_gb=32)).accelerator == "integrated"


def test_notes_describe_the_path() -> None:
    """The notes tuple is human-readable and reflects the chosen branch."""
    notes = memory_budget_for(_profile(os="darwin", gpu="apple_silicon", ram_gb=64)).notes
    assert any("Apple Silicon" in n for n in notes)

    notes = memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=24, ram_gb=64)).notes
    assert any("nvidia" in n for n in notes)

    notes = memory_budget_for(_profile(gpu="none", ram_gb=32, is_battery=True)).notes
    assert any("CPU-only" in n for n in notes)
    assert any("battery-throttled" in n for n in notes)


def test_can_offload_to_cpu_set_when_ram_dwarfs_vram() -> None:
    """A 4090 box with 64 GB RAM can spillover; an 8 GB GPU box at 16 GB RAM cannot."""
    big = memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=24, ram_gb=64))
    assert big.can_offload_to_cpu

    small = memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=8, ram_gb=12))
    # 12 GB RAM with 8 GB VRAM → can't comfortably spillover (RAM not > 1.5 × VRAM)
    assert not small.can_offload_to_cpu


# ---------------------------------------------------------------------------
# WS10.5 — multi-GPU + datacenter accelerator
# ---------------------------------------------------------------------------


def test_dual_pro_workstation_uses_total_vram() -> None:
    """A 2× RTX PRO 6000 box (192 GB total) must size against the total."""
    profile = _profile(
        ram_gb=128,
        gpu="nvidia",
        gpu_vram_gb=96,  # single-card view (legacy field)
        gpu_count=2,
        total_vram_gb=192,
    )
    budget = memory_budget_for(profile)
    # 192 × 0.85 = 163.2 — keeps two-card workstations honest.
    assert 150.0 <= budget.effective_gb_q4 <= 175.0
    # 2 GPUs is below the cluster threshold (≥4); accelerator stays
    # "nvidia" so the resolver's nvidia-flavoured family weights still
    # apply.
    assert budget.accelerator == "nvidia"
    assert budget.gpu_count == 2


def test_4plus_gpu_rig_flips_to_cluster_accelerator() -> None:
    """A quad+ GPU rig labels the accelerator 'cluster'."""
    profile = _profile(
        ram_gb=512,
        gpu="nvidia",
        gpu_vram_gb=80,
        gpu_count=4,
        total_vram_gb=320,
    )
    budget = memory_budget_for(profile)
    assert budget.accelerator == "cluster"
    assert budget.gpu_count == 4


def test_datacenter_accelerator_flag_flips_cluster_label() -> None:
    """A single H200 (datacenter SKU) labels accelerator='cluster' even at gpu_count=1."""
    profile = _profile(
        ram_gb=2048,
        gpu="nvidia",
        gpu_vram_gb=141,
        gpu_count=1,
        total_vram_gb=141,
        datacenter_accelerator=True,
    )
    budget = memory_budget_for(profile)
    assert budget.accelerator == "cluster"
    assert any("datacenter-class" in n for n in budget.notes)


def test_8x_h200_class_caps_at_600() -> None:
    """An 8× H200 (1128 GB total) clamps to the 600 GB datacenter cap."""
    profile = _profile(
        ram_gb=2048,
        gpu="nvidia",
        gpu_vram_gb=141,
        gpu_count=8,
        total_vram_gb=1128,
        datacenter_accelerator=True,
    )
    budget = memory_budget_for(profile)
    assert budget.effective_gb_q4 == 600.0
    assert budget.accelerator == "cluster"
    assert budget.gpu_count == 8


def test_single_card_unchanged_by_topology_fields() -> None:
    """The default topology values keep single-card behaviour identical."""
    profile = _profile(gpu="nvidia", gpu_vram_gb=24, ram_gb=64)
    budget = memory_budget_for(profile)
    assert budget.accelerator == "nvidia"
    assert budget.gpu_count == 1
    # Same numeric range as the original calibration row.
    assert 17.0 <= budget.effective_gb_q4 <= 25.0


def test_total_vram_gb_overrides_single_when_present() -> None:
    """If total_vram_gb > gpu_vram_gb, the budget uses total."""
    single_view = memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=24, ram_gb=64))
    multi_view = memory_budget_for(_profile(gpu="nvidia", gpu_vram_gb=24, ram_gb=64, gpu_count=2, total_vram_gb=48))
    assert multi_view.effective_gb_q4 > single_view.effective_gb_q4


def test_datacenter_marker_helper() -> None:
    """The marker helper recognises common datacenter SKUs and ignores workstation cards."""
    from vaner.setup.hardware import _is_datacenter_gpu_name

    assert _is_datacenter_gpu_name("NVIDIA H100 80GB HBM3")
    assert _is_datacenter_gpu_name("NVIDIA H200 141GB HBM3e")
    assert _is_datacenter_gpu_name("NVIDIA A100-SXM4-80GB")
    assert _is_datacenter_gpu_name("NVIDIA B200")
    assert not _is_datacenter_gpu_name("NVIDIA RTX 4090")
    assert not _is_datacenter_gpu_name("NVIDIA RTX PRO 6000 Blackwell")
    assert not _is_datacenter_gpu_name("AMD Radeon RX 7900 XTX")
