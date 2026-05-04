# SPDX-License-Identifier: Apache-2.0

"""Auto Focus core state and conservative scheduling gates.

The daemon owns this policy. Desktop, MCP, and CLI surfaces may display this
state or submit explicit user actions, but they must not independently decide
which workspace Vaner should proactively work in.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from vaner.cli.commands import mcp_clients
from vaner.models.config import VanerConfig
from vaner.setup import hardware

FocusMode = Literal["auto", "manual-only", "paused"]
FocusStatus = Literal["idle", "standby", "active", "paused", "manual_only"]
WorkspaceTier = Literal["active", "warm_standby", "dormant", "unknown", "paused"]
ResourceMode = Literal["balanced", "low_power", "performance"]

WORK_HERE_TTL_SECONDS = 30 * 60
FOREGROUND_GRACE_SECONDS = 2 * 60
RECENT_ACTIVITY_SECONDS = 15 * 60
MIN_PROACTIVE_FOCUS_CONFIDENCE = 0.70
_FOCUS_MODES = {"auto", "manual-only", "paused"}
_RESOURCE_MODES = {"balanced", "low_power", "performance"}


class ClientObservation(BaseModel):
    id: str
    kind: str
    display_name: str
    source: str = "daemon"
    running: bool = False
    foreground: bool = False
    integration_state: Literal["missing", "installed", "configured", "wired"] = "missing"
    workspace_hints: list[str] = Field(default_factory=list)
    observed_at: float = 0.0
    focus_confidence: float = 0.0
    reason_code: str = ""
    explanation: str = ""


class WorkspaceFocus(BaseModel):
    id: str
    display_name: str
    canonical_path: str | None = None
    trust_state: Literal["unknown", "trusted"] = "unknown"
    integration_state: Literal["unknown", "partial", "wired"] = "unknown"
    tier: WorkspaceTier = "unknown"
    pinned: bool = False
    paused: bool = False
    last_activity_at: float = 0.0
    focus_confidence: float = 0.0
    work_value_confidence: float = 0.0
    eligibility_reasons: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class FocusState(BaseModel):
    mode: FocusMode = "auto"
    status: FocusStatus = "idle"
    resource_mode: ResourceMode = "balanced"
    focus_epoch: int = 0
    active_workspace_id: str | None = None
    active_client_id: str | None = None
    selected_by: str = "standby"
    detected_clients: list[ClientObservation] = Field(default_factory=list)
    workspaces: list[WorkspaceFocus] = Field(default_factory=list)
    explanation: str = "Vaner is idle."
    why_not: list[dict[str, Any]] = Field(default_factory=list)
    proactive_allowed: bool = False


class FocusRouteState(BaseModel):
    """User-facing route view over Auto Focus, client, and compute state."""

    effective_route: dict[str, Any] = Field(default_factory=dict)
    workspace_options: list[dict[str, Any]] = Field(default_factory=list)
    client_options: list[dict[str, Any]] = Field(default_factory=list)
    hardware_options: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""


class RuntimeInfo(BaseModel):
    id: str
    kind: str
    endpoint: str | None = None
    local: bool = True
    detected: bool = False
    available: bool = False
    healthy: bool = False
    eligible: bool = True
    selected: bool = False
    enabled: bool = True
    explanation: str = ""


class ModelInfo(BaseModel):
    id: str
    runtime_id: str
    name: str
    size_label: str = "unknown"
    context_window: int | None = None
    local: bool = True


class DeviceInfo(BaseModel):
    id: str
    kind: str
    name: str
    memory_display_gb: int | None = None
    memory_kind: str = "unknown"
    reserved_for_interactive: bool = False
    allowed_for_background: bool = True
    explanation: str = ""


class ResourceState(BaseModel):
    resource_mode: ResourceMode = "balanced"
    cloud_execution_policy: Literal["disabled_v1", "explicit_only_future"] = "disabled_v1"
    runtimes: list[RuntimeInfo] = Field(default_factory=list)
    models: list[ModelInfo] = Field(default_factory=list)
    devices: list[DeviceInfo] = Field(default_factory=list)
    explanation: str = "Minimal local resource inventory."


class JobInfo(BaseModel):
    id: str
    job_class: str
    workspace_id: str | None = None
    focus_epoch: int = 0
    priority: Literal["foreground", "active", "standby", "maintenance"] = "maintenance"
    cancellable: bool = True
    status: Literal["queued", "running", "deferred", "cancelled", "completed"] = "deferred"
    defer_reason: str = ""
    explanation: str = ""


@dataclass(slots=True)
class _Prefs:
    mode: FocusMode = "auto"
    resource_mode: ResourceMode = "balanced"
    focus_epoch: int = 0
    preferred_client_id: str | None = None
    pinned_workspace_path: str | None = None
    work_here_workspace_path: str | None = None
    work_here_expires_at: float = 0.0
    paused_workspace_paths: set[str] = field(default_factory=set)
    pause_all: bool = False


_SUPPORTED_OBSERVATION_CLIENT_IDS = {spec.id for spec in mcp_clients.CLIENTS} | {"mcp-session", "composer"}
_DEFAULT_DETECT_ALL = mcp_clients.detect_all


def _now() -> float:
    return time.time()


def _safe_resolve(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _confidence(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def _coerce_mode(mode: str) -> FocusMode:
    if mode not in _FOCUS_MODES:
        raise ValueError("mode must be auto|manual-only|paused")
    return mode  # type: ignore[return-value]


def _coerce_resource_mode(mode: str) -> ResourceMode:
    if mode not in _RESOURCE_MODES:
        raise ValueError("resource_mode must be balanced|low_power|performance")
    return mode  # type: ignore[return-value]


def _process_name(value: Any) -> str:
    name = str(value or "").strip().lower()
    if not name:
        return ""
    return Path(name).name.removesuffix(".exe")


def _process_tokens(cmdline: Any) -> list[str]:
    if isinstance(cmdline, str):
        raw = cmdline.split()
    elif isinstance(cmdline, list | tuple):
        raw = [str(part) for part in cmdline]
    else:
        raw = []
    return [token for token in raw if token]


def _token_basenames(tokens: list[str]) -> set[str]:
    return {_process_name(token) for token in tokens if token}


def _claude_management_command(tokens: list[str]) -> bool:
    basenames = [_process_name(token) for token in tokens]
    for index, name in enumerate(basenames):
        if name != "claude":
            continue
        if index + 1 < len(basenames) and basenames[index + 1] in {"mcp", "plugin", "config"}:
            return True
    return False


def _process_info_matches(client_id: str, name: Any, cmdline: Any) -> bool:
    """Return whether a process snapshot represents a live supported client."""

    proc_name = _process_name(name)
    tokens = _process_tokens(cmdline)
    basenames = _token_basenames(tokens)

    if client_id == "codex-cli":
        return proc_name == "codex" or "codex" in basenames or any("@openai/codex" in token.lower() for token in tokens)
    if client_id == "vscode-copilot":
        return proc_name in {"code", "code-insiders"}
    if client_id == "claude-code":
        return (proc_name == "claude" or "claude" in basenames) and not _claude_management_command(tokens)
    if client_id == "claude-desktop":
        return proc_name in {"claude-desktop", "claude desktop"}

    exact_names: dict[str, set[str]] = {
        "cursor": {"cursor"},
        "windsurf": {"windsurf"},
        "zed": {"zed", "zeditor"},
        "continue": {"continue"},
        "cline": {"cline"},
        "roo": {"roo"},
    }
    return proc_name in exact_names.get(client_id, {client_id})


def _iter_process_snapshots() -> list[tuple[str, list[str]]]:
    snapshots: list[tuple[str, list[str]]] = []
    try:
        import psutil  # type: ignore[import-untyped]

        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                snapshots.append((str(proc.info.get("name") or ""), _process_tokens(proc.info.get("cmdline") or [])))
            except Exception:
                continue
    except Exception:
        proc_root = Path("/proc")
        for pid_dir in proc_root.iterdir() if proc_root.exists() else []:
            if not pid_dir.name.isdigit():
                continue
            try:
                name = pid_dir.joinpath("comm").read_text(encoding="utf-8").strip()
            except Exception:
                continue
            try:
                raw_cmdline = pid_dir.joinpath("cmdline").read_bytes().decode("utf-8", errors="ignore")
                cmdline = [part for part in raw_cmdline.split("\0") if part]
            except Exception:
                cmdline = []
            snapshots.append((name, cmdline))
    return snapshots


def _client_process_running(client_id: str) -> bool:
    for name, cmdline in _iter_process_snapshots():
        if _process_info_matches(client_id, name, cmdline):
            return True
    return False


class FocusManager:
    def __init__(self, config: VanerConfig) -> None:
        self.config = config
        self.repo_root = _safe_resolve(config.repo_root)
        self._state_path = self.repo_root / ".vaner" / "focus.json"
        self._runtime_dir = self.repo_root / ".vaner" / "runtime"
        self._observations: dict[str, ClientObservation] = {}
        self._last_state: FocusState | None = None

    @property
    def current_workspace_id(self) -> str:
        return self.workspace_id_for_path(self.repo_root)

    def workspace_id_for_path(self, path: Path) -> str:
        salt_path = self._runtime_dir / "focus_salt"
        try:
            self._runtime_dir.mkdir(parents=True, exist_ok=True)
            if salt_path.exists():
                salt = salt_path.read_text(encoding="utf-8").strip()
            else:
                salt = hashlib.sha256(f"{os.getpid()}:{time.time_ns()}".encode()).hexdigest()
                salt_path.write_text(salt, encoding="utf-8")
        except Exception:
            salt = "vaner-focus-local"
        digest = hashlib.sha256(f"{salt}:{_safe_resolve(path)}".encode()).hexdigest()
        return f"w_{digest[:16]}"

    def _load_prefs(self) -> _Prefs:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return _Prefs()
        if not isinstance(raw, dict):
            return _Prefs()
        focus_epoch = raw.get("focus_epoch") or 0
        try:
            parsed_focus_epoch = max(0, int(focus_epoch))
        except (TypeError, ValueError):
            parsed_focus_epoch = 0
        work_here_expires_at = raw.get("work_here_expires_at") or 0.0
        try:
            parsed_work_here_expires_at = max(0.0, float(work_here_expires_at))
        except (TypeError, ValueError):
            parsed_work_here_expires_at = 0.0
        return _Prefs(
            mode=raw.get("mode", "auto") if raw.get("mode") in _FOCUS_MODES else "auto",
            resource_mode=raw.get("resource_mode", "balanced") if raw.get("resource_mode") in _RESOURCE_MODES else "balanced",
            focus_epoch=parsed_focus_epoch,
            preferred_client_id=raw.get("preferred_client_id") if isinstance(raw.get("preferred_client_id"), str) else None,
            pinned_workspace_path=raw.get("pinned_workspace_path") if isinstance(raw.get("pinned_workspace_path"), str) else None,
            work_here_workspace_path=raw.get("work_here_workspace_path") if isinstance(raw.get("work_here_workspace_path"), str) else None,
            work_here_expires_at=parsed_work_here_expires_at,
            paused_workspace_paths={
                str(item)
                for item in raw.get("paused_workspace_paths", [])
                if isinstance(raw.get("paused_workspace_paths", []), list | tuple | set) and isinstance(item, str)
            },
            pause_all=bool(raw.get("pause_all", False)),
        )

    def _save_prefs(self, prefs: _Prefs) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": prefs.mode,
            "resource_mode": prefs.resource_mode,
            "focus_epoch": prefs.focus_epoch,
            "preferred_client_id": prefs.preferred_client_id,
            "pinned_workspace_path": prefs.pinned_workspace_path,
            "work_here_workspace_path": prefs.work_here_workspace_path,
            "work_here_expires_at": prefs.work_here_expires_at,
            "paused_workspace_paths": sorted(prefs.paused_workspace_paths),
            "pause_all": prefs.pause_all,
        }
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._state_path)

    def _bump(self, prefs: _Prefs) -> None:
        prefs.focus_epoch += 1
        self._save_prefs(prefs)

    def set_mode(self, mode: str, *, resource_mode: str | None = None) -> FocusState:
        coerced_mode = _coerce_mode(mode)
        coerced_resource_mode = _coerce_resource_mode(resource_mode) if resource_mode is not None else None
        prefs = self._load_prefs()
        changed = prefs.mode != coerced_mode
        prefs.mode = coerced_mode
        prefs.pause_all = coerced_mode == "paused"
        if coerced_resource_mode is not None:
            changed = changed or prefs.resource_mode != coerced_resource_mode
            prefs.resource_mode = coerced_resource_mode
        if changed:
            self._bump(prefs)
        else:
            self._save_prefs(prefs)
        return self.build_state()

    def set_resource_mode(self, mode: str) -> FocusState:
        coerced_mode = _coerce_resource_mode(mode)
        prefs = self._load_prefs()
        if prefs.resource_mode != coerced_mode:
            prefs.resource_mode = coerced_mode
            self._bump(prefs)
        else:
            self._save_prefs(prefs)
        return self.build_state()

    def set_route_preferences(
        self,
        *,
        workspace_policy: str | None = None,
        workspace_path: Path | None = None,
        client_id: str | None | object = ...,
        resource_mode: str | None = None,
        ttl_seconds: int | None = None,
    ) -> FocusState:
        """Apply route-level preferences without touching compute/backend config.

        ``client_id`` uses ``...`` as "not supplied", because ``None`` is a
        meaningful value: clear the preferred client and return to automatic
        client selection.
        """
        prefs = self._load_prefs()
        changed = False

        if workspace_policy is not None:
            if workspace_policy not in {"auto", "work_here", "pinned"}:
                raise ValueError("workspace_policy must be auto|work_here|pinned")
            if workspace_policy == "auto":
                changed = changed or bool(prefs.work_here_workspace_path or prefs.pinned_workspace_path)
                prefs.work_here_workspace_path = None
                prefs.work_here_expires_at = 0.0
                prefs.pinned_workspace_path = None
            else:
                target = _safe_resolve(workspace_path or self.repo_root)
                if workspace_policy == "work_here":
                    prefs.work_here_workspace_path = str(target)
                    prefs.work_here_expires_at = _now() + max(60, int(ttl_seconds or WORK_HERE_TTL_SECONDS))
                    prefs.pinned_workspace_path = None
                else:
                    prefs.pinned_workspace_path = str(target)
                    prefs.work_here_workspace_path = None
                    prefs.work_here_expires_at = 0.0
                prefs.mode = "auto"
                prefs.pause_all = False
                changed = True

        if client_id is not ...:
            next_client_id = None if client_id is None else str(client_id).strip()
            supported = {spec.id for spec in mcp_clients.CLIENTS}
            if next_client_id and next_client_id not in supported:
                raise ValueError("client_id must name a supported Vaner MCP client or be null")
            changed = changed or prefs.preferred_client_id != next_client_id
            prefs.preferred_client_id = next_client_id

        if resource_mode is not None:
            coerced = _coerce_resource_mode(resource_mode)
            changed = changed or prefs.resource_mode != coerced
            prefs.resource_mode = coerced

        if changed:
            self._bump(prefs)
        else:
            self._save_prefs(prefs)
        return self.build_state()

    def work_here(self, path: Path | None = None, *, ttl_seconds: int = WORK_HERE_TTL_SECONDS) -> FocusState:
        prefs = self._load_prefs()
        target = _safe_resolve(path or self.repo_root)
        prefs.work_here_workspace_path = str(target)
        prefs.work_here_expires_at = _now() + max(60, int(ttl_seconds))
        prefs.mode = "auto"
        prefs.pause_all = False
        self._bump(prefs)
        return self.build_state()

    def pin(self, path: Path | None = None) -> FocusState:
        prefs = self._load_prefs()
        prefs.pinned_workspace_path = str(_safe_resolve(path or self.repo_root))
        prefs.mode = "auto"
        prefs.pause_all = False
        self._bump(prefs)
        return self.build_state()

    def unpin(self) -> FocusState:
        prefs = self._load_prefs()
        if prefs.pinned_workspace_path is None:
            self._save_prefs(prefs)
            return self.build_state()
        prefs.pinned_workspace_path = None
        self._bump(prefs)
        return self.build_state()

    def pause(self, path: Path | None = None) -> FocusState:
        prefs = self._load_prefs()
        target = str(_safe_resolve(path or self.repo_root))
        if target in prefs.paused_workspace_paths:
            self._save_prefs(prefs)
            return self.build_state()
        prefs.paused_workspace_paths.add(target)
        self._bump(prefs)
        return self.build_state()

    def resume(self, path: Path | None = None) -> FocusState:
        prefs = self._load_prefs()
        target = str(_safe_resolve(path or self.repo_root))
        changed = target in prefs.paused_workspace_paths or prefs.pause_all or prefs.mode == "paused"
        prefs.paused_workspace_paths.discard(target)
        if prefs.pause_all:
            prefs.pause_all = False
            prefs.mode = "auto"
        if prefs.mode == "paused":
            prefs.mode = "auto"
        if changed:
            self._bump(prefs)
        else:
            self._save_prefs(prefs)
        return self.build_state()

    def pause_all(self) -> FocusState:
        prefs = self._load_prefs()
        prefs.pause_all = True
        prefs.mode = "paused"
        self._bump(prefs)
        return self.build_state()

    def record_observation(self, payload: dict[str, Any]) -> FocusState:
        observed = payload.get("observations")
        if not isinstance(observed, list):
            observed = [payload]
        for index, item in enumerate(observed):
            if not isinstance(item, dict):
                continue
            client_id = str(item.get("client_id") or item.get("id") or f"external-{index}").strip()
            if not client_id or client_id not in _SUPPORTED_OBSERVATION_CLIENT_IDS:
                continue
            workspace_hints: list[str] = []
            raw_workspace = item.get("workspace_path") or item.get("workspace")
            if isinstance(raw_workspace, str) and raw_workspace.strip():
                workspace_hints.append(str(_safe_resolve(Path(raw_workspace))))
            for raw in item.get("workspace_hints", []) or []:
                if isinstance(raw, str) and raw.strip():
                    workspace_hints.append(str(_safe_resolve(Path(raw))))
            obs = ClientObservation(
                id=client_id,
                kind=str(item.get("kind") or client_id),
                display_name=str(item.get("display_name") or client_id),
                source=str(item.get("source") or "observation"),
                running=bool(item.get("running", True)),
                foreground=bool(item.get("foreground", False)),
                integration_state=str(item.get("integration_state") or "wired")
                if str(item.get("integration_state") or "wired") in {"missing", "installed", "configured", "wired"}
                else "wired",
                workspace_hints=workspace_hints,
                observed_at=float(item.get("observed_at") or _now()),
                focus_confidence=_confidence(item.get("focus_confidence"), default=0.95 if item.get("foreground") else 0.75),
                reason_code="external_observation",
                explanation=f"{client_id} reported workspace context.",
            )
            self._observations[obs.id] = obs
        return self.build_state()

    def _detect_clients(self, prefs: _Prefs) -> list[ClientObservation]:
        now = _now()
        detected: list[ClientObservation] = []
        if mcp_clients.detect_all is not _DEFAULT_DETECT_ALL:
            source_items = [
                (
                    item.spec,
                    item.status != mcp_clients.ClientStatus.MISSING,
                    item.status == mcp_clients.ClientStatus.CONFIGURED,
                    item.path,
                    item.status.value,
                )
                for item in mcp_clients.detect_all(self.repo_root)
            ]
        else:
            source_items = []
            for spec in mcp_clients.CLIENTS:
                evidence = spec.detect(self.repo_root)
                config_path = spec.config_path(self.repo_root)
                installed = evidence is not None
                configured = False
                if installed:
                    if spec.kind == "json-mcpServers":
                        configured = config_path is not None and mcp_clients._contains_vaner_entry(config_path, container_key="mcpServers")
                    elif spec.kind == "json-servers":
                        configured = config_path is not None and mcp_clients._contains_vaner_entry(config_path, container_key="servers")
                    elif spec.kind == "json-context_servers":
                        configured = config_path is not None and mcp_clients._contains_vaner_entry(
                            config_path, container_key="context_servers"
                        )
                    elif spec.kind == "yaml-continue":
                        configured = (
                            config_path is not None and config_path.exists() and "name: vaner" in config_path.read_text(encoding="utf-8")
                        )
                    elif spec.kind in {"cli-claude", "cli-codex"}:
                        # Hot focus/status paths must never shell out to host CLIs.
                        # The installer owns exact CLI activation checks; focus only
                        # needs a fast, conservative signal for routing.
                        configured = True
                status = "configured" if configured else ("installed" if installed else "missing")
                source_items.append((spec, installed, configured, config_path, status))

        for spec, installed, configured, raw_config_path, status in source_items:
            running = _client_process_running(spec.id) if installed else False
            integration_state: Literal["missing", "installed", "configured", "wired"]
            if configured:
                integration_state = "wired"
            elif installed:
                integration_state = "installed"
            else:
                integration_state = "missing"
            config_path = _safe_resolve(raw_config_path) if raw_config_path is not None else None
            repo_scoped_config = bool(config_path is not None and self.repo_root in config_path.parents)
            cli_managed_config = spec.kind in {"cli-claude", "cli-codex"}
            workspace_hints = [str(self.repo_root)] if configured and (repo_scoped_config or cli_managed_config) else []
            focus_confidence = 0.75 if running and configured else (0.45 if configured else 0.0)
            detected.append(
                ClientObservation(
                    id=spec.id,
                    kind=spec.kind,
                    display_name=spec.label,
                    source="daemon",
                    running=running,
                    foreground=False,
                    integration_state=integration_state,
                    workspace_hints=workspace_hints,
                    observed_at=now,
                    focus_confidence=focus_confidence,
                    reason_code="supported_client_probe",
                    explanation=f"{spec.label} is {status}.",
                )
            )
        # Keep fresh external observations for one recent activity window.
        external: list[ClientObservation] = []
        for key, obs in list(self._observations.items()):
            if now - obs.observed_at > RECENT_ACTIVITY_SECONDS:
                self._observations.pop(key, None)
                continue
            external.append(obs)
        return external + detected

    def build_state(self) -> FocusState:
        prefs = self._load_prefs()
        now = _now()
        if prefs.work_here_workspace_path and prefs.work_here_expires_at <= now:
            prefs.work_here_workspace_path = None
            prefs.work_here_expires_at = 0.0
            self._save_prefs(prefs)
        clients = self._detect_clients(prefs)
        workspaces = self._workspace_candidates(clients, prefs)
        why_not: list[dict[str, Any]] = []

        if prefs.pause_all or prefs.mode == "paused":
            state = FocusState(
                mode="paused",
                status="paused",
                resource_mode=prefs.resource_mode,
                focus_epoch=prefs.focus_epoch,
                detected_clients=clients,
                workspaces=[self._with_tier(ws, "paused") for ws in workspaces],
                explanation="Vaner is paused.",
                why_not=[{"reason_code": "global_paused", "message": "Pause All is active."}],
                proactive_allowed=False,
            )
            self._last_state = state
            return state

        if prefs.mode == "manual-only":
            state = FocusState(
                mode="manual-only",
                status="manual_only",
                resource_mode=prefs.resource_mode,
                detected_clients=clients,
                workspaces=workspaces,
                focus_epoch=prefs.focus_epoch,
                explanation="Manual Only is enabled; automatic proactive work is disabled.",
                why_not=[{"reason_code": "manual_only", "message": "Automatic proactive work is disabled."}],
                proactive_allowed=False,
            )
            self._last_state = state
            return state

        selected: WorkspaceFocus | None = None
        selected_by = "standby"
        active_client_id: str | None = None

        pinned_path = prefs.pinned_workspace_path
        if pinned_path:
            selected = self._workspace_for_path(Path(pinned_path), prefs, focus_confidence=1.0)
            selected_by = "pinned"
        elif prefs.work_here_workspace_path:
            selected = self._workspace_for_path(Path(prefs.work_here_workspace_path), prefs, focus_confidence=1.0)
            selected_by = "work_here"
        else:
            candidates: dict[str, WorkspaceFocus] = {}
            candidate_clients: dict[str, str] = {}
            for client in clients:
                if not client.running and not client.foreground:
                    continue
                if client.integration_state != "wired":
                    continue
                for hint in client.workspace_hints:
                    ws = self._workspace_for_path(Path(hint), prefs, focus_confidence=client.focus_confidence)
                    if ws.paused:
                        continue
                    if ws.id != self.current_workspace_id:
                        continue
                    if ws.trust_state != "trusted" or ws.integration_state != "wired":
                        continue
                    current = candidates.get(ws.id)
                    if current is None or ws.focus_confidence > current.focus_confidence:
                        candidates[ws.id] = ws
                        candidate_clients[ws.id] = client.id
            high = {wid: ws for wid, ws in candidates.items() if ws.focus_confidence >= MIN_PROACTIVE_FOCUS_CONFIDENCE}
            if len(high) == 1:
                selected = next(iter(high.values()))
                active_client_id = candidate_clients.get(selected.id)
                selected_by = "foreground_or_running_client"
            elif len(high) > 1:
                why_not.append(
                    {
                        "reason_code": "ambiguous_workspaces",
                        "message": "Multiple active workspaces were detected and none is pinned.",
                        "action": "Choose Work Here or Pin a workspace.",
                    }
                )

        if selected is not None and selected.paused:
            why_not.append({"reason_code": "workspace_paused", "message": "The selected workspace is paused."})
            selected = None

        if selected is None:
            if not any(client.running for client in clients):
                status: FocusStatus = "idle"
                explanation = "Vaner is idle because no supported client is running."
                why_not.append({"reason_code": "no_supported_client_running", "message": explanation})
            else:
                status = "standby"
                explanation = "Vaner is in standby because no eligible active workspace was selected."
                if not why_not:
                    why_not.append(
                        {
                            "reason_code": "no_wired_workspace",
                            "message": "A supported client is running, but no wired workspace was detected.",
                            "action": "Connect this workspace or choose Work Here.",
                        }
                    )
            state = FocusState(
                mode=prefs.mode,
                status=status,
                resource_mode=prefs.resource_mode,
                focus_epoch=prefs.focus_epoch,
                selected_by="standby",
                detected_clients=clients,
                workspaces=workspaces,
                explanation=explanation,
                why_not=why_not,
                proactive_allowed=False,
            )
            self._last_state = state
            return state

        selected = selected.model_copy(update={"tier": "active", "eligibility_reasons": ["eligible_active_workspace"]})
        merged = self._merge_workspace(workspaces, selected)
        explanation = f"Working in {selected.display_name} because {selected_by.replace('_', ' ')} selected it."
        state = FocusState(
            mode=prefs.mode,
            status="active",
            resource_mode=prefs.resource_mode,
            focus_epoch=prefs.focus_epoch,
            active_workspace_id=selected.id,
            active_client_id=active_client_id,
            selected_by=selected_by,
            detected_clients=clients,
            workspaces=merged,
            explanation=explanation,
            why_not=why_not,
            proactive_allowed=selected.id == self.current_workspace_id and not selected.paused,
        )
        self._last_state = state
        return state

    def _workspace_candidates(self, clients: list[ClientObservation], prefs: _Prefs) -> list[WorkspaceFocus]:
        by_id: dict[str, WorkspaceFocus] = {}
        if (self.repo_root / ".vaner" / "config.toml").exists():
            ws = self._workspace_for_path(self.repo_root, prefs, focus_confidence=0.45)
            by_id[ws.id] = ws
        for client in clients:
            for hint in client.workspace_hints:
                ws = self._workspace_for_path(Path(hint), prefs, focus_confidence=client.focus_confidence)
                old = by_id.get(ws.id)
                if old is None or ws.focus_confidence > old.focus_confidence:
                    by_id[ws.id] = ws
        return sorted(by_id.values(), key=lambda item: (item.tier, item.display_name))

    def _workspace_for_path(self, path: Path, prefs: _Prefs, *, focus_confidence: float) -> WorkspaceFocus:
        canonical = _safe_resolve(path)
        paused = str(canonical) in prefs.paused_workspace_paths
        pinned = prefs.pinned_workspace_path == str(canonical)
        tier: WorkspaceTier = "paused" if paused else "warm_standby"
        return WorkspaceFocus(
            id=self.workspace_id_for_path(canonical),
            display_name=canonical.name or str(canonical),
            canonical_path=str(canonical),
            trust_state="trusted" if (canonical / ".vaner" / "config.toml").exists() or canonical == self.repo_root else "unknown",
            integration_state="wired" if canonical == self.repo_root else "partial",
            tier=tier,
            pinned=pinned,
            paused=paused,
            last_activity_at=_now(),
            focus_confidence=_confidence(focus_confidence, default=0.0),
            blocked_reasons=["workspace_paused"] if paused else [],
        )

    @staticmethod
    def _with_tier(ws: WorkspaceFocus, tier: WorkspaceTier) -> WorkspaceFocus:
        return ws.model_copy(update={"tier": tier, "paused": tier == "paused" or ws.paused})

    @staticmethod
    def _merge_workspace(workspaces: list[WorkspaceFocus], selected: WorkspaceFocus) -> list[WorkspaceFocus]:
        result: list[WorkspaceFocus] = []
        seen = False
        for ws in workspaces:
            if ws.id == selected.id:
                result.append(selected)
                seen = True
            else:
                result.append(ws)
        if not seen:
            result.append(selected)
        return result

    def proactive_gate(self, *, job_class: str = "prepared_work") -> JobInfo:
        state = self.build_state()
        workspace_id = self.current_workspace_id
        if not state.proactive_allowed:
            reason = state.why_not[0]["reason_code"] if state.why_not else "focus_not_active"
            return JobInfo(
                id=f"{job_class}:{state.focus_epoch}",
                job_class=job_class,
                workspace_id=workspace_id,
                focus_epoch=state.focus_epoch,
                priority="active",
                status="deferred",
                defer_reason=reason,
                explanation=state.explanation,
            )
        if state.resource_mode == "low_power" and job_class in {"prepared_work", "deep_reasoning"}:
            return JobInfo(
                id=f"{job_class}:{state.focus_epoch}",
                job_class=job_class,
                workspace_id=workspace_id,
                focus_epoch=state.focus_epoch,
                priority="active",
                status="deferred",
                defer_reason="low_power_mode",
                explanation="Deferred because low-power mode blocks deep background work.",
            )
        return JobInfo(
            id=f"{job_class}:{state.focus_epoch}",
            job_class=job_class,
            workspace_id=workspace_id,
            focus_epoch=state.focus_epoch,
            priority="active",
            status="queued",
            explanation=f"Prepared Work may run for focus epoch {state.focus_epoch}.",
        )

    def jobs_state(self) -> dict[str, Any]:
        gate = self.proactive_gate(job_class="prepared_work")
        return {"jobs": [gate.model_dump(mode="json")], "focus_epoch": gate.focus_epoch}

    def resources_state(self) -> ResourceState:
        prefs = self._load_prefs()
        runtimes: list[RuntimeInfo] = []
        models: list[ModelInfo] = []
        devices: list[DeviceInfo] = []
        try:
            profile = hardware.detect()
        except Exception:
            profile = None
        if profile is not None:
            for runtime in profile.detected_runtimes:
                runtimes.append(
                    RuntimeInfo(
                        id=str(runtime),
                        kind=str(runtime),
                        detected=True,
                        available=True,
                        healthy=True,
                        local=True,
                        explanation=f"{runtime} detected by local hardware probe.",
                    )
                )
            for runtime, model, size in profile.detected_models:
                models.append(ModelInfo(id=f"{runtime}:{model}", runtime_id=runtime, name=model, size_label=size, local=True))
            for index, gpu in enumerate(profile.gpu_devices):
                devices.append(
                    DeviceInfo(
                        id=f"gpu-{index}",
                        kind=gpu.kind,
                        name=gpu.name,
                        memory_display_gb=gpu.memory_display_gb,
                        memory_kind=gpu.memory_kind,
                        allowed_for_background=prefs.resource_mode != "low_power",
                        explanation="Detected local GPU device.",
                    )
                )
        backend_url = self.config.backend.base_url.strip()
        if backend_url:
            local = _is_local_url(backend_url)
            runtimes.append(
                RuntimeInfo(
                    id="backend",
                    kind=self.config.backend.name or "openai-compatible",
                    endpoint=backend_url,
                    local=local,
                    detected=True,
                    available=True,
                    healthy=True,
                    eligible=local,
                    selected=True,
                    explanation="Configured user-facing backend endpoint.",
                )
            )
            if self.config.backend.model:
                models.append(
                    ModelInfo(
                        id=f"backend:{self.config.backend.model}",
                        runtime_id="backend",
                        name=self.config.backend.model,
                        local=local,
                    )
                )
        for index, endpoint in enumerate(self.config.exploration.endpoints):
            local = _is_local_url(endpoint.url)
            runtimes.append(
                RuntimeInfo(
                    id=f"exploration-{index}",
                    kind=endpoint.backend,
                    endpoint=endpoint.url,
                    local=local,
                    detected=True,
                    available=True,
                    healthy=True,
                    eligible=local,
                    explanation="Configured exploration endpoint.",
                )
            )
            models.append(
                ModelInfo(
                    id=f"exploration-{index}:{endpoint.model}",
                    runtime_id=f"exploration-{index}",
                    name=endpoint.model,
                    context_window=endpoint.context_window,
                    local=local,
                )
            )
        return ResourceState(
            resource_mode=prefs.resource_mode,
            runtimes=runtimes,
            models=models,
            devices=devices,
            explanation="Read-only minimal resource inventory from Vaner config and hardware probes.",
        )

    def route_state(self) -> FocusRouteState:
        state = self.build_state()
        prefs = self._load_prefs()
        resources = self.resources_state()
        selected_workspace = next((ws for ws in state.workspaces if ws.id == state.active_workspace_id), None)
        if selected_workspace is None and prefs.pinned_workspace_path:
            selected_workspace = self._workspace_for_path(Path(prefs.pinned_workspace_path), prefs, focus_confidence=1.0)
        if selected_workspace is None and prefs.work_here_workspace_path:
            selected_workspace = self._workspace_for_path(Path(prefs.work_here_workspace_path), prefs, focus_confidence=1.0)

        workspace_policy = "auto"
        expires_at: float | None = None
        if prefs.pinned_workspace_path:
            workspace_policy = "pinned"
        elif prefs.work_here_workspace_path:
            workspace_policy = "work_here"
            expires_at = prefs.work_here_expires_at

        workspace_options = [self._route_workspace_option(ws, selected_workspace) for ws in state.workspaces]
        if selected_workspace is not None and not any(item["id"] == selected_workspace.id for item in workspace_options):
            workspace_options.insert(0, self._route_workspace_option(selected_workspace, selected_workspace))

        client_options = [
            self._route_client_option(client, selected_workspace, prefs.preferred_client_id, state.active_client_id)
            for client in state.detected_clients
            if self._client_eligible_for_workspace(client, selected_workspace)
        ]
        selected_client = next((item for item in client_options if item["selected"]), None)
        if selected_client is None and prefs.preferred_client_id:
            preferred = next((client for client in state.detected_clients if client.id == prefs.preferred_client_id), None)
            if preferred is not None:
                selected_client = self._route_client_option(
                    preferred,
                    selected_workspace,
                    prefs.preferred_client_id,
                    state.active_client_id,
                )
                client_options.insert(0, selected_client)

        route_workspace = self._route_workspace_option(selected_workspace, selected_workspace) if selected_workspace else None
        effective_route = {
            "workspace": route_workspace,
            "client": selected_client,
            "workspace_policy": workspace_policy,
            "automatic": workspace_policy == "auto" and prefs.preferred_client_id is None,
            "resource_mode": state.resource_mode,
            "device": self.config.compute.device,
            "backend": {
                "name": self.config.backend.name,
                "base_url": self.config.backend.base_url,
                "model": self.config.backend.model,
                "api_key_env": self.config.backend.api_key_env,
            },
            "selected_by": state.selected_by,
            "expires_at": expires_at,
        }
        hardware_options = {
            "resource_modes": [
                {"id": "low_power", "label": "Conserve battery"},
                {"id": "balanced", "label": "Balanced"},
                {"id": "performance", "label": "Maximum performance"},
            ],
            "devices": [device.model_dump(mode="json") for device in resources.devices]
            or [{"id": "auto", "kind": "auto", "name": "Auto", "explanation": "Let Vaner choose."}],
            "runtimes": [runtime.model_dump(mode="json") for runtime in resources.runtimes],
            "models": [model.model_dump(mode="json") for model in resources.models],
            "current": {
                "resource_mode": state.resource_mode,
                "device": self.config.compute.device,
                "backend": effective_route["backend"],
            },
        }
        diagnostics = {
            "focus": state.model_dump(mode="json"),
            "raw_detected_clients": [client.model_dump(mode="json") for client in state.detected_clients],
            "why_not": state.why_not,
            "warnings": [],
        }
        if prefs.preferred_client_id and selected_client is None:
            diagnostics["warnings"].append(
                {"code": "preferred_client_unavailable", "message": "Preferred client is not eligible right now."}
            )
        return FocusRouteState(
            effective_route=effective_route,
            workspace_options=workspace_options,
            client_options=client_options,
            hardware_options=hardware_options,
            diagnostics=diagnostics,
            explanation=self._route_explanation(state, route_workspace, selected_client, workspace_policy),
        )

    @staticmethod
    def _route_workspace_option(ws: WorkspaceFocus | None, selected: WorkspaceFocus | None) -> dict[str, Any]:
        if ws is None:
            return {}
        return {
            "id": ws.id,
            "display_name": ws.display_name,
            "canonical_path": ws.canonical_path,
            "tier": ws.tier,
            "selected": selected is not None and ws.id == selected.id,
            "pinned": ws.pinned,
            "paused": ws.paused,
            "eligible": ws.trust_state == "trusted" and ws.integration_state in {"partial", "wired"} and not ws.paused,
            "focus_confidence": ws.focus_confidence,
        }

    @staticmethod
    def _client_eligible_for_workspace(client: ClientObservation, workspace: WorkspaceFocus | None) -> bool:
        if client.integration_state != "wired":
            return False
        if workspace is None or not workspace.canonical_path:
            return client.running or bool(client.workspace_hints)
        canonical = str(_safe_resolve(Path(workspace.canonical_path)))
        return canonical in {str(_safe_resolve(Path(hint))) for hint in client.workspace_hints}

    def _route_client_option(
        self,
        client: ClientObservation,
        workspace: WorkspaceFocus | None,
        preferred_client_id: str | None,
        active_client_id: str | None,
    ) -> dict[str, Any]:
        preferred = preferred_client_id == client.id
        active = active_client_id == client.id
        return {
            "id": client.id,
            "display_name": client.display_name,
            "running": client.running,
            "foreground": client.foreground,
            "integration_state": client.integration_state,
            "selected": preferred or active,
            "preferred": preferred,
            "eligible": self._client_eligible_for_workspace(client, workspace),
            "focus_confidence": client.focus_confidence,
        }

    @staticmethod
    def _route_explanation(
        state: FocusState,
        workspace: dict[str, Any] | None,
        client: dict[str, Any] | None,
        workspace_policy: str,
    ) -> str:
        workspace_label = workspace.get("display_name") if workspace else None
        client_label = client.get("display_name") if client else None
        if workspace_policy == "pinned" and workspace_label:
            return f"Override active: Vaner is pinned to {workspace_label}{f' via {client_label}' if client_label else ''}."
        if workspace_policy == "work_here" and workspace_label:
            return f"Temporary override active: Vaner is working in {workspace_label}{f' via {client_label}' if client_label else ''}."
        if workspace_label and client_label:
            return f"Vaner is following {client_label} in {workspace_label}."
        if workspace_label:
            return f"Vaner is working in {workspace_label}."
        if state.status in {"idle", "standby"}:
            return "Vaner has not selected an active workspace yet."
        return state.explanation


def _is_local_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "0.0.0.0"}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
