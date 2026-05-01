# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)
from vaner.intent.prepared_work import build_prepared_work_cards, build_work_product_inspection
from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductEvidenceRef,
    WorkProductFreshness,
    WorkProductSelfEval,
    WorkProductSourceSnapshot,
    WorkProductStatus,
    WorkProductType,
)


def _product(
    *,
    product_id: str = "wp-1",
    product_type: WorkProductType = WorkProductType.REVIEW_NOTE,
    title: str = "Review note",
    summary: str = "Check the parser edge case.",
    path: str = "src/parser.py",
    confidence: float = 0.8,
    adoptability: WorkProductAdoptability = WorkProductAdoptability.INSPECTABLE,
    status: WorkProductStatus = WorkProductStatus.SURFACED,
    updated_at: float = 100.0,
    stale_risk: float = 0.0,
) -> WorkProduct:
    return WorkProduct(
        id=product_id,
        type=product_type,
        title=title,
        summary=summary,
        body="Prepared body",
        evidence_refs=[WorkProductEvidenceRef(kind="file", path=path, reason="source evidence")],
        source_snapshot=WorkProductSourceSnapshot(
            project_id="test",
            relative_paths=[path],
            file_hashes={path: "abc"},
            generated_at=updated_at,
        ),
        confidence=confidence,
        freshness=WorkProductFreshness.FRESH,
        status=status,
        adoptability=adoptability,
        self_eval=WorkProductSelfEval(
            evidence_coverage=0.8,
            groundedness=0.8,
            stale_risk=stale_risk,
            reason="recent edits and direct source evidence match this target",
        ),
        created_at=updated_at,
        updated_at=updated_at,
        target_key=path,
    )


def _prediction(
    *,
    label: str = "Continue parser refactor",
    anchor: str = "src/parser.py",
    confidence: float = 0.95,
    updated_at: float = 100.0,
) -> PredictedPrompt:
    spec = PredictionSpec(
        id=prediction_id("arc", anchor, label),
        label=label,
        description="Likely next coding step",
        source="arc",
        anchor=anchor,
        confidence=confidence,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=updated_at,
    )
    run = PredictionRun(
        weight=0.7,
        token_budget=2048,
        scenarios_spawned=2,
        scenarios_complete=2,
        readiness="ready",
        updated_at=updated_at,
    )
    artifacts = PredictionArtifacts(prepared_briefing="Grounded prepared context.", scenario_ids=["s1", "s2"])
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


def test_prepared_work_hides_internal_lifecycle_fields() -> None:
    cards = build_prepared_work_cards(work_products=[_product()], predictions=[], now=100.0)

    assert len(cards) == 1
    payload = cards[0].model_dump(mode="json")
    assert payload["source_type"] == "work_product"
    assert payload["primary_action"]["kind"] == "inspect"
    assert [action["arguments"].get("feedback_state") for action in payload["secondary_actions"][:2]] == ["useful", "not_useful"]
    assert "status" not in payload
    assert "adoptability" not in payload
    assert "self_eval" not in payload
    assert "score" not in payload
    assert payload["diagnostic_refs"] == []


def test_diagnostics_are_opt_in() -> None:
    product = _product()

    without = build_prepared_work_cards(work_products=[product], predictions=[], include_diagnostics=False, now=100.0)
    with_diagnostics = build_prepared_work_cards(
        work_products=[product],
        predictions=[],
        include_diagnostics=True,
        now=100.0,
    )

    assert without[0].diagnostic_refs == []
    assert with_diagnostics[0].diagnostic_refs


def test_inspection_is_user_facing_and_export_preview_is_gated() -> None:
    stale = _product(adoptability=WorkProductAdoptability.EXPORTABLE)
    stale.freshness = WorkProductFreshness.STALE
    stale.adoptability = WorkProductAdoptability.INSPECTABLE

    inspected = build_work_product_inspection(stale, now=100.0).model_dump(mode="json")

    assert inspected["source_type"] == "work_product"
    assert inspected["why_prepared"]
    assert inspected["warnings"]
    assert inspected["export_preview"] == ""
    assert all(action["kind"] != "export" for action in inspected["allowed_actions"])
    assert "adoptability" not in inspected
    assert "self_eval" not in inspected


def test_relevant_ready_prediction_can_rank_above_older_exportable_work_product() -> None:
    old_docs = _product(
        product_id="docs",
        product_type=WorkProductType.DOCS_DRIFT,
        title="Update docs",
        summary="Docs mention old parser behavior.",
        path="docs/parser.md",
        confidence=0.7,
        adoptability=WorkProductAdoptability.EXPORTABLE,
        updated_at=0.0,
        stale_risk=0.15,
    )
    prediction = _prediction(updated_at=100.0)

    cards = build_prepared_work_cards(
        work_products=[old_docs],
        predictions=[prediction],
        context_id="src parser",
        now=100.0,
    )

    assert cards[0].source_type == "prediction"
    assert cards[0].primary_action is not None
    assert cards[0].primary_action.kind == "adopt"


def test_advisory_items_are_hidden_by_default_but_can_be_requested() -> None:
    advisory = _product(
        product_id="maybe",
        adoptability=WorkProductAdoptability.ADVISORY,
        title="Possible follow-up",
        path="src/other.py",
    )
    strong = _product(product_id="strong", adoptability=WorkProductAdoptability.INSPECTABLE, title="Strong note")

    default_cards = build_prepared_work_cards(work_products=[advisory, strong], predictions=[], now=100.0)
    requested_cards = build_prepared_work_cards(
        work_products=[advisory, strong],
        predictions=[],
        include_advisory=True,
        now=100.0,
    )

    assert [card.source_id for card in default_cards] == ["strong"]
    assert {card.source_id for card in requested_cards} == {"maybe", "strong"}


def test_advisory_item_can_surface_when_no_stronger_item_exists() -> None:
    advisory = _product(
        product_id="only-advisory",
        adoptability=WorkProductAdoptability.ADVISORY,
        title="Possible follow-up",
        path="src/other.py",
    )

    cards = build_prepared_work_cards(work_products=[advisory], predictions=[], now=100.0)

    assert [card.source_id for card in cards] == ["only-advisory"]


def test_weak_candidate_artifacts_do_not_surface() -> None:
    weak = _product(product_id="weak", confidence=0.4)
    weak.evidence_refs = []

    cards = build_prepared_work_cards(work_products=[weak], predictions=[], include_advisory=True, now=100.0)

    assert cards == []


def test_terminal_and_hidden_items_do_not_surface() -> None:
    cards = build_prepared_work_cards(
        work_products=[
            _product(product_id="dismissed", status=WorkProductStatus.DISMISSED),
            _product(product_id="hidden", adoptability=WorkProductAdoptability.HIDDEN),
        ],
        predictions=[],
        now=100.0,
    )

    assert cards == []
