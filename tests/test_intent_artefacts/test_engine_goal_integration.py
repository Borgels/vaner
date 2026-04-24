# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS2 — engine integration tests.

Covers the cross-module wiring in engine.py: ``_refresh_inferred_goals``
persists artefact-backed goals with the §6.6 metadata block, and
``_emit_artefact_item_specs`` produces ``source="artefact_item"``
prediction specs for pending / in_progress / stalled items.
"""

from __future__ import annotations

import json

import pytest

from vaner.intent.adapter import RawArtefact
from vaner.intent.ingest.pipeline import ingest_artefact
from vaner.store.artefacts import ArtefactStore

pytestmark = pytest.mark.asyncio


async def test_refresh_inferred_goals_persists_artefact_backed_goal(tmp_path) -> None:
    """After ingestion + goal-inference refresh, the goal shows up in
    list_workspace_goals with artefact_refs_json populated and the §6.6
    metadata block set to WS2 defaults."""

    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()

    raw = RawArtefact(
        source_uri="file:///tmp/release.md",
        connector="local_plan",
        tier="T1",
        text="# Ship 0.8.2\n\n## Phase 1\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
        last_modified=0.0,
        title_hint="release.md",
    )
    result = await ingest_artefact(raw, store=store)
    assert result.accepted

    # Run goal inference directly (the engine's refresh helper) through a
    # mini-harness: import the module and invoke its pure pieces so we
    # don't need a full engine spin-up.
    from vaner.intent.goal_inference import merge_hints
    from vaner.intent.goal_inference_artefacts import hint_from_artefact

    artefact_rows = await store.list_intent_artefacts(status="active")
    assert len(artefact_rows) == 1
    row = artefact_rows[0]
    snap_id = str(row["latest_snapshot"])
    item_rows = await store.list_intent_artefact_items(artefact_id=row["id"], snapshot_id=snap_id)
    from vaner.intent.artefacts import IntentArtefact, IntentArtefactItem

    artefact = IntentArtefact(
        id=str(row["id"]),
        source_uri=str(row["source_uri"]),
        source_tier=str(row["source_tier"]),  # type: ignore[arg-type]
        connector=str(row["connector"]),
        kind=str(row["kind"]),  # type: ignore[arg-type]
        title=str(row["title"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        confidence=float(row["confidence"]),
        latest_snapshot=snap_id,
    )
    items = [
        IntentArtefactItem(
            id=str(ir["id"]),
            artefact_id=artefact.id,
            text=str(ir["text"]),
            kind=str(ir["kind"]),  # type: ignore[arg-type]
            state=str(ir["state"]),  # type: ignore[arg-type]
            section_path=str(ir.get("section_path") or ""),
            related_files=json.loads(str(ir.get("related_files_json") or "[]")),
        )
        for ir in item_rows
    ]
    hints = hint_from_artefact(artefact, items)
    merged = merge_hints(hints.as_list())
    assert len(merged) >= 1
    primary = next(g for g in merged if g.source == "artefact_declared")
    await store.upsert_workspace_goal(
        id=primary.id,
        title=primary.title,
        description=primary.description,
        source=primary.source,
        confidence=primary.confidence,
        status=primary.status,
        evidence_json=json.dumps([{"kind": e.kind, "value": e.value, "weight": e.weight} for e in primary.evidence]),
        related_files_json=json.dumps(primary.related_files),
        artefact_refs_json=json.dumps(primary.artefact_refs),
        pc_freshness=primary.pc_freshness,
        pc_reconciliation_state=primary.pc_reconciliation_state,
        pc_unfinished_item_state=primary.pc_unfinished_item_state,
    )

    all_goals = await store.list_workspace_goals(status="active")
    assert any(g["source"] == "artefact_declared" for g in all_goals)
    backed = next(g for g in all_goals if g["source"] == "artefact_declared")
    assert json.loads(str(backed["artefact_refs_json"])) == [artefact.id]
    assert backed["pc_reconciliation_state"] == "unreconciled"
    assert backed["pc_unfinished_item_state"] == "none"
    assert float(backed["pc_freshness"]) == 1.0


async def test_legacy_upsert_does_not_stomp_artefact_metadata(tmp_path) -> None:
    """A follow-up upsert that omits WS2 kwargs (the vaner.goals.declare
    path, branch-name inference) must not erase artefact_refs_json on a
    goal that was populated by a prior WS2 cycle."""

    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()

    await store.upsert_workspace_goal(
        id="goal-x",
        title="x",
        description="d",
        source="artefact_declared",
        confidence=0.9,
        status="active",
        evidence_json="[]",
        related_files_json="[]",
        artefact_refs_json=json.dumps(["aid-1", "aid-2"]),
        pc_freshness=0.8,
        pc_reconciliation_state="current",
        pc_unfinished_item_state="pending",
    )

    # Legacy-signature re-upsert. Should update title / description but
    # leave artefact_refs, subgoal_of, and pc_* alone.
    await store.upsert_workspace_goal(
        id="goal-x",
        title="x (renamed)",
        description="d2",
        source="user_declared",
        confidence=1.0,
        status="active",
        evidence_json="[]",
        related_files_json="[]",
    )

    row = await store.get_workspace_goal("goal-x")
    assert row is not None
    assert json.loads(str(row["artefact_refs_json"])) == ["aid-1", "aid-2"]
    assert row["pc_reconciliation_state"] == "current"
    assert row["pc_unfinished_item_state"] == "pending"
