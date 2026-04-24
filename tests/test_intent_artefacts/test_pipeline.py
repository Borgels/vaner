# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS1 — ingestion pipeline integration tests.

Covers the end-to-end flow from :func:`ingest_artefact`: classify →
dispatch extractor → persist artefact + snapshot + items → emit
``artefact_seen`` signal. Also exercises the no-op re-ingest path, the
supersession-via-updated-content path, and the classifier-reject path.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import aiosqlite
import pytest

from vaner.intent.adapter import RawArtefact
from vaner.intent.ingest.pipeline import ingest_artefact
from vaner.store.artefacts import ArtefactStore

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def store() -> ArtefactStore:
    with tempfile.TemporaryDirectory() as td:
        s = ArtefactStore(Path(td) / "store.db")
        await s.initialize()
        yield s


async def test_pipeline_accepts_classified_plan_and_persists_items(store: ArtefactStore) -> None:
    raw = RawArtefact(
        source_uri="file:///tmp/release.md",
        connector="local_plan",
        tier="T1",
        text="# Release\n\n## Phase 1\n- [ ] a\n- [x] b\n- [ ] c\n- [ ] d\n",
        last_modified=0.0,
        title_hint="release.md",
    )
    result = await ingest_artefact(raw, store=store)

    assert result.accepted
    assert result.artefact is not None
    assert result.snapshot is not None
    assert result.is_new_snapshot
    assert result.emitted_signal_id is not None
    assert len(result.items) >= 4

    # Persistence round-trip.
    rows = await store.list_intent_artefacts()
    assert len(rows) == 1
    assert rows[0]["connector"] == "local_plan"
    assert rows[0]["source_tier"] == "T1"

    items = await store.list_intent_artefact_items(artefact_id=result.artefact.id)
    assert len(items) == len(result.items)


async def test_pipeline_rejects_non_intent_bearing(store: ArtefactStore) -> None:
    raw = RawArtefact(
        source_uri="file:///tmp/blog.md",
        connector="local_plan",
        tier="T1",
        text="Today we launched. We are excited. Nothing was planned here.",
        last_modified=0.0,
        title_hint="blog.md",
    )
    result = await ingest_artefact(raw, store=store)

    assert not result.accepted
    assert result.artefact is None
    assert result.emitted_signal_id is None
    rows = await store.list_intent_artefacts()
    assert rows == []


async def test_pipeline_is_noop_on_unchanged_reingest(store: ArtefactStore) -> None:
    raw = RawArtefact(
        source_uri="file:///tmp/plan.md",
        connector="local_plan",
        tier="T1",
        text="# Plan\n\n- [ ] first\n- [ ] second\n- [x] third\n",
        last_modified=0.0,
        title_hint="plan.md",
    )
    first = await ingest_artefact(raw, store=store)
    second = await ingest_artefact(raw, store=store)

    assert first.accepted and second.accepted
    assert first.is_new_snapshot
    assert not second.is_new_snapshot
    assert first.emitted_signal_id is not None
    assert second.emitted_signal_id is None

    snaps = await store.list_intent_artefact_snapshots(first.artefact.id)
    assert len(snaps) == 1


async def test_pipeline_writes_new_snapshot_on_content_change(store: ArtefactStore) -> None:
    base_text = "# Plan\n\n- [ ] first\n- [ ] second\n- [ ] third\n"
    raw1 = RawArtefact(
        source_uri="file:///tmp/plan.md",
        connector="local_plan",
        tier="T1",
        text=base_text,
        last_modified=0.0,
        title_hint="plan.md",
    )
    first = await ingest_artefact(raw1, store=store)

    raw2 = RawArtefact(
        source_uri="file:///tmp/plan.md",
        connector="local_plan",
        tier="T1",
        text="# Plan\n\n- [x] first\n- [ ] second\n- [ ] third\n",
        last_modified=1.0,
        title_hint="plan.md",
    )
    second = await ingest_artefact(raw2, store=store)

    assert first.is_new_snapshot and second.is_new_snapshot
    assert first.snapshot.id != second.snapshot.id
    snaps = await store.list_intent_artefact_snapshots(first.artefact.id)
    assert len(snaps) == 2


async def test_pipeline_emits_artefact_seen_signal_with_payload(store: ArtefactStore) -> None:
    raw = RawArtefact(
        source_uri="file:///tmp/release.md",
        connector="local_plan",
        tier="T1",
        text="# Release\n\n## Phase 1\n- [ ] a\n- [ ] b\n- [x] c\n",
        last_modified=0.0,
        title_hint="release.md",
    )
    result = await ingest_artefact(raw, store=store)
    assert result.accepted

    async with aiosqlite.connect(store.db_path) as db:
        cursor = await db.execute(
            "SELECT kind, payload_json FROM signal_events WHERE id = ?",
            (result.emitted_signal_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    kind, payload_json = row
    assert kind == "artefact_seen"
    payload = json.loads(payload_json)
    assert payload["artefact_id"] == result.artefact.id
    assert payload["snapshot_id"] == result.snapshot.id
    assert payload["tier"] == "T1"


async def test_pipeline_github_issue_extracts_root_state_and_labels(store: ArtefactStore) -> None:
    raw = RawArtefact(
        source_uri="github://acme/repo/issues/7",
        connector="github_issues",
        tier="T3",
        text="## Tasks\n\n- [ ] investigate\n- [ ] patch\n- [ ] test\n- [x] document\n",
        last_modified=0.0,
        title_hint="Bug in the parser",
        metadata={
            "issue_number": "7",
            "issue_state": "open",
            "issue_labels": "bug,priority-high",
            "issue_assignees": "abolsen",
            "repo": "acme/repo",
        },
    )
    result = await ingest_artefact(raw, store=store)
    assert result.accepted
    root = result.items[0]
    assert root.kind == "section"
    assert root.state == "pending"
    assert "bug" in root.related_entities
    assert "priority-high" in root.related_entities
    assert any("assignee:abolsen" in ref for ref in root.evidence_refs)


async def test_pipeline_emit_signal_false_suppresses_signal(store: ArtefactStore) -> None:
    raw = RawArtefact(
        source_uri="file:///tmp/plan.md",
        connector="local_plan",
        tier="T1",
        text="# Plan\n\n- [ ] a\n- [ ] b\n- [x] c\n",
        last_modified=0.0,
        title_hint="plan.md",
    )
    result = await ingest_artefact(raw, store=store, emit_signal=False)
    assert result.accepted
    assert result.emitted_signal_id is None
    # Artefact should still be persisted.
    rows = await store.list_intent_artefacts()
    assert len(rows) == 1
