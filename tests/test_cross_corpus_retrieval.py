from __future__ import annotations

from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter, CorpusItem, QualityIssue, ReasonerContext, RelationshipEdge


class NotesSource:
    source_type = "notes"
    corpus_id = "notes"
    privacy_zone = "private_local"

    async def list_items(self, limit: int = 500) -> list[CorpusItem]:
        return [
            CorpusItem(
                key="note:sample-review-checklist",
                content="sample module review checklist architecture notes and implementation followup",
                metadata={"path": "notes/sample-review.md"},
                updated_at=1.0,
                corpus_id=self.corpus_id,
                privacy_zone=self.privacy_zone,
            )
        ]

    async def get_item(self, key: str) -> CorpusItem:
        items = await self.list_items()
        for item in items:
            if item.key == key:
                return item
        raise KeyError(key)

    async def extract_relationships(self) -> list[RelationshipEdge]:
        return [
            RelationshipEdge(
                source_key="note:sample-review-checklist",
                target_key="file:sample.py",
                kind="references",
                corpus_id=self.corpus_id,
            )
        ]

    async def check_quality(self) -> list[QualityIssue]:
        return []

    async def get_context_for_reasoning(self) -> ReasonerContext:
        return ReasonerContext(
            corpus_type="notes",
            summary="notes about reviewing the sample module",
            corpus_id=self.corpus_id,
            privacy_zone=self.privacy_zone,
        )


@pytest.mark.asyncio
async def test_query_can_select_context_from_multiple_corpora(temp_repo: Path):
    engine = VanerEngine(
        adapter=CodeRepoAdapter(temp_repo),
        sources=[NotesSource()],
    )
    await engine.prepare()

    package = await engine.query("explain sample module review checklist")

    assert package.selections
    corpora = {selection.corpus_id for selection in package.selections}
    assert "repo" in corpora
    assert "notes" in corpora
    assert any(selection.privacy_zone == "private_local" for selection in package.selections)


@pytest.mark.asyncio
async def test_prepare_corpus_merges_relationship_edges_from_extra_sources(temp_repo: Path):
    engine = VanerEngine(
        adapter=CodeRepoAdapter(temp_repo),
        sources=[NotesSource()],
    )
    await engine.prepare_corpus()

    edges = await engine.store.list_relationship_edges(corpus_id="notes", limit=20)
    assert ("note:sample-review-checklist", "file:sample.py", "references") in edges
