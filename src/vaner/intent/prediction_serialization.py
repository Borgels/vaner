# SPDX-License-Identifier: Apache-2.0
"""Shared prediction wire serialization helpers.

MCP and daemon HTTP expose predictions through different transports, but
clients should see the same additive card and trust fields. Keep this module
free of MCP/FastAPI imports so both surfaces can use it.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from vaner.intent.prediction import PredictedPrompt
from vaner.intent.prediction_card import derive_card_fields


def composer_engagement_payload(prompt: PredictedPrompt) -> dict[str, Any] | None:
    """Return validated composer-engagement metadata for a composer prediction."""

    if prompt.spec.source != "composer_intent":
        return None
    metadata: dict[str, Any] = prompt.artifacts.composer_metadata or {}
    composer_event_id = metadata.get("composer_event_id")
    if not isinstance(composer_event_id, str) or not composer_event_id:
        return None
    lifecycle_state = metadata.get("lifecycle_state", "submitted")
    if not isinstance(lifecycle_state, str):
        lifecycle_state = "submitted"
    inferred_intent_label = metadata.get("inferred_intent_label")
    if inferred_intent_label is not None and not isinstance(inferred_intent_label, str):
        inferred_intent_label = None
    inferred_intent_confidence = metadata.get("inferred_intent_confidence")
    if inferred_intent_confidence is not None and not isinstance(inferred_intent_confidence, (int, float)):
        inferred_intent_confidence = None
    return {
        "composer_event_id": composer_event_id,
        "lifecycle_state": lifecycle_state,
        "inferred_intent_label": inferred_intent_label,
        "inferred_intent_confidence": inferred_intent_confidence,
    }


def prediction_trust_payload(prompt: PredictedPrompt) -> dict[str, Any]:
    """Compute lightweight trust metadata for a prediction card.

    This is intentionally conservative. It reports what the prediction already
    knows: whether it is stale, whether it has watched sources, and whether any
    diagnostic verdict is attached. Live file comparison remains the registry's
    invalidation job, not a serializer side effect.
    """

    run = prompt.run
    artifacts = prompt.artifacts
    watched_sources = sorted(artifacts.file_content_hashes)
    invalidated_by: list[str] = []
    changed_sources: list[str] = []
    diagnostic_status = "not_checked"

    if run.readiness == "stale":
        trust_status = "invalidated"
        freshness = "stale"
        invalidated_by.append(run.invalidation_reason or "readiness_stale")
        diagnostic_status = "invalidated"
    elif run.readiness in {"ready", "drafting"} and (artifacts.prepared_briefing or artifacts.draft_answer):
        trust_status = "ready"
        freshness = "fresh" if watched_sources else "recent"
        diagnostic_status = "verified" if watched_sources else "unverified"
    else:
        trust_status = "preparing"
        freshness = "recent"

    return {
        "trust_status": trust_status,
        "freshness": freshness,
        "invalidated_by": invalidated_by,
        "watched_sources": watched_sources,
        "changed_sources": changed_sources,
        "diagnostic_status": diagnostic_status,
    }


def prediction_card_payload(prompt: PredictedPrompt, *, rank: int | None = None) -> dict[str, Any]:
    """Return additive card/trust fields shared by MCP and daemon HTTP."""

    card = derive_card_fields(prompt)
    payload: dict[str, Any] = {
        "readiness_label": card.readiness_label,
        "eta_bucket": card.eta_bucket,
        "eta_bucket_label": card.eta_bucket_label,
        "adoptable": card.adoptable,
        "suppression_reason": card.suppression_reason,
        "source_label": card.source_label,
        "ui_summary": card.ui_summary,
        **prediction_trust_payload(prompt),
    }
    if rank is not None:
        payload["rank"] = rank
    composer_engagement = composer_engagement_payload(prompt)
    if composer_engagement is not None:
        payload["composer_engagement"] = composer_engagement
    return payload


def serialize_prediction_flat(prompt: PredictedPrompt, *, rank: int | None = None) -> dict[str, Any]:
    """Render the flat MCP prediction-card shape."""

    spec = prompt.spec
    run = prompt.run
    artifacts = prompt.artifacts
    payload: dict[str, Any] = {
        "id": spec.id,
        "label": spec.label,
        "description": spec.description,
        "source": spec.source,
        "confidence": spec.confidence,
        "hypothesis_type": spec.hypothesis_type,
        "specificity": spec.specificity,
        "readiness": run.readiness,
        "weight": run.weight,
        "token_budget": run.token_budget,
        "tokens_used": run.tokens_used,
        "scenarios_complete": run.scenarios_complete,
        "scenarios_spawned": run.scenarios_spawned,
        "evidence_score": artifacts.evidence_score,
        "has_draft": artifacts.draft_answer is not None,
        "has_briefing": artifacts.prepared_briefing is not None,
    }
    if spec.structured is not None:
        payload["structured"] = asdict(spec.structured)
    payload.update(prediction_card_payload(prompt, rank=rank))
    return payload


def serialize_prediction_nested(prompt: PredictedPrompt, *, rank: int | None = None) -> dict[str, Any]:
    """Render the daemon's nested prediction shape plus additive card fields."""

    spec = prompt.spec
    run = prompt.run
    artifacts = prompt.artifacts
    payload: dict[str, Any] = {
        "id": spec.id,
        "spec": {
            "label": spec.label,
            "description": spec.description,
            "source": spec.source,
            "anchor": spec.anchor,
            "confidence": spec.confidence,
            "hypothesis_type": spec.hypothesis_type,
            "specificity": spec.specificity,
            "created_at": spec.created_at,
            "structured": asdict(spec.structured) if spec.structured is not None else None,
        },
        "run": {
            "weight": run.weight,
            "token_budget": run.token_budget,
            "tokens_used": run.tokens_used,
            "model_calls": run.model_calls,
            "scenarios_spawned": run.scenarios_spawned,
            "scenarios_complete": run.scenarios_complete,
            "readiness": run.readiness,
            "updated_at": run.updated_at,
        },
        "artifacts": {
            "scenario_ids": list(artifacts.scenario_ids),
            "evidence_score": artifacts.evidence_score,
            "has_draft": artifacts.draft_answer is not None,
            "has_briefing": artifacts.prepared_briefing is not None,
            "thinking_trace_count": len(artifacts.thinking_traces),
        },
    }
    payload.update(prediction_card_payload(prompt, rank=rank))
    return payload
