# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class PrivacyConfig(BaseModel):
    allowed_paths: list[str] = Field(default_factory=lambda: ["."])
    excluded_patterns: list[str] = Field(default_factory=lambda: ["*.env", "*.key", "*.pem", "credentials*", "secrets*"])
    redact_patterns: list[str] = Field(default_factory=list)
    telemetry: str = "local"


class BackendConfig(BaseModel):
    name: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    prefer_local: bool = True
    fallback_enabled: bool = False
    fallback_base_url: str | None = None
    fallback_model: str | None = None
    fallback_api_key_env: str = "OPENAI_API_KEY"
    remote_budget_per_hour: int = 60


class VanerConfig(BaseModel):
    repo_root: Path
    store_path: Path
    telemetry_path: Path
    max_age_seconds: int = 3600
    max_context_tokens: int = 4096
    backend: BackendConfig = Field(default_factory=BackendConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
