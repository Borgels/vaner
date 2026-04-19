# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import tomllib
from pathlib import Path

from vaner.models.config import BackendConfig, GenerationConfig, PrivacyConfig, ProxyConfig, VanerConfig


def load_config(repo_root: Path) -> VanerConfig:
    config_path = repo_root / ".vaner" / "config.toml"
    parsed: dict[str, object] = {}
    if config_path.exists():
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))

    backend_section = parsed.get("backend", {})
    generation_section = parsed.get("generation", {})
    privacy_section = parsed.get("privacy", {})
    proxy_section = parsed.get("proxy", {})
    limits_section = parsed.get("limits", {})

    backend = BackendConfig(**backend_section) if isinstance(backend_section, dict) else BackendConfig()
    privacy = PrivacyConfig(**privacy_section) if isinstance(privacy_section, dict) else PrivacyConfig()
    generation = GenerationConfig(**generation_section) if isinstance(generation_section, dict) else GenerationConfig()
    proxy = ProxyConfig(**proxy_section) if isinstance(proxy_section, dict) else ProxyConfig()

    max_age_seconds = int(limits_section.get("max_age_seconds", 3600)) if isinstance(limits_section, dict) else 3600
    max_context_tokens = int(limits_section.get("max_context_tokens", 4096)) if isinstance(limits_section, dict) else 4096

    store_path = repo_root / ".vaner" / "store.db"
    telemetry_path = repo_root / ".vaner" / "telemetry.db"
    return VanerConfig(
        repo_root=repo_root,
        store_path=store_path,
        telemetry_path=telemetry_path,
        max_age_seconds=max_age_seconds,
        max_context_tokens=max_context_tokens,
        backend=backend,
        privacy=privacy,
        generation=generation,
        proxy=proxy,
    )
