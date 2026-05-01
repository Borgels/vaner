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
        runtime_available = model.runtime in available_runtimes
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
        if model.runtime != "ollama" and not runtime_available:
            rejected.append({"model_id": model.id, "runtime": model.runtime, "reason": "runtime_unavailable"})
            continue
        score = _score_model(model, workload_tags, installed_match, runtime_available, fit)
        candidates.append(
            (
                score,
                model,
                {
                    "fit": fit,
                    "already_installed": installed_match,
                    "runtime_available": runtime_available,
                },
            )
        )

    if not candidates:
        fallback = min(reg.models, key=lambda m: m.min_effective_memory_gb)
        candidates.append(
            (
                0,
                fallback,
                {
                    "fit": "fallback_cpu",
                    "already_installed": (fallback.runtime, fallback.id) in installed,
                    "runtime_available": fallback.runtime in available_runtimes,
                },
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    score, selected, selected_diag = candidates[0]
    runtime_available = selected.runtime in available_runtimes
    already_installed = (selected.runtime, selected.id) in installed
    needs_runtime_install = selected.runtime == "ollama" and not runtime_available
    needs_model_download = not already_installed
    user_explanation = _plain_explanation(hw, selected, effective_memory_gb, memory_source, already_installed)
    install_plan = _install_plan(selected, needs_runtime_install, needs_model_download)
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


def _effective_memory_gb(hw: HardwareProfile) -> tuple[float, Literal["vram", "unified", "system", "cpu"]]:
    if hw.memory_is_unified and hw.memory_display_gb:
        reserve = 8 if hw.memory_display_gb >= 24 else 4
        return max(2.0, float(hw.memory_display_gb - reserve)), "unified"
    gpu_memories = [d.memory_display_gb for d in hw.gpu_devices if d.memory_kind == "vram" and d.memory_display_gb]
    if gpu_memories:
        # Keep headroom for the desktop, runtime, and context buffers.
        return max(2.0, float(max(gpu_memories) - 2)), "vram"
    if hw.gpu_vram_gb:
        return max(2.0, float(hw.gpu_vram_gb - 2)), "vram"
    if hw.gpu in {"nvidia", "amd"}:
        # A discrete GPU without readable VRAM is not enough evidence for
        # a large-model recommendation. Stay conservative until diagnostics
        # can read the actual accelerator memory.
        return min(8.0, max(2.0, float((hw.memory_display_gb or hw.ram_gb) - 8))), "system"
    if hw.memory_display_gb:
        reserve = 6 if hw.memory_display_gb >= 16 else 3
        return max(2.0, float(hw.memory_display_gb - reserve)), "system"
    return 2.0, "cpu"


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
    if effective_memory_gb >= model.recommended_effective_memory_gb:
        return "recommended"
    if effective_memory_gb >= model.min_effective_memory_gb:
        return "fits"
    return "too_large"


def _score_model(
    model: RecommendedModel,
    workload_tags: set[str],
    installed_match: bool,
    runtime_available: bool,
    fit: str,
) -> float:
    tag_overlap = len(workload_tags.intersection(model.workload_tags))
    score = model.stability_rank * 3.0
    score += model.quality_rank * 2.2
    score += tag_overlap * 35.0
    score += max(0.0, 30.0 - model.download_size_gb) * 1.5
    score += model.recency_rank * 0.35
    if fit == "recommended":
        score += 75
    elif fit == "fits":
        score += 25
    if installed_match:
        score += 120
    elif runtime_available:
        score += 30
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
_LONG_CONTEXT_WORK_STYLES = frozenset(
    {"coding", "research", "planning", "mixed"}
)


def compute_effective_context_window(
    *,
    max_context_window: int,
    weights_gb: float,
    effective_memory_gb: float,
    work_styles: tuple[str, ...] = (),
    runtime: str = "ollama",
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
      - ``runtime``: today only ``ollama`` is wired; left as an input so
        future runtimes (vLLM, llama.cpp w/ flash-attn) can override.

    Returns the chosen window, clamped to [floor, max].

    Heuristic — KV-cache scales roughly linearly with both context
    length and weight size; for Q4 + GQA models a 32K context costs
    around 18% of weight memory. We turn that around: pick the largest
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
    headroom_gb = max(0.0, effective_memory_gb - weights_gb - 1.5)
    # Cost of KV at the family's 32K reference. 0.18 is the rough Q4+GQA
    # constant; flash-attn / paged-attention runtimes get more headroom
    # implicitly because they pack the cache more tightly.
    cost_per_32k = max(0.5, weights_gb * 0.18)
    if cost_per_32k <= 0:
        return min(cap, max(floor, target))

    # How many 32K worth of KV we can afford.
    multiples = int(headroom_gb // cost_per_32k)
    if multiples <= 0:
        # Tight on memory — drop to the floor; runtime quantizes KV if needed.
        return min(cap, floor)
    affordable = floor * max(1, multiples)
    chosen = min(cap, max(target, affordable))
    # Round down to the nearest multiple of 8K so num_ctx is friendly.
    chosen = (chosen // 8192) * 8192
    return min(cap, max(floor, chosen))


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
        "already_installed": bool(diag.get("already_installed")),
        "fit": diag.get("fit", "unknown"),
        "params": combined,
        "runtime_params": runtime_params,
        "model_params": combined,
        "sampling_params": sampling,
        "capability": capability,
    }


def _install_plan(model: RecommendedModel, needs_runtime_install: bool, needs_model_download: bool) -> list[dict[str, Any]]:
    steps = [{"id": "save_config", "label": "Save Vaner settings", "required": True}]
    if needs_runtime_install:
        steps.append({"id": "install_runtime", "label": "Install Ollama", "required": True})
    else:
        steps.append({"id": "check_runtime", "label": "Check local model runner", "required": True})
    if needs_model_download:
        steps.append(
            {
                "id": "download_model",
                "label": f"Download {model.display_name}",
                "required": True,
                "command": ["ollama", "pull", model.id] if model.runtime == "ollama" else [],
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
        return f"Vaner found unified memory and chose {model.display_name} with safe headroom for macOS and the model runner.{installed}"
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
        "system_memory_gb": hw.memory_display_gb or hw.ram_gb,
        "is_unified_memory": hw.memory_is_unified,
        "tier": hw.tier,
    }


__all__ = [
    "ModelRegistry",
    "RecommendedModel",
    "load_model_registry",
    "recommend_local_model",
    "validate_model_registry",
]
