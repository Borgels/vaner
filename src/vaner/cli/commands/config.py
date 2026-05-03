# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
import os
import tomllib
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from vaner.models.config import (
    BackendConfig,
    ComputeConfig,
    ExplorationConfig,
    GatewayConfig,
    GenerationConfig,
    IntegrationsConfig,
    IntentConfig,
    MCPConfig,
    PolicyConfig,
    PrivacyConfig,
    ProxyConfig,
    RefinementConfig,
    SetupConfig,
    SourcesConfig,
    VanerConfig,
)

logger = logging.getLogger(__name__)
ConfigModelT = TypeVar("ConfigModelT", bound=BaseModel)


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
    except tomllib.TOMLDecodeError as exc:
        logger.warning("Ignoring invalid Vaner config TOML at %s: %s", path, exc)
        return {}
    except OSError as exc:
        logger.warning("Unable to read Vaner config at %s: %s", path, exc)
        return {}


def _section_dict(parsed: dict[str, object], section_name: str) -> dict[str, object]:
    section = parsed.get(section_name, {})
    return section if isinstance(section, dict) else {}


def _build_section(
    model: type[ConfigModelT],
    section_name: str,
    section: object,
) -> ConfigModelT:
    if not isinstance(section, dict):
        return model()
    try:
        return model(**section)
    except ValidationError as exc:
        logger.warning("Ignoring invalid Vaner config section [%s]: %s", section_name, exc)
        return model()


def _coerce_limit(limits_section: object, key: str, default: int) -> int:
    if not isinstance(limits_section, dict):
        return default
    raw = limits_section.get(key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid Vaner config value [limits].%s=%r; using %s", key, raw, default)
        return default


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

    backend_section = _section_dict(parsed, "backend")
    generation_section = _section_dict(parsed, "generation")
    privacy_section = _section_dict(parsed, "privacy")
    proxy_section = _section_dict(parsed, "proxy")
    gateway_section = _section_dict(parsed, "gateway")
    mcp_section = _section_dict(parsed, "mcp")
    intent_section = _section_dict(parsed, "intent")
    compute_section = _section_dict(parsed, "compute")
    exploration_section = _section_dict(parsed, "exploration")
    sources_section = _section_dict(parsed, "sources")
    refinement_section = _section_dict(parsed, "refinement")
    integrations_section = _section_dict(parsed, "integrations")
    setup_section = _section_dict(parsed, "setup")
    policy_section = _section_dict(parsed, "policy")
    limits_section = _section_dict(parsed, "limits")

    backend = _build_section(BackendConfig, "backend", backend_section)
    privacy = _build_section(PrivacyConfig, "privacy", privacy_section)
    generation = _build_section(GenerationConfig, "generation", generation_section)
    proxy = _build_section(ProxyConfig, "proxy", proxy_section)
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
    mcp = _build_section(MCPConfig, "mcp", mcp_section)
    if isinstance(intent_section, dict):
        skills_loop_section = intent_section.get("skills_loop", {})
        skill_roots = intent_section.get("skill_roots", [".cursor/skills", ".claude/skills", "skills"])
        intent = IntentConfig(
            enabled=bool(intent_section.get("enabled", True)),
            include_global_skills=bool(intent_section.get("include_global_skills", True)),
            skill_roots=[str(item) for item in skill_roots]
            if isinstance(skill_roots, list)
            else [".cursor/skills", ".claude/skills", "skills"],
            lookback_turns=int(intent_section.get("lookback_turns", 8)),
            skills_loop_enabled=bool(skills_loop_section.get("enabled", True)) if isinstance(skills_loop_section, dict) else True,
            max_feedback_events_per_cycle=int(
                skills_loop_section.get(
                    "max_feedback_events_per_cycle",
                    skills_loop_section.get("max_candidates", 5),
                )
            )
            if isinstance(skills_loop_section, dict)
            else 5,
        )
    else:
        intent = IntentConfig()
    compute = _build_section(ComputeConfig, "compute", compute_section)
    if isinstance(exploration_section, dict):
        mapped_exploration = {
            "exploration_endpoint": exploration_section.get("endpoint", ""),
            "exploration_model": exploration_section.get("model", ""),
            "exploration_backend": exploration_section.get("backend", "auto"),
            "embedding_model": exploration_section.get("embedding_model", "all-MiniLM-L6-v2"),
            "embedding_device": exploration_section.get("embedding_device", "cpu"),
        }
        exploration = ExplorationConfig(**mapped_exploration)
    else:
        exploration = ExplorationConfig()
    sources = _build_section(SourcesConfig, "sources", sources_section)
    refinement = _build_section(RefinementConfig, "refinement", refinement_section)
    integrations = _build_section(IntegrationsConfig, "integrations", integrations_section)
    setup = _build_section(SetupConfig, "setup", setup_section)
    policy = _build_section(PolicyConfig, "policy", policy_section)

    max_age_seconds = _coerce_limit(limits_section, "max_age_seconds", 3600)
    max_context_tokens = _coerce_limit(limits_section, "max_context_tokens", 4096)

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
        sources=sources,
        refinement=refinement,
        integrations=integrations,
        setup=setup,
        policy=policy,
    )


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value)
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

    # lgtm[py/clear-text-storage-sensitive-data] Config stores env-var names
    # such as backend.api_key_env, not the secret value itself.
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path
