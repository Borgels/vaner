# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

from vaner.intent.readiness import is_adoptable, readiness_label
from vaner.models.prepared_work import (
    PreparedWorkAction,
    PreparedWorkActionKind,
    PreparedWorkCard,
    PreparedWorkDiagnosticRef,
    PreparedWorkKind,
    PreparedWorkSourceType,
)
from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductFreshness,
    WorkProductStatus,
    WorkProductType,
)
from vaner.policy.privacy import sanitize_no_absolute_paths

_WORK_PRODUCT_KIND: dict[WorkProductType, PreparedWorkKind] = {
    WorkProductType.REVIEW_NOTE: PreparedWorkKind.REVIEW,
    WorkProductType.BUG_HYPOTHESIS: PreparedWorkKind.BUG,
    WorkProductType.DOCS_DRIFT: PreparedWorkKind.DOCS,
    WorkProductType.VIRTUAL_DIFF: PreparedWorkKind.DIFF,
    WorkProductType.RESEARCH_BRIEF: PreparedWorkKind.BRIEF,
}

_KIND_PRIORITY: dict[PreparedWorkKind, float] = {
    PreparedWorkKind.DIFF: 1.0,
    PreparedWorkKind.BRIEF: 0.92,
    PreparedWorkKind.REVIEW: 0.82,
    PreparedWorkKind.DRAFT: 0.8,
    PreparedWorkKind.PREDICTION: 0.76,
    PreparedWorkKind.BUG: 0.7,
    PreparedWorkKind.DOCS: 0.62,
}

_ACTIONABILITY: dict[str, float] = {
    "export": 1.0,
    "adopt": 0.9,
    "inspect": 0.72,
    "advisory": 0.42,
}


@dataclass(frozen=True)
class _RankedPreparedWorkCard:
    card: PreparedWorkCard
    score: float
    cluster_key: str
    advisory: bool


def build_prepared_work_cards(
    *,
    work_products: list[WorkProduct],
    predictions: list[Any],
    include_advisory: bool = False,
    include_diagnostics: bool = False,
    context_id: str | None = None,
    surface: str = "api",
    limit: int = 20,
    now: float | None = None,
) -> list[PreparedWorkCard]:
    ts = time.time() if now is None else float(now)
    ranked: list[_RankedPreparedWorkCard] = []
    for product in work_products:
        candidate = _card_from_work_product(
            product,
            include_diagnostics=include_diagnostics,
            surface=surface,
            context_id=context_id,
            now=ts,
        )
        if candidate is not None:
            ranked.append(candidate)
    for prediction in predictions:
        candidate = _card_from_prediction(
            prediction,
            include_diagnostics=include_diagnostics,
            context_id=context_id,
            now=ts,
        )
        if candidate is not None:
            ranked.append(candidate)

    stronger = [item for item in ranked if not item.advisory]
    if not include_advisory:
        ranked = stronger if stronger else ranked

    best_by_cluster: dict[str, _RankedPreparedWorkCard] = {}
    for item in ranked:
        previous = best_by_cluster.get(item.cluster_key)
        if previous is None or item.score > previous.score:
            best_by_cluster[item.cluster_key] = item
    ordered = sorted(best_by_cluster.values(), key=lambda item: item.score, reverse=True)
    return [item.card for item in ordered[: max(1, min(100, int(limit)))]]


def _card_from_work_product(
    product: WorkProduct,
    *,
    include_diagnostics: bool,
    surface: str,
    context_id: str | None,
    now: float,
) -> _RankedPreparedWorkCard | None:
    if product.status in {
        WorkProductStatus.CANDIDATE,
        WorkProductStatus.DISMISSED,
        WorkProductStatus.EXPIRED,
        WorkProductStatus.SUPERSEDED,
    }:
        return None
    if product.adoptability == WorkProductAdoptability.HIDDEN:
        return None
    if product.freshness == WorkProductFreshness.STALE:
        return None
    if product.expires_at is not None and product.expires_at <= now:
        return None

    kind = _WORK_PRODUCT_KIND.get(product.type, PreparedWorkKind.REVIEW)
    action_level = _work_product_action_level(product)
    primary, secondary = _work_product_actions(product)
    target_label = _target_label(product.source_snapshot.relative_paths, product.target_key)
    refs: list[PreparedWorkDiagnosticRef] = []
    if include_diagnostics:
        refs = [PreparedWorkDiagnosticRef(kind=ref.kind, path=ref.path, reason=ref.reason) for ref in product.evidence_refs[:8]]
        refs.append(PreparedWorkDiagnosticRef(kind="work_product", id=product.id, reason=product.target_key))
    score = _score(
        kind=kind,
        confidence=product.confidence,
        updated_at=product.updated_at,
        evidence_count=len(product.evidence_refs),
        action_level=action_level,
        stale_risk=product.self_eval.stale_risk,
        context_id=context_id,
        target_text=f"{product.title} {product.summary} {product.target_key}",
        now=now,
    )
    card = PreparedWorkCard(
        id=f"work_product:{product.id}",
        source_id=product.id,
        source_type=PreparedWorkSourceType.WORK_PRODUCT,
        kind=kind,
        title=str(sanitize_no_absolute_paths(product.title)),
        summary=str(sanitize_no_absolute_paths(product.summary)),
        badge=_badge_for_kind(kind),
        confidence_label=_confidence_label(product.confidence),
        freshness_label=_freshness_label(product.updated_at, now),
        target_label=target_label,
        evidence_count=len(product.evidence_refs),
        created_at=product.created_at,
        updated_at=product.updated_at,
        primary_action=primary,
        secondary_actions=secondary,
        diagnostic_refs=refs,
    )
    _ = surface
    return _RankedPreparedWorkCard(
        card=card,
        score=score,
        cluster_key=_cluster_key(product.target_key or target_label, product.type.value),
        advisory=product.adoptability == WorkProductAdoptability.ADVISORY,
    )


def _card_from_prediction(
    prompt: Any,
    *,
    include_diagnostics: bool,
    context_id: str | None,
    now: float,
) -> _RankedPreparedWorkCard | None:
    if not is_adoptable(prompt):
        return None
    spec = prompt.spec
    run = prompt.run
    kind = PreparedWorkKind.DRAFT if bool(getattr(prompt.artifacts, "draft_answer", None)) else PreparedWorkKind.PREDICTION
    primary = PreparedWorkAction(
        kind=PreparedWorkActionKind.ADOPT,
        label="Adopt",
        tool="vaner.predictions.adopt",
        endpoint=f"/predictions/{spec.id}/adopt",
        arguments={"prediction_id": spec.id},
    )
    secondary = [
        PreparedWorkAction(
            kind=PreparedWorkActionKind.INSPECT,
            label="Inspect",
            tool="vaner.predictions.active",
            endpoint=f"/predictions/{spec.id}",
            arguments={"prediction_id": spec.id},
        )
    ]
    refs: list[PreparedWorkDiagnosticRef] = []
    if include_diagnostics:
        refs.append(PreparedWorkDiagnosticRef(kind="prediction", id=spec.id, reason=spec.source))
        for scenario_id in list(getattr(prompt.artifacts, "scenario_ids", []) or [])[:8]:
            refs.append(PreparedWorkDiagnosticRef(kind="record", id=str(scenario_id), reason="prediction scenario"))
    readiness = readiness_label(run.readiness)
    evidence_count = len(list(getattr(prompt.artifacts, "scenario_ids", []) or []))
    confidence = float(spec.confidence)
    score = _score(
        kind=kind,
        confidence=confidence,
        updated_at=float(getattr(run, "updated_at", now) or now),
        evidence_count=evidence_count,
        action_level="adopt",
        stale_risk=0.1 if run.readiness == "ready" else 0.22,
        context_id=context_id,
        target_text=f"{spec.label} {spec.description or ''} {spec.anchor or ''}",
        now=now,
    )
    card = PreparedWorkCard(
        id=f"prediction:{spec.id}",
        source_id=spec.id,
        source_type=PreparedWorkSourceType.PREDICTION,
        kind=kind,
        title=str(sanitize_no_absolute_paths(spec.label)),
        summary=str(sanitize_no_absolute_paths(spec.description or spec.label)),
        badge=readiness,
        confidence_label=_confidence_label(confidence),
        freshness_label=_freshness_label(float(getattr(run, "updated_at", now) or now), now),
        target_label=str(sanitize_no_absolute_paths(spec.anchor or "Current flow")),
        evidence_count=evidence_count,
        created_at=float(getattr(spec, "created_at", now) or now),
        updated_at=float(getattr(run, "updated_at", now) or now),
        primary_action=primary,
        secondary_actions=secondary,
        diagnostic_refs=refs,
    )
    return _RankedPreparedWorkCard(
        card=card,
        score=score,
        cluster_key=_cluster_key(str(spec.anchor or spec.label), "prediction"),
        advisory=False,
    )


def _work_product_action_level(product: WorkProduct) -> str:
    if product.adoptability == WorkProductAdoptability.EXPORTABLE:
        return "export"
    if product.adoptability == WorkProductAdoptability.INSPECTABLE:
        return "inspect"
    return "advisory"


def _work_product_actions(product: WorkProduct) -> tuple[PreparedWorkAction | None, list[PreparedWorkAction]]:
    inspect = PreparedWorkAction(
        kind=PreparedWorkActionKind.INSPECT,
        label="Inspect",
        tool="vaner.work_products.inspect",
        endpoint=f"/work-products/{product.id}",
        arguments={"work_product_id": product.id},
    )
    dismiss = PreparedWorkAction(
        kind=PreparedWorkActionKind.DISMISS,
        label="Dismiss",
        tool="vaner.work_products.dismiss",
        endpoint=f"/work-products/{product.id}/dismiss",
        arguments={"work_product_id": product.id},
    )
    feedback = PreparedWorkAction(
        kind=PreparedWorkActionKind.FEEDBACK,
        label="Feedback",
        tool="vaner.work_products.feedback",
        endpoint=f"/work-products/{product.id}/feedback",
        arguments={"work_product_id": product.id},
    )
    if product.adoptability == WorkProductAdoptability.EXPORTABLE:
        export = PreparedWorkAction(
            kind=PreparedWorkActionKind.EXPORT,
            label="Export",
            tool="vaner.work_products.export",
            endpoint=f"/work-products/{product.id}/export",
            arguments={"work_product_id": product.id},
        )
        return export, [inspect, dismiss, feedback]
    if product.adoptability == WorkProductAdoptability.INSPECTABLE:
        return inspect, [dismiss, feedback]
    return inspect, [dismiss, feedback]


def _score(
    *,
    kind: PreparedWorkKind,
    confidence: float,
    updated_at: float,
    evidence_count: int,
    action_level: str,
    stale_risk: float,
    context_id: str | None,
    target_text: str,
    now: float,
) -> float:
    age_hours = max(0.0, (now - updated_at) / 3600.0)
    freshness = 1.0 / (1.0 + age_hours / 8.0)
    evidence = min(1.0, evidence_count / 4.0)
    context = _context_overlap(context_id, target_text)
    return round(
        0.32 * max(0.0, min(1.0, confidence))
        + 0.22 * freshness
        + 0.16 * _ACTIONABILITY.get(action_level, 0.4)
        + 0.12 * _KIND_PRIORITY.get(kind, 0.5)
        + 0.1 * evidence
        + 0.08 * context
        - 0.12 * max(0.0, min(1.0, stale_risk)),
        6,
    )


def _context_overlap(context_id: str | None, target_text: str) -> float:
    if not context_id:
        return 0.0
    left = set(re.findall(r"[a-z0-9_]+", context_id.lower()))
    right = set(re.findall(r"[a-z0-9_]+", target_text.lower()))
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _confidence_label(value: float) -> str:
    if value >= 0.78:
        return "High"
    if value >= 0.58:
        return "Medium"
    return "Low"


def _freshness_label(updated_at: float, now: float) -> str:
    age_seconds = max(0.0, now - updated_at)
    if age_seconds < 10 * 60:
        return "Fresh"
    if age_seconds < 2 * 60 * 60:
        return "Recent"
    return "Older"


def _badge_for_kind(kind: PreparedWorkKind) -> str:
    return {
        PreparedWorkKind.REVIEW: "Review",
        PreparedWorkKind.BUG: "Bug",
        PreparedWorkKind.DOCS: "Docs",
        PreparedWorkKind.DIFF: "Diff",
        PreparedWorkKind.BRIEF: "Brief",
        PreparedWorkKind.DRAFT: "Draft",
        PreparedWorkKind.PREDICTION: "Ready",
    }[kind]


def _target_label(paths: list[str], fallback: str) -> str:
    if paths:
        if len(paths) == 1:
            return paths[0]
        return f"{paths[0]} +{len(paths) - 1}"
    return fallback or "Workspace"


def _cluster_key(target: str, kind: str) -> str:
    normalized = re.sub(r"[^a-z0-9_./-]+", " ", target.lower()).strip()
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]  # noqa: S324
    return f"{kind}:{digest}"
