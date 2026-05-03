# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

from vaner.intent.prediction_serialization import compact_serialized_prediction
from vaner.intent.readiness import is_adoptable, readiness_label
from vaner.models.prepared_work import (
    PreparedWorkAction,
    PreparedWorkActionKind,
    PreparedWorkCard,
    PreparedWorkDiagnosticRef,
    PreparedWorkEvidenceRef,
    PreparedWorkInspection,
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
    limit: int = 3,
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


def build_prediction_payload_cards(
    *,
    predictions: list[dict[str, Any]],
    include_diagnostics: bool = False,
    context_id: str | None = None,
    limit: int = 12,
    now: float | None = None,
) -> list[PreparedWorkCard]:
    """Build cards from serialized worker-snapshot predictions.

    The HTTP daemon often runs without an in-process engine while the
    long-lived precompute worker owns live prediction state. In that mode
    ``/prepared-work`` still needs UI-safe cards, so this helper accepts the
    already-serialized payload written by the worker.
    """

    ts = time.time() if now is None else float(now)
    ranked: list[_RankedPreparedWorkCard] = []
    for prediction in predictions:
        candidate = _card_from_prediction_payload(
            prediction,
            include_diagnostics=include_diagnostics,
            context_id=context_id,
            now=ts,
        )
        if candidate is not None:
            ranked.append(candidate)
    ordered = sorted(ranked, key=lambda item: item.score, reverse=True)
    return [item.card for item in ordered[: max(1, min(100, int(limit)))]]


def build_plan_draft_cards(
    *,
    drafts: list[Any],
    limit: int = 6,
    now: float | None = None,
) -> list[PreparedWorkCard]:
    ts = time.time() if now is None else float(now)
    cards: list[PreparedWorkCard] = []
    for draft in drafts[: max(1, min(50, int(limit)))]:
        if isinstance(draft, dict):
            def get(key: str, default: Any = None, *, _draft: dict[str, Any] = draft) -> Any:
                return _draft.get(key, default)
        else:
            def get(key: str, default: Any = None, *, _draft: Any = draft) -> Any:
                return getattr(_draft, key, default)
        plan_id = str(get("id", "") or "")
        if not plan_id:
            continue
        updated_at = float(get("updated_at", ts) or ts)
        title = str(sanitize_no_absolute_paths(str(get("title", "Draft plan") or "Draft plan")))
        summary = str(sanitize_no_absolute_paths(str(get("summary", "Vaner captured a draft plan.") or "")))
        tasks = get("tasks", []) or []
        task_count = len(tasks) if isinstance(tasks, list) else 0
        cards.append(
            PreparedWorkCard(
                id=f"plan:{plan_id}",
                source_id=plan_id,
                source_type=PreparedWorkSourceType.PREDICTION,
                kind=PreparedWorkKind.BRIEF,
                title=title,
                summary=summary,
                badge="Plan draft",
                confidence_label="high",
                freshness_label=_freshness_label(updated_at, ts),
                freshness_state=_prediction_freshness_state(updated_at, ts),
                target_label="Draft plan",
                why_prepared="Vaner is preparing against this draft plan in its local runtime area.",
                action_note="Inspect only; Vaner will not change workspace files from this draft.",
                evidence_count=task_count,
                created_at=float(get("created_at", updated_at) or updated_at),
                updated_at=updated_at,
                primary_action=PreparedWorkAction(
                    kind=PreparedWorkActionKind.INSPECT,
                    label="Inspect",
                    tool="vaner.plan_drafts.inspect",
                    endpoint=f"/plans/drafts/{plan_id}",
                    arguments={"plan_id": plan_id},
                ),
                secondary_actions=[],
                diagnostic_refs=[
                    PreparedWorkDiagnosticRef(kind="record", id=plan_id, reason="plan draft"),
                ],
            )
        )
    return cards


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
    if not _passes_visible_gate(product):
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
        freshness_state=_freshness_state(product, now),
        target_label=target_label,
        why_prepared=_why_prepared(product, target_label),
        action_note=_action_note(product),
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
        freshness_state=_prediction_freshness_state(float(getattr(run, "updated_at", now) or now), now),
        target_label=str(sanitize_no_absolute_paths(spec.anchor or "Current flow")),
        why_prepared=_why_prepared_prediction(prompt),
        action_note="",
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


def _card_from_prediction_payload(
    prediction: dict[str, Any],
    *,
    include_diagnostics: bool,
    context_id: str | None,
    now: float,
) -> _RankedPreparedWorkCard | None:
    readiness = str(prediction.get("readiness") or prediction.get("run", {}).get("readiness") or "")
    if readiness not in {"ready", "drafting"}:
        return None
    compact = compact_serialized_prediction(prediction)
    spec = prediction.get("spec") if isinstance(prediction.get("spec"), dict) else {}
    run = prediction.get("run") if isinstance(prediction.get("run"), dict) else {}
    artifacts = prediction.get("artifacts") if isinstance(prediction.get("artifacts"), dict) else {}
    pid = str(compact.get("id") or prediction.get("id") or spec.get("id") or "")
    if not pid:
        return None
    title = str(compact.get("label") or pid)
    summary = str(compact.get("ui_summary") or title)
    anchor = str(spec.get("source") or compact.get("source_label") or "Current flow")
    source = str(compact.get("source_label") or spec.get("source") or "worker snapshot")
    confidence = float(compact.get("confidence") or spec.get("confidence") or prediction.get("confidence") or 0.5)
    updated_at = float(compact.get("updated_at") or run.get("updated_at") or prediction.get("updated_at") or now)
    scenario_ids = artifacts.get("scenario_ids") if isinstance(artifacts.get("scenario_ids"), list) else []
    evidence_count = len(scenario_ids)
    kind = PreparedWorkKind.DRAFT if artifacts.get("has_draft") else PreparedWorkKind.PREDICTION
    refs: list[PreparedWorkDiagnosticRef] = []
    if include_diagnostics:
        refs.append(PreparedWorkDiagnosticRef(kind="prediction", id=pid, reason=source))
        for scenario_id in scenario_ids[:8]:
            refs.append(PreparedWorkDiagnosticRef(kind="record", id=str(scenario_id), reason="prediction scenario"))
    score = _score(
        kind=kind,
        confidence=confidence,
        updated_at=updated_at,
        evidence_count=evidence_count,
        action_level="adopt",
        stale_risk=0.1 if readiness == "ready" else 0.22,
        context_id=context_id,
        target_text=f"{title} {summary} {anchor}",
        now=now,
    )
    card = PreparedWorkCard(
        id=f"prediction:{pid}",
        source_id=pid,
        source_type=PreparedWorkSourceType.PREDICTION,
        kind=kind,
        title=str(sanitize_no_absolute_paths(title)),
        summary=str(sanitize_no_absolute_paths(summary)),
        badge=str(prediction.get("readiness_label") or readiness_label(readiness)),
        confidence_label=_confidence_label(confidence),
        freshness_label=_freshness_label(updated_at, now),
        freshness_state=_prediction_freshness_state(updated_at, now),
        target_label=str(sanitize_no_absolute_paths(anchor)),
        why_prepared=str(sanitize_no_absolute_paths(f"Vaner prepared this because {source} suggests {anchor} may be needed next.")),
        action_note="",
        evidence_count=evidence_count,
        created_at=float(spec.get("created_at") or updated_at),
        updated_at=updated_at,
        primary_action=PreparedWorkAction(
            kind=PreparedWorkActionKind.ADOPT,
            label="Adopt",
            tool="vaner.predictions.adopt",
            endpoint=f"/predictions/{pid}/adopt",
            arguments={"prediction_id": pid},
        ),
        secondary_actions=[
            PreparedWorkAction(
                kind=PreparedWorkActionKind.INSPECT,
                label="Inspect",
                tool="vaner.predictions.active",
                endpoint=f"/predictions/{pid}",
                arguments={"prediction_id": pid},
            )
        ],
        diagnostic_refs=refs,
    )
    return _RankedPreparedWorkCard(
        card=card,
        score=score,
        cluster_key=_cluster_key(anchor or title, "prediction"),
        advisory=False,
    )


def _work_product_action_level(product: WorkProduct) -> str:
    if product.can_export(now=time.time()):
        return "export"
    if product.adoptability == WorkProductAdoptability.INSPECTABLE:
        return "inspect"
    return "advisory"


def _work_product_actions(product: WorkProduct) -> tuple[PreparedWorkAction | None, list[PreparedWorkAction]]:
    inspect = PreparedWorkAction(
        kind=PreparedWorkActionKind.INSPECT,
        label="Inspect",
        tool="vaner.work_products.inspect",
        endpoint=f"/work-products/{product.id}/inspect",
        arguments={"work_product_id": product.id},
    )
    dismiss = PreparedWorkAction(
        kind=PreparedWorkActionKind.DISMISS,
        label="Dismiss",
        tool="vaner.work_products.dismiss",
        endpoint=f"/work-products/{product.id}/dismiss",
        arguments={"work_product_id": product.id},
    )
    useful = PreparedWorkAction(
        kind=PreparedWorkActionKind.FEEDBACK,
        label="Useful",
        tool="vaner.work_products.feedback",
        endpoint=f"/work-products/{product.id}/feedback",
        arguments={"work_product_id": product.id, "feedback_state": "useful"},
    )
    not_useful = PreparedWorkAction(
        kind=PreparedWorkActionKind.FEEDBACK,
        label="Not useful",
        tool="vaner.work_products.feedback",
        endpoint=f"/work-products/{product.id}/feedback",
        arguments={"work_product_id": product.id, "feedback_state": "not_useful"},
    )
    if product.can_export(now=time.time()):
        export = PreparedWorkAction(
            kind=PreparedWorkActionKind.EXPORT,
            label="Export",
            tool="vaner.work_products.export",
            endpoint=f"/work-products/{product.id}/export",
            arguments={"work_product_id": product.id},
        )
        return export, [inspect, useful, not_useful, dismiss]
    if product.adoptability == WorkProductAdoptability.INSPECTABLE:
        return inspect, [useful, not_useful, dismiss]
    return inspect, [useful, not_useful, dismiss]


def build_work_product_inspection(product: WorkProduct, *, now: float | None = None) -> PreparedWorkInspection:
    ts = time.time() if now is None else float(now)
    kind = _WORK_PRODUCT_KIND.get(product.type, PreparedWorkKind.REVIEW)
    target_label = _target_label(product.source_snapshot.relative_paths, product.target_key)
    primary, secondary = _work_product_actions(product)
    actions = [action for action in [primary, *secondary] if action is not None]
    warnings = _inspection_warnings(product)
    can_export = product.can_export(now=ts)
    evidence_refs = [
        PreparedWorkEvidenceRef(
            kind=ref.kind,
            path=str(sanitize_no_absolute_paths(ref.path)),
            symbol=str(sanitize_no_absolute_paths(ref.symbol)),
            reason=str(sanitize_no_absolute_paths(ref.reason)),
            confidence_label=_confidence_label(ref.confidence or product.confidence),
        )
        for ref in product.evidence_refs[:12]
    ]
    body = str(sanitize_no_absolute_paths(product.body))
    return PreparedWorkInspection(
        id=f"work_product:{product.id}",
        source_id=product.id,
        source_type=PreparedWorkSourceType.WORK_PRODUCT,
        kind=kind,
        title=str(sanitize_no_absolute_paths(product.title)),
        summary=str(sanitize_no_absolute_paths(product.summary)),
        body=body,
        why_prepared=_why_prepared(product, target_label),
        confidence_label=_confidence_label(product.confidence),
        freshness_label=_freshness_label(product.updated_at, ts),
        freshness_state=_freshness_state(product, ts),
        target_label=target_label,
        evidence_count=len(product.evidence_refs),
        evidence_refs=evidence_refs,
        allowed_actions=[action for action in actions if can_export or action.kind != PreparedWorkActionKind.EXPORT],
        warnings=warnings,
        export_preview=body if can_export else "",
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _passes_visible_gate(product: WorkProduct) -> bool:
    if not product.title.strip() or not product.summary.strip():
        return False
    if not product.evidence_refs:
        return False
    if product.self_eval.contradiction_risk >= 0.65 or product.self_eval.stale_risk >= 0.72:
        return False
    evidence_floor = min(1.0, 0.35 + len(product.evidence_refs) * 0.2)
    coverage = product.self_eval.evidence_coverage or evidence_floor
    groundedness = product.self_eval.groundedness or evidence_floor
    if product.adoptability == WorkProductAdoptability.EXPORTABLE:
        return product.confidence >= 0.7 and coverage >= 0.5 and groundedness >= 0.5
    if product.adoptability == WorkProductAdoptability.INSPECTABLE:
        return product.confidence >= 0.58 and coverage >= 0.45 and groundedness >= 0.45
    if product.adoptability == WorkProductAdoptability.ADVISORY:
        return product.confidence >= 0.65 and coverage >= 0.45
    return False


def _why_prepared(product: WorkProduct, target_label: str) -> str:
    reason = product.self_eval.reason.strip()
    if reason:
        return str(sanitize_no_absolute_paths(reason))
    evidence_count = len(product.evidence_refs)
    if evidence_count:
        return f"Vaner prepared this from {evidence_count} source reference{'s' if evidence_count != 1 else ''} around {target_label}."
    return f"Vaner prepared this around {target_label}."


def _why_prepared_prediction(prompt: Any) -> str:
    spec = prompt.spec
    source = str(getattr(spec, "source", "") or "recent activity").replace("_", " ")
    anchor = str(getattr(spec, "anchor", "") or "the current flow")
    return str(sanitize_no_absolute_paths(f"Vaner prepared this because {source} suggests {anchor} may be needed next."))


def _action_note(product: WorkProduct) -> str:
    if product.type == WorkProductType.VIRTUAL_DIFF:
        if product.can_export(now=time.time()):
            return "Export returns the prepared diff only; Vaner will not apply it automatically."
        return "Inspect only until the diff is regenerated against the current files."
    if product.adoptability == WorkProductAdoptability.EXPORTABLE:
        return "Export returns Vaner-owned content without changing your files."
    return "Inspect this lead before using it."


def _inspection_warnings(product: WorkProduct) -> list[str]:
    warnings: list[str] = []
    if product.freshness == WorkProductFreshness.STALE:
        warnings.append("This prepared item is stale. Regenerate it before export or adoption.")
    elif product.freshness == WorkProductFreshness.UNKNOWN:
        warnings.append("Freshness is not verified. Inspect evidence before using it.")
    if product.self_eval.contradiction_risk >= 0.35:
        warnings.append("Vaner found some contradiction risk in the supporting evidence.")
    if product.self_eval.stale_risk >= 0.35:
        warnings.append("Vaner found elevated staleness risk in this prepared item.")
    return warnings


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


def _freshness_state(product: WorkProduct, now: float) -> str:
    if product.freshness == WorkProductFreshness.STALE:
        return "stale"
    if product.freshness == WorkProductFreshness.UNKNOWN or product.self_eval.stale_risk >= 0.35:
        return "possibly_stale"
    return _prediction_freshness_state(product.updated_at, now)


def _prediction_freshness_state(updated_at: float, now: float) -> str:
    age_seconds = max(0.0, now - updated_at)
    if age_seconds < 2 * 60 * 60:
        return "fresh"
    if age_seconds < 24 * 60 * 60:
        return "recent"
    return "possibly_stale"


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
