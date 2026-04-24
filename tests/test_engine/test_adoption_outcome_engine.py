# SPDX-License-Identifier: Apache-2.0
"""WS4 — Engine-level adoption-outcome flush + sweep + rollback tests (0.8.4).

Validates:

- ``record_adoption()`` queues a pending descriptor on the registry.
- The engine's ``_flush_pending_adoption_outcomes()`` drains the queue
  into the store.
- ``_sweep_pending_adoption_outcomes()`` confirms outcomes past the
  wall-clock cutoff.
- ``_reject_pending_adoptions_for_predictions()`` batch-rejects on
  rollback signals.
- The flush/sweep run unconditionally regardless of
  ``refinement.enabled`` (log accumulates from day one).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)
from vaner.intent.prediction_registry import PredictionRegistry
from vaner.store import prediction_adoption_outcomes as pao_store

pytestmark = pytest.mark.asyncio


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


def _ready_prediction(*, revision: int = 0, label: str | None = None) -> PredictedPrompt:
    label = label if label is not None else f"test-{uuid.uuid4().hex[:8]}"
    spec = PredictionSpec(
        id=prediction_id("goal", "anchor", label),
        label=label,
        description="",
        source="goal",
        anchor="anchor",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="anchor",
    )
    run = PredictionRun(weight=1.0, token_budget=1024, readiness="ready", revision=revision)
    artifacts = PredictionArtifacts(draft_answer="body.", evidence_score=0.4)
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


def _attach_registry(engine: VanerEngine, predictions: list[PredictedPrompt]) -> PredictionRegistry:
    registry = PredictionRegistry(cycle_token_pool=10_000)
    for p in predictions:
        registry._predictions[p.spec.id] = p  # noqa: SLF001
    engine._prediction_registry = registry  # noqa: SLF001
    return registry


# ---------------------------------------------------------------------------
# record_adoption queues descriptor; flush persists
# ---------------------------------------------------------------------------


async def test_record_adoption_queues_descriptor(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    pred = _ready_prediction()
    registry = _attach_registry(engine, [pred])

    registry.record_adoption(pred.spec.id)

    queued = registry._pending_adoption_descriptors  # noqa: SLF001
    assert len(queued) == 1
    assert queued[0]["prediction_id"] == pred.spec.id
    assert queued[0]["label"] == pred.spec.label


async def test_flush_drains_queue_and_persists_pending_outcomes(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    await engine.initialize()
    preds = [_ready_prediction(label=f"p{i}") for i in range(3)]
    registry = _attach_registry(engine, preds)
    for p in preds:
        registry.record_adoption(p.spec.id)

    written = await engine._flush_pending_adoption_outcomes()  # noqa: SLF001
    assert written == 3

    # Queue is now empty
    assert registry._pending_adoption_descriptors == []  # noqa: SLF001

    # Rows landed in the store
    pending = await pao_store.list_pending_outcomes(engine.store.db_path)
    assert len(pending) == 3
    assert {p.prediction_id for p in pending} == {x.spec.id for x in preds}


async def test_flush_noop_when_registry_missing(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    await engine.initialize()
    # No registry attached → should return 0.
    written = await engine._flush_pending_adoption_outcomes()  # noqa: SLF001
    assert written == 0


async def test_flush_runs_regardless_of_refinement_flag(tmp_path) -> None:
    """WS4 invariant: adoption-log writes do NOT depend on
    ``refinement.enabled``. Data accumulates from day one."""
    engine = _make_engine(tmp_path / "repo")
    await engine.initialize()
    assert engine.config.refinement.enabled is False  # default
    pred = _ready_prediction()
    registry = _attach_registry(engine, [pred])
    registry.record_adoption(pred.spec.id)

    written = await engine._flush_pending_adoption_outcomes()  # noqa: SLF001
    assert written == 1


async def test_had_kept_maturation_set_correctly_on_flush(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    await engine.initialize()
    matured = _ready_prediction(revision=2, label="matured")
    fresh = _ready_prediction(revision=0, label="fresh")
    registry = _attach_registry(engine, [matured, fresh])
    registry.record_adoption(matured.spec.id)
    registry.record_adoption(fresh.spec.id)
    await engine._flush_pending_adoption_outcomes()  # noqa: SLF001

    pending = await pao_store.list_pending_outcomes(engine.store.db_path)
    had_kept = {p.prediction_id: p.had_kept_maturation for p in pending}
    assert had_kept[matured.spec.id] is True
    assert had_kept[fresh.spec.id] is False


# ---------------------------------------------------------------------------
# Sweep: pending → confirmed after cutoff
# ---------------------------------------------------------------------------


async def test_sweep_confirms_outcomes_past_cutoff(tmp_path) -> None:
    """Outcomes adopted more than ``adoption_pending_confirm_seconds``
    ago (with no contradicting signal) flip to ``confirmed`` on the
    end-of-cycle sweep."""

    engine = _make_engine(tmp_path / "repo")
    # Shrink the confirm window for a fast test.
    engine.config.refinement.adoption_pending_confirm_seconds = 30.0
    await engine.initialize()

    pred = _ready_prediction()
    registry = _attach_registry(engine, [pred])
    registry.record_adoption(pred.spec.id)
    await engine._flush_pending_adoption_outcomes()  # noqa: SLF001

    # Manually age the persisted row so adopted_at is comfortably past
    # the cutoff (1 × 30s nominal = 30s; set adopted_at to 60s ago).
    pending_rows = await pao_store.list_pending_outcomes(engine.store.db_path)
    row = pending_rows[0]
    # Rewrite adopted_at via the generic update path (or directly via SQL)
    import aiosqlite

    async with aiosqlite.connect(engine.store.db_path) as db:
        await db.execute(
            "UPDATE prediction_adoption_outcomes SET adopted_at = ? WHERE id = ?",
            (time.time() - 120.0, row.id),
        )
        await db.commit()

    resolved = await engine._sweep_pending_adoption_outcomes()  # noqa: SLF001
    assert resolved == 1

    confirmed = await pao_store.get_outcome(engine.store.db_path, row.id)
    assert confirmed is not None
    assert confirmed.outcome == "confirmed"
    assert confirmed.resolved_at is not None


async def test_sweep_leaves_recent_outcomes_pending(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    await engine.initialize()
    pred = _ready_prediction()
    registry = _attach_registry(engine, [pred])
    registry.record_adoption(pred.spec.id)
    await engine._flush_pending_adoption_outcomes()  # noqa: SLF001

    resolved = await engine._sweep_pending_adoption_outcomes()  # noqa: SLF001
    assert resolved == 0

    pending = await pao_store.list_pending_outcomes(engine.store.db_path)
    assert len(pending) == 1


# ---------------------------------------------------------------------------
# Rejection path — rollback_kept_maturation drives adoption rejection
# ---------------------------------------------------------------------------


async def test_reject_pending_adoptions_for_predictions(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    await engine.initialize()
    a = _ready_prediction(label="A")
    b = _ready_prediction(label="B")
    c = _ready_prediction(label="C")
    registry = _attach_registry(engine, [a, b, c])
    for p in (a, b, c):
        registry.record_adoption(p.spec.id)
    await engine._flush_pending_adoption_outcomes()  # noqa: SLF001

    # Reject only A and C
    rejected = await engine._reject_pending_adoptions_for_predictions(  # noqa: SLF001
        [a.spec.id, c.spec.id], reason="maturation_rollback"
    )
    assert rejected == 2

    pending_remaining = await pao_store.list_pending_outcomes(engine.store.db_path)
    assert {p.prediction_id for p in pending_remaining} == {b.spec.id}


async def test_reject_no_op_for_empty_prediction_list(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    await engine.initialize()
    rejected = await engine._reject_pending_adoptions_for_predictions(  # noqa: SLF001
        [], reason="noop"
    )
    assert rejected == 0


# ---------------------------------------------------------------------------
# Integration: full flush + sweep + reject roundtrip
# ---------------------------------------------------------------------------


async def test_full_outcome_lifecycle_end_to_end(tmp_path) -> None:
    engine = _make_engine(tmp_path / "repo")
    engine.config.refinement.adoption_pending_confirm_seconds = 30.0
    await engine.initialize()

    good = _ready_prediction(label="good")
    bad = _ready_prediction(label="bad")
    registry = _attach_registry(engine, [good, bad])
    registry.record_adoption(good.spec.id)
    registry.record_adoption(bad.spec.id)

    # Flush
    await engine._flush_pending_adoption_outcomes()  # noqa: SLF001

    # Reject the "bad" one (simulate rollback signal)
    await engine._reject_pending_adoptions_for_predictions(  # noqa: SLF001
        [bad.spec.id], reason="reconcile_contradicted"
    )

    # Age the "good" one past the cutoff
    import aiosqlite

    async with aiosqlite.connect(engine.store.db_path) as db:
        await db.execute(
            "UPDATE prediction_adoption_outcomes SET adopted_at = ? WHERE prediction_id = ? AND outcome = 'pending'",
            (time.time() - 120.0, good.spec.id),
        )
        await db.commit()

    # Sweep
    resolved = await engine._sweep_pending_adoption_outcomes()  # noqa: SLF001
    assert resolved == 1

    # Final state: good → confirmed; bad → rejected with reason
    from vaner.models.prediction_adoption_outcome import prediction_label_hash

    good_counts = await pao_store.count_by_outcome_for_label(engine.store.db_path, prediction_label_hash(good.spec.label, good.spec.anchor))
    bad_counts = await pao_store.count_by_outcome_for_label(engine.store.db_path, prediction_label_hash(bad.spec.label, bad.spec.anchor))
    assert good_counts["confirmed"] == 1
    assert good_counts["rejected"] == 0
    assert bad_counts["rejected"] == 1
    assert bad_counts["confirmed"] == 0
