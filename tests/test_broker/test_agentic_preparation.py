from __future__ import annotations

from vaner.broker.agentic_preparation import (
    build_evidence_selection_prompt,
    build_recall_planning_prompt,
    coerce_recall_plan,
    coerce_support_selection,
    promote_support_candidates,
    support_item_budget,
)
from vaner.models.agentic_preparation import EvidenceCandidate


def test_support_item_budget_keeps_direct_fact_minimal() -> None:
    assert support_item_budget("What launch code is required for the Apollo handoff?", default_max=12) == 1


def test_support_item_budget_expands_for_broad_synthesis() -> None:
    assert support_item_budget("Across all incident notes, count follow-up actions by owner.", default_max=12) == 12


def test_agentic_prompts_are_product_general() -> None:
    candidates = [
        EvidenceCandidate(
            key="source:1",
            path="notes/apollo.md",
            title="Apollo handoff",
            text="The launch code is LCH-427.",
        )
    ]

    recall = build_recall_planning_prompt("What is the launch code?", candidates)
    selection = build_evidence_selection_prompt("What is the launch code?", candidates, support_budget=1)

    assert "Vaner's bounded context preparation agent" in recall
    assert "EnterpriseRAG" not in recall
    assert "document_id" not in selection
    assert "support keys" in selection


def test_coerce_recall_plan_filters_original_prompt_and_caps_queries() -> None:
    plan = coerce_recall_plan(
        {"queries": ["What is the launch code?", "Apollo launch code", "Apollo launch code", "handoff LCH"], "reason": "x"},
        max_queries=2,
        original_prompt="What is the launch code?",
    )

    assert plan.queries == ["Apollo launch code", "handoff LCH"]


def test_coerce_support_selection_caps_and_filters_allowed_keys() -> None:
    selection = coerce_support_selection(
        {
            "support_keys": ["source:2", "source:bad", "source:1", "source:2"],
            "coverage_note": "source:2 is direct",
            "answerability": "full",
        },
        allowed_keys={"source:1", "source:2"},
        support_budget=1,
    )

    assert selection.support_keys == ["source:2"]
    assert selection.answerability == "full"


def test_promote_support_candidates_keeps_support_first() -> None:
    candidates = [
        EvidenceCandidate(key="source:1"),
        EvidenceCandidate(key="source:2"),
        EvidenceCandidate(key="source:3"),
    ]

    promoted = promote_support_candidates(candidates, ["source:3", "source:1"])

    assert [candidate.key for candidate in promoted] == ["source:3", "source:1", "source:2"]
