# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PreparedWorkSourceType(StrEnum):
    WORK_PRODUCT = "work_product"
    PREDICTION = "prediction"


class PreparedWorkKind(StrEnum):
    REVIEW = "review"
    BUG = "bug"
    DOCS = "docs"
    DIFF = "diff"
    BRIEF = "brief"
    DRAFT = "draft"
    PREDICTION = "prediction"


class PreparedWorkActionKind(StrEnum):
    INSPECT = "inspect"
    EXPORT = "export"
    ADOPT = "adopt"
    DISMISS = "dismiss"
    FEEDBACK = "feedback"


class PreparedWorkAction(BaseModel):
    kind: PreparedWorkActionKind
    label: str
    tool: str | None = None
    endpoint: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class PreparedWorkDiagnosticRef(BaseModel):
    kind: Literal["file", "symbol", "doc", "record", "prediction", "work_product", "other"] = "other"
    id: str = ""
    path: str = ""
    reason: str = ""


class PreparedWorkCard(BaseModel):
    id: str
    source_id: str
    source_type: PreparedWorkSourceType
    kind: PreparedWorkKind
    title: str
    summary: str
    badge: str
    confidence_label: str
    freshness_label: str
    target_label: str
    evidence_count: int = 0
    created_at: float
    updated_at: float
    primary_action: PreparedWorkAction | None = None
    secondary_actions: list[PreparedWorkAction] = Field(default_factory=list)
    diagnostic_refs: list[PreparedWorkDiagnosticRef] = Field(default_factory=list)

