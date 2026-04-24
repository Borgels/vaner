# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS3 — reconciliation tests.

Covers the matchers in :mod:`vaner.intent.reconcile`, the persisted
``ReconciliationOutcome`` contract, the ``progress_reconciled`` signal
shape, and the ``PredictionRegistry.apply_item_state_delta`` effects
on artefact-item-anchored predictions.
"""

from __future__ import annotations

import json

from vaner.intent.adapter import RawArtefact
from vaner.intent.ingest.pipeline import ingest_artefact
from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_registry import PredictionRegistry
from vaner.intent.reconcile import (
    ReconcileContext,
    _aggregate_unfinished_state,
    _freshness_from_signal,
    _reconciliation_state_from_deltas,
    _target_state_for_commit_match,
    _target_state_for_file_touch,
    _token_overlap,
    reconcile_artefact,
)
from vaner.store.artefacts import ArtefactStore

# The async reconciliation tests set the asyncio mark per-function via
# the fixture; the sync state-machine tests in the first block need no
# marker. A module-level ``pytestmark = pytest.mark.asyncio`` would
# attach the marker to every function, which pytest-asyncio warns about
# on sync tests.


# -------------------------------------------------------------------------
# Matcher state-machine rules
# -------------------------------------------------------------------------


def test_file_touch_transitions_pending_to_in_progress() -> None:
    assert _target_state_for_file_touch("pending") == "in_progress"
    assert _target_state_for_file_touch("stalled") == "in_progress"
    assert _target_state_for_file_touch("complete") is None
    assert _target_state_for_file_touch("contradicted") is None


def test_commit_match_transitions_toward_complete() -> None:
    assert _target_state_for_commit_match("pending") == "complete"
    assert _target_state_for_commit_match("in_progress") == "complete"
    assert _target_state_for_commit_match("stalled") == "complete"
    assert _target_state_for_commit_match("complete") is None


def test_token_overlap_needs_two_distinctive_shared_tokens() -> None:
    # Both strings share "classifier" + "module" — two distinctive
    # tokens, overlap True.
    assert _token_overlap(
        "implement classifier module in src/classifier.py",
        "implement classifier module with two stages",
    )
    # Shares only "test" which is filtered as generic; no match.
    assert not _token_overlap("test the foo", "test the bar")
    # Distinct vocabularies → no match.
    assert not _token_overlap("entirely different", "something unrelated")


def test_reconciliation_state_from_deltas_selects_right_label() -> None:
    from vaner.intent.artefacts import ArtefactItemStateDelta

    def _d(from_state: str, to_state: str) -> ArtefactItemStateDelta:
        return ArtefactItemStateDelta(item_id="x", from_state=from_state, to_state=to_state)  # type: ignore[arg-type]

    assert _reconciliation_state_from_deltas([_d("pending", "contradicted")]) == "contradicted"
    assert _reconciliation_state_from_deltas([_d("in_progress", "stalled")]) == "drifted"
    assert _reconciliation_state_from_deltas([_d("pending", "complete")]) == "current"
    assert _reconciliation_state_from_deltas([]) == "current"


def test_aggregate_unfinished_state_precedence() -> None:
    rows = [
        {"state": "pending", "kind": "task"},
        {"state": "in_progress", "kind": "task"},
        {"state": "complete", "kind": "task"},
    ]
    assert _aggregate_unfinished_state(rows) == "in_progress"
    rows = [{"state": "pending", "kind": "task"}, {"state": "complete", "kind": "task"}]
    assert _aggregate_unfinished_state(rows) == "pending"
    rows = [{"state": "complete", "kind": "task"}]
    assert _aggregate_unfinished_state(rows) == "none"


def test_freshness_signal_driven_not_wall_clock() -> None:
    # Spec §6.6: freshness is signal-driven (the reconciliation pass
    # IS the signal). Fresh observations at the same time → 1.0.
    assert _freshness_from_signal(1000.0, 1000.0) == 1.0
    # 5 minutes gap degrades modestly.
    assert 0.5 < _freshness_from_signal(1000.0 + 300, 1000.0) < 1.0
    # 1-hour gap floors at 0.5.
    assert _freshness_from_signal(1000.0 + 3600, 1000.0) == 0.5


# -------------------------------------------------------------------------
# End-to-end reconciliation behaviour
# -------------------------------------------------------------------------


async def _seed_plan(store: ArtefactStore, text: str, title: str = "plan.md"):
    raw = RawArtefact(
        source_uri=f"file:///tmp/{title}",
        connector="local_plan",
        tier="T1",
        text=text,
        last_modified=0.0,
        title_hint=title,
    )
    result = await ingest_artefact(raw, store=store)
    assert result.accepted
    return result


async def test_file_change_moves_item_to_in_progress(tmp_path) -> None:
    store = ArtefactStore(tmp_path / "s.db")
    await store.initialize()
    result = await _seed_plan(
        store,
        "# Plan\n\n- [ ] update `src/foo.py`\n- [ ] unrelated task\n- [ ] another\n- [ ] one more\n",
    )
    ctx = ReconcileContext(
        artefact_id=result.artefact.id,
        triggering_signal_id="sig-1",
        changed_files=frozenset({"src/foo.py"}),
    )
    rr = await reconcile_artefact(ctx, store=store)
    assert rr is not None
    states = [d.to_state for d in rr.item_state_deltas]
    assert "in_progress" in states

    # Persisted item state matches.
    items = await store.list_intent_artefact_items(artefact_id=result.artefact.id)
    touched = next(it for it in items if "foo.py" in it["text"])
    assert touched["state"] == "in_progress"


async def test_commit_correlation_marks_complete(tmp_path) -> None:
    store = ArtefactStore(tmp_path / "s.db")
    await store.initialize()
    result = await _seed_plan(
        store,
        "# Plan\n\n- [ ] implement classifier module\n- [ ] write extractor tests\n- [ ] polish docs\n- [ ] ship it\n",
    )
    ctx = ReconcileContext(
        artefact_id=result.artefact.id,
        triggering_signal_id="sig-2",
        commit_subjects=("feat(intent): implement classifier module two-stage",),
    )
    rr = await reconcile_artefact(ctx, store=store)
    assert rr is not None
    to_states = [d.to_state for d in rr.item_state_deltas]
    assert "complete" in to_states


async def test_no_matching_signal_still_writes_outcome_and_emits(tmp_path) -> None:
    store = ArtefactStore(tmp_path / "s.db")
    await store.initialize()
    result = await _seed_plan(store, "# Plan\n\n- [ ] one\n- [ ] two\n- [ ] three\n- [ ] four\n")
    ctx = ReconcileContext(
        artefact_id=result.artefact.id,
        triggering_signal_id="sig-empty",
        changed_files=frozenset(),
        commit_subjects=(),
    )
    rr = await reconcile_artefact(ctx, store=store)
    assert rr is not None
    assert rr.item_state_deltas == []
    # Still persists an outcome row for observability.
    outcomes = await store.list_reconciliation_outcomes(artefact_id=result.artefact.id)
    assert len(outcomes) == 1


async def test_reconciliation_outcome_persistence_and_signal_payload(tmp_path) -> None:
    """Spec §10.3: outcome is authoritative persisted state; signal is
    pointer-only."""

    store = ArtefactStore(tmp_path / "s.db")
    await store.initialize()
    result = await _seed_plan(store, "# Plan\n\n- [ ] edit `src/a.py`\n- [ ] edit `src/b.py`\n- [ ] task c\n- [ ] task d\n")
    ctx = ReconcileContext(
        artefact_id=result.artefact.id,
        triggering_signal_id="sig-foo",
        changed_files=frozenset({"src/a.py"}),
    )
    rr = await reconcile_artefact(ctx, store=store)
    assert rr is not None

    # Signal is pointer-only.
    assert rr.signal is not None
    assert rr.signal.kind == "progress_reconciled"
    assert set(rr.signal.payload.keys()) == {"outcome_id", "artefact_id"}

    # Outcome row is fetchable and carries the deltas.
    outcome = await store.get_reconciliation_outcome(rr.outcome_id)
    assert outcome is not None
    deltas = json.loads(str(outcome["item_state_deltas_json"]))
    assert len(deltas) == 1


async def test_user_declared_goal_not_auto_touched_by_reconciliation(tmp_path) -> None:
    """Spec §10.4 safety valve."""

    store = ArtefactStore(tmp_path / "s.db")
    await store.initialize()
    # Declare a user goal that points at the same artefact.
    result = await _seed_plan(store, "# Plan\n\n- [ ] edit `src/a.py`\n- [ ] work\n- [ ] more\n- [ ] extra\n")
    await store.upsert_workspace_goal(
        id="user-goal",
        title="Ship the plan",
        description="",
        source="user_declared",
        confidence=1.0,
        status="active",
        evidence_json="[]",
        related_files_json="[]",
        artefact_refs_json=json.dumps([result.artefact.id]),
        pc_freshness=1.0,
        pc_reconciliation_state="unreconciled",
        pc_unfinished_item_state="none",
    )

    ctx = ReconcileContext(
        artefact_id=result.artefact.id,
        triggering_signal_id="sig-1",
        changed_files=frozenset({"src/a.py"}),
    )
    await reconcile_artefact(ctx, store=store)

    row = await store.get_workspace_goal("user-goal")
    assert row is not None
    # pc_* fields stay at the values the user declared.
    assert row["pc_reconciliation_state"] == "unreconciled"
    assert row["pc_unfinished_item_state"] == "none"


# -------------------------------------------------------------------------
# PredictionRegistry — apply_item_state_delta
# -------------------------------------------------------------------------


def test_registry_item_delta_complete_marks_spent() -> None:
    registry = PredictionRegistry(cycle_token_pool=10_000)
    spec = PredictionSpec(
        id=prediction_id("artefact_item", "item-x", "Step: do thing"),
        label="Step: do thing",
        description="",
        source="artefact_item",
        anchor="item-x",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="concrete",
    )
    registry.merge([spec], cycle_n=1)
    outcome = registry.apply_item_state_delta(item_id="item-x", from_state="pending", to_state="complete")
    assert outcome == "spent"
    prompt = registry.get(spec.id)
    assert prompt is not None
    assert prompt.run.spent is True


def test_registry_item_delta_contradicted_stales_with_weight_decay() -> None:
    registry = PredictionRegistry(cycle_token_pool=10_000)
    spec = PredictionSpec(
        id=prediction_id("artefact_item", "item-y", "step"),
        label="step",
        description="",
        source="artefact_item",
        anchor="item-y",
        confidence=0.8,
        hypothesis_type="likely_next",
        specificity="concrete",
    )
    registry.merge([spec], cycle_n=1)
    prompt = registry.get(spec.id)
    assert prompt is not None
    initial_weight = prompt.run.weight
    outcome = registry.apply_item_state_delta(item_id="item-y", from_state="in_progress", to_state="contradicted")
    assert outcome == "staled"
    assert prompt.run.weight < initial_weight


def test_registry_item_delta_stalled_demotes_weight() -> None:
    registry = PredictionRegistry(cycle_token_pool=10_000)
    spec = PredictionSpec(
        id=prediction_id("artefact_item", "item-z", "step"),
        label="step",
        description="",
        source="artefact_item",
        anchor="item-z",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="concrete",
    )
    registry.merge([spec], cycle_n=1)
    prompt = registry.get(spec.id)
    assert prompt is not None
    initial_weight = prompt.run.weight
    outcome = registry.apply_item_state_delta(item_id="item-z", from_state="pending", to_state="stalled")
    assert outcome == "demoted"
    assert prompt.run.weight < initial_weight


def test_registry_item_delta_no_matching_spec_returns_none() -> None:
    registry = PredictionRegistry(cycle_token_pool=10_000)
    outcome = registry.apply_item_state_delta(item_id="item-missing", from_state="pending", to_state="complete")
    assert outcome is None
