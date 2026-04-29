# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.prediction_v2_eval import PredictionV2Metrics, evaluate_promotion_gates


def test_v2_promotion_gates_pass_when_thresholds_are_met() -> None:
    result = evaluate_promotion_gates(
        PredictionV2Metrics(
            v1_top3_action_match=0.20,
            v2_top3_action_match=0.36,
            v1_evidence_recall=0.35,
            v2_evidence_recall=0.56,
            v1_evidence_precision=0.70,
            v2_evidence_precision=0.66,
            false_draft_ready_rate=0.005,
            false_evidence_ready_rate=0.08,
            adoptable_quality_win_rate_delta=0.03,
            wasted_precompute_rate_delta=0.12,
            judged_quality_win_rate_delta=0.06,
            learned_ranker_score=0.71,
            transparent_scorer_score=0.69,
        )
    )

    assert result.passed
    assert result.failed_gates == ()
    assert result.learned_ranker_promotable


def test_v2_promotion_gates_report_failed_thresholds() -> None:
    result = evaluate_promotion_gates(
        PredictionV2Metrics(
            v1_top3_action_match=0.20,
            v2_top3_action_match=0.25,
            v1_evidence_recall=0.35,
            v2_evidence_recall=0.40,
            v1_evidence_precision=0.70,
            v2_evidence_precision=0.60,
            false_draft_ready_rate=0.02,
            false_evidence_ready_rate=0.08,
            adoptable_quality_win_rate_delta=0.0,
            wasted_precompute_rate_delta=0.20,
            judged_quality_win_rate_delta=0.01,
            learned_ranker_score=0.65,
            transparent_scorer_score=0.69,
        )
    )

    assert not result.passed
    assert "top3_action_match_delta_lt_15pp" in result.failed_gates
    assert "evidence_recall_delta_lt_20pp" in result.failed_gates
    assert "evidence_precision_regressed_gt_5pp" in result.failed_gates
    assert "false_draft_ready_rate_gte_1pct" in result.failed_gates
    assert not result.learned_ranker_promotable
