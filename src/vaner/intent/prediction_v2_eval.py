# SPDX-License-Identifier: Apache-2.0
"""Promotion gates for Prediction Engine v2 shadow evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PredictionV2Metrics:
    v1_top3_action_match: float
    v2_top3_action_match: float
    v1_evidence_recall: float
    v2_evidence_recall: float
    v1_evidence_precision: float
    v2_evidence_precision: float
    false_draft_ready_rate: float
    false_evidence_ready_rate: float
    adoptable_quality_win_rate_delta: float
    wasted_precompute_rate_delta: float
    judged_quality_win_rate_delta: float
    learned_ranker_score: float | None = None
    transparent_scorer_score: float | None = None


@dataclass(frozen=True, slots=True)
class PromotionGateResult:
    passed: bool
    failed_gates: tuple[str, ...] = field(default_factory=tuple)
    learned_ranker_promotable: bool = False


def evaluate_promotion_gates(metrics: PredictionV2Metrics) -> PromotionGateResult:
    failed: list[str] = []
    if metrics.v2_top3_action_match - metrics.v1_top3_action_match < 0.15:
        failed.append("top3_action_match_delta_lt_15pp")
    if metrics.v2_evidence_recall - metrics.v1_evidence_recall < 0.20:
        failed.append("evidence_recall_delta_lt_20pp")
    if metrics.v1_evidence_precision - metrics.v2_evidence_precision > 0.05:
        failed.append("evidence_precision_regressed_gt_5pp")
    if metrics.false_draft_ready_rate >= 0.01:
        failed.append("false_draft_ready_rate_gte_1pct")
    if metrics.adoptable_quality_win_rate_delta <= 0.0:
        failed.append("adoptable_quality_not_better_than_current")
    if metrics.wasted_precompute_rate_delta > 0.10 and metrics.judged_quality_win_rate_delta < 0.05:
        failed.append("wasted_precompute_worse_without_quality_gain")

    learned_ranker_promotable = False
    if metrics.learned_ranker_score is not None and metrics.transparent_scorer_score is not None:
        learned_ranker_promotable = metrics.learned_ranker_score > metrics.transparent_scorer_score

    return PromotionGateResult(
        passed=not failed,
        failed_gates=tuple(failed),
        learned_ranker_promotable=learned_ranker_promotable,
    )
