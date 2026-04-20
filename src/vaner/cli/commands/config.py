# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from vaner.models.config import (
    BackendConfig,
    ComputeConfig,
    ExplorationConfig,
    GatewayConfig,
    GenerationConfig,
    IntentConfig,
    MCPConfig,
    PrivacyConfig,
    ProxyConfig,
    VanerConfig,
)

logger = logging.getLogger(__name__)


def global_config_path() -> Path:
    env_override = os.environ.get("VANER_GLOBAL_CONFIG", "").strip()
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path.home() / ".config" / "vaner" / "config.toml"


def _load_toml_if_exists(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_config(repo_root: Path) -> VanerConfig:
    config_path = repo_root / ".vaner" / "config.toml"
    parsed_global = _load_toml_if_exists(global_config_path())
    parsed_local = _load_toml_if_exists(config_path)
    parsed: dict[str, object] = dict(parsed_global)
    for key, value in parsed_local.items():
        if isinstance(value, dict) and isinstance(parsed.get(key), dict):
            merged = dict(parsed.get(key, {}))
            merged.update(value)
            parsed[key] = merged
        else:
            parsed[key] = value

    backend_section = parsed.get("backend", {})
    generation_section = parsed.get("generation", {})
    privacy_section = parsed.get("privacy", {})
    proxy_section = parsed.get("proxy", {})
    gateway_section = parsed.get("gateway", {})
    mcp_section = parsed.get("mcp", {})
    intent_section = parsed.get("intent", {})
    compute_section = parsed.get("compute", {})
    exploration_section = parsed.get("exploration", {})
    limits_section = parsed.get("limits", {})

    backend = BackendConfig(**backend_section) if isinstance(backend_section, dict) else BackendConfig()
    privacy = PrivacyConfig(**privacy_section) if isinstance(privacy_section, dict) else PrivacyConfig()
    generation = GenerationConfig(**generation_section) if isinstance(generation_section, dict) else GenerationConfig()
    proxy = ProxyConfig(**proxy_section) if isinstance(proxy_section, dict) else ProxyConfig()
    if isinstance(gateway_section, dict):
        passthrough_section = gateway_section.get("passthrough", {})
        annotate_section = gateway_section.get("annotate", {})
        shadow_section = gateway_section.get("shadow", {})
        routes_section = gateway_section.get("routes", {})
        annotate_value = str(annotate_section.get("system_note", "off")) if isinstance(annotate_section, dict) else "off"
        if annotate_value not in {"off", "min", "full"}:
            annotate_value = "off"
        gateway = GatewayConfig(
            passthrough_enabled=bool(passthrough_section.get("enabled", False)) if isinstance(passthrough_section, dict) else False,
            routes={str(key): str(value) for key, value in routes_section.items()} if isinstance(routes_section, dict) else {},
            annotate_response_trailer=bool(annotate_section.get("response_trailer", False))
            if isinstance(annotate_section, dict)
            else False,
            annotate_system_note=annotate_value,  # type: ignore[arg-type]
            shadow_rate=float(shadow_section.get("rate", 0.0)) if isinstance(shadow_section, dict) else 0.0,
        )
    else:
        gateway = GatewayConfig()
    mcp = MCPConfig(**mcp_section) if isinstance(mcp_section, dict) else MCPConfig()
    if isinstance(intent_section, dict):
        skills_loop = intent_section.get("skills_loop", {})
        mapped_intent = dict(intent_section)
        if isinstance(skills_loop, dict):
            if "enabled" in skills_loop:
                mapped_intent["skills_loop_enabled"] = bool(skills_loop["enabled"])
            if "max_feedback_events_per_cycle" in skills_loop:
                mapped_intent["max_feedback_events_per_cycle"] = int(skills_loop["max_feedback_events_per_cycle"])
        mapped_intent.pop("skills_loop", None)
        intent = IntentConfig(**mapped_intent)
    else:
        intent = IntentConfig()
    compute = ComputeConfig(**compute_section) if isinstance(compute_section, dict) else ComputeConfig()
    if isinstance(exploration_section, dict):
        legacy_keys = sorted(
            {str(key) for key in exploration_section.keys() if str(key) == "embedding_device" or str(key).startswith("exploration_")}
        )
        if legacy_keys:
            logger.warning(
                "Legacy exploration config keys detected and ignored: %s",
                ", ".join(legacy_keys),
            )
        mapped_exploration = {
            "endpoint": exploration_section.get("endpoint", ""),
            "model": exploration_section.get("model", ""),
            "backend": exploration_section.get("backend", "auto"),
            "api_key": exploration_section.get("api_key", ""),
            "embedding_model": exploration_section.get("embedding_model", "all-MiniLM-L6-v2"),
        }
        exploration = ExplorationConfig(**mapped_exploration)
    else:
        exploration = ExplorationConfig()

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
        gateway=gateway,
        mcp=mcp,
        intent=intent,
        compute=compute,
        exploration=exploration,
    )


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        chunks: list[str] = []
        for key, item in value.items():
            escaped_key = str(key).replace('"', '\\"')
            chunks.append(f'"{escaped_key}" = {_toml_literal(item)}')
        return "{ " + ", ".join(chunks) + " }"
    escaped = str(value).replace('"', '\\"')
    return f'"{escaped}"'


def set_compute_value(repo_root: Path, key: str, value: Any) -> Path:
    return set_config_value(repo_root, "compute", key, value)


def set_config_value(repo_root: Path, section: str, key: str, value: Any) -> Path:
    config_path = repo_root / ".vaner" / "config.toml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    lines = config_path.read_text(encoding="utf-8").splitlines()
    section_start = None
    section_end = len(lines)
    for idx, line in enumerate(lines):
        if line.strip() == f"[{section}]":
            section_start = idx
            break

    if section_start is not None:
        for idx in range(section_start + 1, len(lines)):
            stripped = lines[idx].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section_end = idx
                break
        key_written = False
        for idx in range(section_start + 1, section_end):
            stripped = lines[idx].strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.split("=", 1)[0].strip() == key:
                lines[idx] = f"{key} = {_toml_literal(value)}"
                key_written = True
                break
        if not key_written:
            lines.insert(section_end, f"{key} = {_toml_literal(value)}")
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        lines.append(f"{key} = {_toml_literal(value)}")

    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path
