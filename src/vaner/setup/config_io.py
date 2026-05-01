# SPDX-License-Identifier: Apache-2.0
"""Read and write Simple-Mode setup sections in ``.vaner/config.toml``."""

from __future__ import annotations

import logging
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from vaner.setup.answers import SetupAnswers

logger = logging.getLogger(__name__)


def read_setup_section(repo_root: Path) -> dict[str, Any]:
    """Read the raw ``[setup]`` table from ``.vaner/config.toml``."""

    return _read_config_section(repo_root, "setup")


def read_policy_section(repo_root: Path) -> dict[str, Any]:
    """Read the raw ``[policy]`` table from ``.vaner/config.toml``."""

    return _read_config_section(repo_root, "policy")


def persist_setup_and_policy(
    repo_root: Path,
    answers: SetupAnswers,
    bundle_id: str,
    *,
    completed_at: datetime | None = None,
) -> Path:
    """Write wizard answers and selected policy bundle to ``config.toml``.

    The update is idempotent for the same inputs and leaves unrelated
    sections/keys untouched.
    """

    config_path = _init_repo_config(repo_root)
    text = config_path.read_text(encoding="utf-8")
    setup_values: dict[str, object] = {
        "mode": "simple",
        "work_styles": list(answers.work_styles),
        "priority": answers.priority,
        "compute_posture": answers.compute_posture,
        "cloud_posture": answers.cloud_posture,
        "background_posture": answers.background_posture,
        "version": 1,
    }
    if completed_at is not None:
        setup_values["completed_at"] = completed_at.isoformat()
    text = update_toml_section(text, "setup", setup_values)
    text = update_toml_section(
        text,
        "policy",
        {"selected_bundle_id": bundle_id, "auto_select": True},
    )
    _atomic_write_text(config_path, text)
    return config_path


def persist_runtime_recommendation(repo_root: Path, recommendation: dict[str, Any]) -> Path:
    """Persist the concrete runtime/model selected by setup recommendation."""

    config_path = _init_repo_config(repo_root)
    text = config_path.read_text(encoding="utf-8")
    selected = recommendation.get("selected", {})
    if not isinstance(selected, dict):
        selected = {}
    runtime = str(selected.get("runtime") or "ollama")
    model_id = str(selected.get("model_id") or selected.get("id") or "")
    base_url = str(selected.get("base_url") or "http://127.0.0.1:11434/v1")
    params = selected.get("params") if isinstance(selected.get("params"), dict) else {}
    reasoning_mode = str(params.get("reasoning_mode") or "allowed")
    max_response_tokens = int(params.get("max_response_tokens") or 3072)
    reasoning_token_budget = int(params.get("reasoning_token_budget") or 4096)
    hardware = recommendation.get("hardware", {})
    memory_source = hardware.get("memory_source") if isinstance(hardware, dict) else None
    accelerator_type = hardware.get("accelerator_type") if isinstance(hardware, dict) else None
    device = "auto"
    if accelerator_type == "nvidia":
        device = "cuda"
    elif accelerator_type == "apple_silicon":
        device = "mps"
    elif memory_source in {"system", "cpu"}:
        device = "cpu"

    text = update_toml_section(
        text,
        "backend",
        {
            "name": runtime,
            "base_url": base_url,
            "model": model_id,
            "api_key_env": "",
            "prefer_local": True,
            "reasoning_mode": reasoning_mode,
            "max_response_tokens": max_response_tokens,
            "reasoning_token_budget": reasoning_token_budget,
        },
    )
    text = update_toml_section(
        text,
        "exploration",
        {
            "exploration_endpoint": base_url.removesuffix("/v1") if runtime == "ollama" else base_url,
            "exploration_model": model_id,
            "exploration_backend": "ollama" if runtime == "ollama" else "openai",
        },
    )
    text = update_toml_section(
        text,
        "compute",
        {
            "device": device,
            "embedding_device": device if device in {"cuda", "mps"} else "cpu",
        },
    )
    _atomic_write_text(config_path, text)
    return config_path


def toml_literal(value: object) -> str:
    """Render a Python scalar/list as the TOML literal shape setup writes."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return '""'
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, str):
                escaped = item.replace("\\", "\\\\").replace('"', '\\"')
                items.append(f'"{escaped}"')
            else:
                items.append(toml_literal(item))
        return "[" + ", ".join(items) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def update_toml_section(text: str, section: str, values: dict[str, object]) -> str:
    """Update keys within one TOML section, preserving unrelated content."""

    if not values:
        return text
    lines = text.splitlines()
    header = f"[{section}]"
    start: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == header:
            start = idx
            break
    if start is None:
        lines.append("")
        lines.append(header)
        for key, val in values.items():
            lines.append(f"{key} = {toml_literal(val)}")
        out = "\n".join(lines)
        return out + ("\n" if not out.endswith("\n") else "")

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = idx
            break
    remaining = dict(values)
    for idx in range(start + 1, end):
        stripped = lines[idx].lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[idx] = f"{key} = {toml_literal(remaining.pop(key))}"
    if remaining:
        insert_at = end
        for key, val in remaining.items():
            lines.insert(insert_at, f"{key} = {toml_literal(val)}")
            insert_at += 1
    out = "\n".join(lines)
    return out + ("\n" if not out.endswith("\n") else "")


def _read_config_section(repo_root: Path, section_name: str) -> dict[str, Any]:
    config_path = repo_root / ".vaner" / "config.toml"
    if not config_path.exists():
        return {}
    try:
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        logger.warning("Ignoring invalid Vaner setup config TOML at %s: %s", config_path, exc)
        return {}
    except OSError as exc:
        logger.warning("Unable to read Vaner setup config at %s: %s", config_path, exc)
        return {}
    section = parsed.get(section_name, {})
    return section if isinstance(section, dict) else {}


def _atomic_write_text(path: Path, text: str) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            # Successful replace moves the temp file into place before cleanup.
            pass


def _init_repo_config(repo_root: Path) -> Path:
    # Keep setup config I/O behavior aligned with `vaner init` without
    # importing Typer/Rich-heavy modules until a write is actually needed.
    from vaner.cli.commands.init import init_repo

    return init_repo(repo_root)
