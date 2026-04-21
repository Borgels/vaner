from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

ScenarioKind = Literal["debug", "explain", "change", "research", "refactor"]
ScenarioFreshness = Literal["fresh", "recent", "stale"]
ScenarioCost = Literal["low", "medium", "high"]
ScenarioOutcome = Literal["useful", "irrelevant", "partial"]


class EvidenceRef(BaseModel):
    key: str
    source_path: str = ""
    excerpt: str = ""
    weight: float = 0.0
    start_line: int | None = None
    end_line: int | None = None


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
    memory_state: str = "candidate"
    pinned: bool = False
