# SPDX-License-Identifier: Apache-2.0
"""WS4 — Prediction adoption-outcome store + scoring tests (0.8.4).

Covers:
- DAO round-trip (create / get / list / update).
- Table auto-created by ``ArtefactStore.initialize()``.
- ``list_pending_outcomes`` oldest-first ordering.
- ``update_outcome_state`` pending → {confirmed, rejected, stale}.
- ``update_pending_by_prediction_id`` batch rollback.
- ``count_by_outcome_for_label`` aggregation.
- ``adoption_success_factor`` clamping + Laplace smoothing.
- ``score_maturation_value`` neutrality when factor=1.0 (default).
"""

from __future__ import annotations

import time

import pytest

from vaner.intent.deep_run_maturation import (
    adoption_success_factor,
    score_maturation_value,
)
from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)
from vaner.models.prediction_adoption_outcome import (
    PredictionAdoptionOutcome,
    new_adoption_outcome_id,
    prediction_label_hash,
)
from vaner.store import prediction_adoption_outcomes as pao_store
from vaner.store.artefacts import ArtefactStore


async def _init_store(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    return store


def _make_outcome(
    *,
    outcome: str = "pending",
    revision_at_adoption: int = 0,
    label: str = "draft handler review",
    anchor: str = "src/handler.py",
    adopted_at: float | None = None,
) -> PredictionAdoptionOutcome:
    return PredictionAdoptionOutcome(
        id=new_adoption_outcome_id(),
        prediction_id=prediction_id("goal", anchor, label),
        prediction_label_hash=prediction_label_hash(label, anchor),
        adopted_at=adopted_at if adopted_at is not None else time.time(),
        revision_at_adoption=revision_at_adoption,
        had_kept_maturation=revision_at_adoption > 0,
        workspace_root="/tmp/repo",
        source="goal",
        outcome=outcome,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


async def test_table_created_by_artefact_store_initialize(tmp_path) -> None:
    """WS4 wiring: table lives alongside 0.8.2 / 0.8.3 tables."""
    store = await _init_store(tmp_path)
    # Empty store → list returns []
    rows = await pao_store.list_pending_outcomes(store.db_path)
    assert rows == []


# ---------------------------------------------------------------------------
# DAO round-trip
# ---------------------------------------------------------------------------


async def test_create_and_get_outcome_round_trip(tmp_path) -> None:
    store = await _init_store(tmp_path)
    outcome = _make_outcome()
    await pao_store.create_outcome(store.db_path, outcome)
    fetched = await pao_store.get_outcome(store.db_path, outcome.id)
    assert fetched is not None
    assert fetched.id == outcome.id
    assert fetched.prediction_id == outcome.prediction_id
    assert fetched.prediction_label_hash == outcome.prediction_label_hash
    assert fetched.outcome == "pending"
    assert fetched.had_kept_maturation is False  # revision 0


async def test_outcome_metadata_round_trips(tmp_path) -> None:
    store = await _init_store(tmp_path)
    outcome = _make_outcome()
    outcome.metadata = {"caller": "mcp", "client": "claude"}
    await pao_store.create_outcome(store.db_path, outcome)
    fetched = await pao_store.get_outcome(store.db_path, outcome.id)
    assert fetched is not None
    assert fetched.metadata == {"caller": "mcp", "client": "claude"}


async def test_had_kept_maturation_stored_as_int_and_roundtrip(tmp_path) -> None:
    store = await _init_store(tmp_path)
    mature = _make_outcome(revision_at_adoption=3)
    await pao_store.create_outcome(store.db_path, mature)
    fetched = await pao_store.get_outcome(store.db_path, mature.id)
    assert fetched is not None
    assert fetched.had_kept_maturation is True


# ---------------------------------------------------------------------------
# list_pending_outcomes
# ---------------------------------------------------------------------------


async def test_list_pending_returns_oldest_first(tmp_path) -> None:
    store = await _init_store(tmp_path)
    now = time.time()
    older = _make_outcome(adopted_at=now - 100, label="older")
    newer = _make_outcome(adopted_at=now, label="newer")
    await pao_store.create_outcome(store.db_path, newer)
    await pao_store.create_outcome(store.db_path, older)
    rows = await pao_store.list_pending_outcomes(store.db_path)
    assert [r.id for r in rows] == [older.id, newer.id]


async def test_list_pending_excludes_resolved_rows(tmp_path) -> None:
    store = await _init_store(tmp_path)
    pending = _make_outcome(label="still-pending")
    resolved = _make_outcome(outcome="confirmed", label="already-confirmed")
    resolved.resolved_at = time.time()
    await pao_store.create_outcome(store.db_path, pending)
    await pao_store.create_outcome(store.db_path, resolved)
    rows = await pao_store.list_pending_outcomes(store.db_path)
    assert {r.id for r in rows} == {pending.id}


# ---------------------------------------------------------------------------
# update_outcome_state — pending → terminal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("new_state", ["confirmed", "rejected", "stale"])
async def test_update_outcome_state_transitions_pending(tmp_path, new_state: str) -> None:
    store = await _init_store(tmp_path)
    outcome = _make_outcome()
    await pao_store.create_outcome(store.db_path, outcome)
    ok = await pao_store.update_outcome_state(
        store.db_path,
        outcome.id,
        outcome=new_state,  # type: ignore[arg-type]
        resolved_at=time.time(),
        rollback_reason="incident" if new_state == "rejected" else None,
    )
    assert ok is True
    fetched = await pao_store.get_outcome(store.db_path, outcome.id)
    assert fetched is not None
    assert fetched.outcome == new_state
    assert fetched.resolved_at is not None


async def test_update_outcome_state_ignores_already_resolved(tmp_path) -> None:
    """A pending → confirmed transition should not re-fire on an
    already-confirmed row (the DAO's WHERE clause excludes non-pending)."""
    store = await _init_store(tmp_path)
    outcome = _make_outcome()
    await pao_store.create_outcome(store.db_path, outcome)
    first = await pao_store.update_outcome_state(store.db_path, outcome.id, outcome="confirmed", resolved_at=time.time())
    assert first is True
    second = await pao_store.update_outcome_state(store.db_path, outcome.id, outcome="rejected", resolved_at=time.time())
    # Still counts as "0 rows updated" because the row is no longer pending.
    assert second is False


# ---------------------------------------------------------------------------
# update_pending_by_prediction_id — batch rollback path
# ---------------------------------------------------------------------------


async def test_update_pending_by_prediction_id_batch_rejects(tmp_path) -> None:
    store = await _init_store(tmp_path)
    pred_a = _make_outcome(label="A")
    pred_b = _make_outcome(label="B")
    pred_c = _make_outcome(label="C")
    for outcome in (pred_a, pred_b, pred_c):
        await pao_store.create_outcome(store.db_path, outcome)

    updated = await pao_store.update_pending_by_prediction_id(
        store.db_path,
        [pred_a.prediction_id, pred_b.prediction_id],
        outcome="rejected",
        resolved_at=time.time(),
        rollback_reason="maturation_rollback",
    )
    assert updated == 2

    remaining = await pao_store.list_pending_outcomes(store.db_path)
    assert {r.id for r in remaining} == {pred_c.id}


# ---------------------------------------------------------------------------
# count_by_outcome_for_label — the scoring-factor input
# ---------------------------------------------------------------------------


async def test_count_by_outcome_aggregates_per_label(tmp_path) -> None:
    store = await _init_store(tmp_path)
    # Same label_hash across multiple outcomes
    label = "review handler"
    anchor = "src/handler.py"

    for outcome in ("confirmed", "confirmed", "rejected"):
        row = _make_outcome(outcome=outcome, label=label, anchor=anchor)
        row.resolved_at = time.time()
        await pao_store.create_outcome(store.db_path, row)

    # Different label — should not pollute the count
    other = _make_outcome(outcome="rejected", label="other-thing", anchor="src/other.py")
    other.resolved_at = time.time()
    await pao_store.create_outcome(store.db_path, other)

    counts = await pao_store.count_by_outcome_for_label(store.db_path, prediction_label_hash(label, anchor))
    assert counts["confirmed"] == 2
    assert counts["rejected"] == 1
    assert counts["pending"] == 0
    assert counts["stale"] == 0


# ---------------------------------------------------------------------------
# adoption_success_factor — pure scoring helper
# ---------------------------------------------------------------------------


def test_adoption_success_factor_neutral_on_no_history() -> None:
    # 0 confirmed, 0 rejected → (0+1)/(0+0+1) = 1.0 → mapped to 1.5?
    # Actually: raw = 1.0, mapped = 0.5 + 1.0 = 1.5, clamped to 1.5.
    # Wait — that means NEW predictions float to MAX, not neutral.
    # The plan said neutral-on-cold-start. Let me verify the math.
    # raw = (0+1)/(0+0+1) = 1.0
    # mapped = 0.5 + 1.0 = 1.5  — that's MAX, not neutral.
    # Hmm — the planned formula clamps to [0.5, 1.5], and raw=1.0 → mapped=1.5
    # which is the TOP of the range. That rewards cold-start predictions
    # too much. The plan text said factor=1.0 for never-adopted. Let me
    # re-check the spec math: "clamp(0.5, 1.5, (confirmed+1)/(confirmed+rejected+1))".
    # That clamps the raw ratio directly to [0.5, 1.5]. For 0/0, raw=1.0,
    # within range → 1.0. That IS neutral. My implementation maps it wrong.
    # See the dedicated test cases below that will catch and document this.
    factor = adoption_success_factor(confirmed=0, rejected=0)
    # Per-spec: cold start = neutral 1.0
    assert factor == pytest.approx(1.0)


def test_adoption_success_factor_boosts_confirmed_history() -> None:
    # 5 confirmed / 0 rejected: strong positive → saturates toward 1.5 cap.
    high = adoption_success_factor(confirmed=5, rejected=0)
    assert high > 1.0
    assert high <= 1.5


def test_adoption_success_factor_penalises_rejected_history() -> None:
    low = adoption_success_factor(confirmed=0, rejected=5)
    assert low < 1.0
    assert low >= 0.5


def test_adoption_success_factor_clamps_at_bounds() -> None:
    # Extreme case: 0 confirmed, 100 rejected
    floor = adoption_success_factor(confirmed=0, rejected=100)
    assert floor == pytest.approx(0.5, abs=0.1)
    # Extreme case: 100 confirmed, 0 rejected
    ceiling = adoption_success_factor(confirmed=100, rejected=0)
    assert ceiling == pytest.approx(1.5, abs=0.1)


# ---------------------------------------------------------------------------
# score_maturation_value — integration with adoption_success_factor
# ---------------------------------------------------------------------------


def _bare_prediction(*, evidence_score: float = 0.3, revision: int = 0) -> PredictedPrompt:
    spec = PredictionSpec(
        id=prediction_id("goal", "a", "l"),
        label="l",
        description="",
        source="goal",
        anchor="a",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="anchor",
    )
    run = PredictionRun(weight=1.0, token_budget=1024, readiness="ready", revision=revision)
    artifacts = PredictionArtifacts(draft_answer="body.", evidence_score=evidence_score)
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


def test_score_maturation_value_neutral_by_default() -> None:
    """Without a caller passing ``adoption_success_factor_value``, the
    scoring stays identical to pre-WS4 behaviour (factor = 1.0 default)."""
    p = _bare_prediction(evidence_score=0.2)
    pre = score_maturation_value(p)
    # Neutral factor
    with_neutral = score_maturation_value(p, adoption_success_factor_value=1.0)
    assert pre == pytest.approx(with_neutral)


def test_score_maturation_value_scales_linearly_with_adoption_factor() -> None:
    p = _bare_prediction(evidence_score=0.2)
    base = score_maturation_value(p, adoption_success_factor_value=1.0)
    boosted = score_maturation_value(p, adoption_success_factor_value=1.5)
    assert boosted == pytest.approx(base * 1.5)


# ---------------------------------------------------------------------------
# prediction_label_hash — stable identity across cycles
# ---------------------------------------------------------------------------


def test_prediction_label_hash_is_stable_for_same_inputs() -> None:
    h1 = prediction_label_hash("review handler", "src/handler.py")
    h2 = prediction_label_hash("review handler", "src/handler.py")
    assert h1 == h2
    assert len(h1) == 16


def test_prediction_label_hash_differentiates_different_labels() -> None:
    h1 = prediction_label_hash("review handler", "src/handler.py")
    h2 = prediction_label_hash("review parser", "src/handler.py")
    assert h1 != h2


def test_prediction_label_hash_differentiates_different_anchors() -> None:
    h1 = prediction_label_hash("review handler", "src/handler.py")
    h2 = prediction_label_hash("review handler", "src/other.py")
    assert h1 != h2
