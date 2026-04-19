# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pydantic import BaseModel, Field


class ContextSelection(BaseModel):
    artefact_key: str
    source_path: str
    score: float
    stale: bool
    token_count: int
    rationale: str
    corpus_id: str = "default"
    privacy_zone: str = "local"


class ContextPackage(BaseModel):
    id: str
    prompt_hash: str
    assembled_at: float
    token_budget: int
    token_used: int
    selections: list[ContextSelection] = Field(default_factory=list)
    injected_context: str = ""
    cache_tier: str = "miss"
    """How this package was sourced: ``"full_hit"`` | ``"partial_hit"`` | ``"miss"``."""
    partial_similarity: float = 0.0
    """Cosine similarity (0-1) when this is a partial cache hit; 0 otherwise."""
