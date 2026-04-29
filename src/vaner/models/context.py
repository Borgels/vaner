# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pydantic import BaseModel, Field

from vaner.models.answerable import AnswerabilityMetadata, AnswerableBriefing


class ContextSelection(BaseModel):
    artefact_key: str
    source_path: str
    score: float
    stale: bool
    token_count: int
    rationale: str
    corpus_id: str = "default"
    privacy_zone: str = "local"
    provenance: str = "prediction"
    """Evidence source label: prediction | vaner_resolve | retrieval_floor | external_rag."""
    conflict_notes: list[str] = Field(default_factory=list)


class ContextPackage(BaseModel):
    id: str
    prompt_hash: str
    assembled_at: float
    token_budget: int
    token_used: int
    selections: list[ContextSelection] = Field(default_factory=list)
    injected_context: str = ""
    conflict_notes: list[str] = Field(default_factory=list)
    answerable_briefing: AnswerableBriefing | None = None
    answerability_metadata: AnswerabilityMetadata | None = None
    cache_tier: str = "miss"
    """How this package was sourced: ``"full_hit"`` | ``"partial_hit"`` | ``"miss"``."""
    partial_similarity: float = 0.0
    """Cosine similarity (0-1) when this is a partial cache hit; 0 otherwise."""
