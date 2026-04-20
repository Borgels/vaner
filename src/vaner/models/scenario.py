from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

ScenarioKind = Literal["debug", "explain", "change", "research"]
ScenarioFreshness = Literal["fresh", "recent", "stale"]
ScenarioCost = Literal["low", "medium", "high"]
ScenarioOutcome = Literal["useful", "irrelevant", "partial", "wrong"]
MemoryState = Literal["candidate", "trusted", "stale", "demoted"]


class EvidenceRef(BaseModel):
    key: str
    source_path: str = ""
    excerpt: str = ""
    weight: float = 0.0


class Scenario(BaseModel):
    id: str
    kind: ScenarioKind
    score: float = 0.0
    confidence: float = 0.0
    entities: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    prepared_context: str = ""
    coverage_gaps: list[str] = Field(default_factory=list)
    freshness: ScenarioFreshness = "fresh"
    cost_to_expand: ScenarioCost = "medium"
    created_at: float = Field(default_factory=lambda: datetime.now(UTC).timestamp())
    expanded_at: float | None = None
    last_refreshed_at: float = Field(default_factory=lambda: datetime.now(UTC).timestamp())
    last_outcome: ScenarioOutcome | None = None
    context_envelope_json: str = "{}"
    memory_state: MemoryState = "candidate"
    memory_confidence: float = 0.0
    memory_last_validated_at: float | None = None
    memory_evidence_hashes_json: str = "[]"
    prior_successes: int = 0
    contradiction_signal: float = 0.0
    pinned: int = 0
