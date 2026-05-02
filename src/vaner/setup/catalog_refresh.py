# SPDX-License-Identifier: Apache-2.0
"""Catalog refresher for ``model_registry.json``.

The seed at ``vaner/defaults/catalog_seed.json`` carries family-level
metadata (workload tags, sampling/runtime parameter defaults from the
model authors, quality/stability/recency ranks). The refresher emits
**one registry row per family** using the family's ``:latest`` Ollama
tag. ``:latest`` is what whoever published the family declared the
canonical pull, so it's the safest default; users who want a different
size can edit ``backend.model`` afterwards.

For each family the refresher:

1. ``HEAD https://registry.ollama.ai/v2/library/<family>/manifests/latest``.
   404 → skip the family (not pullable). Anything else → continue.
2. ``GET`` the manifest, sum the size of layers whose ``mediaType``
   starts with ``application/vnd.ollama.image.model`` to get the
   on-disk weight size in bytes.
3. Derive ``params_b`` by dividing weight size by the seed's default
   quantization profile's ``bytes_per_param``. Memory budgets follow.

Fallbacks: ``--offline`` / unreachable network produces a seed-only
registry where each family becomes a single ``<family>:latest`` row
with the seed's declared default sizing (params_b unknown, budgets
seed-derived).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from typing import Any

logger = logging.getLogger(__name__)

OLLAMA_REGISTRY_BASE = "https://registry.ollama.ai/v2/library/{name}/manifests/{tag}"
HF_API_BASE = "https://huggingface.co/api/models/{repo}"
DEFAULT_HTTP_TIMEOUT = 8.0
OLLAMA_MODEL_LAYER_PREFIX = "application/vnd.ollama.image.model"


@dataclass(frozen=True, slots=True)
class FamilySeed:
    id: str
    display_name: str
    runtime: str
    ollama_family: str
    hf_repo_template: str
    workload_tags: tuple[str, ...]
    quality_rank: int
    stability_rank: int
    recency_rank: int
    parameters: dict[str, Any] = field(default_factory=dict)


def load_catalog_seed() -> dict[str, Any]:
    """Read the bundled ``catalog_seed.json``."""

    text = resources.files("vaner.defaults").joinpath("catalog_seed.json").read_text(encoding="utf-8")
    return json.loads(text)


def families_from_seed(seed: dict[str, Any]) -> list[FamilySeed]:
    families: list[FamilySeed] = []
    for entry in seed.get("families", []):
        families.append(
            FamilySeed(
                id=str(entry["id"]),
                display_name=str(entry.get("display_name", entry["id"])),
                runtime=str(entry.get("runtime", "ollama")),
                ollama_family=str(entry.get("ollama_family", entry["id"])),
                hf_repo_template=str(entry.get("hf_repo_template", "")),
                workload_tags=tuple(str(t) for t in entry.get("workload_tags", [])),
                quality_rank=int(entry.get("quality_rank", 0)),
                stability_rank=int(entry.get("stability_rank", 0)),
                recency_rank=int(entry.get("recency_rank", 0)),
                parameters=dict(entry.get("parameters", {})),
            )
        )
    return families


def quantization_bytes_per_param(seed: dict[str, Any], quant: str) -> float:
    profiles = seed.get("quantization_profiles", {})
    profile = profiles.get(quant) or profiles.get("Q4_K_M")
    if not profile:
        return 0.55
    return float(profile.get("bytes_per_param", 0.55))


def estimate_memory_budget(params_b: float, bytes_per_param: float, context_window: int) -> tuple[float, float]:
    """Return (min_effective_gb, recommended_effective_gb)."""

    weights_gb = params_b * bytes_per_param
    if weights_gb <= 0:
        return 0.0, 0.0
    context_overhead = max(0.5, weights_gb * (context_window / 32768) * 0.18)
    min_gb = round(weights_gb * 1.12 + 0.5, 1)
    rec_gb = round(weights_gb + context_overhead + 1.5, 1)
    return min_gb, max(rec_gb, min_gb)


def fetch_ollama_manifest(family: str, *, tag: str = "latest", timeout: float = DEFAULT_HTTP_TIMEOUT) -> dict[str, Any] | None:
    """GET the OCI manifest for ``<family>:<tag>`` from Ollama's registry.

    Returns the parsed JSON manifest or ``None`` on any failure (404,
    network, JSON parse).
    """

    import urllib.error
    import urllib.request

    url = OLLAMA_REGISTRY_BASE.format(name=family, tag=tag)
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.docker.distribution.manifest.v2+json",
                "User-Agent": "vaner-catalog-refresh/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.debug("ollama manifest %s:%s -> HTTP %s", family, tag, exc.code)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("ollama manifest %s:%s failed: %s", family, tag, exc)
        return None


def manifest_weights_bytes(manifest: dict[str, Any]) -> int:
    """Sum the size of every layer whose mediaType is the model weights.

    Ollama places each model file as a layer with mediaType
    ``application/vnd.ollama.image.model`` (or a subtype thereof). We
    sum across all such layers so MoE families that ship sharded model
    layers get the full weight size, not just the first shard.
    """

    total = 0
    for layer in manifest.get("layers", []) or []:
        media = str(layer.get("mediaType", ""))
        if media.startswith(OLLAMA_MODEL_LAYER_PREFIX):
            size = layer.get("size")
            if isinstance(size, (int, float)) and size > 0:
                total += int(size)
    return total


def fetch_hf_params_b(repo: str, *, timeout: float = DEFAULT_HTTP_TIMEOUT) -> float | None:
    """Best-effort lookup of HF parameter count, in billions."""

    if not repo:
        return None
    import urllib.error
    import urllib.request

    url = HF_API_BASE.format(repo=repo)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("HF lookup failed for %s: %s", repo, exc)
        return None

    safetensors = data.get("safetensors")
    if isinstance(safetensors, dict):
        total = safetensors.get("total")
        if isinstance(total, (int, float)) and total > 0:
            return float(total) / 1_000_000_000
    return None


def build_registry_entry_for_family(
    seed: dict[str, Any],
    family: FamilySeed,
    *,
    quant: str,
    online: bool,
    manifest_fetcher=fetch_ollama_manifest,
) -> dict[str, Any] | None:
    """Translate one family into a single ``<family>:latest`` registry row.

    Returns ``None`` when the family is unreachable on Ollama (online
    mode) — caller skips it.
    """

    bytes_per_param = quantization_bytes_per_param(seed, quant)
    params_b: float = 0.0
    download_gb: float = 0.0

    if online:
        manifest = manifest_fetcher(family.ollama_family)
        if manifest is None:
            return None
        weights_bytes = manifest_weights_bytes(manifest)
        if weights_bytes <= 0:
            logger.debug("ollama manifest for %s has no model layer", family.id)
            return None
        download_gb = round(weights_bytes / (1024**3), 1)
        # Derive params from on-disk size + quant profile. This is more
        # honest than guessing; the user pulls exactly these bytes.
        params_b = round(weights_bytes / (1024**3) / bytes_per_param, 1)
    else:
        # Offline path: emit a placeholder so the registry has a row per
        # family. Memory budgets stay 0 — callers should refresh online.
        params_b = 0.0
        download_gb = 0.0

    context_window = int(family.parameters.get("context_window", 8192))
    min_gb, rec_gb = estimate_memory_budget(params_b, bytes_per_param, context_window)

    parameters = dict(family.parameters)
    parameters.setdefault("num_ctx", context_window)

    full_tag = f"{family.ollama_family}:latest" if family.runtime == "ollama" else family.id

    return {
        "id": full_tag,
        "display_name": family.display_name,
        "runtime": family.runtime,
        "workload_tags": list(family.workload_tags),
        "quality_rank": family.quality_rank,
        "stability_rank": family.stability_rank,
        "recency_rank": family.recency_rank,
        "download_size_gb": download_gb,
        "min_effective_memory_gb": min_gb,
        "recommended_effective_memory_gb": rec_gb,
        "parameters": parameters,
        "family_id": family.id,
        "params_b": params_b,
        "quantization": quant,
    }


def build_registry(
    *,
    online: bool = True,
    seed: dict[str, Any] | None = None,
    manifest_fetcher=fetch_ollama_manifest,
) -> dict[str, Any]:
    """Produce a full ``model_registry.json`` payload.

    `manifest_fetcher` is parameterized so tests can swap in a mock.
    """

    seed = seed or load_catalog_seed()
    default_quant = str(seed.get("default_quantization", "Q4_K_M"))
    families = families_from_seed(seed)
    models: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for family in families:
        try:
            entry = build_registry_entry_for_family(
                seed,
                family,
                quant=default_quant,
                online=online,
                manifest_fetcher=manifest_fetcher,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("catalog: failed for %s (%s)", family.id, exc)
            skipped.append({"family": family.id, "reason": f"build_error: {exc}"})
            continue
        if entry is None:
            skipped.append({"family": family.id, "reason": "ollama_latest_not_found"})
            continue
        models.append(entry)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "verified_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "generator": "vaner.setup.catalog_refresh",
        "online": bool(online),
        "sources": [
            "https://registry.ollama.ai",
            "https://huggingface.co/api/models",
        ]
        if online
        else ["catalog_seed.json"],
        "models": models,
    }
    if skipped:
        payload["skipped"] = skipped
    return payload
