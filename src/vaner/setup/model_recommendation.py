# SPDX-License-Identifier: Apache-2.0
"""Hardware-aware local model recommendation for desktop first-run."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

from vaner.setup.answers import SetupAnswers
from vaner.setup.hardware import HardwareProfile, Runtime, detect

REGISTRY_SCHEMA_VERSION = 1
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_NATIVE_ENDPOINT = "http://127.0.0.1:11434"


@dataclass(frozen=True, slots=True)
class RecommendedModel:
    id: str
    display_name: str
    runtime: Runtime
    workload_tags: tuple[str, ...]
    quality_rank: int
    stability_rank: int
    recency_rank: int
    download_size_gb: float
    min_effective_memory_gb: float
    recommended_effective_memory_gb: float
    parameters: dict[str, Any]
    family_id: str = ""
    params_b: float = 0.0
    active_params_b: float = 0.0
    architecture: str = "dense"
    quantization: str = ""
    accelerator_tags: tuple[str, ...] = ()

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> RecommendedModel:
        return cls(
            id=str(raw["id"]),
            display_name=str(raw.get("display_name") or raw["id"]),
            runtime=str(raw.get("runtime", "ollama")),  # type: ignore[arg-type]
            workload_tags=tuple(str(v) for v in raw.get("workload_tags", [])),
            quality_rank=int(raw.get("quality_rank", 0)),
            stability_rank=int(raw.get("stability_rank", 0)),
            recency_rank=int(raw.get("recency_rank", 0)),
            download_size_gb=float(raw.get("download_size_gb", 0)),
            min_effective_memory_gb=float(raw.get("min_effective_memory_gb", 0)),
            recommended_effective_memory_gb=float(raw.get("recommended_effective_memory_gb", raw.get("min_effective_memory_gb", 0))),
            parameters=dict(raw.get("parameters", {})),
            family_id=str(raw.get("family_id", raw.get("id", ""))),
            params_b=float(raw.get("params_b", 0) or 0),
            active_params_b=float(raw.get("active_params_b", 0) or 0),
            architecture=str(raw.get("architecture", "dense") or "dense"),
            quantization=str(raw.get("quantization", "") or ""),
            accelerator_tags=tuple(str(v) for v in raw.get("accelerator_tags", []) if isinstance(v, str)),
        )


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    schema_version: int
    verified_at: str
    sources: tuple[str, ...]
    models: tuple[RecommendedModel, ...]
    valid: bool = True
    warning: str | None = None


def load_model_registry() -> ModelRegistry:
    try:
        text = resources.files("vaner.defaults").joinpath("model_registry.json").read_text(encoding="utf-8")
        raw = json.loads(text)
        registry = validate_model_registry(raw)
        return registry
    except Exception as exc:
        return _fallback_registry(f"model registry unavailable: {exc}")


def validate_model_registry(raw: object) -> ModelRegistry:
    if not isinstance(raw, dict):
        raise ValueError("registry must be a JSON object")
    schema_version = int(raw.get("schema_version", 0))
    if schema_version != REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"unsupported model registry schema_version={schema_version}")
    models_raw = raw.get("models")
    if not isinstance(models_raw, list) or not models_raw:
        raise ValueError("registry must contain at least one model")
    models = tuple(RecommendedModel.from_raw(m) for m in models_raw if isinstance(m, dict))
    if not models:
        raise ValueError("registry did not contain any valid model entries")
    return ModelRegistry(
        schema_version=schema_version,
        verified_at=str(raw.get("verified_at", "unknown")),
        sources=tuple(str(src) for src in raw.get("sources", []) if isinstance(src, str)),
        models=models,
    )


def _fallback_registry(warning: str) -> ModelRegistry:
    return ModelRegistry(
        schema_version=REGISTRY_SCHEMA_VERSION,
        verified_at="fallback",
        sources=(),
        valid=False,
        warning=warning,
        models=(
            RecommendedModel(
                id="qwen3:4b",
                display_name="Qwen 3 4B",
                runtime="ollama",
                workload_tags=("general", "lightweight"),
                quality_rank=60,
                stability_rank=90,
                recency_rank=60,
                download_size_gb=3,
                min_effective_memory_gb=4,
                recommended_effective_memory_gb=6,
                parameters={
                    "context_window": 8192,
                    "reasoning_mode": "allowed",
                    "max_response_tokens": 2048,
                    "reasoning_token_budget": 2048,
                },
                family_id="qwen3",
                params_b=4.0,
                architecture="dense",
                quantization="Q4_K_M",
            ),
        ),
    )


def recommend_local_model(
    answers: SetupAnswers | None = None,
    hardware: HardwareProfile | None = None,
    *,
    registry: ModelRegistry | None = None,
) -> dict[str, Any]:
    """Return the desktop setup recommendation contract."""

    hw = hardware or detect()
    reg = registry or load_model_registry()
    effective_memory_gb, memory_source = _effective_memory_gb(hw)
    workload_tags = _workload_tags(answers)
    installed = {(runtime, model_id) for runtime, model_id, _size in hw.detected_models}
    available_runtimes = set(hw.detected_runtimes)

    candidates: list[tuple[float, RecommendedModel, dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for model in reg.models:
        fit = _fit_status(model, effective_memory_gb)
        installed_match = (model.runtime, model.id) in installed
        disk_status = _disk_status(model, hw)
        if disk_status["status"] == "insufficient" and not installed_match:
            rejected.append(
                {
                    "model_id": model.id,
                    "runtime": model.runtime,
                    "reason": "insufficient_disk",
                    **disk_status,
                }
            )
            continue
        runtime_available = model.runtime in available_runtimes
        runtime_installable = _runtime_installable(model.runtime, hw)
        if fit == "too_large":
            rejected.append(
                {
                    "model_id": model.id,
                    "runtime": model.runtime,
                    "reason": "needs_more_memory",
                    "min_effective_memory_gb": model.min_effective_memory_gb,
                    "effective_memory_gb": effective_memory_gb,
                }
            )
            continue
        if not runtime_available and not runtime_installable:
            rejected.append({"model_id": model.id, "runtime": model.runtime, "reason": "runtime_unavailable"})
            continue
        score = _score_model(
            model,
            workload_tags,
            installed_match,
            runtime_available,
            fit,
            effective_memory_gb=effective_memory_gb,
            answers=answers,
            hardware=hw,
            disk_status=disk_status["status"],
        )
        candidates.append(
            (
                score,
                model,
                {
                    "fit": fit,
                    "already_installed": installed_match,
                    "runtime_available": runtime_available,
                    "runtime_installable": runtime_installable,
                    "disk": disk_status,
                },
            )
        )

    if not candidates:
        fallback = min(reg.models, key=lambda m: (m.download_size_gb or 0.0, m.min_effective_memory_gb))
        candidates.append(
            (
                0,
                fallback,
                {
                    "fit": "fallback_cpu",
                    "already_installed": (fallback.runtime, fallback.id) in installed,
                    "runtime_available": fallback.runtime in available_runtimes,
                    "disk": _disk_status(fallback, hw),
                },
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    score, selected, selected_diag = candidates[0]
    runtime_available = selected.runtime in available_runtimes
    already_installed = (selected.runtime, selected.id) in installed
    needs_runtime_install = not runtime_available
    needs_model_download = not already_installed
    user_explanation = _plain_explanation(hw, selected, effective_memory_gb, memory_source, already_installed)
    install_plan = _install_plan(selected, needs_runtime_install, needs_model_download, disk_status=selected_diag.get("disk"))
    runtime = _runtime_payload(selected.runtime)
    work_styles_tuple: tuple[str, ...] = tuple(answers.work_styles) if answers else ()
    selected_payload = _selected_payload(
        selected,
        runtime,
        selected_diag,
        effective_memory_gb=effective_memory_gb,
        work_styles=work_styles_tuple,
    )

    return {
        "schema_version": 1,
        "generator": "vaner-core",
        "registry": {
            "schema_version": reg.schema_version,
            "verified_at": reg.verified_at,
            "sources": list(reg.sources),
            "valid": reg.valid,
            "warning": reg.warning,
        },
        "user": {
            "detected_accelerator": _accelerator_summary(hw),
            "selected_runtime": runtime,
            "selected_model": selected_payload,
            "needs_runtime_install": needs_runtime_install,
            "needs_model_download": needs_model_download,
            "next_actions": [step["label"] for step in install_plan],
            "explanation": user_explanation,
        },
        "hardware": _hardware_summary(hw, effective_memory_gb, memory_source),
        "budget": {
            "accelerator": hw.gpu,
            "accelerator_label": _accelerator_label(hw),
            "effective_gb_q4": effective_memory_gb,
            "memory_source": memory_source,
            "disk_free_gb": hw.disk_free_gb,
            "gpu_count": _gpu_count(hw),
            "gpu_total_memory_gb": _gpu_total_memory_gb(hw),
            "notes": [],
        },
        "selected": selected_payload,
        "alternatives": [
            _selected_payload(
                model,
                _runtime_payload(model.runtime),
                diag,
                effective_memory_gb=effective_memory_gb,
                work_styles=work_styles_tuple,
            )
            for _score, model, diag in candidates[1:4]
        ],
        "install_plan": install_plan,
        "diagnostics": {
            "score": score,
            "candidate_models": [
                {
                    "model_id": model.id,
                    "runtime": model.runtime,
                    "score": candidate_score,
                    **diag,
                }
                for candidate_score, model, diag in candidates
            ],
            "rejected_models": rejected,
            "raw_hardware": {
                "memory_total_bytes": hw.memory_total_bytes,
                "memory_is_unified": hw.memory_is_unified,
                "disk_free_gb": hw.disk_free_gb,
                "gpu_devices": [
                    {
                        "name": d.name,
                        "vendor": d.vendor,
                        "kind": d.kind,
                        "memory_total_bytes": d.memory_total_bytes,
                        "memory_kind": d.memory_kind,
                    }
                    for d in hw.gpu_devices
                ],
            },
        },
    }


def _effective_memory_gb(hw: HardwareProfile) -> tuple[float, Literal["vram", "unified", "inferred_gpu", "system", "cpu"]]:
    if _looks_like_nvidia_unified_memory(hw) and hw.memory_display_gb:
        reserve = 8 if hw.memory_display_gb >= 24 else 4
        return max(2.0, float(hw.memory_display_gb - reserve)), "unified"
    if hw.memory_is_unified and hw.gpu == "apple_silicon" and hw.memory_display_gb:
        reserve = 8 if hw.memory_display_gb >= 24 else 4
        return max(2.0, float(hw.memory_display_gb - reserve)), "unified"
    gpu_memories = [d.memory_display_gb for d in hw.gpu_devices if d.memory_kind == "vram" and d.memory_display_gb]
    if gpu_memories:
        # Keep headroom for the desktop, runtime, and context buffers.
        return max(2.0, float(max(gpu_memories) - 2)), "vram"
    if hw.gpu_vram_gb:
        return max(2.0, float(hw.gpu_vram_gb - 2)), "vram"
    if hw.gpu == "nvidia" and hw.memory_display_gb >= 96:
        # NVIDIA + large host memory + missing VRAM telemetry is common on
        # new developer-class systems where NVML/nvidia-smi reporting may be
        # incomplete or unified-memory platforms are not named clearly. Do
        # not treat host RAM as fully usable GPU memory, but do avoid a tiny
        # CPU-class default.
        return 30.0, "inferred_gpu"
    if hw.gpu in {"nvidia", "amd"}:
        # A discrete GPU without readable VRAM is not enough evidence for a
        # large local-model recommendation. System RAM is useful for the OS
        # and caches, not for fast Vaner inference.
        return 2.0, "system"
    if hw.memory_display_gb:
        return 2.0, "system"
    return 2.0, "cpu"


def _looks_like_nvidia_unified_memory(hw: HardwareProfile) -> bool:
    if hw.gpu != "nvidia":
        return False
    if hw.memory_is_unified:
        return True
    names = " ".join(device.name.lower() for device in hw.gpu_devices)
    return any(marker in names for marker in ("dgx spark", "gb10", "grace blackwell"))


def _gpu_count(hw: HardwareProfile) -> int:
    return len([d for d in hw.gpu_devices if d.kind not in {"cpu", "integrated"}]) or (1 if hw.gpu in {"nvidia", "amd"} else 0)


def _gpu_total_memory_gb(hw: HardwareProfile) -> int:
    values = [int(d.memory_display_gb or 0) for d in hw.gpu_devices if d.memory_display_gb and d.memory_kind in {"vram", "unified"}]
    if values:
        return sum(values)
    return int(hw.gpu_vram_gb or 0)


def _workload_tags(answers: SetupAnswers | None) -> set[str]:
    tags = {"general", "summarization"}
    if answers is None:
        return tags | {"coding"}
    for style in answers.work_styles:
        if style in {"coding", "planning", "mixed"}:
            tags.add("coding")
        if style in {"writing", "research", "support", "learning", "mixed"}:
            tags.add("summarization")
    return tags


def _fit_status(model: RecommendedModel, effective_memory_gb: float) -> Literal["recommended", "fits", "too_large"]:
    """Bucket how well a model fits the user's accelerator budget.

    The registry's ``recommended_effective_memory_gb`` is derived from
    the family's *max* context window — qwen3.6 with its 262K ceiling
    needs much more memory to be "recommended" by that yardstick. That's
    accurate but punitive: a 32 GB card can comfortably run qwen3.6 27B
    with a hardware-selected long context, which is what
    :func:`compute_effective_context_window` picks at runtime. So we
    relax the "recommended" tier to weights plus a practical KV budget so
    the picker doesn't always default to a much smaller model on
    hardware that can clearly run the larger one. The strict
    "recommended" tier remains the upper bound.
    """
    # The registry budgets are calculated against the model's architectural
    # max context. Setup picks a runtime-effective context later, so fit
    # should test the practical floor-context load too: weights + runtime
    # reserve + one 32K KV slice. This is especially important for current
    # MoE models where active params make context cheaper than total params
    # imply, while weights still need to fit.
    kv_reference_gb = _kv_reference_gb(model) or model.download_size_gb
    practical_min = (model.download_size_gb or 0.0) + 3.0 + max(0.5, kv_reference_gb * 0.16)
    relaxed_recommended = max(practical_min + 4.0, model.min_effective_memory_gb)
    if effective_memory_gb >= min(model.recommended_effective_memory_gb, relaxed_recommended):
        return "recommended"
    if effective_memory_gb >= min(model.min_effective_memory_gb, practical_min):
        return "fits"
    return "too_large"


def _disk_status(model: RecommendedModel, hw: HardwareProfile) -> dict[str, Any]:
    free_gb = int(getattr(hw, "disk_free_gb", 0) or 0)
    download_gb = max(0.0, float(model.download_size_gb or 0.0))
    # Keep room for the compressed download, expanded cache/metadata, and a
    # little operational headroom. Installed models still report a need here;
    # `already_installed` gets scored separately and the install plan can skip
    # the download step.
    required_gb = round(download_gb * 1.15 + 8.0, 1) if download_gb > 0 else 0.0
    if free_gb <= 0 or required_gb <= 0:
        return {"status": "unknown", "free_gb": free_gb, "required_gb": required_gb}
    if free_gb < required_gb:
        return {"status": "insufficient", "free_gb": free_gb, "required_gb": required_gb}
    if free_gb < required_gb + 25.0:
        return {"status": "tight", "free_gb": free_gb, "required_gb": required_gb}
    return {"status": "enough", "free_gb": free_gb, "required_gb": required_gb}


def _score_model(
    model: RecommendedModel,
    workload_tags: set[str],
    installed_match: bool,
    runtime_available: bool,
    fit: str,
    *,
    effective_memory_gb: float,
    answers: SetupAnswers | None,
    hardware: HardwareProfile,
    disk_status: str = "unknown",
) -> float:
    """Score a candidate against the user's hardware + workload tags.

    Weight choices encode "Vaner picks the best model that fits"
    rather than "the safest small model". Quality and recency carry
    more weight than stability or download size — a 22 GB download
    is a one-time cost on a user's machine, but a quality / recency
    delta is the experience every cycle. Stability stays in the
    sum because brand-new releases occasionally have rough edges,
    but its multiplier is reduced so the latest-of-latest entry
    isn't penalised into oblivion."""
    tag_overlap = len(workload_tags.intersection(model.workload_tags))
    score = model.stability_rank * 1.5
    score += model.quality_rank * 3.0
    score += tag_overlap * 35.0
    score += max(0.0, 30.0 - model.download_size_gb) * 0.5
    score += model.recency_rank * 1.0
    score += _context_score(model)
    score += _hardware_utilization_score(model, effective_memory_gb, answers)
    score += _runtime_affinity_score(model, hardware, runtime_available)
    score += _architecture_score(model, hardware)
    if model.recency_rank < 80:
        score -= (80 - model.recency_rank) * 8.0
    if fit == "recommended":
        score += 75
    elif fit == "fits":
        score += 25
    if installed_match:
        score += 120
    elif runtime_available:
        score += 30
    if disk_status == "tight":
        score -= 50
    if answers is not None:
        if answers.priority in {"speed", "low_resource"} or answers.compute_posture == "light":
            score -= max(0.0, model.download_size_gb - 30.0) * 2.1
        if answers.priority == "quality":
            score += model.quality_rank * 0.35
        if answers.compute_posture == "available_power":
            score += min(90.0, model.recommended_effective_memory_gb * 0.35)
        if answers.background_posture == "deep_run_aggressive":
            score += min(80.0, _max_context_window(model) / 131072.0 * 10.0)
    return score


def _runtime_installable(runtime: Runtime, hw: HardwareProfile) -> bool:
    if runtime == "ollama":
        return True
    if runtime == "mlx":
        return hw.os == "darwin" and hw.gpu == "apple_silicon"
    if runtime == "vllm":
        return hw.os == "linux" and hw.gpu == "nvidia"
    return False


def _max_context_window(model: RecommendedModel) -> int:
    try:
        return int(model.parameters.get("context_window", _CONTEXT_WINDOW_FLOOR))
    except (TypeError, ValueError):
        return _CONTEXT_WINDOW_FLOOR


def _context_score(model: RecommendedModel) -> float:
    # Reward native long-context models without letting context alone beat
    # model quality. 32K => 0, 262K => about 45, 1M => about 75.
    window = max(_CONTEXT_WINDOW_FLOOR, _max_context_window(model))
    multiples = max(1.0, window / _CONTEXT_WINDOW_FLOOR)
    import math

    return min(90.0, math.log2(multiples) * 15.0)


def _hardware_utilization_score(model: RecommendedModel, effective_memory_gb: float, answers: SetupAnswers | None) -> float:
    if effective_memory_gb <= 0 or model.recommended_effective_memory_gb <= 0:
        return 0.0
    usage = min(1.0, model.recommended_effective_memory_gb / effective_memory_gb)
    # Balanced users on very large boxes should not get a tiny-model default.
    # Speed / low-resource explicitly opts back toward smaller models.
    if answers and (answers.priority in {"speed", "low_resource"} or answers.compute_posture == "light"):
        return -40.0 * usage
    return min(160.0, 180.0 * (usage**0.5))


def _runtime_affinity_score(model: RecommendedModel, hw: HardwareProfile, runtime_available: bool) -> float:
    score = 0.0
    tags = set(model.accelerator_tags)
    if hw.gpu == "apple_silicon":
        if model.runtime == "mlx":
            score += 125.0
        elif model.runtime == "ollama":
            score += 20.0
        if "apple_silicon" in tags or "unified_memory" in tags:
            score += 35.0
        if model.quantization.upper() in {"MXFP8", "MLX"} or "mlx" in tags:
            score += 55.0
        if "blackwell" in tags or model.quantization.upper() == "NVFP4":
            score -= 70.0
    elif hw.gpu == "nvidia":
        if model.runtime == "vllm":
            score += 90.0
        elif model.runtime == "ollama":
            score += 35.0
        if "cuda" in tags or "nvidia" in tags:
            score += 30.0
        if _looks_like_nvidia_unified_memory(hw):
            if "dgx_spark" in tags or "unified_memory" in tags:
                score += 90.0
        elif "dgx_spark" in tags:
            score -= 55.0
        if "blackwell" in tags and _has_blackwell_gpu(hw):
            score += 65.0
        if model.quantization.upper() in {"MXFP8", "MLX"}:
            score -= 35.0
    elif model.runtime == "ollama":
        score += 25.0
    if not runtime_available and model.runtime != "ollama":
        score -= 25.0
    return score


def _has_blackwell_gpu(hw: HardwareProfile) -> bool:
    names = " ".join(device.name.lower() for device in hw.gpu_devices)
    return any(marker in names for marker in ("rtx 50", "5090", "5080", "5070", "5060", "blackwell", "pro 6000", "dgx spark", "gb10"))


def _architecture_score(model: RecommendedModel, hw: HardwareProfile) -> float:
    if model.architecture.lower() != "moe":
        return 0.0
    active = model.active_params_b or model.params_b
    total = model.params_b or active
    if active <= 0 or total <= 0:
        return 25.0
    sparse_ratio = max(0.0, min(1.0, 1.0 - (active / total)))
    score = 35.0 + sparse_ratio * 45.0
    if (hw.memory_is_unified or _looks_like_nvidia_unified_memory(hw)) and hw.memory_display_gb >= 128:
        score += 30.0
    return score


def _runtime_payload(runtime: Runtime) -> dict[str, Any]:
    if runtime == "ollama":
        return {
            "id": "ollama",
            "label": "Ollama",
            "base_url": OLLAMA_BASE_URL,
            "native_endpoint": OLLAMA_NATIVE_ENDPOINT,
            "install_managed": True,
        }
    if runtime == "mlx":
        return {
            "id": "mlx",
            "label": "MLX",
            "base_url": "http://127.0.0.1:8080/v1",
            "native_endpoint": "http://127.0.0.1:8080",
            "install_managed": True,
        }
    if runtime == "vllm":
        return {
            "id": "vllm",
            "label": "vLLM",
            "base_url": "http://127.0.0.1:8000/v1",
            "native_endpoint": "http://127.0.0.1:8000",
            "install_managed": True,
        }
    return {"id": runtime, "label": runtime, "base_url": "", "install_managed": False}


# Keys in a model's flat ``parameters`` block that are runtime-side (Ollama
# / vLLM options, KV-cache, batching) versus model-side sampling defaults.
# Anything not listed here stays in ``params`` as a model-level capability
# (context_window, reasoning_mode, max_response_tokens, ...).
_RUNTIME_PARAM_KEYS = frozenset(
    {
        "keep_alive",
        "num_ctx",
        "num_predict",
        "num_thread",
        "num_batch",
        "num_gpu",
        "main_gpu",
        "low_vram",
        "f16_kv",
        "use_mmap",
        "use_mlock",
        "rope_frequency_base",
        "rope_frequency_scale",
        "stop",
    }
)
_SAMPLING_PARAM_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "repeat_penalty",
        "repeat_last_n",
        "min_p",
        "tfs_z",
        "typical_p",
        "presence_penalty",
        "frequency_penalty",
        "mirostat",
        "mirostat_eta",
        "mirostat_tau",
        "seed",
    }
)


# Floor for effective context window — the cockpit and the desktop both
# rely on at least 32K tokens being available for prepared briefings to
# fit. Going below this defeats the point of running a local model with a
# native long-context architecture.
_CONTEXT_WINDOW_FLOOR = 32768

# Archetypes that benefit from longer context (whole-file, repo-scope,
# multi-doc work). Anything not in this set gets the floor as the target.
_LONG_CONTEXT_WORK_STYLES = frozenset({"coding", "research", "planning", "trading", "mixed"})


def compute_effective_context_window(
    *,
    max_context_window: int,
    weights_gb: float,
    effective_memory_gb: float,
    work_styles: tuple[str, ...] = (),
    runtime: str = "ollama",
    kv_reference_gb: float | None = None,
) -> int:
    """Pick a runtime-effective context window.

    Inputs:
      - ``max_context_window``: the model's architectural ceiling (from
        the registry's ``parameters.context_window`` / ``max_context_window``).
      - ``weights_gb``: the model's on-disk size (≈ VRAM weight load).
      - ``effective_memory_gb``: the user's available accelerator memory
        from the hardware probe.
      - ``work_styles``: the wizard's archetype answers (coding,
        research, …) — long-context archetypes get a higher target.
      - ``runtime``: runtime family identifier; local runtimes can tune
        context/KV behavior as support evolves.

    Returns the chosen window, clamped to [floor, max].

    Heuristic — KV-cache scales roughly linearly with both context
    length and weight size; for Q4 + GQA models a 32K context costs
    around 16% of weight memory. We turn that around: pick the largest
    multiple of 32K that fits in the headroom we have after weights and
    a small safety reserve.
    """

    floor = _CONTEXT_WINDOW_FLOOR
    cap = max(floor, int(max_context_window or floor))

    long_archetype = bool(set(work_styles) & _LONG_CONTEXT_WORK_STYLES)
    target = 65536 if long_archetype else floor

    if weights_gb <= 0 or effective_memory_gb <= 0:
        # Unknown sizing — honor the floor; let the runtime cap itself.
        chosen = min(cap, max(floor, target))
        return chosen

    # Headroom available for KV / activations after the weights load.
    # Effective memory already reserves room for the desktop/runtime; this
    # extra reserve keeps long-context defaults from pinning VRAM at the edge.
    runtime_reserve_gb = 3.0
    headroom_gb = max(0.0, effective_memory_gb - weights_gb - runtime_reserve_gb)
    # Cost of KV at the family's 32K reference. 0.16 is the rough Q4+GQA
    # constant; flash-attn / paged-attention runtimes get more headroom
    # implicitly because they pack the cache more tightly.
    kv_gb = kv_reference_gb if kv_reference_gb and kv_reference_gb > 0 else weights_gb
    cost_per_32k = max(0.5, kv_gb * 0.16)
    if cost_per_32k <= 0:
        return min(cap, max(floor, target))

    # How many 32K worth of KV we can afford.
    multiples = int(headroom_gb // cost_per_32k)
    if multiples <= 0:
        # Tight on memory — drop to the floor; runtime quantizes KV if needed.
        return min(cap, floor)
    affordable = floor * max(1, multiples)
    chosen = min(cap, max(target, affordable))
    chosen = min(chosen, _default_context_tier_cap(effective_memory_gb, weights_gb, runtime))
    # Round down to the nearest multiple of 8K so num_ctx is friendly.
    chosen = (chosen // 8192) * 8192
    return min(cap, max(floor, chosen))


def _default_context_tier_cap(effective_memory_gb: float, weights_gb: float, runtime: str) -> int:
    """Cap first-run context by memory tier.

    This is intentionally more conservative than "what might fit". Vaner's
    default runner is Ollama, and onboarding must avoid accidental CPU
    offload / KV pressure. Larger windows remain available through advanced
    or custom profiles; this function chooses the safe first-run default.
    """

    if effective_memory_gb <= 0:
        return _CONTEXT_WINDOW_FLOOR
    if effective_memory_gb < 12:
        return 32768
    if effective_memory_gb < 18:
        return 65536
    if effective_memory_gb < 28:
        return 131072
    if effective_memory_gb < 44:
        return 65536 if weights_gb >= 20 else 131072
    if effective_memory_gb < 96:
        return 262144
    if runtime == "ollama":
        return 262144
    return 524288


def _kv_reference_gb(model: RecommendedModel) -> float:
    if model.architecture.lower() == "moe" and model.active_params_b > 0:
        # MoE models load all weights, but the active expert set gives a
        # better default proxy for KV/context scaling than total parameters.
        active_weight_gb = model.active_params_b * 0.55
        return max(active_weight_gb, model.download_size_gb * 0.08)
    return model.download_size_gb or 0.0


def _split_parameters(parameters: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Split a registry ``parameters`` block into capability / runtime / sampling.

    The legacy registry stored everything in one flat dict; the catalog
    refresher seeds family-aware sampling defaults (top_k, top_p, …) and
    runtime-side knobs (num_ctx, keep_alive). Splitting here keeps the
    desktop's ``model_params`` vs ``runtime_params`` shape stable while
    letting registry authors keep one flat block.
    """

    capability: dict[str, Any] = {}
    runtime_params: dict[str, Any] = {}
    sampling: dict[str, Any] = {}
    for key, value in parameters.items():
        if key in _RUNTIME_PARAM_KEYS:
            runtime_params[key] = value
        elif key in _SAMPLING_PARAM_KEYS:
            sampling[key] = value
        else:
            capability[key] = value
    # Preserve the existing default — Ollama keeps the model warm for 10
    # minutes after the last request — when the registry omits it.
    runtime_params.setdefault("keep_alive", "10m")
    # Mirror the model's context window into the runtime so Ollama uses
    # the full window. Without num_ctx, Ollama defaults to 2048.
    if "num_ctx" not in runtime_params and "context_window" in capability:
        runtime_params["num_ctx"] = capability["context_window"]
    if "num_predict" not in runtime_params and "max_response_tokens" in capability:
        runtime_params["num_predict"] = capability["max_response_tokens"]
    return capability, runtime_params, sampling


def _selected_payload(
    model: RecommendedModel,
    runtime: dict[str, Any],
    diag: dict[str, Any],
    *,
    effective_memory_gb: float | None = None,
    work_styles: tuple[str, ...] = (),
) -> dict[str, Any]:
    capability, runtime_params, sampling = _split_parameters(model.parameters)
    # Pick a hw/archetype-aware effective context window. Floors at 32K,
    # caps at the model's architectural max, scales up with available
    # memory + long-context archetypes (coding, research, planning).
    max_ctx = int(capability.get("context_window", _CONTEXT_WINDOW_FLOOR))
    effective_ctx = compute_effective_context_window(
        max_context_window=max_ctx,
        weights_gb=model.download_size_gb or 0.0,
        effective_memory_gb=float(effective_memory_gb or 0.0),
        work_styles=work_styles,
        runtime=model.runtime,
        kv_reference_gb=_kv_reference_gb(model),
    )
    capability["context_window"] = effective_ctx
    capability["max_context_window"] = max_ctx
    runtime_params["num_ctx"] = effective_ctx
    # Legacy ``params`` and ``model_params`` consumers expect a single
    # combined view; ``runtime_params`` is split out for the runtime layer.
    combined = {**capability, **sampling}
    return {
        "id": model.id,
        "model_id": model.id,
        "display_name": model.display_name,
        "runtime": runtime["id"],
        "runtime_label": runtime["label"],
        "base_url": runtime.get("base_url", ""),
        "workload_tags": list(model.workload_tags),
        "download_size_gb": model.download_size_gb,
        "min_effective_memory_gb": model.min_effective_memory_gb,
        "recommended_effective_memory_gb": model.recommended_effective_memory_gb,
        "family_id": model.family_id,
        "params_b": model.params_b,
        "active_params_b": model.active_params_b,
        "architecture": model.architecture,
        "quantization": model.quantization,
        "accelerator_tags": list(model.accelerator_tags),
        "already_installed": bool(diag.get("already_installed")),
        "fit": diag.get("fit", "unknown"),
        "disk": diag.get("disk", {"status": "unknown"}),
        "params": combined,
        "runtime_params": runtime_params,
        "model_params": combined,
        "sampling_params": sampling,
        "capability": capability,
    }


def _install_plan(
    model: RecommendedModel,
    needs_runtime_install: bool,
    needs_model_download: bool,
    *,
    disk_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    steps = [{"id": "save_config", "label": "Save Vaner settings", "required": True}]
    if disk_status and disk_status.get("status") in {"tight", "insufficient"}:
        steps.append(
            {
                "id": "confirm_disk_space",
                "label": "Free disk space" if disk_status.get("status") == "insufficient" else "Confirm disk space",
                "required": True,
                "free_gb": disk_status.get("free_gb"),
                "required_gb": disk_status.get("required_gb"),
            }
        )
    if needs_runtime_install:
        if model.runtime == "mlx":
            steps.append(
                {
                    "id": "install_runtime",
                    "label": "Install MLX",
                    "required": True,
                    "command": ["python", "-m", "pip", "install", "mlx-lm"],
                }
            )
        elif model.runtime == "vllm":
            steps.append(
                {
                    "id": "install_runtime",
                    "label": "Install vLLM",
                    "required": True,
                    "command": ["python", "-m", "pip", "install", "vllm"],
                }
            )
        else:
            steps.append({"id": "install_runtime", "label": "Install Ollama", "required": True})
    else:
        steps.append({"id": "check_runtime", "label": "Check local model runner", "required": True})
    if needs_model_download:
        command: list[str] = []
        if model.runtime == "ollama":
            command = ["ollama", "pull", model.id]
        elif model.runtime == "mlx":
            command = ["mlx_lm.server", "--model", model.id, "--port", "8080"]
        steps.append(
            {
                "id": "download_model",
                "label": f"Download {model.display_name}",
                "required": True,
                "command": command,
            }
        )
    else:
        steps.append({"id": "check_model", "label": f"Use installed {model.display_name}", "required": True})
    steps.extend(
        [
            {"id": "start_engine", "label": "Start Vaner", "required": True},
            {"id": "health_check", "label": "Check Vaner is ready", "required": True},
        ]
    )
    return steps


def _accelerator_summary(hw: HardwareProfile) -> str:
    if hw.gpu_devices:
        labels = []
        for device in hw.gpu_devices:
            if device.memory_display_gb and device.memory_kind == "vram":
                labels.append(f"{device.name} ({device.memory_display_gb} GB VRAM)")
            elif device.memory_kind == "unified" and hw.memory_display_gb:
                labels.append(f"{device.name} ({hw.memory_display_gb} GB unified memory)")
            else:
                labels.append(device.name)
        return ", ".join(labels)
    if hw.memory_display_gb:
        return f"CPU ({hw.memory_display_gb} GB system memory)"
    return "CPU"


def _accelerator_label(hw: HardwareProfile) -> str:
    if hw.gpu == "apple_silicon":
        return "Apple Silicon"
    if hw.gpu == "nvidia":
        return "NVIDIA GPU"
    if hw.gpu == "amd":
        return "AMD GPU"
    if hw.gpu == "integrated":
        return "Integrated GPU"
    return "CPU"


def _plain_explanation(
    hw: HardwareProfile,
    model: RecommendedModel,
    effective_memory_gb: float,
    memory_source: str,
    already_installed: bool,
) -> str:
    installed = " It is already installed, so setup can reuse it." if already_installed else ""
    if memory_source == "vram":
        return f"Vaner found enough GPU memory for {model.display_name} with headroom for normal desktop use.{installed}"
    if memory_source == "unified":
        return f"Vaner found unified accelerator memory and chose {model.display_name} with safe headroom for the desktop and model runner.{installed}"
    if memory_source == "inferred_gpu":
        return (
            f"Vaner found an NVIDIA GPU but could not read exact GPU memory, so it chose {model.display_name} "
            f"as a cautious GPU-class default instead of a tiny CPU model.{installed}"
        )
    if hw.gpu == "none":
        return (
            f"Vaner did not find a dedicated GPU, so it chose {model.display_name} as a safer local setup. "
            f"Responses may be slower.{installed}"
        )
    return f"Vaner chose {model.display_name} because it fits this computer's available local model memory.{installed}"


def _hardware_summary(hw: HardwareProfile, effective_memory_gb: float, memory_source: str) -> dict[str, Any]:
    return {
        "accelerator": _accelerator_summary(hw),
        "accelerator_type": hw.gpu,
        "effective_memory_gb": effective_memory_gb,
        "memory_source": memory_source,
        "gpu_count": _gpu_count(hw),
        "gpu_total_memory_gb": _gpu_total_memory_gb(hw),
        "system_memory_gb": hw.memory_display_gb or hw.ram_gb,
        "is_unified_memory": hw.memory_is_unified or _looks_like_nvidia_unified_memory(hw),
        "disk_free_gb": hw.disk_free_gb,
        "tier": hw.tier,
    }


__all__ = [
    "ModelRegistry",
    "RecommendedModel",
    "load_model_registry",
    "recommend_local_model",
    "validate_model_registry",
]
