# SPDX-License-Identifier: Apache-2.0
"""Continuous hardware-to-memory-budget estimator (0.8.8 WS10).

The original :func:`vaner.setup.hardware.tier_for` collapses every machine
into one of four tiers (``light`` / ``capable`` / ``high_performance`` /
``unknown``). A 32 GB consumer desktop with one mid-range GPU and a
workstation with 2× RTX PRO 6000 (192 GB combined VRAM) both fall into
``high_performance``; nothing the recommendation pipeline does
afterwards can recover that lost fidelity.

This module replaces the bucketed view with a continuous **memory
budget** scalar — the largest comfortably-loadable model size at q4
quantisation expressed in gigabytes. Downstream code (the registry
resolver in :mod:`vaner.models.recommended`) maps the budget to a
specific model selection; the budget itself is the only number that
needs to be calibrated.

The legacy :func:`tier_for` stays in place for backwards compatibility;
nothing here removes or changes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from vaner.setup.hardware import HardwareProfile

# ``cluster`` is reserved for WS10.5's multi-GPU detection; the current
# probes never emit it (gpu_count defaults to 1 and is populated only
# when the GPU probe is extended). We declare it here so consumers can
# branch on it without a follow-up ABI break.
Accelerator = Literal[
    "nvidia",
    "amd",
    "apple_silicon",
    "integrated",
    "cpu_only",
    "cluster",
]


@dataclass(frozen=True, slots=True)
class MemoryBudget:
    """Continuous memory-budget estimate for the local machine.

    Attributes:
        effective_gb_q4: gigabytes of working memory the engine should
            target for a *local* model loaded at q4 quantisation.
            Continuous, not bucketed — the registry resolver maps this
            to a concrete model. Always non-negative; a value of ``0.0``
            means "we could not estimate; recommend the daemon's
            existing fallback path."
        accelerator: which compute path the budget assumes. Drives
            quantisation defaults (e.g. q4_K_M for nvidia, q4_0 for
            cpu_only) and the registry resolver's family preferences
            (e.g. apple_silicon prefers GGUF for llama.cpp + MLX builds).
        gpu_count: number of GPUs detected. Defaults to 1 today; bumped
            by WS10.5's multi-GPU detection.
        can_offload_to_cpu: True when llama.cpp-style spillover is
            available (system RAM > working set). Lets the resolver
            consider models slightly larger than VRAM at the cost of
            speed.
        notes: short human-readable strings describing why the budget
            landed where it did. Surfaced verbatim in the desktop
            wizard's "Recommended preset" card.
    """

    effective_gb_q4: float
    accelerator: Accelerator
    gpu_count: int = 1
    can_offload_to_cpu: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Tunables — calibration constants
# ---------------------------------------------------------------------------

# Apple Silicon shares unified memory between CPU and GPU. The OS keeps
# a portion reserved for itself; modern macOS will let one process
# claim ~60-75% before pressure. We use 0.55 as a conservative-but-real
# fraction — in practice users see this work without OOMing the rest of
# their workspace.
_APPLE_UNIFIED_USABLE_FRACTION = 0.55

# NVIDIA / AMD discrete VRAM: the model needs to fit *with* a KV-cache
# headroom and CUDA runtime overhead. 0.85 leaves room for a 32k-ctx
# KV-cache on a typical model.
_DISCRETE_VRAM_USABLE_FRACTION = 0.85

# CPU-only spillover is bandwidth-bound. We honour the user's RAM but
# bias the budget down hard so the registry resolver doesn't recommend
# 70B-class models that technically fit in 64 GB but run at 0.5 t/s.
_CPU_ONLY_USABLE_FRACTION = 0.40

# Battery throttling is real on laptops without sustained-load thermal
# headroom. Drop the budget another 25% when on battery.
_BATTERY_PENALTY = 0.75

# Extreme-large datacenter rigs (8× H200, DGX Spark cluster) get capped
# at this number — beyond it we tell the user "you have more headroom
# than we model" rather than pretending our recommendation is
# authoritative.
_BUDGET_CAP_GB = 600.0


# ---------------------------------------------------------------------------
# Estimator
# ---------------------------------------------------------------------------


def memory_budget_for(profile: HardwareProfile) -> MemoryBudget:
    """Estimate the local-model memory budget for ``profile``.

    Pure function over the snapshot; no I/O, no clocks, no caching.
    Calibration tunables live as module constants so a future
    telemetry-driven re-fit (Phase 3) can adjust them without touching
    callers.
    """
    if profile.ram_gb <= 0:
        # Unknown system → empty budget; resolver should fall back.
        return MemoryBudget(
            effective_gb_q4=0.0,
            accelerator="cpu_only",
            gpu_count=0,
            can_offload_to_cpu=False,
            notes=("RAM probe returned 0; budget unknown",),
        )

    notes: list[str] = []

    if profile.gpu == "apple_silicon":
        budget = profile.ram_gb * _APPLE_UNIFIED_USABLE_FRACTION
        if profile.is_battery:
            budget *= _BATTERY_PENALTY
            notes.append("battery-throttled")
        notes.append("Apple Silicon unified memory")
        return MemoryBudget(
            effective_gb_q4=_clamp(budget),
            accelerator="apple_silicon",
            gpu_count=1,
            can_offload_to_cpu=False,
            notes=tuple(notes),
        )

    if profile.gpu in ("nvidia", "amd") and profile.gpu_vram_gb:
        # Discrete GPU with known VRAM: the GPU itself is the budget.
        # System RAM beyond VRAM is only useful for llama.cpp spillover,
        # which we flag separately so the resolver can choose to
        # consider larger models with degraded speed.
        gpu_budget = profile.gpu_vram_gb * _DISCRETE_VRAM_USABLE_FRACTION
        can_offload = profile.ram_gb > profile.gpu_vram_gb * 1.5
        if profile.is_battery:
            gpu_budget *= _BATTERY_PENALTY
            notes.append("battery-throttled")
        notes.append(f"{profile.gpu} discrete GPU, {profile.gpu_vram_gb} GB VRAM")
        if can_offload:
            notes.append(f"CPU offload available ({profile.ram_gb} GB system RAM)")
        return MemoryBudget(
            effective_gb_q4=_clamp(gpu_budget),
            accelerator=profile.gpu,  # type: ignore[arg-type]
            gpu_count=1,
            can_offload_to_cpu=can_offload,
            notes=tuple(notes),
        )

    if profile.gpu in ("nvidia", "amd"):
        # Discrete GPU detected but VRAM unknown (Linux lspci-only
        # path). Don't pretend we know — assume a conservative 8 GB
        # equivalent and let the resolver pick something that fits a
        # mid-range card.
        notes.append(f"{profile.gpu} GPU detected, VRAM unknown — conservative estimate")
        budget = 8.0 * _DISCRETE_VRAM_USABLE_FRACTION
        if profile.is_battery:
            budget *= _BATTERY_PENALTY
            notes.append("battery-throttled")
        return MemoryBudget(
            effective_gb_q4=_clamp(budget),
            accelerator=profile.gpu,  # type: ignore[arg-type]
            gpu_count=1,
            can_offload_to_cpu=profile.ram_gb >= 32,
            notes=tuple(notes),
        )

    # No discrete GPU (or only integrated graphics): CPU spillover is
    # the budget. Penalise heavily — these machines technically fit
    # large models but run them at unusable speeds.
    accelerator: Accelerator = "integrated" if profile.gpu == "integrated" else "cpu_only"
    budget = profile.ram_gb * _CPU_ONLY_USABLE_FRACTION
    if profile.is_battery:
        budget *= _BATTERY_PENALTY
        notes.append("battery-throttled")
    if profile.thermal_constrained:
        budget *= _BATTERY_PENALTY
        notes.append("thermal-constrained")
    notes.append("CPU-only inference (no discrete GPU)")
    return MemoryBudget(
        effective_gb_q4=_clamp(budget),
        accelerator=accelerator,
        gpu_count=0,
        can_offload_to_cpu=True,
        notes=tuple(notes),
    )


def _clamp(budget: float) -> float:
    """Clamp the budget to ``[0, _BUDGET_CAP_GB]`` and round to 1 decimal."""
    if budget <= 0:
        return 0.0
    if budget > _BUDGET_CAP_GB:
        return _BUDGET_CAP_GB
    return round(budget, 1)


__all__ = [
    "Accelerator",
    "MemoryBudget",
    "memory_budget_for",
]
