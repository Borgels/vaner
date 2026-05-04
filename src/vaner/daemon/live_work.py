# SPDX-License-Identifier: Apache-2.0
"""Small runtime journal for live worker/prediction progress.

The precompute worker owns the live engine process, while the HTTP daemon is a
control plane. This JSONL journal is the narrow bridge between them: append-only
structured progress events, bounded on write, and safe for the cockpit to tail.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal

LIVE_WORK_EVENTS_FILENAME = "live_work_events.jsonl"
MAX_LIVE_WORK_EVENTS = 500

LiveEntityType = Literal["prediction", "scenario", "work_product", "worker"]


def live_work_events_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "runtime" / LIVE_WORK_EVENTS_FILENAME


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            # os.replace already moved the temp file into place.
            pass


def append_live_work_event(
    repo_root: Path,
    *,
    entity_type: LiveEntityType,
    entity_id: str,
    stage: str,
    status: str,
    summary: str,
    cycle_id: str | None = None,
    job_id: str | None = None,
    scenario_id: str | None = None,
    targets: list[str] | None = None,
    model: str | None = None,
    latency_ms: float | None = None,
    token_usage: dict[str, Any] | None = None,
    artifact_kind: str | None = None,
    safe_preview: str | None = None,
    metadata: dict[str, Any] | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    event = {
        "event_id": f"lw-{uuid.uuid4().hex[:16]}",
        "ts": float(ts or time.time()),
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "stage": str(stage),
        "status": str(status),
        "summary": str(summary),
        "cycle_id": cycle_id,
        "job_id": job_id,
        "scenario_id": scenario_id,
        "targets": [str(target) for target in (targets or []) if target],
        "model": model,
        "latency_ms": latency_ms,
        "token_usage": token_usage or {},
        "artifact_kind": artifact_kind,
        "safe_preview": safe_preview,
        "metadata": metadata or {},
    }
    path = live_work_events_path(repo_root)
    existing = read_live_work_events(repo_root, limit=MAX_LIVE_WORK_EVENTS)
    rows = [*existing, event][-MAX_LIVE_WORK_EVENTS:]
    _atomic_write_text(path, "".join(f"{json.dumps(row, sort_keys=True)}\n" for row in rows))
    return event


def read_live_work_events(
    repo_root: Path,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    path = live_work_events_path(repo_root)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if entity_type and str(row.get("entity_type") or "") != entity_type:
            continue
        if entity_id and str(row.get("entity_id") or "") != entity_id:
            continue
        events.append(row)
    return events[-max(1, min(1000, int(limit))) :]
