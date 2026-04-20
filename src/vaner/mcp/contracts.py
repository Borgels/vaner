# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Domain = Literal["code", "docs", "support", "operations", "research", "general"]
ProvenanceMode = Literal["predictive_hit", "cached_result", "fresh_resolution", "retrieval_fallback"]
Budget = Literal["low", "medium", "high"]
AbstainReason = Literal["low_confidence", "ambiguous_intent", "insufficient_evidence", "memory_conflict"]
FeedbackRating = Literal["useful", "partial", "wrong", "irrelevant"]
ExpandMode = Literal["details", "neighbors", "dependencies", "timeline", "related"]
SearchMode = Literal["semantic", "lexical", "hybrid", "symbol", "path"]
MemoryState = Literal["candidate", "trusted", "stale", "demoted"]
MemorySection = Literal["invariants", "conventions", "decision_digest", "hotspots", "feedback"]


class ContextEnvelope(BaseModel):
    domain: Domain = "code"
    current_artifact: str | None = None
    selection: str | None = None
    recent_queries: list[str] = Field(default_factory=list)
    agent_goal: str | None = None


class EvidenceItem(BaseModel):
    id: str
    source: str
    kind: Literal["file", "symbol", "doc", "record", "other"] = "file"
    locator: dict = Field(default_factory=dict)
    reason: str = ""
    fingerprint: str | None = None


class Alternative(BaseModel):
    source: str
    reason_rejected: str


class MemoryMeta(BaseModel):
    state: MemoryState
    confidence: float
    last_validated_at: float
    evidence_count: int
    prior_successes: int = 0
    contradiction_signal: float = 0.0


class Provenance(BaseModel):
    mode: ProvenanceMode
    cache: Literal["cold", "warm", "hot"] = "cold"
    freshness: Literal["fresh", "recent", "stale"] = "fresh"
    memory: MemoryMeta | None = None


class ConflictSignal(BaseModel):
    has_conflict: bool
    strength: float
    reason: str = ""


class Resolution(BaseModel):
    intent: str
    confidence: float
    summary: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    alternatives_considered: list[Alternative] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    provenance: Provenance
    resolution_id: str


class Abstain(BaseModel):
    abstained: Literal[True] = True
    reason: AbstainReason
    message: str
    suggestions: list[str] = Field(default_factory=list)
    conflict: ConflictSignal | None = None


CACHE_TIER_TO_PROVENANCE: dict[str, ProvenanceMode] = {
    "full_hit": "predictive_hit",
    "partial_hit": "cached_result",
    "warm_start": "fresh_resolution",
    "miss": "retrieval_fallback",
}
