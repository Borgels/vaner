# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

from vaner import __version__
from vaner.cli.commands.config import load_config
from vaner.cli.commands.daemon import COCKPIT_PROCESS, DAEMON_PROCESS, process_status
from vaner.daemon.preflight import check_inotify_budget, check_repo_root
from vaner.store.scenarios import ScenarioStore


def _probe(url: str, timeout: float = 1.5) -> tuple[bool, str]:
    try:
        response = httpx.get(url, timeout=timeout)
        return response.status_code == 200, f"status={response.status_code}"
    except Exception as exc:
        return False, str(exc)


def _backend_reachable(base_url: str) -> tuple[bool, str]:
    stripped = base_url.rstrip("/")
    if not stripped:
        return False, "backend URL is unset"
    tags_url = f"{stripped.replace('/v1', '')}/api/tags" if "/v1" in stripped else f"{stripped}/api/tags"
    ok, detail = _probe(tags_url, timeout=1.2)
    return ok, detail


def _scenario_counts(repo_root: Path) -> dict[str, int]:
    async def _counts() -> dict[str, int]:
        store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
        await store.initialize()
        return await store.freshness_counts()

    try:
        return asyncio.run(_counts())
    except Exception:
        return {"fresh": 0, "recent": 0, "stale": 0, "total": 0}


def _release_probe(enabled: bool) -> dict[str, object]:
    if not enabled:
        return {"ok": True, "detail": "disabled"}
    try:
        response = httpx.get("https://pypi.org/pypi/vaner/json", timeout=0.3)
        latest = str(response.json().get("info", {}).get("version", "")).strip()
        up_to_date = (not latest) or latest == __version__
        return {
            "ok": up_to_date,
            "detail": f"installed={__version__} latest={latest or 'unknown'}",
            "latest": latest,
        }
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "latest": ""}


def runtime_snapshot(repo_root: Path, cockpit_url: str) -> dict[str, object]:
    config = load_config(repo_root)
    daemon_state = process_status(repo_root, DAEMON_PROCESS)
    cockpit_state = process_status(repo_root, COCKPIT_PROCESS)
    cockpit_ok, cockpit_detail = _probe(f"{cockpit_url.rstrip('/')}/health", timeout=1.8)
    backend_ok, backend_detail = _backend_reachable(config.backend.base_url)
    root_check = check_repo_root(repo_root, force=False)
    inotify_check = check_inotify_budget(repo_root)
    update_check = _release_probe(enabled=os.environ.get("VANER_DOCTOR_CHECK_UPDATES", "1").strip() != "0")

    return {
        "repo_root": str(repo_root),
        "config": config,
        "daemon": daemon_state,
        "cockpit_process": cockpit_state,
        "daemon_pid_alive": bool(daemon_state["running"]),
        "cockpit_pid_alive": bool(cockpit_state["running"]),
        "cockpit_reachable": cockpit_ok,
        "cockpit_detail": cockpit_detail,
        "backend_reachable": backend_ok,
        "backend_detail": backend_detail,
        "inotify_headroom_pct": inotify_check.get("headroom_pct", 100.0),
        "inotify": inotify_check,
        "repo_root_sensible": bool(root_check.get("ok")),
        "repo_root_detail": str(root_check.get("detail", "")),
        "repo_root_fix": str(root_check.get("fix", "")),
        "cli_up_to_date": bool(update_check.get("ok")),
        "cli_update_detail": str(update_check.get("detail", "")),
        "scenario_counts": _scenario_counts(repo_root),
    }
