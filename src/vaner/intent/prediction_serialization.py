# SPDX-License-Identifier: Apache-2.0
"""Shared prediction wire serialization helpers.

MCP and daemon HTTP expose predictions through different transports, but
clients should see the same additive card and trust fields. Keep this module
free of MCP/FastAPI imports so both surfaces can use it.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from vaner.intent.assist_decision import evaluate_prediction_relevance, normalize_prediction_label
from vaner.intent.prediction import PredictedPrompt
from vaner.intent.prediction_card import derive_card_fields
from vaner.policy.privacy import sanitize_no_absolute_paths


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


def serialize_prediction_compact(prompt: PredictedPrompt, *, rank: int | None = None) -> dict[str, Any]:
    """Render a dashboard-safe prediction row.

    This shape is meant for default UI/MCP surfaces. It intentionally omits
    free-form description, anchor, and structured semantic hint fields because
    those may be derived from local chat/query history. Detail/inspect paths can
    still return the full nested shape when the user explicitly asks for one
    prediction.
    """

    spec = prompt.spec
    run = prompt.run
    artifacts = prompt.artifacts
    card = _safe_card_payload(prediction_card_payload(prompt, rank=rank))
    action_type = getattr(spec.structured, "action_type", "") if spec.structured is not None else ""
    payload = {
        "id": spec.id,
        "label": _safe_text(
            spec.label,
            fallback=_fallback_prediction_label(card["source_label"], action_type),
            max_len=140,
            reject_long_history=True,
        ),
        "source": spec.source,
        "source_label": card["source_label"],
        "confidence": float(spec.confidence),
        "hypothesis_type": spec.hypothesis_type,
        "specificity": spec.specificity,
        "readiness": run.readiness,
        "readiness_label": card["readiness_label"],
        "eta_bucket": card["eta_bucket"],
        "eta_bucket_label": card["eta_bucket_label"],
        "scenarios_complete": run.scenarios_complete,
        "scenarios_spawned": run.scenarios_spawned,
        "tokens_used": run.tokens_used,
        "token_budget": run.token_budget,
        "adoptable": card["adoptable"],
        "suppression_reason": card["suppression_reason"],
        "ui_summary": card["ui_summary"],
        "trust_status": card["trust_status"],
        "freshness": card["freshness"],
        "diagnostic_status": card["diagnostic_status"],
        "changed_sources": card["changed_sources"],
        "invalidated_by": card["invalidated_by"],
        "watched_sources": list(card["watched_sources"])[:8],
        "has_draft": artifacts.draft_answer is not None,
        "has_briefing": artifacts.prepared_briefing is not None,
        "evidence_count": len(artifacts.scenario_ids),
        "updated_at": run.updated_at,
    }
    if rank is not None:
        payload["rank"] = rank
    relevance = evaluate_prediction_relevance(payload)
    payload["label"] = relevance.display_label
    payload.update(relevance.as_dict())
    return payload


def compact_serialized_prediction(row: dict[str, Any], *, rank: int | None = None) -> dict[str, Any]:
    """Compact a serialized flat or nested prediction row for default surfaces."""

    spec = row.get("spec") if isinstance(row.get("spec"), dict) else {}
    run = row.get("run") if isinstance(row.get("run"), dict) else {}
    artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
    pid = str(row.get("id") or spec.get("id") or row.get("prediction_id") or "")
    source_label = str(row.get("source_label") or spec.get("source") or row.get("source") or "Prediction")
    structured = spec.get("structured") if isinstance(spec.get("structured"), dict) else {}
    label = _safe_text(
        row.get("label") or spec.get("label") or row.get("title") or pid or "Prepared prediction",
        fallback=_fallback_prediction_label(source_label, str(structured.get("action_type") or "")),
        max_len=140,
        reject_long_history=True,
    )
    label = normalize_prediction_label({**row, "label": label, "source_label": source_label})
    summary = _safe_text(
        row.get("ui_summary") or row.get("description") or spec.get("description") or label,
        fallback=f"{source_label} prediction is ready.",
        max_len=180,
        reject_long_history=True,
    )
    readiness = str(row.get("readiness") or run.get("readiness") or "")
    scenario_ids = artifacts.get("scenario_ids") if isinstance(artifacts.get("scenario_ids"), list) else []
    evidence_targets = [str(item) for item in list(row.get("evidence_targets") or structured.get("evidence_targets") or []) if item]
    watched_sources = list(row.get("watched_sources") or [])[:8] or evidence_targets[:8]
    compact = {
        "id": pid,
        "label": label,
        "source": str(row.get("source") or spec.get("source") or ""),
        "source_label": source_label,
        "confidence": float(row.get("confidence") or spec.get("confidence") or 0.0),
        "hypothesis_type": row.get("hypothesis_type") or spec.get("hypothesis_type"),
        "specificity": row.get("specificity") or spec.get("specificity"),
        "readiness": readiness,
        "readiness_label": row.get("readiness_label") or readiness.replace("_", " ").title(),
        "eta_bucket": row.get("eta_bucket"),
        "eta_bucket_label": row.get("eta_bucket_label"),
        "scenarios_complete": int(row.get("scenarios_complete") or run.get("scenarios_complete") or 0),
        "scenarios_spawned": int(row.get("scenarios_spawned") or run.get("scenarios_spawned") or 0),
        "tokens_used": int(row.get("tokens_used") or run.get("tokens_used") or 0),
        "token_budget": int(row.get("token_budget") or run.get("token_budget") or 0),
        "adoptable": bool(row.get("adoptable")),
        "suppression_reason": row.get("suppression_reason"),
        "ui_summary": summary,
        "trust_status": row.get("trust_status"),
        "freshness": row.get("freshness"),
        "diagnostic_status": row.get("diagnostic_status"),
        "changed_sources": list(row.get("changed_sources") or [])[:8],
        "invalidated_by": list(row.get("invalidated_by") or [])[:8],
        "watched_sources": watched_sources,
        "evidence_targets": evidence_targets[:8],
        "has_draft": bool(row.get("has_draft") or artifacts.get("has_draft")),
        "has_briefing": bool(row.get("has_briefing") or artifacts.get("has_briefing")),
        "evidence_count": len(scenario_ids) if scenario_ids else int(artifacts.get("evidence_count") or row.get("evidence_count") or 0),
        "updated_at": float(run.get("updated_at") or row.get("updated_at") or 0.0),
    }
    if rank is not None:
        compact["rank"] = rank
    elif row.get("rank") is not None:
        compact["rank"] = row.get("rank")
    relevance = evaluate_prediction_relevance(compact)
    compact["label"] = relevance.display_label
    compact.update(relevance.as_dict())
    return compact


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
    relevance = evaluate_prediction_relevance(payload)
    payload["label"] = relevance.display_label
    payload.update(relevance.as_dict())
    return payload


def _safe_card_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source_label = str(payload.get("source_label") or "Prediction")
    payload = dict(payload)
    payload["source_label"] = _safe_text(source_label, fallback="Prediction", max_len=80)
    payload["ui_summary"] = _safe_text(
        payload.get("ui_summary") or "",
        fallback=f"{payload['source_label']} prediction is ready.",
        max_len=180,
        reject_long_history=True,
    )
    payload["watched_sources"] = [str(sanitize_no_absolute_paths(str(item))) for item in list(payload.get("watched_sources") or [])[:8]]
    payload["changed_sources"] = [str(sanitize_no_absolute_paths(str(item))) for item in list(payload.get("changed_sources") or [])[:8]]
    return payload


def _safe_text(
    value: Any,
    *,
    fallback: str,
    max_len: int,
    reject_long_history: bool = False,
) -> str:
    raw = "" if value is None else str(value)
    text = str(sanitize_no_absolute_paths(raw)).strip()
    if not text:
        return fallback
    if reject_long_history and _looks_like_raw_history(text):
        return fallback
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max(0, max_len - 1)].rstrip() + "…"
    return text


def _looks_like_raw_history(text: str) -> bool:
    if len(text) > 260:
        return True
    if len([line for line in text.splitlines() if line.strip()]) > 1:
        return True
    if text.count("?") >= 2:
        return True
    lower = text.lower()
    if "recent queries clustered" in lower or "chat history" in lower:
        return True
    chat_markers = (
        " i am ",
        " i'm ",
        " i'd ",
        " i've ",
        " we ",
        " we're ",
        " we've ",
        " you ",
        " your ",
        " why ",
        " shouldn't ",
        " couldn't ",
    )
    padded = f" {lower} "
    return any(marker in padded for marker in chat_markers)


def _fallback_prediction_label(source_label: str, action_type: str = "") -> str:
    action = str(action_type or "").strip().replace("_", " ")
    if action:
        return f"Prepared {action} prediction"
    source = str(source_label or "Vaner").strip()
    return f"{source} prediction"
