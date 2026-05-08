# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvidenceCandidate(BaseModel):
    key: str
    path: str = ""
    title: str = ""
    text: str = ""
    rank: int = 0
    score: float = 0.0


class AgenticRecallPlan(BaseModel):
    queries: list[str] = Field(default_factory=list)
    reason: str = ""


class EvidenceSupportSelection(BaseModel):
    support_keys: list[str] = Field(default_factory=list)
    coverage_note: str = ""
    answerability: Literal["full", "partial", "none"] = "none"


class AgenticPreparationTrace(BaseModel):
    planned_queries: list[str] = Field(default_factory=list)
    expanded_candidate_keys: list[str] = Field(default_factory=list)
    support_keys: list[str] = Field(default_factory=list)
    answerability: Literal["full", "partial", "none"] = "none"
    coverage_note: str = ""
    failed: bool = False
    failure_reason: str = ""


class AgenticPreparationConfig(BaseModel):
    max_queries: int = 6
    max_candidates: int = 96
    max_support_items: int = 12
    max_excerpt_chars: int = 2400
