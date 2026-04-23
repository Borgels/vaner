# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

from vaner.telemetry.metrics import MetricsStore


async def build_stage_payloads(metrics_store: MetricsStore) -> dict[str, dict[str, Any]]:
    quality = await metrics_store.memory_quality_snapshot()
    calibration = await metrics_store.calibration_snapshot()
    return {
        "prediction": {
            "next_prompt_top1_rate": quality.get("next_prompt_top1_rate", 0.0),
            "next_prompt_top3_rate": quality.get("next_prompt_top3_rate", 0.0),
            "next_prompt_logloss": quality.get("next_prompt_logloss", 0.0),
            "next_prompt_brier": quality.get("next_prompt_brier", 0.0),
            "confidence_conditioned_utility": quality.get("confidence_conditioned_utility", 0.0),
        },
        "calibration": {"rows": calibration},
        "draft": {
            "draft_usefulness_rate": quality.get("draft_usefulness_rate", 0.0),
            "draft_predicted_prompt_similarity_total": quality.get("draft_predicted_prompt_similarity_total", 0.0),
            "draft_evidence_overlap_total": quality.get("draft_evidence_overlap_total", 0.0),
            "draft_answer_reuse_ratio_total": quality.get("draft_answer_reuse_ratio_total", 0.0),
            "draft_directionally_correct_total": quality.get("draft_directionally_correct_total", 0.0),
        },
        "budget": {
            "budget_utilization": quality.get("budget_utilization", 0.0),
            "allocated_ms_total": quality.get("cycle_budget_allocated_ms_total", 0.0),
            "used_ms_total": quality.get("cycle_budget_used_ms_total", 0.0),
            "bucket_allocated": {
                "exploit": quality.get("bucket_budget_exploit_allocated_ms_total", 0.0),
                "hedge": quality.get("bucket_budget_hedge_allocated_ms_total", 0.0),
                "invest": quality.get("bucket_budget_invest_allocated_ms_total", 0.0),
                "no_regret": quality.get("bucket_budget_no_regret_allocated_ms_total", 0.0),
            },
            "bucket_used": {
                "exploit": quality.get("bucket_budget_exploit_used_ms_total", 0.0),
                "hedge": quality.get("bucket_budget_hedge_used_ms_total", 0.0),
                "invest": quality.get("bucket_budget_invest_used_ms_total", 0.0),
                "no_regret": quality.get("bucket_budget_no_regret_used_ms_total", 0.0),
            },
            "predictive_lead_seconds_avg": quality.get("predictive_lead_seconds_avg", 0.0),
        },
    }
