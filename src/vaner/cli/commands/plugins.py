# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PluginAction = Literal["added", "updated", "skipped", "failed", "unsupported"]


@dataclass(slots=True)
class PluginResult:
    client_id: str
    path: Path | None
    action: PluginAction
    error: str | None = None


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _claude_code_plugin_target() -> Path:
    return _home() / ".claude" / "plugins" / "vaner"


PLUGIN_SURFACES: dict[str, Path] = {
    "claude-code": _claude_code_plugin_target(),
}


def _packaged_claude_plugin_source() -> Path:
    return Path(__file__).resolve().parents[2] / "defaults" / "plugins" / "claude-code" / "vaner"


def _repo_claude_plugin_source() -> Path:
    return Path(__file__).resolve().parents[4] / "plugins" / "vaner"


def claude_code_plugin_source() -> Path:
    """Return the Vaner Claude Code plugin bundle.

    Wheels include the bundle under ``vaner/defaults``. Editable/source
    checkouts keep the canonical copy at repo-root ``plugins/vaner``.
    """

    packaged = _packaged_claude_plugin_source()
    if (packaged / ".claude-plugin" / "plugin.json").exists():
        return packaged
    return _repo_claude_plugin_source()


def write_plugin_for_client(client_id: str, *, dry_run: bool = False, force: bool = False) -> PluginResult:
    if client_id != "claude-code":
        return PluginResult(client_id=client_id, path=None, action="unsupported")

    source = claude_code_plugin_source()
    marker = source / ".claude-plugin" / "plugin.json"
    if not marker.exists():
        return PluginResult(client_id=client_id, path=None, action="failed", error=f"Claude Code plugin bundle not found at {source}")

    target = _claude_code_plugin_target()
    target_marker = target / ".claude-plugin" / "plugin.json"
    if target_marker.exists() and not force:
        return PluginResult(client_id=client_id, path=target, action="skipped")
    action: PluginAction = "updated" if target.exists() else "added"
    if dry_run:
        return PluginResult(client_id=client_id, path=target, action=action)

    try:
        if target.exists() and force:
            shutil.rmtree(target)
        shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for script in (target / "scripts").glob("*"):
            if script.is_file() and script.suffix in {".sh", ".py"}:
                script.chmod(script.stat().st_mode | 0o755)
    except Exception as exc:  # pragma: no cover - defensive I/O guard
        return PluginResult(client_id=client_id, path=target, action="failed", error=str(exc))
    return PluginResult(client_id=client_id, path=target, action=action)
