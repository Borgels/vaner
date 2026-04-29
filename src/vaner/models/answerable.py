# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Answerability = Literal["full", "weak", "none", "conflict"]
EvidenceChannel = Literal["prediction", "vaner_resolve", "retrieval_floor", "external_rag"]
EvidenceSectionKind = Literal[
    "direct_answer_evidence",
    "supporting_context",
    "lower_confidence_context",
    "conflicts_and_cautions",
    "provenance",
]
TruncationRisk = Literal["low", "medium", "high"]
EvidenceAssemblyMode = Literal["off", "shadow", "advisory", "safe", "active"]
EvidenceRole = Literal[
    "direct_answer_evidence",
    "supporting_context",
    "conflict_evidence",
    "provenance_evidence",
    "lower_confidence_context",
    "background_context",
    "duplicate_context",
    "stale_context",
    "unsafe_or_misleading_context",
]
EvidenceDecision = Literal[
    "include",
    "include_protected",
    "dedupe",
    "compact_metadata",
    "demote",
    "omit_duplicate",
    "omit_stale",
    "omit_misleading",
    "transport_limited",
]


class ConflictNote(BaseModel):
    reason: str
    paths: list[str] = Field(default_factory=list)
    channels: list[EvidenceChannel] = Field(default_factory=list)


class AnswerableEvidenceItem(BaseModel):
    path: str
    title: str = ""
    source: str = ""
    channel: EvidenceChannel = "vaner_resolve"
    why_selected: str = ""
    excerpt: str = ""
    relevance_to_query: str = ""
    confidence: float = 0.0
    token_count: int = 0
    revision_or_hash: str | None = None
    truncated: bool = False


class AnswerableEvidenceSection(BaseModel):
    kind: EvidenceSectionKind
    items: list[AnswerableEvidenceItem] = Field(default_factory=list)


class EvidenceAssemblyDecision(BaseModel):
    path: str
    role: EvidenceRole
    decision: EvidenceDecision
    reason: str = ""
    protected_reason: str = ""
    token_delta: int = 0
    duplicate_of: str | None = None
    conflict_related: bool = False
    directness_score: float = 0.0
    source_reliability: float = 0.0
    would_change_output_in_shadow: bool = False


class EvidenceAssemblyMetadata(BaseModel):
    assembly_mode: EvidenceAssemblyMode = "off"
    quality_bias: str = "protect_recall"
    cost_sensitivity: str = "balanced"
    baseline_context_tokens: int = 0
    result_context_tokens: int = 0
    estimated_incremental_primary_cost: float = 0.0
    items_included: int = 0
    items_protected: int = 0
    items_deduped: int = 0
    items_metadata_compacted: int = 0
    items_demoted: int = 0
    items_omitted_duplicate: int = 0
    items_omitted_stale: int = 0
    items_transport_limited: int = 0
    decision_reasons: list[str] = Field(default_factory=list)
    protected_reasons: list[str] = Field(default_factory=list)
    technical_limit_hit: str | None = None
    token_delta: int = 0
    decisions: list[EvidenceAssemblyDecision] = Field(default_factory=list)


class AnswerabilityMetadata(BaseModel):
    answerability: Answerability = "none"
    answerability_reason: str = ""
    direct_evidence_count: int = 0
    supporting_evidence_count: int = 0
    lower_confidence_evidence_count: int = 0
    conflict_count: int = 0
    primary_paths: list[str] = Field(default_factory=list)
    omitted_relevant_paths: list[str] = Field(default_factory=list)
    token_budget_used: int = 0
    truncation_applied: bool = False
    truncation_risk: TruncationRisk = "low"
    direct_evidence_truncated: bool = False
    critical_excerpt_preserved: bool = True
    dropped_direct_evidence_count: int = 0
    evidence_channels_present: list[EvidenceChannel] = Field(default_factory=list)
    evidence_assembly: EvidenceAssemblyMetadata = Field(default_factory=EvidenceAssemblyMetadata)


class AnswerableBriefing(BaseModel):
    text: str = ""
    answer_plan: str = ""
    sections: list[AnswerableEvidenceSection] = Field(default_factory=list)
    metadata: AnswerabilityMetadata = Field(default_factory=AnswerabilityMetadata)
    conflicts: list[ConflictNote] = Field(default_factory=list)
