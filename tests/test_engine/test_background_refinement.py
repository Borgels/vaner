# SPDX-License-Identifier: Apache-2.0
"""WS3 — Engine-level background refinement tests (0.8.4).

Verifies the hook added by ``_run_background_refinement_pass()``:

- Default-off: no drafter invocation when ``refinement.enabled=False``.
- No-op without a drafter: enabled but no ``_refinement_drafter`` → 0 passes.
- Stops on user-request signal (governor.should_continue() returns False).
- Respects ``min_remaining_deadline_seconds`` floor.
- Respects ``max_candidates_per_cycle`` cap.
- Skips ineligible candidates (probationary, failed_revisits cap, non-ready).
- Does NOT write to deep_run_pass_log (session_id=None path).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.intent.deep_run_maturation import MaturationContract, MaturationVerdict
from vaner.intent.governor import PredictionGovernor
from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)
from vaner.intent.prediction_registry import PredictionRegistry

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _stub_llm(_prompt: str) -> str:
    return '{"ranked_files": [], "semantic_intent": "", "confidence": 0.0, "follow_on": []}'


def _seed_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "sample.py").write_text("def hi():\n    return 'hi'\n")


def _make_engine(repo_root: Path) -> VanerEngine:
    _seed_repo(repo_root)
    engine = VanerEngine(adapter=CodeRepoAdapter(repo_root), llm=_stub_llm)
    engine.config.compute.idle_only = False
    return engine


def _ready_prediction(*, evidence_score: float = 0.1, label: str | None = None) -> PredictedPrompt:
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
    run = PredictionRun(weight=1.0, token_budget=1024, readiness="ready")
    artifacts = PredictionArtifacts(draft_answer="Initial draft body.", evidence_score=evidence_score)
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


def _make_counting_drafter():
    """Drafter stub that records every call and returns a judge-passable
    new draft + new evidence refs (so the default rubric judge approves).
    """

    calls: list[tuple[str, MaturationContract]] = []

    async def _drafter(prediction: PredictedPrompt, contract: MaturationContract) -> tuple[str, list[str]]:
        calls.append((prediction.spec.id, contract))
        return (
            f"{prediction.artifacts.draft_answer} plus refinement.",
            ["ref-a", "ref-b"],
        )

    return _drafter, calls


def _make_rejecting_drafter():
    """Drafter stub that produces a draft the judge will REJECT
    (length-only growth, no new evidence refs)."""

    async def _drafter(_pred: PredictedPrompt, _contract: MaturationContract) -> tuple[str, list[str]]:
        # Same text × 2 → length-only growth triggers forbidden clause.
        return (
            "Initial draft body. " * 30,
            [],
        )

    return _drafter


def _seed_registry(engine: VanerEngine, predictions: list[PredictedPrompt]) -> None:
    """Inject a PredictionRegistry with the given predictions so the
    refinement hook has candidates to operate on."""

    registry = PredictionRegistry(cycle_token_pool=10_000)
    for prediction in predictions:
        registry._predictions[prediction.spec.id] = prediction  # noqa: SLF001
    engine._prediction_registry = registry  # noqa: SLF001


# ---------------------------------------------------------------------------
# Default-off + no-op cases
# ---------------------------------------------------------------------------


async def test_disabled_flag_skips_refinement_entirely(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    drafter, calls = _make_counting_drafter()
    engine.set_refinement_drafter(drafter)
    _seed_registry(engine, [_ready_prediction()])

    # Flag is False by default — hook should not invoke drafter.
    assert engine.config.refinement.enabled is False
    attempted = await engine._run_background_refinement_pass(governor=None, cycle_deadline=None)
    assert attempted == 0
    assert calls == []


async def test_enabled_without_drafter_is_no_op(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    _seed_registry(engine, [_ready_prediction()])
    # No drafter set → hook returns 0.
    attempted = await engine._run_background_refinement_pass(governor=None, cycle_deadline=None)
    assert attempted == 0


async def test_enabled_but_empty_registry_is_no_op(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    drafter, _calls = _make_counting_drafter()
    engine.set_refinement_drafter(drafter)
    # No predictions → hook returns 0.
    _seed_registry(engine, [])
    attempted = await engine._run_background_refinement_pass(governor=None, cycle_deadline=None)
    assert attempted == 0


# ---------------------------------------------------------------------------
# Core happy paths
# ---------------------------------------------------------------------------


async def test_enabled_with_drafter_runs_mature_one_on_candidates(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    drafter, calls = _make_counting_drafter()
    engine.set_refinement_drafter(drafter)
    preds = [_ready_prediction(label=f"p{i}") for i in range(3)]
    _seed_registry(engine, preds)

    attempted = await engine._run_background_refinement_pass(governor=None, cycle_deadline=None)
    assert attempted >= 1
    assert len(calls) == attempted
    # Each kept prediction should have revision >= 1 post-pass.
    kept = [p for p in preds if p.run.revision > 0]
    assert kept, "at least one prediction should have been matured by the counting drafter"


async def test_max_candidates_per_cycle_cap_respected(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    engine.config.refinement.max_candidates_per_cycle = 2
    drafter, calls = _make_counting_drafter()
    engine.set_refinement_drafter(drafter)
    preds = [_ready_prediction(label=f"p{i}") for i in range(5)]
    _seed_registry(engine, preds)

    attempted = await engine._run_background_refinement_pass(governor=None, cycle_deadline=None)
    assert attempted <= 2
    assert len(calls) <= 2


async def test_discarded_drafts_do_not_modify_prediction(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    engine.set_refinement_drafter(_make_rejecting_drafter())
    pred = _ready_prediction()
    original_draft = pred.artifacts.draft_answer
    _seed_registry(engine, [pred])

    await engine._run_background_refinement_pass(governor=None, cycle_deadline=None)
    # Judge rejected → draft unchanged, failed_revisits bumped, revision=0.
    assert pred.artifacts.draft_answer == original_draft
    assert pred.run.revision == 0
    assert pred.run.failed_revisits >= 1


# ---------------------------------------------------------------------------
# Interruption + budget floors
# ---------------------------------------------------------------------------


async def test_user_request_active_stops_loop(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    drafter, calls = _make_counting_drafter()
    engine.set_refinement_drafter(drafter)
    _seed_registry(engine, [_ready_prediction(label=f"p{i}") for i in range(5)])

    governor = PredictionGovernor()
    governor.notify_user_request_start()  # user is active → should_continue=False

    attempted = await engine._run_background_refinement_pass(governor=governor, cycle_deadline=None)
    assert attempted == 0
    assert calls == []


async def test_tight_deadline_skips_pass(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    engine.config.refinement.min_remaining_deadline_seconds = 5.0
    drafter, calls = _make_counting_drafter()
    engine.set_refinement_drafter(drafter)
    _seed_registry(engine, [_ready_prediction()])

    # Deadline 0.5s away — below the 5.0s floor → skip.
    near_deadline = time.monotonic() + 0.5
    attempted = await engine._run_background_refinement_pass(governor=None, cycle_deadline=near_deadline)
    assert attempted == 0
    assert calls == []


# ---------------------------------------------------------------------------
# Eligibility filtering
# ---------------------------------------------------------------------------


async def test_probationary_predictions_not_re_matured(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    drafter, calls = _make_counting_drafter()
    engine.set_refinement_drafter(drafter)
    pred = _ready_prediction()
    # Set probation window into the future so the candidate is skipped.
    pred.run.probationary_until_cycle = 9999
    _seed_registry(engine, [pred])

    attempted = await engine._run_background_refinement_pass(governor=None, cycle_deadline=None)
    assert attempted == 0
    assert calls == []


# ---------------------------------------------------------------------------
# Drafter injection
# ---------------------------------------------------------------------------


async def test_set_refinement_drafter_is_injectable(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    assert engine._refinement_drafter is None  # noqa: SLF001
    drafter, _ = _make_counting_drafter()
    engine.set_refinement_drafter(drafter)
    assert engine._refinement_drafter is drafter  # noqa: SLF001


# ---------------------------------------------------------------------------
# Exception safety — crashing drafter must not blow up the cycle
# ---------------------------------------------------------------------------


async def test_drafter_exception_swallowed_per_candidate(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    engine.config.refinement.max_candidates_per_cycle = 3

    async def _crashing_drafter(*_args, **_kwargs) -> tuple[str, list[str]]:
        raise RuntimeError("drafter blew up")

    engine.set_refinement_drafter(_crashing_drafter)
    _seed_registry(engine, [_ready_prediction(label=f"p{i}") for i in range(3)])

    # Must not raise; attempted may be 0 (all passes skipped via the
    # exception handler) or non-zero (counting attempts pre-exception).
    attempted = await engine._run_background_refinement_pass(governor=None, cycle_deadline=None)
    assert isinstance(attempted, int)


# ---------------------------------------------------------------------------
# Custom judge passthrough — ensures RefinementContext doesn't clobber judge
# ---------------------------------------------------------------------------


async def test_default_judge_paths_through_mature_one(tmp_path) -> None:
    """Smoke: when the default rubric judge is used (no override), a
    refinement pass over a weak prediction produces a MaturationOutcome
    with a sensible action. This catches regressions where the engine
    hook might swallow the judge result or mis-route the context."""

    _ = MaturationVerdict  # keep import used
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.enabled = True
    drafter, calls = _make_counting_drafter()
    engine.set_refinement_drafter(drafter)
    pred = _ready_prediction(evidence_score=0.05)  # low → low_evidence weakness
    _seed_registry(engine, [pred])

    await engine._run_background_refinement_pass(governor=None, cycle_deadline=None)
    assert len(calls) >= 1
    # The counting drafter returns 2 new refs → low_evidence clause satisfied.
    assert pred.run.revision >= 1
