from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from vaner.cli.commands.config import load_config
from vaner.daemon.runner import VanerDaemon
from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter, CorpusItem, MutationEvent, RelationshipEdge
from vaner.models.signal import SignalEvent


class NotesAdapter(CodeRepoAdapter):
    corpus_type = "notes"
    source_type = "notes"
    corpus_id = "notes"
    privacy_zone = "private_local"

    async def list_items(self, limit: int = 500) -> list[CorpusItem]:
        return [
            CorpusItem(
                key="note:review-checklist",
                content="run code review checklist",
                metadata={"path": "notes/review.md", "corpus_id": self.corpus_id},
                updated_at=1.0,
                corpus_id=self.corpus_id,
                privacy_zone=self.privacy_zone,
            )
        ]

    async def detect_mutations(self, since: float) -> list[MutationEvent]:
        return [
            MutationEvent(
                source="notes-sync",
                kind="note_seen",
                payload={"path": "notes/review.md"},
                timestamp=since + 1,
                corpus_id=self.corpus_id,
                privacy_zone=self.privacy_zone,
            )
        ]

    async def extract_relationships(self) -> list[RelationshipEdge]:
        return [
            RelationshipEdge(
                source_key="note:review-checklist",
                target_key="file:sample.py",
                kind="references",
                corpus_id=self.corpus_id,
            )
        ]


@pytest.mark.asyncio
async def test_store_filters_query_history_and_signals_by_corpus(temp_repo: Path):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()
    await engine.query("explain sample module")

    notes_engine = VanerEngine(adapter=NotesAdapter(temp_repo))
    await notes_engine.initialize()
    await notes_engine.observe(
        SignalEvent(
            id=str(uuid.uuid4()),
            source="notes-test",
            kind="manual",
            timestamp=123.0,
            payload={"path": "notes/review.md"},
        )
    )
    await notes_engine.inject_history(["review the checklist"], session_id="notes-session")

    repo_history = await engine.store.list_query_history(corpus_id="repo", limit=10)
    notes_history = await notes_engine.store.list_query_history(corpus_id="notes", limit=10)
    assert repo_history
    assert notes_history
    assert all(row["corpus_id"] == "repo" for row in repo_history)
    assert all(row["corpus_id"] == "notes" for row in notes_history)

    notes_signals = await notes_engine.store.list_signal_events(corpus_id="notes", limit=10)
    assert notes_signals
    assert all(event.payload.get("corpus_id") == "notes" for event in notes_signals)


@pytest.mark.asyncio
async def test_prepare_corpus_persists_relationship_edges_with_corpus_ids(temp_repo: Path):
    engine = VanerEngine(adapter=NotesAdapter(temp_repo))
    await engine.prepare_corpus()

    edges = await engine.store.list_relationship_edges(corpus_id="notes", limit=20)
    assert edges
    assert ("note:review-checklist", "file:sample.py", "references") in edges


@pytest.mark.asyncio
async def test_prepare_corpus_persists_repo_relationship_edges_under_repo_corpus(temp_repo: Path):
    (temp_repo / "consumer.py").write_text("import sample\n", encoding="utf-8")
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare_corpus()

    repo_edges = await engine.store.list_relationship_edges(corpus_id="repo", limit=100)
    assert repo_edges
    assert ("file:consumer.py", "file:sample.py", "imports") in repo_edges


@pytest.mark.asyncio
async def test_daemon_signals_are_stamped_with_repo_corpus(temp_repo: Path):
    config = load_config(temp_repo)
    daemon = VanerDaemon(config)
    changed_files = [temp_repo / "sample.py"]
    await daemon.run_once(changed_files=changed_files)

    signals = await daemon.store.list_signal_events(corpus_id="repo", limit=20)
    assert signals
    assert all(signal.payload.get("corpus_id") == "repo" for signal in signals)
