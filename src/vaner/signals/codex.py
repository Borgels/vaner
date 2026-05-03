# SPDX-License-Identifier: Apache-2.0
"""Codex CLI prompt/session signal contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CapturePolicy = Literal["local_raw_redacted", "hash_only"]


class CodexPromptSignal(BaseModel):
    """A locally-redacted Codex prompt submission observation."""

    host_app: str = Field(default="codex-cli", min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=256)
    turn_id: str | None = Field(default=None, max_length=256)
    workspace_id: str | None = Field(default=None, max_length=2048)
    timestamp: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=256)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_text_redacted: str | None = Field(default=None, max_length=200_000)
    length_chars: int = Field(ge=0)
    capture_policy: CapturePolicy = "local_raw_redacted"
    source_event_id: str | None = Field(default=None, max_length=256)


__all__ = ["CapturePolicy", "CodexPromptSignal"]
