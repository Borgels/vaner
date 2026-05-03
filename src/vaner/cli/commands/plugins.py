# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
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


def _codex_plugin_target() -> Path:
    return _home() / ".codex" / "plugins" / "vaner-codex"


def _codex_plugin_cache_target(version: str) -> Path:
    return _home() / ".codex" / "plugins" / "cache" / "vaner-local" / "vaner-codex" / version


def _codex_config_path() -> Path:
    return _home() / ".codex" / "config.toml"


def _codex_hooks_path() -> Path:
    return _home() / ".codex" / "hooks.json"


PLUGIN_SURFACES: dict[str, Callable[[], Path]] = {
    "claude-code": _claude_code_plugin_target,
    "codex-cli": _codex_plugin_target,
}


def _packaged_claude_plugin_source() -> Path:
    return Path(__file__).resolve().parents[2] / "defaults" / "plugins" / "claude-code" / "vaner"


def _repo_claude_plugin_source() -> Path:
    return Path(__file__).resolve().parents[4] / "plugins" / "vaner"


def _packaged_codex_plugin_source() -> Path:
    return Path(__file__).resolve().parents[2] / "defaults" / "plugins" / "codex" / "vaner-codex"


def _repo_codex_plugin_source() -> Path:
    return Path(__file__).resolve().parents[4] / "plugins" / "vaner-codex"


def claude_code_plugin_source() -> Path:
    """Return the Vaner Claude Code plugin bundle.

    Wheels include the bundle under ``vaner/defaults``. Editable/source
    checkouts keep the canonical copy at repo-root ``plugins/vaner``.
    """

    packaged = _packaged_claude_plugin_source()
    if (packaged / ".claude-plugin" / "plugin.json").exists():
        return packaged
    return _repo_claude_plugin_source()


def codex_plugin_source() -> Path:
    """Return the Vaner Codex plugin bundle."""

    packaged = _packaged_codex_plugin_source()
    if (packaged / ".codex-plugin" / "plugin.json").exists():
        return packaged
    return _repo_codex_plugin_source()


def _chmod_plugin_scripts(target: Path) -> None:
    scripts_dir = target / "scripts"
    if not scripts_dir.exists():
        return
    for script in scripts_dir.glob("*"):
        if script.is_file() and script.suffix in {".sh", ".py"}:
            script.chmod(script.stat().st_mode | 0o755)


def _copy_plugin_tree(source: Path, target: Path, *, force: bool = False) -> None:
    if target.exists() and force:
        shutil.rmtree(target)
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _chmod_plugin_scripts(target)


def _codex_plugin_version(source: Path) -> str:
    manifest = json.loads((source / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "").strip()
    return version or "0.0.0"


def _enable_codex_plugin_config() -> bool:
    """Enable the local Vaner plugin in Codex's user config.

    Copying a bundle to ``~/.codex/plugins`` only makes it available. Codex
    treats ``[plugins."vaner-codex@vaner-local"] enabled = true`` as the
    install/enable bit the TUI writes from the Plugins menu.
    """

    path = _codex_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    section = '[plugins."vaner-codex@vaner-local"]'
    if section not in text:
        prefix = "" if not text or text.endswith("\n") else "\n"
        path.write_text(f"{text}{prefix}\n{section}\nenabled = true\n".lstrip("\n"), encoding="utf-8")
        return True

    pattern = re.compile(r'(?ms)^(\[plugins\."vaner-codex@vaner-local"\]\n)(.*?)(?=^\[|\Z)')
    match = pattern.search(text)
    if match is None:
        return False
    body = match.group(2)
    enabled_pattern = re.compile(r"(?m)^enabled\s*=\s*(true|false)\s*$")
    enabled_match = enabled_pattern.search(body)
    if enabled_match:
        if enabled_match.group(1) == "true":
            return False
        new_body = enabled_pattern.sub("enabled = true", body, count=1)
    else:
        new_body = body + ("" if body.endswith("\n") else "\n") + "enabled = true\n"
    path.write_text(text[: match.start(2)] + new_body + text[match.end(2) :], encoding="utf-8")
    return True


def _enable_codex_hooks_feature() -> bool:
    path = _codex_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if "[features]" not in text:
        prefix = "" if not text or text.endswith("\n") else "\n"
        path.write_text(f"{text}{prefix}\n[features]\ncodex_hooks = true\n".lstrip("\n"), encoding="utf-8")
        return True

    pattern = re.compile(r"(?ms)^(\[features\]\n)(.*?)(?=^\[|\Z)")
    match = pattern.search(text)
    if match is None:
        return False
    body = match.group(2)
    enabled_pattern = re.compile(r"(?m)^codex_hooks\s*=\s*(true|false)\s*$")
    enabled_match = enabled_pattern.search(body)
    if enabled_match:
        if enabled_match.group(1) == "true":
            return False
        new_body = enabled_pattern.sub("codex_hooks = true", body, count=1)
    else:
        new_body = body + ("" if body.endswith("\n") else "\n") + "codex_hooks = true\n"
    path.write_text(text[: match.start(2)] + new_body + text[match.end(2) :], encoding="utf-8")
    return True


def _vaner_codex_global_hooks(target: Path) -> dict[str, list[dict[str, object]]]:
    scripts = target / "scripts"
    return {
        "SessionStart": [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"python3 {scripts / 'codex_session_start.py'}", "timeout": 5}],
            }
        ],
        "UserPromptSubmit": [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"python3 {scripts / 'codex_prompt_submit.py'}", "timeout": 3}],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"python3 {scripts / 'codex_tool_event.py'}", "timeout": 2}],
            }
        ],
        "Stop": [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"python3 {scripts / 'codex_tool_event.py'}", "timeout": 2}],
            }
        ],
    }


def _is_vaner_codex_hook(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks") or []:
        if not isinstance(hook, dict):
            continue
        command = str(hook.get("command") or "")
        if "/vaner-codex/scripts/codex_" in command:
            return True
    return False


def _install_codex_global_hooks(target: Path) -> bool:
    """Install Vaner's Codex hooks through the runtime-supported hook file.

    Codex 0.128 executes config-layer hooks from ``~/.codex/hooks.json``.
    Plugin-local ``hooks.json`` is kept in the bundle for future Codex
    support, but the installer writes this global hook layer so current
    clients actually emit prompt/session signals.
    """

    path = _codex_hooks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    hooks_root = payload.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        hooks_root = {}
        payload["hooks"] = hooks_root

    before = json.dumps(payload, sort_keys=True)
    for event, managed_entries in _vaner_codex_global_hooks(target).items():
        existing = hooks_root.get(event)
        if not isinstance(existing, list):
            existing = []
        existing = [entry for entry in existing if not _is_vaner_codex_hook(entry)]
        hooks_root[event] = [*existing, *managed_entries]

    after = json.dumps(payload, sort_keys=True)
    if after == before and path.exists():
        return False
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True


def _install_codex_plugin_activation(source: Path, target: Path, *, force: bool = False) -> bool:
    version = _codex_plugin_version(source)
    cache_target = _codex_plugin_cache_target(version)
    cache_preexisting = cache_target.exists()
    _copy_plugin_tree(source, cache_target, force=force)
    _copy_plugin_tree(source, target, force=force)
    config_changed = _enable_codex_plugin_config()
    hooks_feature_changed = _enable_codex_hooks_feature()
    hooks_changed = _install_codex_global_hooks(target)
    return (not cache_preexisting) or config_changed or hooks_feature_changed or hooks_changed


def write_plugin_for_client(client_id: str, *, dry_run: bool = False, force: bool = False) -> PluginResult:
    if client_id not in PLUGIN_SURFACES:
        return PluginResult(client_id=client_id, path=None, action="unsupported")

    source = claude_code_plugin_source() if client_id == "claude-code" else codex_plugin_source()
    marker_dir = ".claude-plugin" if client_id == "claude-code" else ".codex-plugin"
    marker = source / marker_dir / "plugin.json"
    if not marker.exists():
        return PluginResult(client_id=client_id, path=None, action="failed", error=f"{client_id} plugin bundle not found at {source}")

    target = PLUGIN_SURFACES[client_id]()
    target_marker = target / marker_dir / "plugin.json"
    if target_marker.exists() and not force:
        if dry_run:
            return PluginResult(client_id=client_id, path=target, action="skipped")
        if client_id == "codex-cli":
            try:
                changed = _install_codex_plugin_activation(source, target, force=False)
            except Exception as exc:  # pragma: no cover - defensive I/O guard
                return PluginResult(client_id=client_id, path=target, action="failed", error=str(exc))
            return PluginResult(client_id=client_id, path=target, action="updated" if changed else "skipped")
        return PluginResult(client_id=client_id, path=target, action="skipped")
    action: PluginAction = "updated" if target.exists() else "added"
    if dry_run:
        return PluginResult(client_id=client_id, path=target, action=action)

    try:
        _copy_plugin_tree(source, target, force=force)
        if client_id == "codex-cli":
            _install_codex_plugin_activation(source, target, force=force)
    except Exception as exc:  # pragma: no cover - defensive I/O guard
        return PluginResult(client_id=client_id, path=target, action="failed", error=str(exc))
    return PluginResult(client_id=client_id, path=target, action=action)
