# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.eval_prepared_work import (
    PreparedWorkBenchmarkCase,
    build_prepared_work_benchmark_report,
    score_prepared_work_case,
)
from vaner.models.prepared_work import (
    PreparedWorkAction,
    PreparedWorkActionKind,
    PreparedWorkCard,
    PreparedWorkKind,
    PreparedWorkSourceType,
)


def _card(
    *,
    kind: PreparedWorkKind = PreparedWorkKind.DIFF,
    target: str = "src/parser.py",
    evidence_count: int = 2,
    action: PreparedWorkActionKind = PreparedWorkActionKind.EXPORT,
    freshness_state: str = "fresh",
) -> PreparedWorkCard:
    return PreparedWorkCard(
        id="work_product:wp",
        source_id="wp",
        source_type=PreparedWorkSourceType.WORK_PRODUCT,
        kind=kind,
        title="Prepared fix",
        summary="Patch is ready.",
        badge="Diff",
        confidence_label="High",
        freshness_label="Fresh",
        freshness_state=freshness_state,
        target_label=target,
        why_prepared="Direct source evidence matched the target.",
        action_note="Export returns a diff only.",
        evidence_count=evidence_count,
        created_at=1.0,
        updated_at=2.0,
        primary_action=PreparedWorkAction(kind=action, label="Export", endpoint="/work-products/wp/export"),
    )


def test_prepared_work_benchmark_scores_precision_and_value() -> None:
    case = PreparedWorkBenchmarkCase(
        case_id="dev-diff",
        archetype="developer",
        prompt="Fix parser bug",
        expected_kinds=["diff"],
        expected_targets=["src/parser.py"],
        idle_seconds=300,
    )

    result = score_prepared_work_case(case, [_card()], post_prep_latency_ms=42.0, estimated_cost_usd=0.01)
    report = build_prepared_work_benchmark_report([result], run_id="run")

    assert result.artifact_precision == 1.0
    assert result.adoption_value > 0.9
    assert result.groundedness == 1.0
    assert report.artifact_precision_mean == 1.0
    assert report.estimated_cost_usd_total == 0.01


def test_prepared_work_benchmark_penalizes_stale_exportable_artifacts() -> None:
    case = PreparedWorkBenchmarkCase(case_id="stale", archetype="developer", prompt="Review stale diff")

    result = score_prepared_work_case(case, [_card(freshness_state="stale")])
    report = build_prepared_work_benchmark_report([result], run_id="run")

    assert result.stale_misleading_count == 1
    assert report.stale_misleading_rate == 1.0
