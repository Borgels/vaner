# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkingSet(BaseModel):
    session_id: str
    artefact_keys: list[str] = Field(default_factory=list)
    updated_at: float
    reason: str = "signal_recency"


class SessionState(BaseModel):
    id: str
    repo_path: str
    started_at: float
    branch: str
    recent_files: list[str] = Field(default_factory=list)
