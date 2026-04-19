# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ArtefactKind(StrEnum):
    FILE_SUMMARY = "file_summary"
    DIR_SUMMARY = "dir_summary"
    DIFF_SUMMARY = "diff_summary"
    REPO_INDEX = "repo_index"


class Artefact(BaseModel):
    key: str
    kind: ArtefactKind
    source_path: str
    source_mtime: float
    generated_at: float
    model: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    relevance_score: float = 0.0
    access_count: int = 0
    last_accessed: float | None = None
    signal_id: str | None = None
