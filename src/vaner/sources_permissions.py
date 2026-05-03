# SPDX-License-Identifier: Apache-2.0
"""Daemon/CLI source permission helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from vaner.intent.connectors.global_client_plans import GlobalClientPlansAdapter
from vaner.setup.config_io import update_toml_section
from vaner.store.artefacts import ArtefactStore


def build_sources_permissions(config: Any, *, artefact_counts: dict[str, int] | None = None) -> dict[str, Any]:
    intent_cfg = config.sources.intent_artefacts
    global_cfg = intent_cfg.global_client_plans
    adapter = GlobalClientPlansAdapter(
        workspace_root=config.repo_root,
        selected_clients=tuple(global_cfg.selected_clients),
        allowed_roots=tuple(global_cfg.allowed_roots),
        include_rollout_summaries=global_cfg.include_rollout_summaries,
        max_file_bytes=global_cfg.max_file_bytes,
        max_files=global_cfg.max_files,
        excludelist=tuple(global_cfg.excludelist),
    )
    decisions = adapter.inspect_candidates()
    accepted = [item for item in decisions if item.status == "accepted"]
    skipped = [item for item in decisions if item.status != "accepted"]
    return {
        "workspace": str(config.repo_root.resolve()),
        "privacy_boundary": {
            "raw_live_typing": "not captured unless a host exposes an official draft adapter",
            "codex_live_intent": "submitted_prompt_only",
            "global_plans": "opt_in_workspace_matched",
        },
        "sources": {
            "repo_local_plans": {
                "id": "repo_local_plans",
                "enabled": bool(intent_cfg.enabled and intent_cfg.tiers.T1 != "off"),
                "default": True,
                "privacy_zone": "workspace",
                "accepted_count": int((artefact_counts or {}).get("local_plan", 0)),
            },
            "global_client_plans": {
                "id": "global_client_plans",
                "enabled": bool(global_cfg.enabled),
                "default": False,
                "privacy_zone": "global_local_files_workspace_matched",
                "selected_clients": list(global_cfg.selected_clients),
                "allowed_roots": list(global_cfg.allowed_roots),
                "workspace_match_policy": global_cfg.workspace_match_policy,
                "include_rollout_summaries": global_cfg.include_rollout_summaries,
                "accepted_count": len(accepted),
                "skipped_count": len(skipped),
                "accepted_examples": [_decision_dict(item) for item in accepted[:8]],
                "skipped_examples": [_decision_dict(item) for item in skipped[:8]],
                "ingested_count": int((artefact_counts or {}).get("global_client_plans", 0)),
            },
        },
        "updated_at": time.time(),
    }


async def artefact_counts_by_connector(repo_root: Path) -> dict[str, int]:
    store = ArtefactStore(repo_root / ".vaner" / "artefacts.db")
    await store.initialize()
    rows = await store.list_intent_artefacts(limit=500)
    counts: dict[str, int] = {}
    for row in rows:
        connector = str(row.get("connector") or "unknown")
        counts[connector] = counts.get(connector, 0) + 1
    return counts


def apply_sources_permissions(repo_root: Path, payload: dict[str, Any]) -> Path:
    source_payload = payload.get("global_client_plans", payload)
    if not isinstance(source_payload, dict):
        source_payload = {}
    values: dict[str, object] = {}
    for key in (
        "enabled",
        "selected_clients",
        "allowed_roots",
        "include_rollout_summaries",
        "max_file_bytes",
        "max_files",
        "excludelist",
    ):
        if key in source_payload:
            values[key] = source_payload[key]
    if not values:
        values["enabled"] = True
    config_path = repo_root / ".vaner" / "config.toml"
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("", encoding="utf-8")
    text = config_path.read_text(encoding="utf-8")
    text = update_toml_section(text, "sources.intent_artefacts.global_client_plans", values)
    config_path.write_text(text, encoding="utf-8")
    return config_path


def _decision_dict(item: Any) -> dict[str, Any]:
    path = str(item.path)
    return {
        "path": path,
        "display_path": _display_path(path),
        "client": item.client,
        "status": item.status,
        "reason": item.reason,
        "matched_by": item.matched_by,
    }


def _display_path(path: str) -> str:
    home = str(Path.home())
    return path.replace(home, "~", 1) if path.startswith(home) else path


def dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)

