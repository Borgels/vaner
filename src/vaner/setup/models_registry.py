# SPDX-License-Identifier: Apache-2.0
"""Curated model registry for `vaner setup models-recommended`.

Vaner is local-first; the model loop runs on Ollama by default and the
desktop's onboarding wizard wants a single recommended model that
"just works" on the user's GPU. Pre-fix the registry didn't exist and
``vaner setup recommend`` returned an empty ``model_recommendation``,
so the wizard's recommended-preset card had nothing to surface.

This module is the small, opinionated registry that powers the
recommendation. Entries are curated rather than scraped — we want
"Vaner's pick", not "every model on Ollama".

Picking rules
-------------
1. Group entries by `family`.
2. Compute the user's effective memory budget:
     - dedicated GPU → VRAM
     - unified memory (Apple) → unified pool minus 4 GB headroom
     - integrated / cpu-only → system RAM minus 4 GB
3. Filter to entries whose `min_effective_gb_q4 ≤ budget * 0.85` (leave
   ~15% headroom for the runtime + KV cache).
4. Among the survivors, prefer entries tagged for the user's first
   work-style (e.g. `coding` → `qwen2.5-coder:32b`), then pick the
   largest by `params_b`.
5. Fall back to the smallest entry if nothing fits — a tiny model on
   CPU is better than nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vaner.setup.hardware import HardwareProfile


@dataclass(frozen=True)
class ModelEntry:
    id: str
    display_name: str
    family: str
    params_b: float
    min_effective_gb_q4: float
    recommended_effective_memory_gb: float
    workload_tags: tuple[str, ...]


# Curated registry. Order is intentional — biggest member of each
# family first, so iteration over `MODELS` finds the largest fitting
# entry naturally. Memory numbers are conservative q4 footprints
# (model + context + small KV cache headroom).
MODELS: tuple[ModelEntry, ...] = (
    ModelEntry(
        id="qwen3:32b",
        display_name="Qwen 3 32B",
        family="qwen3",
        params_b=32.0,
        min_effective_gb_q4=20.0,
        recommended_effective_memory_gb=24.0,
        workload_tags=("general", "writing", "research", "support", "learning", "mixed"),
    ),
    ModelEntry(
        id="qwen3:14b",
        display_name="Qwen 3 14B",
        family="qwen3",
        params_b=14.0,
        min_effective_gb_q4=10.0,
        recommended_effective_memory_gb=12.0,
        workload_tags=("general", "writing", "research", "support", "learning", "mixed"),
    ),
    ModelEntry(
        id="qwen3:8b",
        display_name="Qwen 3 8B",
        family="qwen3",
        params_b=8.0,
        min_effective_gb_q4=6.0,
        recommended_effective_memory_gb=8.0,
        workload_tags=("general", "writing", "research", "support", "learning", "mixed"),
    ),
    ModelEntry(
        id="qwen3:4b",
        display_name="Qwen 3 4B",
        family="qwen3",
        params_b=4.0,
        min_effective_gb_q4=3.5,
        recommended_effective_memory_gb=5.0,
        workload_tags=("general", "writing", "support", "learning", "mixed"),
    ),
    ModelEntry(
        id="qwen2.5-coder:32b",
        display_name="Qwen 2.5 Coder 32B",
        family="qwen2.5-coder",
        params_b=32.0,
        min_effective_gb_q4=20.0,
        recommended_effective_memory_gb=24.0,
        workload_tags=("coding",),
    ),
    ModelEntry(
        id="qwen2.5-coder:14b",
        display_name="Qwen 2.5 Coder 14B",
        family="qwen2.5-coder",
        params_b=14.0,
        min_effective_gb_q4=10.0,
        recommended_effective_memory_gb=12.0,
        workload_tags=("coding",),
    ),
    ModelEntry(
        id="qwen2.5-coder:7b",
        display_name="Qwen 2.5 Coder 7B",
        family="qwen2.5-coder",
        params_b=7.0,
        min_effective_gb_q4=5.0,
        recommended_effective_memory_gb=7.0,
        workload_tags=("coding",),
    ),
)


def _gpu_devices(hw: HardwareProfile) -> list:
    """Per-device GPU list when the WS-up-json HardwareProfile shape is
    in flight, empty otherwise. Stays main-compatible — older
    HardwareProfile dataclasses don't carry the field at all."""
    return list(getattr(hw, "gpu_devices", None) or [])


def _effective_budget_gb(hw: HardwareProfile) -> tuple[float, str, str]:
    """Return (gb, memory_source, accelerator_kind) for the picker.

    `memory_source` is one of "vram", "unified", "system", "cpu" and
    matches the desktop's RecommendedBudget shape.
    """

    # Prefer per-device data when the daemon emitted it (newer
    # HardwareProfile shape — see `feat/up-json-and-gpu-devices`).
    devices = _gpu_devices(hw)
    if devices:
        # Pick the device with the most memory.
        best = max(devices, key=lambda d: getattr(d, "memory_total_bytes", 0) or 0)
        bytes_total = getattr(best, "memory_total_bytes", 0) or 0
        gb = bytes_total / (1024**3)
        kind = getattr(best, "memory_kind", "vram")
        vendor = getattr(best, "vendor", None) or hw.gpu
        if kind == "vram":
            return (gb, "vram", vendor)
        if kind == "unified":
            return (max(0.0, gb - 4.0), "unified", vendor)
    # Fallback to the older HardwareProfile shape (main as of 0.8.8).
    if hw.gpu in ("nvidia", "amd") and hw.gpu_vram_gb:
        return (float(hw.gpu_vram_gb), "vram", hw.gpu)
    if hw.gpu == "apple_silicon" and hw.ram_gb:
        return (max(0.0, float(hw.ram_gb) - 4.0), "unified", "apple_silicon")
    if hw.ram_gb:
        return (max(0.0, float(hw.ram_gb) - 4.0), "system", hw.gpu or "cpu_only")
    return (0.0, "system", "cpu_only")


def _accelerator_label(hw: HardwareProfile) -> str:
    devices = _gpu_devices(hw)
    if devices:
        best = max(devices, key=lambda d: getattr(d, "memory_total_bytes", 0) or 0)
        return getattr(best, "name", None) or "GPU"
    if hw.gpu == "nvidia":
        return "NVIDIA GPU"
    if hw.gpu == "amd":
        return "AMD GPU"
    if hw.gpu == "apple_silicon":
        return "Apple Silicon"
    if hw.gpu == "integrated":
        return "Integrated graphics"
    return "CPU"


def _entry_to_dict(entry: ModelEntry, *, already_installed: bool = False) -> dict[str, Any]:
    return {
        "id": entry.id,
        "model_id": entry.id,
        "display_name": entry.display_name,
        "family": entry.family,
        "params_b": entry.params_b,
        "min_effective_gb_q4": entry.min_effective_gb_q4,
        "min_effective_memory_gb": entry.min_effective_gb_q4,
        "recommended_effective_memory_gb": entry.recommended_effective_memory_gb,
        "workload_tags": list(entry.workload_tags),
        "runtime": "ollama",
        "runtime_label": "Ollama (local)",
        "already_installed": already_installed,
    }


def recommend(
    hw: HardwareProfile,
    *,
    work_styles: tuple[str, ...] = ("mixed",),
    installed_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build the JSON payload `vaner setup models-recommended` emits.

    Mirrors the desktop's :type:`ModelsRecommendedPayload` so the
    onboarding wizard's recommended-preset card lights up without any
    field-mapping on the call site.
    """

    budget_gb, memory_source, accelerator = _effective_budget_gb(hw)
    headroom = budget_gb * 0.85
    primary_style = next((s for s in work_styles if s and s != "mixed"), work_styles[0] if work_styles else "mixed")

    def fits(entry: ModelEntry) -> bool:
        return entry.min_effective_gb_q4 <= headroom

    def matches_style(entry: ModelEntry) -> bool:
        return primary_style in entry.workload_tags or "general" in entry.workload_tags

    candidates = [m for m in MODELS if fits(m)]
    if not candidates:
        # Nothing fits — pick the smallest entry as a last resort.
        candidates = [min(MODELS, key=lambda m: m.min_effective_gb_q4)]

    # Prefer style match; among those, the largest fits the budget best.
    style_match = [m for m in candidates if matches_style(m)]
    pool = style_match if style_match else candidates
    selected = max(pool, key=lambda m: m.params_b)

    accelerator_kind = {
        "nvidia": "nvidia",
        "amd": "amd",
        "apple_silicon": "apple_silicon",
        "integrated": "integrated",
    }.get(accelerator, "cpu_only")

    selected_dict = _entry_to_dict(selected, already_installed=selected.id in installed_models)
    alternatives = [_entry_to_dict(m, already_installed=m.id in installed_models) for m in MODELS if m.id != selected.id]

    return {
        "registry": {
            "schema_version": 1,
            "generator": "vaner-cli-builtin-models-registry",
            "model_count": len(MODELS),
            "sources": [{"name": "vaner-builtin", "snapshot_at": None, "note": "Curated Vaner-default registry."}],
            "valid": True,
        },
        "budget": {
            "effective_gb_q4": round(budget_gb, 1),
            "accelerator": accelerator_kind,
            "accelerator_label": _accelerator_label(hw),
            "memory_source": memory_source,
            "can_offload_to_cpu": accelerator_kind != "cpu_only",
        },
        "hardware": {
            "accelerator": accelerator_kind,
            "accelerator_type": memory_source,
            "effective_memory_gb": round(budget_gb, 1),
            "memory_source": memory_source,
            "system_memory_gb": float(hw.ram_gb or 0),
            "is_unified_memory": memory_source == "unified",
            "tier": hw.tier or "unknown",
        },
        "selected": selected_dict,
        "alternatives": alternatives,
        "user": {
            "detected_accelerator": _accelerator_label(hw),
            "selected_runtime": {"id": "ollama", "label": "Ollama (local)"},
            "selected_model": selected_dict,
            "needs_runtime_install": False,
            "needs_model_download": selected.id not in installed_models,
            "next_actions": [
                f"ollama pull {selected.id}",
            ]
            if selected.id not in installed_models
            else [],
            "explanation": (
                f"{selected.display_name} fits in your {round(budget_gb, 1)} GB "
                f"{memory_source} budget with comfortable headroom for context and KV cache."
            ),
        },
    }
