# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from vaner.models.prepared_work import PreparedWorkCard
from vaner.policy.privacy import sanitize_no_absolute_paths


class PreparedWorkBenchmarkCase(BaseModel):
    case_id: str
    archetype: str
    prompt: str
    expected_kinds: list[str] = Field(default_factory=list)
    expected_targets: list[str] = Field(default_factory=list)
    idle_seconds: float = 0.0


class PreparedWorkCaseResult(BaseModel):
    case_id: str
    archetype: str
    surfaced_count: int
    useful_count: int
    false_positive_count: int
    exportable_count: int
    artifact_precision: float
    adoption_value: float
    groundedness: float
    stale_misleading_count: int
    post_prep_latency_ms: float
    estimated_cost_usd: float = 0.0
    cost_per_useful_artifact_usd: float = 0.0
    cards: list[dict[str, object]] = Field(default_factory=list)


class PreparedWorkBenchmarkReport(BaseModel):
    run_id: str
    profile: str = "prepared_work_long_idle"
    case_count: int
    artifact_precision_mean: float
    false_positive_rate: float
    adoption_value_mean: float
    groundedness_mean: float
    stale_misleading_rate: float
    post_prep_latency_ms_mean: float
    estimated_cost_usd_total: float
    cost_per_useful_artifact_usd: float
    cases: list[PreparedWorkCaseResult]


def score_prepared_work_case(
    case: PreparedWorkBenchmarkCase,
    cards: Sequence[PreparedWorkCard],
    *,
    post_prep_latency_ms: float = 0.0,
    estimated_cost_usd: float = 0.0,
) -> PreparedWorkCaseResult:
    expected_kinds = {item.strip().lower() for item in case.expected_kinds if item.strip()}
    expected_targets = {item.strip().lower() for item in case.expected_targets if item.strip()}
    useful = 0
    exportable = 0
    grounded_scores: list[float] = []
    stale_misleading = 0
    rendered_cards: list[dict[str, object]] = []
    for card in cards:
        kind_match = not expected_kinds or card.kind.value.lower() in expected_kinds
        target = card.target_label.lower()
        target_match = not expected_targets or any(expected in target for expected in expected_targets)
        evidence_ok = card.evidence_count > 0
        if kind_match and target_match and evidence_ok:
            useful += 1
        if card.primary_action is not None and card.primary_action.kind.value == "export":
            exportable += 1
        if card.freshness_state == "stale" and card.primary_action is not None and card.primary_action.kind.value == "export":
            stale_misleading += 1
        grounded_scores.append(min(1.0, card.evidence_count / 2.0))
        rendered_cards.append(
            sanitize_no_absolute_paths(
                {
                    "id": card.id,
                    "kind": card.kind.value,
                    "title": card.title,
                    "target_label": card.target_label,
                    "evidence_count": card.evidence_count,
                    "freshness_state": card.freshness_state,
                    "primary_action": card.primary_action.kind.value if card.primary_action is not None else None,
                }
            )
        )
    surfaced = len(cards)
    false_positive = max(0, surfaced - useful)
    precision = useful / max(1, surfaced)
    adoption_value = min(1.0, (useful * 0.55 + exportable * 0.25 + sum(grounded_scores) * 0.2) / max(1, surfaced))
    groundedness = sum(grounded_scores) / max(1, len(grounded_scores))
    return PreparedWorkCaseResult(
        case_id=case.case_id,
        archetype=case.archetype,
        surfaced_count=surfaced,
        useful_count=useful,
        false_positive_count=false_positive,
        exportable_count=exportable,
        artifact_precision=round(precision, 4),
        adoption_value=round(adoption_value, 4),
        groundedness=round(groundedness, 4),
        stale_misleading_count=stale_misleading,
        post_prep_latency_ms=max(0.0, float(post_prep_latency_ms)),
        estimated_cost_usd=max(0.0, float(estimated_cost_usd)),
        cost_per_useful_artifact_usd=round(max(0.0, float(estimated_cost_usd)) / max(1, useful), 6),
        cards=rendered_cards,
    )


def build_prepared_work_benchmark_report(
    results: Sequence[PreparedWorkCaseResult],
    *,
    run_id: str | None = None,
    profile: str = "prepared_work_long_idle",
) -> PreparedWorkBenchmarkReport:
    result_list = list(results)
    case_count = len(result_list)
    stale_total = sum(result.stale_misleading_count for result in result_list)
    surfaced_total = sum(result.surfaced_count for result in result_list)
    false_positive_total = sum(result.false_positive_count for result in result_list)
    useful_total = sum(result.useful_count for result in result_list)
    cost_total = sum(result.estimated_cost_usd for result in result_list)
    return PreparedWorkBenchmarkReport(
        run_id=run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        profile=profile,
        case_count=case_count,
        artifact_precision_mean=round(sum(result.artifact_precision for result in result_list) / max(1, case_count), 4),
        false_positive_rate=round(false_positive_total / max(1, surfaced_total), 4),
        adoption_value_mean=round(sum(result.adoption_value for result in result_list) / max(1, case_count), 4),
        groundedness_mean=round(sum(result.groundedness for result in result_list) / max(1, case_count), 4),
        stale_misleading_rate=round(stale_total / max(1, surfaced_total), 4),
        post_prep_latency_ms_mean=round(sum(result.post_prep_latency_ms for result in result_list) / max(1, case_count), 2),
        estimated_cost_usd_total=round(cost_total, 6),
        cost_per_useful_artifact_usd=round(cost_total / max(1, useful_total), 6),
        cases=result_list,
    )


def benchmark_timestamp() -> float:
    return time.time()
