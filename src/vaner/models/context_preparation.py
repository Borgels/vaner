# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ContextNeed = Literal[
    "direct_reference",
    "task_continuation",
    "evidence_gathering",
    "multi_source_synthesis",
    "decision_support",
    "conflict_resolution",
    "creative_grounding",
    "research_mapping",
    "implementation_support",
    "absence_check",
    "working_set_extension",
]

ContextArchetype = Literal["developer", "writer", "researcher", "operator", "general"]


class ContextFacet(BaseModel):
    name: str
    value: str
    required: bool = False


class ContextConstraint(BaseModel):
    kind: str
    value: str
    required: bool = True


class ContextPreparationProfile(BaseModel):
    need: ContextNeed = "evidence_gathering"
    archetype: ContextArchetype = "general"
    facets: list[ContextFacet] = Field(default_factory=list)
    constraints: list[ContextConstraint] = Field(default_factory=list)
    source_hints: list[str] = Field(default_factory=list)
    expected_evidence_count: int = 1
    confidence: float = 0.5
    notes: list[str] = Field(default_factory=list)


class ContextCoverageReport(BaseModel):
    covered_facets: list[str] = Field(default_factory=list)
    missing_constraints: list[str] = Field(default_factory=list)
    direct_evidence_count: int = 0
    conflict_pair_coverage: bool = False
    actionability: Literal["full", "weak", "none", "conflict"] = "none"
    truncation_risk: Literal["low", "medium", "high"] = "low"
    source_agreement: float = 0.0
    weak_expansion_dependency: bool = False
    compactness_risk: Literal["low", "medium", "high"] = "low"
    gap_flags: list[str] = Field(default_factory=list)


class ContextSourceStats(BaseModel):
    source: str
    candidate_count: int = 0
    selected_count: int = 0


class PreparedContextDiagnostics(BaseModel):
    profile: ContextPreparationProfile = Field(default_factory=ContextPreparationProfile)
    source_counts: list[ContextSourceStats] = Field(default_factory=list)
    fused_candidate_count: int = 0
    selected_count: int = 0
    hard_constraints_extracted: list[str] = Field(default_factory=list)
    hard_constraints_satisfied: list[str] = Field(default_factory=list)
    hard_constraints_missing: list[str] = Field(default_factory=list)
    coverage: ContextCoverageReport = Field(default_factory=ContextCoverageReport)
    dropped_direct_evidence: int = 0
    weak_expansion_dependency: bool = False
    token_used: int = 0
    truncation_risk: Literal["low", "medium", "high"] = "low"
    latency_ms: float = 0.0
    compactness_score: float = 1.0
    provenance_coverage: float = 0.0

