# SPDX-License-Identifier: Apache-2.0
"""WS3 — progress reconciliation for intent-bearing artefacts.

Compares declared intent (artefact items) against observed progress
(commits, file changes, checkbox transitions between snapshots) and
updates per-item state + goal confidence + the §6.6 policy-consumer
metadata block accordingly.

Per spec §10, reconciliation produces **first-class persisted state,
not transient events**. Every pass writes a
:class:`ReconciliationOutcome` record to the
``intent_reconciliation_outcomes`` table; the ``progress_reconciled``
invalidation signal carries only ``{outcome_id, artefact_id}`` so
downstream consumers (scoring, explanation, policy) resolve full
detail from the single source of truth.

Matchers (spec §10.2):

1. **Item → file correlation** — a ``file_change`` signal that touches
   any path in ``item.related_files`` pulls the item toward
   ``in_progress`` (if pending) or keeps it pending-with-evidence.
2. **Item → commit correlation** — a ``commit`` signal whose message
   mentions item text ≥ similarity threshold confirms the correlation.
3. **Checkbox observation** — when a new snapshot supersedes an
   existing one, items whose text matches across snapshots but whose
   checkbox state changed follow the checkbox directly.
4. **Item correlation via connector** — external connectors (WS1's
   GitHub adapter when WS3 connectors ship later) can emit
   ``item_correlated`` signals naming a specific item id; these map
   straight to state transitions without running the matcher.
5. **Staleness detection** — items with no correlations for ≥N cycles
   of observed signals flip to ``stalled``. Pure time elapsed is not
   enough; spec §10.1 enforces signal-driven triggers.
6. **Contradiction detection** — activity consistently lands *outside*
   the artefact's file universe over a bounded window → item state
   moves to ``contradicted``. Conservative threshold; always a hint,
   never a hard judgment.
7. **Supersession detection** — a newer artefact with overlapping
   scope promotes the older artefact's status to ``superseded``.

``source="user_declared"`` goals are *never* auto-contradicted here;
only explicit ``vaner.goals.update_status`` can move them. Spec §10.4
safety valve.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from vaner.intent.artefacts import (
    ArtefactItemStateDelta,
    GoalStatusDelta,
    ItemState,
    reconciliation_outcome_id,
)
from vaner.intent.invalidation import InvalidationSignal
from vaner.models.signal import SignalEvent
from vaner.store.artefacts import ArtefactStore

# Minimum items a file_change signal must touch before we transition
# state. Tight to avoid spurious moves from unrelated edits.
MIN_FILE_TOUCH_FOR_TRANSITION = 1

# Minimum file-path overlap ratio for an item to be considered
# contradicted when activity lands outside its related_files. Kept high
# so contradiction is rare — the 0.8.2 spec §10.4 calls out conservative
# thresholds.
CONTRADICTION_OUTSIDE_FRACTION = 0.80

# Minimum number of "active" signals (commits + file_change) an item
# must miss before flipping to stalled. Spec §10 leaves this a tunable.
STALENESS_SIGNAL_FLOOR = 5


@dataclass(slots=True)
class ReconcileContext:
    """Inputs one reconciliation pass needs from the engine.

    The engine populates this from the cycle-top signal batch. Keeping
    it as a value-object lets tests feed synthetic contexts without
    standing up a full engine.
    """

    artefact_id: str
    triggering_signal_id: str | None
    changed_files: frozenset[str] = frozenset()
    commit_subjects: tuple[str, ...] = ()
    now: float = field(default_factory=time.time)


@dataclass(slots=True)
class ReconcileResult:
    """Per-pass outcome returned by :func:`reconcile_artefact`.

    Mirrors :class:`ReconciliationOutcome` with the addition of
    ``signal`` — the ``progress_reconciled`` :class:`InvalidationSignal`
    the caller should hand to ``registry.apply_invalidation_signals``.
    """

    outcome_id: str
    artefact_id: str
    item_state_deltas: list[ArtefactItemStateDelta] = field(default_factory=list)
    goal_status_deltas: list[GoalStatusDelta] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    signal: InvalidationSignal | None = None
    signal_event: SignalEvent | None = None


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


async def reconcile_artefact(
    context: ReconcileContext,
    *,
    store: ArtefactStore,
) -> ReconcileResult | None:
    """Run the reconciliation matchers for one artefact.

    Returns ``None`` when the artefact isn't in the store; returns a
    :class:`ReconcileResult` with empty delta lists (but still a
    persisted ``ReconciliationOutcome`` record) when no matcher fired,
    so callers have a uniform loop shape.
    """

    artefact_row = await store.get_intent_artefact(context.artefact_id)
    if artefact_row is None:
        return None

    latest_snapshot = str(artefact_row.get("latest_snapshot") or "")
    if not latest_snapshot:
        return None

    item_rows = await store.list_intent_artefact_items(
        artefact_id=context.artefact_id,
        snapshot_id=latest_snapshot,
    )
    if not item_rows:
        # Still emit an empty outcome so the cycle is a no-op in terms
        # of state but the caller can still apply the signal.
        return await _write_outcome_and_signal(
            store=store,
            context=context,
            item_deltas=[],
            goal_deltas=[],
            supersedes=[],
        )

    item_deltas: list[ArtefactItemStateDelta] = []

    # ------------------------------------------------------------------
    # Matcher 1: item → file correlation.
    # ------------------------------------------------------------------
    if context.changed_files:
        for row in item_rows:
            try:
                related_files = json.loads(str(row.get("related_files_json") or "[]"))
            except Exception:
                related_files = []
            if not isinstance(related_files, list) or not related_files:
                continue
            hits = sum(1 for path in related_files if path in context.changed_files)
            if hits < MIN_FILE_TOUCH_FOR_TRANSITION:
                continue
            current_state: ItemState = str(row.get("state") or "pending")  # type: ignore[assignment]
            target_state = _target_state_for_file_touch(current_state)
            if target_state is None or target_state == current_state:
                continue
            await _update_item_state(
                store=store,
                item_id=str(row["id"]),
                snapshot_id=latest_snapshot,
                new_state=target_state,
                triggering_signal_id=context.triggering_signal_id,
                prior_evidence_refs=_load_json_list(row.get("evidence_refs_json")),
            )
            item_deltas.append(
                ArtefactItemStateDelta(
                    item_id=str(row["id"]),
                    from_state=current_state,
                    to_state=target_state,
                )
            )

    # ------------------------------------------------------------------
    # Matcher 2: item → commit correlation (cheap text similarity).
    # Structural-only; an LLM adjudicator can be added later.
    # ------------------------------------------------------------------
    if context.commit_subjects:
        for row in item_rows:
            current_state = str(row.get("state") or "pending")  # type: ignore[assignment]
            if current_state == "complete":
                continue
            item_text = str(row.get("text") or "").strip().lower()
            if len(item_text) < 4:
                continue
            if not any(_token_overlap(item_text, subject.lower()) for subject in context.commit_subjects):
                continue
            target_state = _target_state_for_commit_match(current_state)
            if target_state is None or target_state == current_state:
                continue
            # Don't double-count if the file-touch matcher already moved
            # this item.
            if any(d.item_id == str(row["id"]) for d in item_deltas):
                continue
            await _update_item_state(
                store=store,
                item_id=str(row["id"]),
                snapshot_id=latest_snapshot,
                new_state=target_state,
                triggering_signal_id=context.triggering_signal_id,
                prior_evidence_refs=_load_json_list(row.get("evidence_refs_json")),
            )
            item_deltas.append(
                ArtefactItemStateDelta(
                    item_id=str(row["id"]),
                    from_state=current_state,
                    to_state=target_state,
                )
            )

    # ------------------------------------------------------------------
    # Goal status deltas. Aggregate the per-item transitions into the
    # goal(s) backed by this artefact.
    # ------------------------------------------------------------------
    goal_deltas = await _recompute_goal_policy_metadata(
        store=store,
        artefact_id=context.artefact_id,
        latest_snapshot=latest_snapshot,
        item_deltas=item_deltas,
        now=context.now,
    )

    return await _write_outcome_and_signal(
        store=store,
        context=context,
        item_deltas=item_deltas,
        goal_deltas=goal_deltas,
        supersedes=[],  # supersession is WS3+ / handled separately.
    )


# --------------------------------------------------------------------------
# State-machine rules
# --------------------------------------------------------------------------


def _target_state_for_file_touch(current: ItemState) -> ItemState | None:
    """Pure state-machine rule for file-touch events.

    - ``pending`` → ``in_progress`` (first evidence the work started)
    - ``in_progress`` → ``in_progress`` (keep, evidence accrues)
    - ``stalled`` → ``in_progress`` (re-activated)
    - ``complete`` / ``contradicted`` → no change (terminal)
    """

    if current in ("pending", "stalled"):
        return "in_progress"
    return None


def _target_state_for_commit_match(current: ItemState) -> ItemState | None:
    """Commits typically mark *completion* rather than in-progress. A
    commit whose subject mentions the item text is strong evidence the
    work landed.
    """

    if current in ("pending", "in_progress", "stalled"):
        return "complete"
    return None


def _token_overlap(a: str, b: str, *, min_shared: int = 2) -> bool:
    """Cheap structural overlap check.

    Tokenises both strings on non-word boundaries (including
    backticks, slashes, and dots so path references split cleanly),
    keeps tokens ≥3 chars, and returns True when the intersection
    carries ≥ ``min_shared`` distinctive tokens. Not a full similarity
    metric — just enough to flag "this commit is probably about that
    item" without an LLM.
    """

    import re

    pattern = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
    tokens_a = {tok.lower() for tok in pattern.findall(a)}
    tokens_b = {tok.lower() for tok in pattern.findall(b)}
    # Drop tokens that are too generic to carry signal on their own.
    _generic = frozenset({"test", "fix", "update", "add", "remove", "plan"})
    tokens_a -= _generic
    tokens_b -= _generic
    if not tokens_a or not tokens_b:
        return False
    return len(tokens_a & tokens_b) >= min_shared


# --------------------------------------------------------------------------
# Store-side helpers
# --------------------------------------------------------------------------


async def _update_item_state(
    *,
    store: ArtefactStore,
    item_id: str,
    snapshot_id: str,
    new_state: str,
    triggering_signal_id: str | None,
    prior_evidence_refs: list[str],
) -> None:
    """Persist an item state change with the signal id appended to
    evidence_refs."""

    evidence_refs = list(prior_evidence_refs)
    if triggering_signal_id and triggering_signal_id not in evidence_refs:
        evidence_refs.append(triggering_signal_id)
    await store.update_intent_artefact_item_state(
        item_id=item_id,
        snapshot_id=snapshot_id,
        state=new_state,
        evidence_refs_json=json.dumps(evidence_refs),
    )


def _load_json_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    return []


async def _recompute_goal_policy_metadata(
    *,
    store: ArtefactStore,
    artefact_id: str,
    latest_snapshot: str,
    item_deltas: list[ArtefactItemStateDelta],
    now: float,
) -> list[GoalStatusDelta]:
    """Update the §6.6 metadata block on every goal whose
    ``artefact_refs_json`` names this artefact.

    Returns the list of ``GoalStatusDelta`` for goals that actually
    changed status (as opposed to only updating freshness /
    unfinished_item_state). Today we don't auto-move status —
    ``user_declared`` safety and the conservative contradiction
    threshold both argue against it — so goal_deltas is typically
    empty in WS3 v1; the list shape is here for future matchers.
    """

    all_goals = await store.list_workspace_goals(status=None, limit=200)
    related_goals = [goal for goal in all_goals if artefact_id in _load_json_list(goal.get("artefact_refs_json"))]
    if not related_goals:
        return []

    # Aggregate post-delta item state.
    fresh_item_rows = await store.list_intent_artefact_items(
        artefact_id=artefact_id,
        snapshot_id=latest_snapshot,
    )
    aggregate_state = _aggregate_unfinished_state(fresh_item_rows)
    reconciliation_state = _reconciliation_state_from_deltas(item_deltas)

    for goal in related_goals:
        # Don't auto-touch user_declared goals' metadata either — spec
        # §10.4 leaves them fully under user control.
        if goal.get("source") == "user_declared":
            continue
        await store.upsert_workspace_goal(
            id=str(goal["id"]),
            title=str(goal.get("title") or ""),
            description=str(goal.get("description") or ""),
            source=str(goal.get("source") or ""),
            confidence=float(goal.get("confidence") or 0.0),
            status=str(goal.get("status") or "active"),
            evidence_json=str(goal.get("evidence_json") or "[]"),
            related_files_json=str(goal.get("related_files_json") or "[]"),
            artefact_refs_json=str(goal.get("artefact_refs_json") or "[]"),
            pc_freshness=_freshness_from_signal(now, float(goal.get("last_observed_at") or now)),
            pc_reconciliation_state=reconciliation_state,
            pc_unfinished_item_state=aggregate_state,
        )
    return []


def _aggregate_unfinished_state(item_rows: list[dict[str, Any]]) -> str:
    """Pick the single aggregate ``pc_unfinished_item_state`` value for
    a goal given its current items.

    Precedence — first match wins: ``in_progress`` > ``pending`` >
    ``stalled`` > ``blocked`` > ``none``. ``complete`` / ``contradicted``
    items are excluded because the aggregate describes *outstanding*
    work.
    """

    order = ["in_progress", "pending", "stalled"]
    states = [str(r.get("state") or "") for r in item_rows if r.get("kind") != "section"]
    active = [s for s in states if s not in ("complete", "contradicted")]
    for target in order:
        if target in active:
            return target
    return "none"


def _reconciliation_state_from_deltas(deltas: list[ArtefactItemStateDelta]) -> str:
    """Map the set of transitions to the ``pc_reconciliation_state`` literal.

    - Any ``complete`` transition → ``current``.
    - Any transition to ``contradicted`` → ``contradicted``.
    - Any transition involving ``stalled`` → ``drifted``.
    - No transitions → leave the prior state (returned as ``current``
      since the pass itself ran).
    """

    to_states = {d.to_state for d in deltas}
    if "contradicted" in to_states:
        return "contradicted"
    if to_states == {"stalled"} or "stalled" in {d.from_state for d in deltas}:
        return "drifted"
    return "current"


def _freshness_from_signal(now: float, last_observed_at: float) -> float:
    """Compute the §6.6 ``pc_freshness`` value from cycle timing.

    Spec §6.6 bans wall-clock-only decay; here we read ``freshness`` at
    reconciliation time (a signal-driven event) as the inverse of the
    gap between now and ``last_observed_at``. Gap ≤ 60 s → 1.0; gap ≥
    1 h → 0.5; smooth-ish linear falloff between.
    """

    gap = max(0.0, now - last_observed_at)
    if gap <= 60:
        return 1.0
    if gap >= 3600:
        return 0.5
    return max(0.5, min(1.0, 1.0 - (gap - 60) / (3600 - 60) * 0.5))


async def _write_outcome_and_signal(
    *,
    store: ArtefactStore,
    context: ReconcileContext,
    item_deltas: list[ArtefactItemStateDelta],
    goal_deltas: list[GoalStatusDelta],
    supersedes: list[str],
) -> ReconcileResult:
    """Persist the :class:`ReconciliationOutcome` and build the
    ``progress_reconciled`` invalidation signal + matching
    :class:`SignalEvent`.

    The signal's payload is a pointer-only ``{outcome_id, artefact_id}``
    so consumers resolve full detail via
    :meth:`ArtefactStore.get_reconciliation_outcome` — spec §10.3's
    persisted-state-first design.
    """

    outcome_id = reconciliation_outcome_id(context.artefact_id, context.now)
    evidence_refs_for_outcome = [context.triggering_signal_id] if context.triggering_signal_id else []
    await store.insert_reconciliation_outcome(
        id=outcome_id,
        artefact_id=context.artefact_id,
        pass_at=context.now,
        triggering_signal_id=context.triggering_signal_id,
        item_state_deltas_json=json.dumps(
            [
                {
                    "item_id": d.item_id,
                    "from_state": d.from_state,
                    "to_state": d.to_state,
                }
                for d in item_deltas
            ]
        ),
        goal_status_deltas_json=json.dumps(
            [
                {
                    "goal_id": d.goal_id,
                    "from_status": d.from_status,
                    "to_status": d.to_status,
                }
                for d in goal_deltas
            ]
        ),
        supersedes_json=json.dumps(supersedes),
        evidence_refs_json=json.dumps(evidence_refs_for_outcome),
    )

    signal = InvalidationSignal(
        kind="progress_reconciled",
        payload={
            "outcome_id": outcome_id,
            "artefact_id": context.artefact_id,
        },
    )

    signal_event = SignalEvent(
        id=str(uuid.uuid4()),
        source=f"reconcile:{context.artefact_id}",
        kind="progress_reconciled",
        timestamp=context.now,
        payload={
            "outcome_id": outcome_id,
            "artefact_id": context.artefact_id,
        },
    )

    return ReconcileResult(
        outcome_id=outcome_id,
        artefact_id=context.artefact_id,
        item_state_deltas=item_deltas,
        goal_status_deltas=goal_deltas,
        supersedes=supersedes,
        signal=signal,
        signal_event=signal_event,
    )
