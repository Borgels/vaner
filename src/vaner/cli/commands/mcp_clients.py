# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

ConfigKind = Literal[
    "json-mcpServers",
    "json-servers",
    "json-context_servers",
    "yaml-continue",
    "cli-claude",
    "cli-codex",
]

WriteAction = Literal["added", "updated", "skipped", "failed"]


class ClientStatus(StrEnum):
    INSTALLED = "installed"
    CONFIGURED = "configured"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ClientSpec:
    id: str
    label: str
    kind: ConfigKind
    detect: Callable[[Path], Path | None]
    config_path: Callable[[Path], Path | None]
    manual_snippet_hint: str


@dataclass(slots=True)
class DetectedClient:
    spec: ClientSpec
    status: ClientStatus
    path: Path | None
    detail: str = ""


@dataclass(slots=True)
class WriteResult:
    client_id: str
    path: Path | None
    action: WriteAction
    backup: Path | None = None
    error: str | None = None
    manual_snippet: str | None = None


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _platform() -> str:
    if os.name == "nt":
        return "windows"
    if os.uname().sysname.lower() == "darwin":
        return "darwin"
    return "linux"


def _appdata() -> Path | None:
    raw = os.environ.get("APPDATA")
    return Path(raw).expanduser() if raw else None


def _claude_desktop_dir() -> Path:
    platform = _platform()
    if platform == "darwin":
        return _home() / "Library" / "Application Support" / "Claude"
    if platform == "windows":
        return (_appdata() or (_home() / "AppData" / "Roaming")) / "Claude"
    return _home() / ".config" / "Claude"


def _vscode_user_dir() -> Path:
    platform = _platform()
    if platform == "darwin":
        return _home() / "Library" / "Application Support" / "Code" / "User"
    if platform == "windows":
        return (_appdata() or (_home() / "AppData" / "Roaming")) / "Code" / "User"
    return _home() / ".config" / "Code" / "User"


def _cline_path() -> Path:
    return _vscode_user_dir() / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"


def _detect_cursor(repo_root: Path) -> Path | None:
    candidates = [repo_root / ".cursor", _home() / ".cursor"]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def _detect_claude_desktop(_repo_root: Path) -> Path | None:
    root = _claude_desktop_dir()
    if root.exists():
        return root
    return None


def _detect_binary(binary_name: str) -> Callable[[Path], Path | None]:
    def _inner(_repo_root: Path) -> Path | None:
        binary = shutil.which(binary_name)
        return Path(binary) if binary else None

    return _inner


def _detect_vscode(_repo_root: Path) -> Path | None:
    if shutil.which("code"):
        return _vscode_user_dir()
    user_dir = _vscode_user_dir()
    if user_dir.exists():
        return user_dir
    return None


def _detect_windsurf(_repo_root: Path) -> Path | None:
    root = _home() / ".codeium" / "windsurf"
    return root if root.exists() else None


def _detect_zed(_repo_root: Path) -> Path | None:
    root = _home() / ".config" / "zed"
    if root.exists():
        return root
    binary = shutil.which("zed")
    return Path(binary) if binary else None


def _detect_continue(_repo_root: Path) -> Path | None:
    root = _home() / ".continue"
    return root if root.exists() else None


def _detect_cline(_repo_root: Path) -> Path | None:
    path = _cline_path()
    return path if path.exists() else None


def _detect_roo(_repo_root: Path) -> Path | None:
    root = _home() / ".roo"
    return root if root.exists() else None


def _cursor_config(_repo_root: Path) -> Path:
    repo_cursor = _repo_root / ".cursor" / "mcp.json"
    if repo_cursor.exists():
        return repo_cursor
    return _home() / ".cursor" / "mcp.json"


def _claude_desktop_config(_repo_root: Path) -> Path:
    return _claude_desktop_dir() / "claude_desktop_config.json"


def _vscode_config(_repo_root: Path) -> Path:
    return _vscode_user_dir() / "mcp.json"


def _windsurf_config(_repo_root: Path) -> Path:
    return _home() / ".codeium" / "windsurf" / "mcp_config.json"


def _zed_config(_repo_root: Path) -> Path:
    return _home() / ".config" / "zed" / "settings.json"


def _continue_config(_repo_root: Path) -> Path:
    return _home() / ".continue" / "mcpServers" / "vaner.yaml"


def _cline_config(_repo_root: Path) -> Path:
    return _cline_path()


def _roo_config(_repo_root: Path) -> Path:
    return _home() / ".roo" / "mcp_settings.json"


def _none_path(_repo_root: Path) -> None:
    return None


CLIENTS: list[ClientSpec] = [
    ClientSpec("cursor", "Cursor", "json-mcpServers", _detect_cursor, _cursor_config, "Cursor user MCP config"),
    ClientSpec(
        "claude-desktop",
        "Claude Desktop",
        "json-mcpServers",
        _detect_claude_desktop,
        _claude_desktop_config,
        "Claude Desktop MCP config",
    ),
    ClientSpec("claude-code", "Claude Code", "cli-claude", _detect_binary("claude"), _none_path, "claude mcp add command"),
    ClientSpec("vscode-copilot", "VS Code (Copilot)", "json-servers", _detect_vscode, _vscode_config, "VS Code user MCP config"),
    ClientSpec("codex-cli", "Codex CLI", "cli-codex", _detect_binary("codex"), _none_path, "codex mcp add command"),
    ClientSpec("windsurf", "Windsurf", "json-mcpServers", _detect_windsurf, _windsurf_config, "Windsurf MCP config"),
    ClientSpec("zed", "Zed", "json-context_servers", _detect_zed, _zed_config, "Zed context_servers config"),
    ClientSpec("continue", "Continue", "yaml-continue", _detect_continue, _continue_config, "Continue MCP server yaml"),
    ClientSpec("cline", "Cline", "json-mcpServers", _detect_cline, _cline_config, "Cline MCP settings"),
    ClientSpec("roo", "Roo Code", "json-mcpServers", _detect_roo, _roo_config, "Roo user MCP settings"),
]


def resolve_launcher(repo_root: Path | None = None) -> tuple[str, list[str]]:
    command = shutil.which("vaner") or "vaner"
    root_arg = str(repo_root) if repo_root is not None else "."
    return command, ["mcp", "--path", root_arg]


def _contains_vaner_entry(config_path: Path, *, container_key: str) -> bool:
    if not config_path.exists():
        return False
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    container = payload.get(container_key)
    if not isinstance(container, dict):
        return False
    if "vaner" in container:
        return True
    return any(str(key).startswith("vaner-") for key in container.keys())


def detect_all(repo_root: Path | None = None) -> list[DetectedClient]:
    root = repo_root or Path.cwd()
    detected: list[DetectedClient] = []
    for spec in CLIENTS:
        evidence = spec.detect(root)
        config_path = spec.config_path(root)
        if evidence is None:
            detected.append(DetectedClient(spec=spec, status=ClientStatus.MISSING, path=config_path, detail="not detected"))
            continue
        if spec.kind == "json-mcpServers":
            configured = config_path is not None and _contains_vaner_entry(config_path, container_key="mcpServers")
        elif spec.kind == "json-servers":
            configured = config_path is not None and _contains_vaner_entry(config_path, container_key="servers")
        elif spec.kind == "json-context_servers":
            configured = config_path is not None and _contains_vaner_entry(config_path, container_key="context_servers")
        elif spec.kind == "yaml-continue":
            configured = config_path is not None and config_path.exists() and "name: vaner" in config_path.read_text(encoding="utf-8")
        else:
            configured = False
        status = ClientStatus.CONFIGURED if configured else ClientStatus.INSTALLED
        detail = "already configured" if configured else "installed"
        detected.append(DetectedClient(spec=spec, status=status, path=config_path, detail=detail))
    return detected


def generic_snippet(launcher_cmd: str, launcher_args: list[str]) -> dict[str, object]:
    return {
        "json": {"mcpServers": {"vaner": {"command": launcher_cmd, "args": launcher_args}}},
        "cli": f"claude mcp add --transport stdio --scope user vaner -- {launcher_cmd} {' '.join(launcher_args)}",
    }


def _write_backup(path: Path, *, keep_latest: int = 3) -> Path:
    backup_path = path.with_suffix(path.suffix + f".vaner-backup-{int(time.time())}")
    backup_path.write_bytes(path.read_bytes())
    try:
        pattern = f"{path.name}.vaner-backup-*"
        backups = sorted(path.parent.glob(pattern), key=lambda candidate: candidate.stat().st_mtime, reverse=True)
        for stale in backups[keep_latest:]:
            stale.unlink(missing_ok=True)
    except OSError:
        # Best-effort cleanup only; never fail the caller for retention maintenance.
        pass
    return backup_path


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".vaner-tmp-", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _json_entry(container_key: str, launcher_cmd: str, launcher_args: list[str]) -> dict[str, object]:
    if container_key == "context_servers":
        return {"command": {"path": launcher_cmd, "args": launcher_args}}
    return {"command": launcher_cmd, "args": launcher_args}


def _merge_json_server(
    *,
    client_id: str,
    path: Path,
    container_key: str,
    launcher_cmd: str,
    launcher_args: list[str],
    server_key: str = "vaner",
    dry_run: bool,
    force: bool,
) -> WriteResult:
    entry = _json_entry(container_key, launcher_cmd, launcher_args)
    backup: Path | None = None
    doc: dict[str, object]
    raw: str | None = None
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        try:
            parsed = json.loads(raw)
            doc = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            if not force:
                return WriteResult(
                    client_id=client_id,
                    path=path,
                    action="failed",
                    error=f"malformed JSON at {path}; pass --force to overwrite or fix the file",
                )
            doc = {}
    else:
        doc = {}

    container = doc.get(container_key)
    if not isinstance(container, dict):
        container = {}
        doc[container_key] = container
    previous = container.get(server_key)
    action: WriteAction = "updated" if previous is not None else "added"
    container[server_key] = entry
    if previous == entry:
        action = "skipped"
    rendered = json.dumps(doc, indent=2) + "\n"

    if raw is not None and raw == rendered:
        return WriteResult(client_id=client_id, path=path, action="skipped")

    if dry_run or action == "skipped":
        return WriteResult(client_id=client_id, path=path, action=action, backup=backup)

    if path.exists():
        backup = _write_backup(path)

    try:
        _atomic_write(path, rendered)
    except Exception as exc:  # pragma: no cover - defensive I/O guard
        return WriteResult(client_id=client_id, path=path, action="failed", backup=backup, error=str(exc))
    return WriteResult(client_id=client_id, path=path, action=action, backup=backup)


def _render_continue_yaml(launcher_cmd: str, launcher_args: list[str]) -> str:
    args_block = "".join(f"  - {arg}\n" for arg in launcher_args)
    return f"name: vaner\nversion: 0.0.1\nschema: v1\ncommand: {launcher_cmd}\nargs:\n{args_block}"


def _merge_yaml_continue(*, client_id: str, path: Path, launcher_cmd: str, launcher_args: list[str], dry_run: bool) -> WriteResult:
    backup: Path | None = None
    rendered = _render_continue_yaml(launcher_cmd, launcher_args)
    action: WriteAction = "added"
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == rendered:
            return WriteResult(client_id=client_id, path=path, action="skipped")
        action = "updated"
        if not dry_run:
            backup = _write_backup(path)
    if dry_run:
        return WriteResult(client_id=client_id, path=path, action=action, backup=backup)
    try:
        _atomic_write(path, rendered)
    except Exception as exc:  # pragma: no cover
        return WriteResult(client_id=client_id, path=path, action="failed", backup=backup, error=str(exc))
    return WriteResult(client_id=client_id, path=path, action=action, backup=backup)


def _write_cli_client(
    *,
    client_id: str,
    executable: str,
    argv: list[str],
    launcher_cmd: str,
    launcher_args: list[str],
) -> WriteResult:
    if not shutil.which(executable):
        snippet = json.dumps(generic_snippet(launcher_cmd, launcher_args)["json"], indent=2)
        return WriteResult(
            client_id=client_id,
            path=None,
            action="skipped",
            error=f"{executable} binary not found",
            manual_snippet=snippet,
        )
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=30)
    except Exception as exc:  # pragma: no cover
        return WriteResult(client_id=client_id, path=None, action="failed", error=str(exc))
    if result.returncode == 0:
        return WriteResult(client_id=client_id, path=None, action="added")
    return WriteResult(
        client_id=client_id,
        path=None,
        action="failed",
        error=(result.stderr or result.stdout).strip()[:500],
    )


def write_client(
    detected: DetectedClient,
    *,
    launcher_cmd: str,
    launcher_args: list[str],
    server_key: str = "vaner",
    path_override: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> WriteResult:
    spec = detected.spec
    target_path = path_override or detected.path
    if spec.kind == "json-mcpServers":
        assert target_path is not None
        return _merge_json_server(
            client_id=spec.id,
            path=target_path,
            container_key="mcpServers",
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
            server_key=server_key,
            dry_run=dry_run,
            force=force,
        )
    if spec.kind == "json-servers":
        assert target_path is not None
        return _merge_json_server(
            client_id=spec.id,
            path=target_path,
            container_key="servers",
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
            server_key=server_key,
            dry_run=dry_run,
            force=force,
        )
    if spec.kind == "json-context_servers":
        assert target_path is not None
        return _merge_json_server(
            client_id=spec.id,
            path=target_path,
            container_key="context_servers",
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
            server_key=server_key,
            dry_run=dry_run,
            force=force,
        )
    if spec.kind == "yaml-continue":
        assert target_path is not None
        return _merge_yaml_continue(
            client_id=spec.id,
            path=target_path,
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
            dry_run=dry_run,
        )
    if spec.kind == "cli-claude":
        argv = ["claude", "mcp", "add", "--transport", "stdio", "--scope", "user", "vaner", "--", launcher_cmd, *launcher_args]
        return _write_cli_client(
            client_id=spec.id,
            executable="claude",
            argv=argv,
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
        )
    if spec.kind == "cli-codex":
        argv = ["codex", "mcp", "add", "vaner", "--", launcher_cmd, *launcher_args]
        return _write_cli_client(
            client_id=spec.id,
            executable="codex",
            argv=argv,
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
        )
    return WriteResult(client_id=spec.id, path=target_path, action="failed", error=f"Unsupported kind: {spec.kind}")


def _remove_vaner_from_json(path: Path, *, container_key: str) -> bool:
    """Strip `vaner` and `vaner-*` entries from a json client config.

    Returns True when the file was modified or deleted, False otherwise.
    Used by both `vaner uninstall` and `vaner clients uninstall`.
    """
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    container = payload.get(container_key)
    if not isinstance(container, dict):
        return False
    keys_to_remove = [key for key in container if str(key) == "vaner" or str(key).startswith("vaner-")]
    if not keys_to_remove:
        return False
    for key in keys_to_remove:
        container.pop(key, None)
    if not container:
        payload.pop(container_key, None)
    if payload:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return True


def remove_client(detected: DetectedClient, *, dry_run: bool = False) -> WriteResult:
    """Remove the Vaner entry from a single detected client's config.

    Symmetric to :func:`write_client`. Returns the same `WriteResult` shape
    (`action="updated"` when an entry was removed, `"skipped"` when nothing
    matched). For CLI-driven clients (Claude Code, Codex CLI) we shell out
    to their respective `mcp remove` command.
    """
    spec = detected.spec
    target_path = detected.path

    if spec.kind in ("json-mcpServers", "json-servers", "json-context_servers"):
        if target_path is None:
            return WriteResult(client_id=spec.id, path=None, action="skipped", error="no config path")
        container_key = {
            "json-mcpServers": "mcpServers",
            "json-servers": "servers",
            "json-context_servers": "context_servers",
        }[spec.kind]
        if dry_run:
            return WriteResult(client_id=spec.id, path=target_path, action="skipped", error="dry-run")
        changed = _remove_vaner_from_json(target_path, container_key=container_key)
        return WriteResult(
            client_id=spec.id,
            path=target_path,
            action="updated" if changed else "skipped",
        )

    if spec.kind == "yaml-continue":
        if target_path is None or not target_path.exists():
            return WriteResult(client_id=spec.id, path=target_path, action="skipped")
        try:
            text = target_path.read_text(encoding="utf-8")
        except Exception as exc:
            return WriteResult(client_id=spec.id, path=target_path, action="failed", error=str(exc))
        if "name: vaner" not in text:
            return WriteResult(client_id=spec.id, path=target_path, action="skipped")
        if dry_run:
            return WriteResult(client_id=spec.id, path=target_path, action="skipped", error="dry-run")
        target_path.unlink(missing_ok=True)
        return WriteResult(client_id=spec.id, path=target_path, action="updated")

    if spec.kind == "cli-claude":
        argv = ["claude", "mcp", "remove", "--scope", "user", "vaner"]
        return _run_cli_remove(spec.id, "claude", argv, dry_run=dry_run)
    if spec.kind == "cli-codex":
        argv = ["codex", "mcp", "remove", "vaner"]
        return _run_cli_remove(spec.id, "codex", argv, dry_run=dry_run)
    return WriteResult(client_id=spec.id, path=target_path, action="skipped")


def _run_cli_remove(client_id: str, executable: str, argv: list[str], *, dry_run: bool) -> WriteResult:
    if dry_run:
        return WriteResult(client_id=client_id, path=None, action="skipped", error="dry-run")
    if not shutil.which(executable):
        return WriteResult(
            client_id=client_id,
            path=None,
            action="skipped",
            error=f"{executable} binary not found",
        )
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=30)
    except Exception as exc:  # pragma: no cover
        return WriteResult(client_id=client_id, path=None, action="failed", error=str(exc))
    if result.returncode == 0:
        return WriteResult(client_id=client_id, path=None, action="updated")
    return WriteResult(
        client_id=client_id,
        path=None,
        action="failed",
        error=(result.stderr or result.stdout).strip()[:500],
    )


@dataclass(slots=True)
class LauncherDrift:
    """Single client's view of `vaner` binary path drift."""

    client_id: str
    label: str
    config_path: Path | None
    drift: bool
    current_in_config: str | None
    expected: str
    detail: str = ""


def _load_configured_command(detected: DetectedClient) -> str | None:
    """Read the `command` (or `command.path` for Zed) the client uses for vaner.

    Returns None when the config file doesn't exist, isn't valid JSON, or
    doesn't carry a vaner entry. CLI-managed clients (claude/codex) don't
    expose their config to us this way and return None — they need to be
    re-run to pick up new launcher paths.
    """
    spec = detected.spec
    if spec.kind not in ("json-mcpServers", "json-servers", "json-context_servers"):
        return None
    if detected.path is None or not detected.path.exists():
        return None
    container_key = {
        "json-mcpServers": "mcpServers",
        "json-servers": "servers",
        "json-context_servers": "context_servers",
    }[spec.kind]
    try:
        payload = json.loads(detected.path.read_text(encoding="utf-8"))
    except Exception:
        return None
    container = payload.get(container_key)
    if not isinstance(container, dict):
        return None
    for key, entry in container.items():
        if not (str(key) == "vaner" or str(key).startswith("vaner-")):
            continue
        if not isinstance(entry, dict):
            continue
        cmd = entry.get("command")
        if isinstance(cmd, dict):
            # Zed uses `{"command": {"path": "...", "args": [...]}}`.
            inner = cmd.get("path")
            if isinstance(inner, str):
                return inner
        elif isinstance(cmd, str):
            return cmd
    return None


def launcher_drift(detected: DetectedClient) -> LauncherDrift:
    """Report whether *detected*'s configured launcher matches the current `vaner` binary."""
    expected = shutil.which("vaner") or "vaner"
    if detected.status != ClientStatus.CONFIGURED:
        return LauncherDrift(
            client_id=detected.spec.id,
            label=detected.spec.label,
            config_path=detected.path,
            drift=False,
            current_in_config=None,
            expected=expected,
            detail="not configured",
        )
    current = _load_configured_command(detected)
    if current is None:
        return LauncherDrift(
            client_id=detected.spec.id,
            label=detected.spec.label,
            config_path=detected.path,
            drift=False,
            current_in_config=None,
            expected=expected,
            detail="cli-managed; cannot inspect",
        )
    drifted = current != expected
    return LauncherDrift(
        client_id=detected.spec.id,
        label=detected.spec.label,
        config_path=detected.path,
        drift=drifted,
        current_in_config=current,
        expected=expected,
        detail="drift detected" if drifted else "in sync",
    )


def print_other_client_help(launcher_cmd: str, launcher_args: list[str]) -> str:
    snippet = generic_snippet(launcher_cmd, launcher_args)
    lines = [
        "Using a different MCP client? Paste this into its MCP config:",
        "",
        json.dumps(snippet["json"], indent=2),
        "",
        "Or via CLI (most clients support one of):",
        f"  {snippet['cli']}",
        "",
        "Full list of supported clients:   https://docs.vaner.ai/mcp",
        (
            "Request support for a new client: "
            "https://github.com/Borgels/vaner/issues/new?"
            "labels=client-support&title=Add+MCP+client+support%3A+%3Cname%3E"
        ),
    ]
    return "\n".join(lines)
