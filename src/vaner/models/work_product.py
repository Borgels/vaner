# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class WorkProductType(StrEnum):
    REVIEW_NOTE = "review_note"
    BUG_HYPOTHESIS = "bug_hypothesis"
    DOCS_DRIFT = "docs_drift"
    VIRTUAL_DIFF = "virtual_diff"
    RESEARCH_BRIEF = "research_brief"


class WorkProductStatus(StrEnum):
    CANDIDATE = "candidate"
    SELF_EVALUATED = "self_evaluated"
    SURFACED = "surfaced"
    DISMISSED = "dismissed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class WorkProductAdoptability(StrEnum):
    HIDDEN = "hidden"
    ADVISORY = "advisory"
    INSPECTABLE = "inspectable"
    EXPORTABLE = "exportable"


class WorkProductFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class WorkProductFeedbackState(StrEnum):
    NONE = "none"
    USEFUL = "useful"
    PARTIAL = "partial"
    IRRELEVANT = "irrelevant"


def _is_absolute_path(value: str) -> bool:
    if not value:
        return False
    return value.startswith("/") or (len(value) > 2 and value[1] == ":" and value[2] in {"\\", "/"})


def _validate_relative_path(value: str) -> str:
    cleaned = value.replace("\\", "/").strip()
    if _is_absolute_path(cleaned):
        raise ValueError("work product paths must be relative")
    if cleaned.startswith("../") or "/../" in cleaned or cleaned == "..":
        raise ValueError("work product paths must not traverse parents")
    return cleaned


class WorkProductEvidenceRef(BaseModel):
    kind: Literal["file", "symbol", "doc", "record", "other"] = "file"
    path: str = ""
    symbol: str = ""
    reason: str = ""
    confidence: float | None = None

    @field_validator("path")
    @classmethod
    def _path_must_be_relative(cls, value: str) -> str:
        return _validate_relative_path(value)


class WorkProductSourceSnapshot(BaseModel):
    project_id: str = "default"
    relative_paths: list[str] = Field(default_factory=list)
    file_hashes: dict[str, str] = Field(default_factory=dict)
    base_commit: str | None = None
    generated_at: float
    generator_version: str = "work_product.v1"
    config_id: str = "default"

    @field_validator("relative_paths")
    @classmethod
    def _relative_paths_must_be_relative(cls, value: list[str]) -> list[str]:
        return [_validate_relative_path(item) for item in value]

    @field_validator("file_hashes")
    @classmethod
    def _file_hash_keys_must_be_relative(cls, value: dict[str, str]) -> dict[str, str]:
        return {_validate_relative_path(key): str(hash_value) for key, hash_value in value.items()}


class WorkProductSelfEval(BaseModel):
    evidence_coverage: float = 0.0
    groundedness: float = 0.0
    contradiction_risk: float = 0.0
    stale_risk: float = 0.0
    goal_alignment: float = 0.0
    reason: str = ""


class WorkProduct(BaseModel):
    id: str
    type: WorkProductType
    title: str
    summary: str
    body: str
    evidence_refs: list[WorkProductEvidenceRef] = Field(default_factory=list)
    source_snapshot: WorkProductSourceSnapshot
    confidence: float = 0.0
    freshness: WorkProductFreshness = WorkProductFreshness.UNKNOWN
    expires_at: float | None = None
    status: WorkProductStatus = WorkProductStatus.CANDIDATE
    adoptability: WorkProductAdoptability = WorkProductAdoptability.HIDDEN
    provenance: dict[str, Any] = Field(default_factory=dict)
    self_eval: WorkProductSelfEval = Field(default_factory=WorkProductSelfEval)
    feedback_state: WorkProductFeedbackState = WorkProductFeedbackState.NONE
    created_at: float
    updated_at: float
    target_key: str = ""
    supersedes: str | None = None

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @model_validator(mode="after")
    def _exportable_requires_strong_state(self) -> WorkProduct:
        if self.adoptability == WorkProductAdoptability.EXPORTABLE and not self.can_export():
            raise ValueError("exportable work products must be fresh, active, and surfaced/self_evaluated")
        return self

    def can_export(self, *, now: float | None = None) -> bool:
        if self.adoptability != WorkProductAdoptability.EXPORTABLE:
            return False
        if self.freshness == WorkProductFreshness.STALE:
            return False
        if self.status in {
            WorkProductStatus.CANDIDATE,
            WorkProductStatus.DISMISSED,
            WorkProductStatus.EXPIRED,
            WorkProductStatus.SUPERSEDED,
        }:
            return False
        if now is not None and self.expires_at is not None and now >= self.expires_at:
            return False
        return True


class ExportedWorkProduct(BaseModel):
    id: str
    type: WorkProductType
    title: str
    body: str
    content_type: str = "text/markdown"
    source_snapshot: WorkProductSourceSnapshot
    evidence_refs: list[WorkProductEvidenceRef] = Field(default_factory=list)
