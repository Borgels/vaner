# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievalFloorRequest(BaseModel):
    """Stable, boring contract for a bounded retrieval floor provider."""

    query: str
    corpus_id: str = "default"
    max_items: int = 5
    max_tokens: int = 1024
    allowed_sources: list[str] = Field(default_factory=list)
    request_id: str = ""
    timeout_ms: int = 1500


class RetrievalFloorItem(BaseModel):
    source_id: str
    path_or_url: str
    title: str = ""
    excerpt: str
    score: float = 0.0
    rank: int = 0
    provenance: str = "retrieval_floor"
    revision_or_hash: str = ""
    retrieved_at: float = 0.0


class RetrievalFloorResponse(BaseModel):
    provider_name: str = "builtin_floor"
    provider_version: str = "1"
    latency_ms: float = 0.0
    error: str = ""
    degraded: bool = False
    items: list[RetrievalFloorItem] = Field(default_factory=list)


class RetrievalFloorMetrics(BaseModel):
    invocation_count: int = 0
    helped_count: int = 0
    hurt_count: int = 0
    token_overhead: int = 0
    latency_overhead_ms: float = 0.0
    conflict_count: int = 0
