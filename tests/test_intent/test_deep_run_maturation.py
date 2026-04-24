# SPDX-License-Identifier: Apache-2.0
"""WS3 — Deep-Run maturation tests (0.8.3).

Coverage: contract builder, default rubric judge (skeptical-default
discipline), candidate selection (eligibility + ranking +
diminishing-returns + probation), mature_one orchestrator (kept
path, discarded path, evidence-score increment, probation arming),
rollback hook.
"""

from __future__ import annotations

import time
import uuid

import pytest

from vaner.intent.deep_run import DeepRunSession
from vaner.intent.deep_run_maturation import (
    MaturationContract,
    MaturationVerdict,
    RefinementContext,
    build_contract,
    default_rubric_judge,
    mature_one,
    rollback_kept_maturation,
    score_maturation_value,
    select_maturation_candidates,
)
from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session(**overrides: object) -> DeepRunSession:
    defaults: dict[str, object] = {
        "ends_at": time.time() + 3600,
        "preset": "balanced",
        "focus": "active_goals",
        "horizon_bias": "balanced",
        "locality": "local_preferred",
        "cost_cap_usd": 0.0,
        "workspace_root": "/tmp/repo",
    }
    defaults.update(overrides)
    return DeepRunSession.new(**defaults)  # type: ignore[arg-type]


def _ctx(cycle_index: int = 10, **session_overrides: object) -> RefinementContext:
    """Helper: build a RefinementContext equivalent to
    ``RefinementContext.from_deep_run_session(session, cycle_index=cycle_index)``
    for tests that exercise Deep-Run semantics. Test-only convenience;
    production code constructs contexts via the two class methods."""

    return RefinementContext.from_deep_run_session(_session(**session_overrides), cycle_index=cycle_index)


def _prediction(
    *,
    draft: str | None = "Initial draft.",
    evidence_score: float = 0.3,
    readiness: str = "ready",
    revision: int = 0,
    failed_revisits: int = 0,
    probationary_until_cycle: int | None = None,
    maturation_eligible: bool = True,
    label: str | None = None,
) -> PredictedPrompt:
    # Default label is uuid-suffixed so two predictions built in the
    # same test get distinct ids (prediction_id hashes source+anchor+label).
    label = label if label is not None else f"test-{uuid.uuid4().hex[:8]}"
    spec = PredictionSpec(
        id=prediction_id("history", "anchor", label),
        label=label,
        description="",
        source="history",
        anchor="anchor",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="anchor",
    )
    run = PredictionRun(
        weight=1.0,
        token_budget=1024,
        readiness=readiness,  # type: ignore[arg-type]
        revision=revision,
        failed_revisits=failed_revisits,
        probationary_until_cycle=probationary_until_cycle,
        maturation_eligible=maturation_eligible,
    )
    artifacts = PredictionArtifacts(draft_answer=draft, evidence_score=evidence_score)
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------


def test_contract_low_evidence_demands_two_new_refs() -> None:
    pred = _prediction(evidence_score=0.10)
    contract = build_contract(pred, pass_id="p1")
    assert contract.target_weakness == "low_evidence"
    assert contract.required_new_evidence_refs == 2


def test_contract_shallow_draft_demands_paragraph_and_ref() -> None:
    pred = _prediction(draft="short.", evidence_score=0.9)
    contract = build_contract(pred, pass_id="p1")
    assert contract.target_weakness == "shallow_draft"
    keys = [c.key for c in contract.must_clauses]
    assert "add_substantive_paragraph_min_80_words" in keys
    assert "new_evidence_refs_min_1" in keys


def test_contract_universally_forbids_length_only_growth() -> None:
    pred = _prediction()
    contract = build_contract(pred, pass_id="p1")
    keys = [c.key for c in contract.forbidden_clauses]
    assert "no_length_only_growth" in keys
    assert "no_evidence_ref_removal" in keys
    assert "anchor_preserved" in keys


# ---------------------------------------------------------------------------
# Default rubric judge — skeptical-default discipline
# ---------------------------------------------------------------------------


async def test_judge_rejects_empty_new_draft() -> None:
    pred = _prediction()
    contract = build_contract(pred, pass_id="p1")
    verdict = await default_rubric_judge(
        prediction=pred,
        contract=contract,
        old_draft=pred.artifacts.draft_answer,
        new_draft="   ",
        new_evidence_refs=["a", "b"],
    )
    assert verdict.kept is False
    assert verdict.failed_clause == "empty_new_draft"


async def test_judge_rejects_length_only_growth_with_no_new_refs() -> None:
    pred = _prediction(draft="A short draft.", evidence_score=0.9)
    contract = build_contract(pred, pass_id="p1")
    new = (
        "A much longer draft that simply adds many more words, repeats existing "
        "content, restates the original sentence, and pads with synonyms and "
        "filler phrases without adding any new evidence whatsoever."
    )
    verdict = await default_rubric_judge(
        prediction=pred,
        contract=contract,
        old_draft=pred.artifacts.draft_answer,
        new_draft=new,
        new_evidence_refs=[],
    )
    assert verdict.kept is False
    assert verdict.failed_clause == "no_length_only_growth"


async def test_judge_accepts_when_low_evidence_clause_satisfied() -> None:
    pred = _prediction(evidence_score=0.10, draft="initial body.")
    contract = build_contract(pred, pass_id="p1")
    verdict = await default_rubric_judge(
        prediction=pred,
        contract=contract,
        old_draft=pred.artifacts.draft_answer,
        new_draft="initial body. plus refinement.",
        new_evidence_refs=["ref-a", "ref-b"],
    )
    assert verdict.kept is True
    assert "new_evidence_refs_min_2" in verdict.satisfied_clauses


async def test_judge_rejects_low_evidence_when_too_few_new_refs() -> None:
    pred = _prediction(evidence_score=0.10)
    contract = build_contract(pred, pass_id="p1")
    verdict = await default_rubric_judge(
        prediction=pred,
        contract=contract,
        old_draft=pred.artifacts.draft_answer,
        new_draft="something different",
        new_evidence_refs=["ref-a"],  # only 1, need 2
    )
    assert verdict.kept is False
    assert verdict.failed_clause == "new_evidence_refs_min_2"


async def test_judge_accepts_shallow_draft_with_substantive_paragraph_and_ref() -> None:
    pred = _prediction(draft="terse.", evidence_score=0.9)  # high evidence forces shallow_draft target
    contract = build_contract(pred, pass_id="p1")
    new_paragraph = " ".join(["substantive"] * 90)
    new_draft = f"terse.\n\n{new_paragraph}"
    verdict = await default_rubric_judge(
        prediction=pred,
        contract=contract,
        old_draft=pred.artifacts.draft_answer,
        new_draft=new_draft,
        new_evidence_refs=["ref-x"],
    )
    assert verdict.kept is True


async def test_judge_rejects_shallow_draft_with_only_paraphrase() -> None:
    pred = _prediction(draft="terse content.", evidence_score=0.9)
    contract = build_contract(pred, pass_id="p1")
    new_draft = "TERSE Content."  # paraphrase / case-only
    verdict = await default_rubric_judge(
        prediction=pred,
        contract=contract,
        old_draft=pred.artifacts.draft_answer,
        new_draft=new_draft,
        new_evidence_refs=["ref-x"],
    )
    assert verdict.kept is False
    assert verdict.failed_clause == "add_substantive_paragraph_min_80_words"


# ---------------------------------------------------------------------------
# score_maturation_value
# ---------------------------------------------------------------------------


def test_maturation_value_diminishes_with_revision() -> None:
    base = _prediction(revision=0, evidence_score=0.2)
    later = _prediction(revision=3, evidence_score=0.2)
    assert score_maturation_value(base) > score_maturation_value(later)


def test_maturation_value_higher_when_evidence_lower() -> None:
    weak = _prediction(evidence_score=0.1)
    strong = _prediction(evidence_score=0.9)
    assert score_maturation_value(weak) > score_maturation_value(strong)


def test_maturation_value_pending_state_outranks_complete() -> None:
    p = _prediction(evidence_score=0.4)
    assert score_maturation_value(p, item_state="pending") > score_maturation_value(p, item_state="complete")


def test_maturation_value_scales_with_goal_confidence_and_alignment() -> None:
    p = _prediction()
    low = score_maturation_value(p, goal_confidence=0.2, artefact_alignment_score=0.5)
    high = score_maturation_value(p, goal_confidence=0.9, artefact_alignment_score=2.0)
    assert high > low


# ---------------------------------------------------------------------------
# select_maturation_candidates — eligibility + ranking
# ---------------------------------------------------------------------------


def test_selection_excludes_non_ready_predictions() -> None:
    drafting = _prediction(readiness="drafting")
    ready = _prediction(readiness="ready")
    candidates = select_maturation_candidates([drafting, ready], context=_ctx(cycle_index=10), max_candidates=10)
    eligible_ids = {c.prediction.spec.id for c in candidates if c.eligible}
    assert ready.spec.id in eligible_ids
    assert drafting.spec.id not in eligible_ids


def test_selection_excludes_probationary_predictions() -> None:
    p = _prediction(probationary_until_cycle=12)
    candidates = select_maturation_candidates([p], context=_ctx(cycle_index=10), max_candidates=10)
    eligible = [c for c in candidates if c.eligible]
    assert eligible == []
    skipped = [c for c in candidates if not c.eligible]
    assert "probationary" in (skipped[0].skip_reason or "")


def test_selection_admits_after_probation_window_closes() -> None:
    p = _prediction(probationary_until_cycle=8)
    candidates = select_maturation_candidates([p], context=_ctx(cycle_index=10), max_candidates=10)
    eligible = [c for c in candidates if c.eligible]
    assert len(eligible) == 1


def test_selection_excludes_at_revision_cap() -> None:
    """Balanced preset has max_revisits_per_prediction=4; a prediction
    with revision=4 must be excluded from further maturation."""
    p = _prediction(revision=4)
    candidates = select_maturation_candidates([p], context=_ctx(cycle_index=10, preset="balanced"), max_candidates=10)
    assert all(not c.eligible for c in candidates)


def test_selection_excludes_at_failure_cap() -> None:
    p = _prediction(failed_revisits=4)
    candidates = select_maturation_candidates([p], context=_ctx(cycle_index=10, preset="balanced"), max_candidates=10)
    assert all(not c.eligible for c in candidates)


def test_selection_respects_maturation_eligible_opt_out() -> None:
    p = _prediction(maturation_eligible=False)
    candidates = select_maturation_candidates([p], context=_ctx(cycle_index=10), max_candidates=10)
    assert all(not c.eligible for c in candidates)


def test_selection_orders_by_score_descending() -> None:
    weak = _prediction(evidence_score=0.1, label="weak")
    strong = _prediction(evidence_score=0.8, label="strong")
    candidates = select_maturation_candidates([strong, weak], context=_ctx(cycle_index=10), max_candidates=10)
    eligible = [c for c in candidates if c.eligible]
    assert [c.prediction.spec.label for c in eligible] == ["weak", "strong"]


def test_selection_caps_at_max_candidates() -> None:
    preds = [_prediction(label=f"p{i}", evidence_score=0.1 * i) for i in range(10)]
    candidates = select_maturation_candidates(preds, context=_ctx(cycle_index=10), max_candidates=3)
    eligible = [c for c in candidates if c.eligible]
    assert len(eligible) == 3


def test_aggressive_preset_allows_more_revisits_than_conservative() -> None:
    """Aggressive max_revisits=8, Conservative=2. Same prediction at
    revision=4 should be eligible under Aggressive but not Conservative."""
    p = _prediction(revision=4)
    cons = select_maturation_candidates([p], context=_ctx(cycle_index=10, preset="conservative"), max_candidates=10)
    agg = select_maturation_candidates([p], context=_ctx(cycle_index=10, preset="aggressive"), max_candidates=10)
    assert all(not c.eligible for c in cons)
    assert any(c.eligible for c in agg)


# ---------------------------------------------------------------------------
# mature_one — kept path, discarded path, probation arming, rollback
# ---------------------------------------------------------------------------


async def _drafter_returning(text: str, refs: list[str]):
    async def _stub(_pred, _contract):
        return text, refs

    return _stub


async def test_mature_one_kept_path_persists_and_arms_probation() -> None:
    pred = _prediction(draft="initial draft", evidence_score=0.10, revision=0)
    session = _session()
    drafter = await _drafter_returning("initial draft. with refinement.", ["ref-a", "ref-b"])
    outcome = await mature_one(
        pred,
        context=RefinementContext.from_deep_run_session(session, cycle_index=5),
        drafter=drafter,
        pass_id="p1",
    )
    assert outcome.action == "matured_kept"
    assert outcome.verdict.kept is True
    assert pred.artifacts.draft_answer == "initial draft. with refinement."
    assert pred.run.revision == 1
    assert pred.run.last_matured_cycle == 5
    assert pred.run.probationary_until_cycle == 5 + 3  # _PROBATION_CYCLES
    assert pred.run.failed_revisits == 0


async def test_mature_one_discarded_path_keeps_old_draft_and_increments_failures() -> None:
    pred = _prediction(draft="initial draft", evidence_score=0.10, revision=0)
    session = _session()
    drafter = await _drafter_returning("initial draft. plus refinement.", [])
    outcome = await mature_one(
        pred,
        context=RefinementContext.from_deep_run_session(session, cycle_index=5),
        drafter=drafter,
        pass_id="p1",
    )
    assert outcome.action == "matured_discarded"
    assert outcome.verdict.kept is False
    assert pred.artifacts.draft_answer == "initial draft"
    assert pred.run.revision == 0
    assert pred.run.failed_revisits == 1
    assert pred.run.probationary_until_cycle is None


async def test_mature_one_kept_resets_failed_revisits_counter() -> None:
    pred = _prediction(failed_revisits=2, evidence_score=0.10)
    session = _session()
    drafter = await _drafter_returning(pred.artifacts.draft_answer + " plus refinement.", ["a", "b"])
    await mature_one(
        pred,
        context=RefinementContext.from_deep_run_session(session, cycle_index=1),
        drafter=drafter,
        pass_id="px",
    )
    assert pred.run.failed_revisits == 0
    assert pred.run.revision == 1


async def test_mature_one_uses_custom_judge() -> None:
    pred = _prediction()

    async def _always_kept_judge(**kwargs) -> MaturationVerdict:
        return MaturationVerdict(
            kept=True,
            satisfied_clauses=("custom",),
            failed_clause=None,
            reason="custom judge",
        )

    drafter = await _drafter_returning("anything", [])
    outcome = await mature_one(
        pred,
        context=_ctx(cycle_index=1),
        drafter=drafter,
        judge=_always_kept_judge,
        pass_id="p",
    )
    assert outcome.verdict.kept is True
    assert "custom" in outcome.verdict.satisfied_clauses


# ---------------------------------------------------------------------------
# Rollback hook
# ---------------------------------------------------------------------------


def test_rollback_restores_prior_draft_and_decrements_revision() -> None:
    pred = _prediction(
        draft="matured-draft",
        evidence_score=0.6,
        revision=2,
        failed_revisits=0,
        probationary_until_cycle=12,
    )
    rollback_kept_maturation(
        pred,
        rollback_to_draft="prior-draft",
        rollback_to_evidence_score=0.4,
        cycle_index=11,
    )
    assert pred.artifacts.draft_answer == "prior-draft"
    assert pred.artifacts.evidence_score == pytest.approx(0.4)
    assert pred.run.revision == 1
    assert pred.run.failed_revisits == 1
    assert pred.run.probationary_until_cycle is None
    assert pred.run.last_matured_cycle == 11


def test_rollback_floors_revision_at_zero() -> None:
    pred = _prediction(revision=0)
    rollback_kept_maturation(pred, rollback_to_draft=None, rollback_to_evidence_score=0.0, cycle_index=1)
    assert pred.run.revision == 0


# ---------------------------------------------------------------------------
# MaturationContract / MaturationVerdict are immutable dataclasses
# ---------------------------------------------------------------------------


def test_contract_is_frozen() -> None:
    contract = MaturationContract(
        pass_id="p",
        target_weakness="low_evidence",
        must_clauses=(),
        forbidden_clauses=(),
    )
    with pytest.raises((AttributeError, TypeError)):
        contract.pass_id = "other"  # type: ignore[misc]


def test_verdict_is_frozen() -> None:
    verdict = MaturationVerdict(kept=True, satisfied_clauses=(), failed_clause=None, reason="")
    with pytest.raises((AttributeError, TypeError)):
        verdict.kept = False  # type: ignore[misc]
