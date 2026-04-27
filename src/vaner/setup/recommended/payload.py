# SPDX-License-Identifier: Apache-2.0
"""Wire-shape serializer for the recommended-model endpoint.

One serializer shared by HTTP (``GET /models/recommended``) and MCP
(``vaner.models.recommended``). Keeping the projection in a single
place lets both surfaces stay byte-identical and simplifies desktop
client wiring.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from vaner.setup.hardware import HardwareProfile
from vaner.setup.memory_budget import MemoryBudget, memory_budget_for
from vaner.setup.recommended.resolver import alternatives_for, pick_for
from vaner.setup.recommended.schema import RecommendedModel, Registry

# Hardening (0.8.8): work_styles enters via untrusted surfaces (HTTP
# query string, MCP tool arg, CLI flag). The resolver only consults
# values that match a fixed mapping, so unknowns fall through harmlessly,
# but we still cap the input size to avoid pathological cases (a
# 1 MB query string hitting the daemon).
_WORK_STYLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_MAX_WORK_STYLES = 16


def sanitize_work_styles(raw: Iterable[str]) -> tuple[str, ...]:
    """Filter an untrusted iterable down to safe WorkStyle slugs.

    Drops empty strings, rejects entries that don't match the
    work-style regex, deduplicates while preserving first-seen
    order, and truncates at :data:`_MAX_WORK_STYLES`. Never raises —
    the resolver treats unknown slugs as no-op intent contributions,
    so silent filtering is the right policy.
    """
    out: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str):
            continue
        candidate = value.strip().lower()
        if not candidate or candidate in seen:
            continue
        if not _WORK_STYLE_RE.fullmatch(candidate):
            continue
        seen.add(candidate)
        out.append(candidate)
        if len(out) >= _MAX_WORK_STYLES:
            break
    return tuple(out)


def _budget_to_dict(budget: MemoryBudget) -> dict[str, Any]:
    return {
        "effective_gb_q4": budget.effective_gb_q4,
        "accelerator": budget.accelerator,
        "gpu_count": budget.gpu_count,
        "can_offload_to_cpu": budget.can_offload_to_cpu,
        "notes": list(budget.notes),
    }


def _model_to_dict(model: RecommendedModel) -> dict[str, Any]:
    return {
        "id": model.id,
        "family": model.family,
        "params_b": model.params_b,
        "min_effective_gb_q4": model.min_effective_gb_q4,
        "intent_lean": list(model.intent_lean),
        "ollama_id": model.ollama_id,
        "huggingface_id": model.huggingface_id,
        "context_length": model.context_length,
        "popularity_rank": model.popularity_rank,
        "rank_source": model.rank_source,
    }


def models_recommended_payload(
    registry: Registry,
    hardware_profile: HardwareProfile,
    work_styles: Iterable[str] = (),
    *,
    alternatives_limit: int = 3,
) -> dict[str, Any]:
    # Sanitise work_styles defensively — the function is reachable from
    # untrusted HTTP / MCP / CLI surfaces. Sanitisation also caps
    # length so a pathological caller can't make the resolver iterate
    # an unbounded set.
    work_styles = sanitize_work_styles(work_styles)
    """Build the unified wire response for recommendation queries.

    Shape:

    .. code-block:: json

        {
          "registry": {
            "schema_version": 1,
            "generated_at": "2026-04-27T19:00:00Z",
            "generator": "refresh_recommended_models.py@deadbeef",
            "model_count": 24,
            "sources": [{"name": "ollama-library", "snapshot_at": "..."}]
          },
          "budget": {
            "effective_gb_q4": 18.0,
            "accelerator": "apple_silicon",
            "gpu_count": 1,
            "can_offload_to_cpu": false,
            "notes": ["Apple Silicon unified memory"]
          },
          "selected": {model dict} | null,
          "alternatives": [{model dict}, ...]
        }

    Empty registries / non-fitting budgets produce ``selected=null`` and
    ``alternatives=[]``; the desktop wizard reads those and falls back
    to its "Vaner will pick when the daemon starts" copy.
    """
    budget = memory_budget_for(hardware_profile)
    selected = pick_for(registry, budget, work_styles)
    alts = alternatives_for(registry, budget, work_styles, limit=alternatives_limit)

    return {
        "registry": {
            "schema_version": registry.schema_version,
            "generated_at": registry.generated_at.isoformat(),
            "generator": registry.generator,
            "model_count": len(registry.models),
            "sources": [
                {
                    "name": s.name,
                    "snapshot_at": s.snapshot_at.isoformat(),
                    "note": s.note,
                }
                for s in registry.sources
            ],
        },
        "budget": _budget_to_dict(budget),
        "selected": _model_to_dict(selected) if selected is not None else None,
        "alternatives": [_model_to_dict(m) for m in alts],
    }


__all__ = ["models_recommended_payload", "sanitize_work_styles"]
