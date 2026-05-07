# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.models.context_preparation import ContextPreparationPlan, ContextPreparationProfile, ContextToolStep


def choose_preparation_plan(profile: ContextPreparationProfile, prompt: str) -> ContextPreparationPlan:
    """Choose a bounded, deterministic context-preparation plan.

    The policy names general preparation capabilities, not benchmark or dataset
    shortcuts. Tool execution remains bounded by selector/config budgets.
    """

    need = profile.need
    if need == "multi_source_synthesis":
        return ContextPreparationPlan(
            name="multi_source_synthesis",
            need=need,
            steps=[
                ContextToolStep(tool="source_class_search", mode="canonical", budget=96),
                ContextToolStep(tool="semantic_search", mode="query_variants", budget=96),
                ContextToolStep(tool="aggregate_sources", mode="structured_provenance", budget=40),
                ContextToolStep(tool="coverage_check", mode="facets_constraints", budget=0),
            ],
        )
    if need == "scheduling":
        return ContextPreparationPlan(
            name="scheduling_evidence",
            need=need,
            steps=[
                ContextToolStep(tool="time_entity_extraction", mode="strict", budget=0),
                ContextToolStep(tool="source_class_search", mode="calendar_invite_preferred", budget=64),
                ContextToolStep(tool="semantic_search", mode="query_variants", budget=64),
                ContextToolStep(tool="conflict_scan", mode="time_evidence", budget=16),
                ContextToolStep(tool="coverage_check", mode="facets_constraints", budget=0),
            ],
        )
    if need == "implementation_support":
        return ContextPreparationPlan(
            name="implementation_support",
            need=need,
            steps=[
                ContextToolStep(tool="exact_reference_lookup", mode="paths_symbols", budget=32),
                ContextToolStep(tool="relationship_expand", mode="dependencies", budget=48),
                ContextToolStep(tool="working_set_lookup", mode="recent_activity", budget=32),
                ContextToolStep(tool="test_lookup", mode="adjacent_tests", budget=24),
                ContextToolStep(tool="risk_check", mode="regression", budget=0),
            ],
        )
    if need == "source_evidence":
        return ContextPreparationPlan(
            name="source_evidence",
            need=need,
            steps=[
                ContextToolStep(tool="exact_reference_lookup", mode="quoted_refs", budget=32),
                ContextToolStep(tool="semantic_search", mode="evidence_spans", budget=64),
                ContextToolStep(tool="source_class_search", mode="canonical", budget=48),
                ContextToolStep(tool="coverage_check", mode="provenance", budget=0),
            ],
        )
    if need == "conflict_resolution":
        return ContextPreparationPlan(
            name="conflict_resolution",
            need=need,
            steps=[
                ContextToolStep(tool="semantic_search", mode="current_and_superseded", budget=80),
                ContextToolStep(tool="source_class_search", mode="canonical", budget=64),
                ContextToolStep(tool="conflict_scan", mode="preserve_pairs", budget=24),
                ContextToolStep(tool="coverage_check", mode="conflict_pairs", budget=0),
            ],
        )
    return ContextPreparationPlan(
        name="evidence_gathering",
        need=need,
        steps=[
            ContextToolStep(tool="lexical_search", mode="balanced", budget=64),
            ContextToolStep(tool="semantic_search", mode="optional", budget=64),
            ContextToolStep(tool="coverage_check", mode="facets_constraints", budget=0),
        ],
    )


def plan_uses_tool(plan: ContextPreparationPlan, tool_name: str) -> bool:
    return any(step.tool == tool_name for step in plan.steps)


def plan_tool_names(plan: ContextPreparationPlan | None) -> set[str]:
    if plan is None:
        return set()
    return {str(step.tool) for step in plan.steps}
