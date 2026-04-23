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
MemorySection = Literal["invariants", "conventions", "decision_digests", "hotspot_notes", "feedback"]


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


class ResolutionMetrics(BaseModel):
    """Runtime economics for a single ``vaner.resolve`` call.

    Populated when the caller sets ``include_metrics=True`` on the request.
    Gives the consumer (the backend LLM or its agent host) a way to see the
    token/latency/cache characteristics of the context they just received —
    so they can size their prompt, budget their cost, and decide whether to
    trust the cache tier without guessing.
    """

    briefing_tokens: int = 0
    evidence_tokens: int = 0
    total_context_tokens: int = 0
    cache_tier: str = "miss"  # "miss" | "warm_start" | "partial_hit" | "full_hit" | "predictive_hit"
    freshness: str = "fresh"  # "fresh" | "recent" | "stale"
    elapsed_ms: float = 0.0
    # Rough cost estimate in USD assuming ``total_context_tokens`` are billed
    # at ``estimated_cost_per_1k_tokens``. The rate is a hint — the caller can
    # override with their actual pricing. Defaults to 0 (unknown model pricing).
    estimated_cost_per_1k_tokens: float = 0.0
    estimated_cost_usd: float = 0.0


class Resolution(BaseModel):
    intent: str
    confidence: float
    summary: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    alternatives_considered: list[Alternative] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    context_envelope: ContextEnvelope | None = None
    provenance: Provenance
    resolution_id: str
    # Opt-in, additive. When a consumer passes ``include_briefing=True`` on
    # the resolve request, ``prepared_briefing`` carries the full formatted
    # context briefing (pre-compiled artefact summaries) — the output that
    # differentiates Vaner from a plain top-K RAG response. Default None.
    prepared_briefing: str | None = None
    # When ``include_predicted_response=True`` and a draft answer was cached
    # speculatively during precompute, it is returned verbatim. Consumers use
    # this to skip a round-trip when Vaner's prediction is high-confidence.
    predicted_response: str | None = None
    # Honest token accounting for the briefing field so callers can size the
    # downstream prompt. Both default 0 when ``prepared_briefing`` is None.
    briefing_token_used: int = 0
    briefing_token_budget: int = 0
    # When ``include_metrics=True``, carries per-call runtime economics. Lets
    # consumers observe Vaner's cache-tier / token / latency / cost footprint
    # without a side channel.
    metrics: ResolutionMetrics | None = None
    # Phase 4 / Phase C: when this Resolution was produced by a
    # ``vaner.predictions.adopt`` call, carries the source prediction's id so
    # downstream agents know the prepared package came from an adopted
    # prediction (and can surface that provenance in their UI).
    adopted_from_prediction_id: str | None = None


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
