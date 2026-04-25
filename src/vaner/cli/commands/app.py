# SPDX-License-Identifier: Apache-2.0
# mypy: ignore-errors

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tomllib
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from errno import EADDRINUSE
from pathlib import Path
from time import perf_counter
from typing import Any, get_origin

import aiosqlite
import httpx
import typer
from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from vaner import __version__, api
from vaner.cli.commands import mcp_clients
from vaner.cli.commands.config import load_config, set_compute_value, set_config_value
from vaner.cli.commands.daemon import (
    COCKPIT_PROCESS,
    DAEMON_PROCESS,
    clear_pid,
    daemon_status,
    log_path,
    process_status,
    run_daemon_forever,
    start_daemon,
    stop_daemon,
    write_pid,
)
from vaner.cli.commands.deep_run import deep_run_app
from vaner.cli.commands.distill import distill_skill_file
from vaner.cli.commands.explain import render_human, render_json
from vaner.cli.commands.guidance import guidance_app
from vaner.cli.commands.init import (
    BACKEND_PRESETS,
    COMPUTE_PRESETS,
    apply_backend_config,
    apply_compute_preset,
    init_repo,
    interactive_backend_choice,
    interactive_compute_choice,
    write_mcp_configs,
)
from vaner.cli.commands.inspect import inspect_decision as inspect_decision_output
from vaner.cli.commands.inspect import inspect_last as inspect_last_output
from vaner.cli.commands.inspect import list_decisions as list_decisions_output
from vaner.cli.commands.primer import PRIMER_SURFACES, write_primers
from vaner.cli.commands.profile import export_pins, import_pins, pin_fact, profile_show, unpin_fact
from vaner.cli.commands.runtime_snapshot import runtime_snapshot
from vaner.cli.commands.supervisor import run_down, run_up
from vaner.daemon.http import create_daemon_http_app
from vaner.daemon.preflight import check_repo_root
from vaner.daemon.runner import VanerDaemon
from vaner.eval import evaluate_repo, run_eval
from vaner.models.config import VanerConfig
from vaner.router.backends import forward_chat_completion_with_request
from vaner.router.proxy import create_app
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore

app = typer.Typer(help="Vaner CLI", invoke_without_command=True)
daemon_app = typer.Typer(help="Daemon controls")
config_app = typer.Typer(help="Show config")
profile_app = typer.Typer(help="Profile memory controls")
scenarios_app = typer.Typer(help="Scenario cockpit commands")
_VERBOSE = False
_console = Console()


@dataclass(slots=True)
class _UninstallStats:
    mcp_configs_updated: int = 0
    skills_removed: int = 0
    state_files_removed: int = 0


def _repo_root(path: str | None) -> Path:
    if path:
        return Path(path).resolve()
    env_path = os.environ.get("VANER_PATH", "").strip()
    return Path(env_path).resolve() if env_path else Path.cwd()


def _fail(message: str, *, hint: str | None = None, code: int = 1) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    if hint:
        typer.secho(f"hint: {hint}", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(code=code)


def _remove_vaner_from_json_config(path: Path, *, container_key: str = "mcpServers") -> bool:
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


def _remove_managed_skill(path: Path) -> bool:
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    for parent in path.parents:
        if parent in {Path.home(), Path("/")}:
            break
        try:
            parent.rmdir()
        except OSError:
            break
    return True


def _remove_mcp_config_entries(repo_root: Path) -> int:
    updated = 0
    updated_paths: set[Path] = set()

    repo_cursor = repo_root / ".cursor" / "mcp.json"
    if _remove_vaner_from_json_config(repo_cursor, container_key="mcpServers"):
        updated += 1
        updated_paths.add(repo_cursor)

    for detected in mcp_clients.detect_all(repo_root):
        config_path = detected.path
        if config_path is None:
            continue
        if config_path in updated_paths:
            continue
        if detected.spec.kind == "json-mcpServers":
            if _remove_vaner_from_json_config(config_path, container_key="mcpServers"):
                updated += 1
        elif detected.spec.kind == "json-servers":
            if _remove_vaner_from_json_config(config_path, container_key="servers"):
                updated += 1
        elif detected.spec.kind == "json-context_servers":
            if _remove_vaner_from_json_config(config_path, container_key="context_servers"):
                updated += 1
        elif detected.spec.kind == "yaml-continue":
            if config_path.exists() and "name: vaner" in config_path.read_text(encoding="utf-8"):
                config_path.unlink(missing_ok=True)
                updated += 1
    return updated


def _annotation_name(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", str(annotation))
    if origin is list:
        args = getattr(annotation, "__args__", ())
        inner = _annotation_name(args[0]) if args else "Any"
        return f"list[{inner}]"
    if origin is dict:
        args = getattr(annotation, "__args__", ())
        if len(args) == 2:
            return f"dict[{_annotation_name(args[0])}, {_annotation_name(args[1])}]"
        return "dict[Any, Any]"
    if origin is tuple:
        args = getattr(annotation, "__args__", ())
        return f"tuple[{', '.join(_annotation_name(arg) for arg in args)}]"
    if origin is type(None):
        return "None"
    if str(origin).endswith("Literal"):
        args = getattr(annotation, "__args__", ())
        return f"Literal[{', '.join(repr(arg) for arg in args)}]"
    return str(annotation).replace("typing.", "")


def _display_value(value: Any) -> str:
    jsonable = to_jsonable_python(value)
    if isinstance(jsonable, bool):
        return "true" if jsonable else "false"
    if isinstance(jsonable, str):
        return jsonable
    return json.dumps(jsonable, ensure_ascii=True)


def _format_fix_hint(fix: str) -> str:
    if not _console.is_terminal:
        return fix
    if "mcp" in fix.lower():
        return f"{fix} [link=https://docs.vaner.ai/mcp]docs.vaner.ai/mcp[/link]"
    return fix


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost"}


def _require_safe_mcp_sse_exposure(host: str) -> None:
    if not _is_loopback_host(host):
        raise typer.BadParameter("MCP SSE transport only supports loopback hosts by default.")


def _friendly_error_message(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"File not found: {exc}. Check your repo path or run `vaner init`."
    if isinstance(exc, PermissionError):
        return f"Permission denied: {exc}. Check file permissions for `.vaner/` and your repo."
    if isinstance(exc, httpx.HTTPError):
        return f"Backend request failed: {exc}. Check network access and API key configuration."
    if isinstance(exc, aiosqlite.Error):
        return f"Database error: {exc}. Remove `.vaner/store.db` if it is corrupted, then rerun."
    return f"Vaner failed: {exc}"


def _is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) != 0


def _next_free_port(host: str, start_port: int, max_offset: int = 10) -> int | None:
    for candidate in range(start_port, start_port + max_offset + 1):
        if _is_port_free(host, candidate):
            return candidate
    return None


def _detect_local_runtime() -> dict[str, object]:
    probes = [
        ("ollama", "http://127.0.0.1:11434/api/tags"),
        ("lmstudio", "http://127.0.0.1:1234/v1/models"),
        ("openai-compatible", "http://127.0.0.1:8000/v1/models"),
    ]
    for name, url in probes:
        try:
            response = httpx.get(url, timeout=1.2)
            if response.status_code < 400:
                return {"detected": True, "name": name, "url": url}
        except Exception:
            continue
    return {"detected": False}


def _detect_hardware_profile() -> dict[str, object]:
    profile: dict[str, object] = {"device": "cpu", "gpu_count": 0, "vram_gb": 0}
    try:  # pragma: no cover - torch/cuda depends on environment
        import torch

        if torch.cuda.is_available():
            profile["device"] = "cuda"
            profile["gpu_count"] = torch.cuda.device_count()
            if torch.cuda.device_count() > 0:
                props = torch.cuda.get_device_properties(0)
                profile["vram_gb"] = round(props.total_memory / (1024**3), 1)
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            profile["device"] = "mps"
    except Exception:
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            try:
                output = subprocess.check_output([nvidia_smi, "--list-gpus"], text=True, timeout=1.5)
                lines = [line for line in output.splitlines() if line.strip()]
                if lines:
                    profile["device"] = "cuda"
                    profile["gpu_count"] = len(lines)
            except Exception:
                pass
    return profile


async def _scenario_store(repo_root: Path) -> ScenarioStore:
    store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    await store.initialize()
    return store


def _canon_key(key: str) -> str:
    normalized = key.strip()
    if normalized in {"max_age_seconds", "max_context_tokens"}:
        return f"limits.{normalized}"
    return normalized


def _config_attr_path_for_key(key: str) -> str:
    if key == "intent.skills_loop.enabled":
        return "intent.skills_loop_enabled"
    if key == "intent.skills_loop.max_feedback_events_per_cycle":
        return "intent.max_feedback_events_per_cycle"
    if key.startswith("limits."):
        return key.removeprefix("limits.")
    return key


def _config_write_target_for_key(key: str) -> tuple[str, str]:
    if key.startswith("limits."):
        return "limits", key.removeprefix("limits.")
    parts = key.split(".")
    if len(parts) < 2:
        raise ValueError("Key must include a section prefix.")
    return ".".join(parts[:-1]), parts[-1]


def _annotation_for_path(root_model: type[Any], parts: list[str]) -> tuple[Any, str]:
    if parts and parts[0] == "limits":
        if len(parts) != 2 or parts[1] not in {"max_age_seconds", "max_context_tokens"}:
            raise ValueError(f"Unsupported setting: {'.'.join(parts)}")
        field = VanerConfig.model_fields[parts[1]]
        return field.annotation, field.description or ""
    model = root_model
    breadcrumb: list[str] = []
    for idx, part in enumerate(parts):
        fields = getattr(model, "model_fields", {})
        field = fields.get(part)
        breadcrumb.append(part)
        if field is None:
            if part == "skills_loop" and ".".join(breadcrumb[:-1]) == "intent":
                continue
            if len(breadcrumb) >= 3 and ".".join(breadcrumb[:2]) == "gateway.routes":
                return str, "Dynamic gateway route target URL."
            raise ValueError(f"Unsupported setting: {'.'.join(parts)}")
        annotation = field.annotation
        description = field.description or ""
        origin = get_origin(annotation)
        if idx == len(parts) - 1:
            return annotation, description
        if origin is dict and len(breadcrumb) >= 2 and ".".join(breadcrumb) == "gateway.routes":
            return str, "Dynamic gateway route target URL."
        if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
            model = annotation
            continue
        raise ValueError(f"Unsupported setting: {'.'.join(parts)}")
    raise ValueError(f"Unsupported setting: {'.'.join(parts)}")


def _coerce_value(raw: str, annotation: Any) -> Any:
    candidate: Any = raw
    if annotation is bool:
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError(raw)
    if get_origin(annotation) in {list, dict}:
        try:
            candidate = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(raw) from exc
    adapter = TypeAdapter(annotation)
    return adapter.validate_python(candidate)


def _value_at_path(config: VanerConfig, dotted_path: str) -> Any:
    current: Any = config
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part)
    return current


def _iter_config_keys(config: VanerConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in ("backend", "generation", "privacy", "proxy", "gateway", "mcp", "intent", "compute", "exploration"):
        section_model = getattr(config, section)
        section_fields = section_model.__class__.model_fields
        for field_name, field in section_fields.items():
            if section == "intent" and field_name in {"skills_loop_enabled", "max_feedback_events_per_cycle"}:
                continue
            key = f"{section}.{field_name}"
            value = getattr(section_model, field_name)
            rows.append({"key": key, "value": value, "annotation": field.annotation, "description": field.description or ""})
            if section == "gateway" and field_name == "routes" and isinstance(value, dict):
                for route_key, route_value in value.items():
                    rows.append(
                        {
                            "key": f"gateway.routes.{route_key}",
                            "value": route_value,
                            "annotation": str,
                            "description": "Dynamic gateway route target URL.",
                        }
                    )
    rows.append(
        {
            "key": "intent.skills_loop.enabled",
            "value": config.intent.skills_loop_enabled,
            "annotation": bool,
            "description": "Enable closed-loop skill attribution feedback.",
        }
    )
    rows.append(
        {
            "key": "intent.skills_loop.max_feedback_events_per_cycle",
            "value": config.intent.max_feedback_events_per_cycle,
            "annotation": int,
            "description": "Max feedback events consumed each cycle.",
        }
    )
    rows.append(
        {
            "key": "limits.max_age_seconds",
            "value": config.max_age_seconds,
            "annotation": int,
            "description": VanerConfig.model_fields["max_age_seconds"].description or "",
        }
    )
    rows.append(
        {
            "key": "limits.max_context_tokens",
            "value": config.max_context_tokens,
            "annotation": int,
            "description": VanerConfig.model_fields["max_context_tokens"].description or "",
        }
    )
    rows.append(
        {
            "key": "gateway.routes.<prefix>",
            "value": "<url>",
            "annotation": str,
            "description": "Set prefix route: vaner config set gateway.routes.gpt- https://api.openai.com/v1",
        }
    )
    return sorted(rows, key=lambda row: row["key"])


@app.callback()
def app_callback(
    verbose: bool = typer.Option(False, "--verbose", help="Show full traceback on errors"),
    version: bool = typer.Option(False, "--version", help="Show installed Vaner version and exit.", is_eager=True),
) -> None:
    if version:
        typer.echo(f"vaner {__version__}")
        raise typer.Exit()
    global _VERBOSE
    _VERBOSE = verbose


@app.command("init", help="Initialize Vaner config and MCP client wiring.", rich_help_panel="Get started")
def init(
    path: str | None = typer.Option(None, help="Repository root"),
    backend_preset: str | None = typer.Option(
        None,
        "--backend-preset",
        help=f"Configure [backend] using a preset: {', '.join(sorted(BACKEND_PRESETS))} or 'skip'.",
    ),
    backend_url: str | None = typer.Option(None, "--backend-url", help="Override backend base_url."),
    backend_model: str | None = typer.Option(None, "--backend-model", help="Override backend model name."),
    backend_api_key_env: str | None = typer.Option(None, "--backend-api-key-env", help="Env var holding the cloud provider API key."),
    compute_preset: str | None = typer.Option(
        None,
        "--compute-preset",
        help=f"Compute preset: {', '.join(sorted(COMPUTE_PRESETS))}. Default: background.",
    ),
    max_session_minutes: int | None = typer.Option(
        None,
        "--max-session-minutes",
        help="Hard wall-clock cap for a continuous `vaner daemon` session (minutes).",
    ),
    interactive: bool | None = typer.Option(
        None,
        "--interactive/--no-interactive",
        help="Force or skip the backend/compute picker. Defaults to interactive when stdin is a TTY.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing backend config even if already populated.",
    ),
    no_mcp: bool = typer.Option(False, "--no-mcp", help="Skip writing MCP client config files"),
    no_primer: bool = typer.Option(
        False,
        "--no-primer",
        help="Skip installing per-client usage primers (guidance markdown for each detected MCP client).",
    ),
    user_primer: bool = typer.Option(
        False,
        "--user-primer",
        help=(
            "Also install the Claude Code primer at the user scope (~/.claude/CLAUDE.md) in addition to the repo scope (.claude/CLAUDE.md)."
        ),
    ),
) -> None:
    """Initialize Vaner in the current repo and (optionally) pick a model backend.

    Running ``vaner init`` a second time with no flags is idempotent: existing
    ``[backend]`` values are preserved unless ``--force`` is passed.
    """
    repo_root = _repo_root(path)
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        task = progress.add_task("Initializing repository config...", total=None)
        config_path = init_repo(repo_root)
        progress.update(task, description="Initialization complete")
    typer.echo(f"Initialized Vaner at {config_path}")

    if not no_mcp:
        try:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
                task = progress.add_task("Writing MCP client configuration...", total=None)
                written, launcher_command = write_mcp_configs(repo_root)
                progress.update(task, description="MCP configuration written")
            typer.echo("Configured MCP clients:")
            for item in written:
                typer.echo(f"  - {item}")
            typer.echo(f"Scaffolded MCP client configs using `{launcher_command}`")
        except Exception as exc:
            typer.echo(f"Warning: could not write MCP client configs: {exc}")

    if not no_primer:
        try:
            supported = sorted(PRIMER_SURFACES.keys())
            primer_results = write_primers(
                supported,
                repo_root,
                include_user_scope=user_primer,
            )
            installed = [r for r in primer_results if r.action in ("added", "updated")]
            skipped = [r for r in primer_results if r.action == "skipped"]
            failed = [r for r in primer_results if r.action == "failed"]
            if installed:
                typer.echo("Installed per-client usage primers:")
                for r in installed:
                    typer.echo(f"  - {r.path} ({r.action})")
            if skipped:
                typer.echo(f"Primer already up to date for {len(skipped)} client(s); no changes needed.")
            for r in failed:
                typer.echo(f"Warning: could not write primer for {r.client_id}: {r.error}")
        except Exception as exc:  # pragma: no cover - defensive
            typer.echo(f"Warning: could not install primers: {exc}")

    import sys as _sys

    resolved_interactive = interactive if interactive is not None else _sys.stdin.isatty()

    if backend_preset is None and (backend_url or backend_model) is None and resolved_interactive:
        choice = interactive_backend_choice()
        if choice and choice != "skip":
            backend_preset = choice

    if backend_preset or backend_url or backend_model:
        preset_id = backend_preset or "custom"
        changed = apply_backend_config(
            config_path,
            preset_id,
            base_url=backend_url,
            model=backend_model,
            api_key_env=backend_api_key_env,
            force=force,
        )
        if changed:
            typer.echo(f"Applied backend preset '{preset_id}' to {config_path.name}")
        else:
            typer.echo("Backend config already populated; pass --force to overwrite.")

    if compute_preset is None and resolved_interactive and backend_preset != "skip":
        compute_preset = interactive_compute_choice()

    if compute_preset or max_session_minutes:
        if apply_compute_preset(config_path, compute_preset or "background", max_session_minutes=max_session_minutes):
            typer.echo(
                f"Applied compute preset '{compute_preset or 'background'}'"
                + (f" (max_session_minutes={max_session_minutes})" if max_session_minutes else "")
            )

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        task = progress.add_task("Running local runtime and hardware checks...", total=None)
        runtime = _detect_local_runtime()
        hardware = _detect_hardware_profile()
        progress.update(task, description="Local checks complete")
    detected_device = "cpu"
    detected_embedding_device = "cpu"
    if hardware.get("device") == "cuda":
        detected_device = "cuda:0"
        detected_embedding_device = "cuda"
    elif hardware.get("device") == "mps":
        detected_device = "mps"
        detected_embedding_device = "mps"
    set_compute_value(repo_root, "device", detected_device)
    set_compute_value(repo_root, "embedding_device", detected_embedding_device)
    typer.echo(f"Detected compute defaults: device={detected_device}, embedding_device={detected_embedding_device}")
    if runtime.get("detected"):
        typer.echo(f"Detected local runtime: {runtime['name']} ({runtime['url']})")
    else:
        typer.echo("No local runtime detected on localhost ports (11434/1234/8000).")
        typer.echo("Recommended: curl -fsSL https://vaner.ai/install.sh | bash -s -- --with-ollama")
    typer.echo(f"Hardware profile: device={hardware['device']} gpu_count={hardware['gpu_count']} vram_gb={hardware['vram_gb']}")
    typer.echo("Next: connect your AI client → https://docs.vaner.ai/mcp")
    checklist_lines = [
        "1. Start the background worker: `vaner daemon start` (or `vaner daemon run-forever`).",
        "2. Ensure local runtime is ready: `ollama serve`, then `ollama pull <model>`.",
        "3. Open the cockpit: `vaner show` (http://127.0.0.1:8473/).",
        "4. Enable the `vaner` MCP tool in your client and reload the window.",
        "5. Ask the agent to use Vaner MCP tools for non-trivial tasks.",
        "",
        "Vaner is local-first. No background process runs unless you start the daemon",
        "or your MCP client invokes it. `vaner doctor` will tell you what's missing.",
    ]
    _console.print(Panel("\n".join(checklist_lines), title="What happens next", border_style="cyan"))
    has_vscode = (repo_root / ".vscode").exists() or os.environ.get("TERM_PROGRAM") == "vscode"
    has_cursor = os.environ.get("CURSOR_TRACE_ID") is not None or os.environ.get("CURSOR_AGENT") is not None
    if has_vscode or has_cursor:
        typer.echo("Detected VS Code/Cursor environment.")
        typer.echo("Install extension: cd ide/vscode && npm install && npm run build")
        typer.echo("Then load the extension and run `Vaner: Open Cockpit`.")
    if resolved_interactive and typer.confirm("Install shell completion for current shell?", default=True):
        vaner_bin = shutil.which("vaner")
        if vaner_bin:
            completion_cmd = [vaner_bin, "--install-completion"]
        else:
            completion_cmd = [sys.executable, "-m", "vaner.cli.commands.app", "--install-completion"]
        completed = subprocess.run(completion_cmd, check=False)
        if completed.returncode == 0:
            typer.echo("Shell completion installed.")
        else:
            typer.echo("Could not install completion automatically. Run `vaner --install-completion` manually.")
    else:
        typer.echo("Tip: enable command completion with `vaner --install-completion`.")


@daemon_app.command("start")
def daemon_start(
    path: str | None = typer.Option(None, help="Repository root"),
    once: bool = typer.Option(True, help="Run one cycle only"),
    interval_seconds: int = typer.Option(15, "--interval-seconds", help="Loop interval for background mode"),
    force: bool = typer.Option(False, "--force", help="Allow broad/non-repo root paths."),
) -> None:
    repo_root = _repo_root(path)
    root_check = check_repo_root(repo_root, force=force)
    if not root_check.get("ok"):
        _fail(str(root_check.get("detail")), hint=str(root_check.get("fix")))
    written = start_daemon(repo_root, once=once, interval_seconds=interval_seconds)
    typer.echo(f"Daemon started. Artefacts written: {written}")


@daemon_app.command("stop")
def daemon_stop(path: str | None = typer.Option(None, help="Repository root")) -> None:
    ok = stop_daemon(_repo_root(path))
    typer.echo("Daemon stopped." if ok else "Daemon not running.")


@daemon_app.command("status")
def daemon_show_status(path: str | None = typer.Option(None, help="Repository root")) -> None:
    typer.echo(daemon_status(_repo_root(path)))


@daemon_app.command("run-forever", hidden=True)
def daemon_run_forever(
    path: str | None = typer.Option(None, help="Repository root"),
    interval_seconds: int = typer.Option(15, "--interval-seconds", help="Loop interval"),
) -> None:
    run_daemon_forever(_repo_root(path), interval_seconds=interval_seconds)


@daemon_app.command("serve-http")
def daemon_serve_http(
    path: str | None = typer.Option(None, help="Repository root"),
    host: str = typer.Option("127.0.0.1", "--host", help="Cockpit host"),
    port: int = typer.Option(8473, "--port", help="Cockpit port"),
    with_engine: bool = typer.Option(
        True,
        "--with-engine/--no-engine",
        help=(
            "Instantiate a live VanerEngine in the daemon process and run "
            "periodic precompute cycles. Required for the /predictions/* "
            "surface and the vaner.predictions.* MCP tools. Disable with "
            "--no-engine if you only need the static cockpit endpoints."
        ),
    ),
) -> None:
    import uvicorn

    from vaner.engine import build_default_engine

    repo_root = _repo_root(path)
    config = load_config(repo_root)
    engine = build_default_engine(repo_root, config) if with_engine else None
    app_instance = create_daemon_http_app(config, engine=engine)
    write_pid(repo_root, COCKPIT_PROCESS, os.getpid())
    try:
        uvicorn.run(app_instance, host=host, port=port)
    except OSError as exc:
        if exc.errno not in {EADDRINUSE, 98}:
            raise
        current = process_status(repo_root, COCKPIT_PROCESS)
        next_port = _next_free_port(host, port)
        if current.get("running") and current.get("pid") != os.getpid():
            _fail(
                f"Cockpit port {host}:{port} is already in use by an existing Vaner cockpit process.",
                hint="Run `vaner down --path .` or use a different port with `vaner daemon serve-http --port <port>`.",
            )
        if next_port is not None:
            _fail(
                f"Cockpit port {host}:{port} is busy.",
                hint=f"Try `vaner daemon serve-http --path . --host {host} --port {next_port}`.",
            )
        _fail(
            f"Cockpit port {host}:{port} is busy and no fallback port was found.",
            hint="Free the port or stop conflicting process, then rerun `vaner daemon serve-http`.",
        )
    finally:
        clear_pid(repo_root, COCKPIT_PROCESS)


@app.command("inspect", help="Inspect context decision records.", rich_help_panel="Inspect and debug")
def inspect(
    path: str | None = typer.Option(None, help="Repository root"),
    last: bool = typer.Option(False, "--last", help="Show last context decision"),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed scoring factors"),
    as_json: bool = typer.Option(False, "--json", help="Show decision record as JSON"),
) -> None:
    if last:
        typer.echo(inspect_last_output(_repo_root(path), verbose=verbose, as_json=as_json))
        return
    typer.echo(api.inspect(_repo_root(path)))


@app.command("query", help="Assemble and print context for a prompt.", rich_help_panel="Inspect and debug")
def query(
    prompt: str,
    path: str | None = typer.Option(None, help="Repository root"),
    explain: bool = typer.Option(False, "--explain", help="Show why this context package was chosen"),
    verbose: bool = typer.Option(False, "--verbose", help="Include detailed score factors with --explain"),
    as_json: bool = typer.Option(False, "--json", help="Show explanation as JSON with --explain"),
) -> None:
    package = api.query(prompt, _repo_root(path))
    typer.echo(package.injected_context)
    if not explain:
        return
    decision_record = api.inspect_last_decision(_repo_root(path))
    if decision_record is None:
        typer.echo("\nNo context decisions recorded yet.")
        return
    typer.echo("")
    typer.echo(render_json(decision_record) if as_json else render_human(decision_record, verbose=verbose))


@app.command("why", help="Explain why a decision was selected.", rich_help_panel="Inspect and debug")
def why(
    decision_id: str | None = typer.Argument(None, help="Decision id, defaults to latest"),
    path: str | None = typer.Option(None, help="Repository root"),
    list_ids: bool = typer.Option(False, "--list", help="List recent decision ids"),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Number of ids to list"),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed scoring factors"),
    as_json: bool = typer.Option(False, "--json", help="Show decision record as JSON"),
) -> None:
    repo_root = _repo_root(path)
    if list_ids:
        typer.echo(list_decisions_output(repo_root, limit=limit))
        return
    typer.echo(inspect_decision_output(repo_root, decision_id, verbose=verbose, as_json=as_json))


@app.command("distill-skill", help="Convert a decision record into SKILL.md.", rich_help_panel="Use with an agent")
def distill_skill(
    decision_id: str = typer.Argument(..., help="Decision id to distill"),
    name: str | None = typer.Option(None, "--name", help="Optional skill name override"),
    out_dir: str | None = typer.Option(None, "--out-dir", help="Output directory"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing SKILL.md"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    repo_root = _repo_root(path)
    destination = distill_skill_file(
        repo_root,
        decision_id,
        out_dir=Path(out_dir).resolve() if out_dir else None,
        skill_name=name,
        force=force,
    )
    typer.echo(f"Wrote distilled skill: {destination}")


@app.command("prepare", help="Prepare repository artefacts for retrieval.", rich_help_panel="Background and local")
def prepare(path: str | None = typer.Option(None, help="Repository root")) -> None:
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        task = progress.add_task("Preparing artefacts...", total=None)
        generated = api.prepare(_repo_root(path))
        progress.update(task, description="Preparation complete")
    typer.echo(f"Prepared artefacts: {generated}")


@app.command("predict", help="Show top predicted next intents.", rich_help_panel="Benchmark")
def predict(
    path: str | None = typer.Option(None, help="Repository root"),
    top_k: int = typer.Option(5, "--top-k", help="Number of predictions to return"),
) -> None:
    predictions = api.predict(_repo_root(path), top_k=top_k)
    typer.echo(json.dumps(predictions, indent=2))


@app.command("precompute", help="Run one precompute cycle.", rich_help_panel="Background and local")
def precompute(path: str | None = typer.Option(None, help="Repository root")) -> None:
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        task = progress.add_task("Running precompute cycle...", total=None)
        produced = api.precompute(_repo_root(path))
        progress.update(task, description="Precompute complete")
    typer.echo(f"Precompute cycle completed. Full packages cached: {produced}")


@app.command("forget", help="Delete local Vaner state files.", rich_help_panel="Background and local")
def forget(path: str | None = typer.Option(None, help="Repository root")) -> None:
    removed = api.forget(_repo_root(path))
    typer.echo(f"Removed {removed} local state files.")


@app.command("uninstall", help="Remove managed Vaner client wiring and skills.", rich_help_panel="Background and local")
def uninstall(
    path: str | None = typer.Option(None, help="Repository root"),
    keep_state: bool = typer.Option(False, "--keep-state", help="Keep local .vaner state files."),
) -> None:
    repo_root = _repo_root(path)
    stats = _UninstallStats()
    stats.mcp_configs_updated = _remove_mcp_config_entries(repo_root)
    managed_skills = [
        repo_root / ".cursor" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md",
    ]
    for skill_path in managed_skills:
        if _remove_managed_skill(skill_path):
            stats.skills_removed += 1
    if not keep_state:
        stats.state_files_removed = api.forget(repo_root)

    typer.echo(
        "Uninstall complete: "
        f"updated {stats.mcp_configs_updated} client configs, "
        f"removed {stats.skills_removed} managed skills, "
        f"removed {stats.state_files_removed} local state files."
    )


@config_app.command("show")
def config_show(
    path: str | None = typer.Option(None, help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON payload."),
) -> None:
    config = load_config(_repo_root(path))
    if as_json:
        typer.echo(json.dumps(to_jsonable_python(config.model_dump(mode="python")), indent=2))
        return
    defaults = VanerConfig(
        repo_root=config.repo_root,
        store_path=config.store_path,
        telemetry_path=config.telemetry_path,
    )
    table = Table(title="Vaner configuration", show_lines=False)
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    table.add_column("Status", style="magenta")
    for row in _iter_config_keys(config):
        if "<prefix>" in row["key"]:
            status = "template"
        else:
            try:
                default_value = _value_at_path(defaults, _config_attr_path_for_key(row["key"]))
            except Exception:
                default_value = None
            status = "overridden" if row["value"] != default_value else "default"
        table.add_row(row["key"], _display_value(row["value"]), status)
    _console.print(table)


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Setting path (for example: backend.model)."),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    canonical = _canon_key(key)
    config = load_config(_repo_root(path))
    try:
        value = _value_at_path(config, _config_attr_path_for_key(canonical))
    except Exception:
        _fail(
            f"Unsupported setting: {canonical}",
            hint="Run `vaner config keys` to list valid keys.",
        )
    typer.echo(json.dumps(value) if not isinstance(value, str) else value)


@config_app.command("keys")
def config_keys(path: str | None = typer.Option(None, help="Repository root")) -> None:
    rows = _iter_config_keys(load_config(_repo_root(path)))
    table = Table(title="Settable configuration keys")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Type", style="green")
    table.add_column("Current")
    table.add_column("Description")
    for row in rows:
        annotation = row["annotation"]
        annotation_name = _annotation_name(annotation)
        current = _display_value(row["value"])
        table.add_row(row["key"], annotation_name, str(current), row["description"])
    _console.print(table)


@config_app.command("edit")
def config_edit(path: str | None = typer.Option(None, help="Repository root")) -> None:
    repo_root = _repo_root(path)
    config_path = repo_root / ".vaner" / "config.toml"
    if not config_path.exists():
        _fail(f"Config file not found: {config_path}", hint="Run `vaner init` first.")
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(config_path)], check=False)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Setting path (for example: backend.model)."),
    value: str = typer.Argument(..., help="Setting value"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    canonical = _canon_key(key)
    parts = canonical.split(".")
    if len(parts) < 2:
        _fail("Key must include section prefix, e.g. compute.cpu_fraction")
    try:
        annotation, _ = _annotation_for_path(VanerConfig, parts)
    except ValueError:
        _fail(f"Unsupported setting: {canonical}", hint="Run `vaner config keys` to list valid keys.")
    try:
        parsed_value = _coerce_value(value, annotation)
    except Exception:
        hint = f"Expected type: {_annotation_name(annotation)}"
        if get_origin(annotation) in {list, dict}:
            hint = f'{hint}; for list/dict values pass JSON, e.g. \'["a","b"]\''
        _fail(f"Invalid value for {canonical}: {value}", hint=hint, code=1)
    section, field = _config_write_target_for_key(canonical)
    config_path = set_config_value(_repo_root(path), section, field, parsed_value)
    typer.echo(f"Updated {canonical} in {config_path}")


@profile_app.command("show")
def profile_show_command(path: str | None = typer.Option(None, help="Repository root")) -> None:
    payload = profile_show(_repo_root(path))
    typer.echo(json.dumps(payload, indent=2))


@profile_app.command("pin")
def profile_pin_command(
    assignment: str = typer.Argument(..., help="Pin as KEY=VALUE"),
    path: str | None = typer.Option(None, help="Repository root"),
    scope: str = typer.Option("user", "--scope", help="Pin scope: user|project|workflow"),
) -> None:
    saved = pin_fact(_repo_root(path), assignment, scope=scope)
    typer.echo(f"Pinned {saved['key']} in scope={saved['scope']}.")


@profile_app.command("unpin")
def profile_unpin_command(
    key: str = typer.Argument(..., help="Pin key to remove"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    removed = unpin_fact(_repo_root(path), key)
    typer.echo(f"Removed pin '{key}'." if removed else f"Pin '{key}' not found.")


@profile_app.command("export")
def profile_export_command(
    path: str | None = typer.Option(None, help="Repository root"),
    out: str | None = typer.Option(None, "--out", help="Destination file"),
) -> None:
    output_path = export_pins(_repo_root(path), Path(out).resolve() if out else None)
    typer.echo(f"Exported pins to {output_path}")


@profile_app.command("import")
def profile_import_command(
    source: str = typer.Argument(..., help="Pin file to import"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    imported = import_pins(_repo_root(path), Path(source).resolve())
    typer.echo(f"Imported {imported} pins.")


@app.command("eval", help="Evaluate Vaner quality in current repository.", rich_help_panel="Benchmark")
def eval_repo(path: str | None = typer.Option(None, help="Repository root")) -> None:
    result = evaluate_repo(_repo_root(path))
    typer.echo(result.model_dump_json(indent=2))


@app.command("run-eval", help="Run eval suite from custom case file.", rich_help_panel="Benchmark")
def run_eval_command(
    path: str | None = typer.Option(None, help="Repository root"),
    cases_file: str | None = typer.Option(None, "--cases-file", help="Path to eval cases JSON"),
    output_dir: str | None = typer.Option(None, "--output-dir", help="Directory for eval run JSON output"),
) -> None:
    repo_root = _repo_root(path)
    result = run_eval(
        repo_root,
        cases_path=Path(cases_file).resolve() if cases_file else None,
        output_dir=Path(output_dir).resolve() if output_dir else None,
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("mcp", help="Start Vaner MCP server for IDE clients.", rich_help_panel="Use with an agent")
def mcp_server(
    path: str | None = typer.Option(None, help="Repository root"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport: stdio | sse"),
    host: str = typer.Option("127.0.0.1", "--host", help="SSE server host (sse transport only)"),
    port: int = typer.Option(8472, "--port", "-p", help="SSE server port (sse transport only)"),
) -> None:
    """Start the Vaner MCP server for native IDE integration.

    Stdio mode (for Claude Desktop / Cursor):

        vaner mcp --path /your/repo

    SSE mode (for network/remote access):

        vaner mcp --path /your/repo --transport sse --port 8472

    See src/vaner/mcp/server.py for configuration examples.
    """
    import asyncio as _asyncio

    if transport == "sse":
        _require_safe_mcp_sse_exposure(host)

    try:
        from vaner.mcp.server import run_sse, run_stdio
    except ImportError as exc:  # pragma: no cover
        _fail(f"MCP not available: {exc}", hint="Install optional extras: pip install 'vaner[mcp]'.")

    repo_root = _repo_root(path)

    if transport == "sse":
        typer.echo(f"Starting Vaner MCP server (SSE) on {host}:{port}  repo={repo_root}")
        try:
            _asyncio.run(run_sse(repo_root, host=host, port=port))
        except RuntimeError as exc:
            _fail(str(exc), hint="Install optional extras: pip install 'vaner[mcp]'.")
    else:
        try:
            _asyncio.run(run_stdio(repo_root))
        except RuntimeError as exc:
            _fail(str(exc), hint="Install optional extras: pip install 'vaner[mcp]'.")


@app.command("proxy", help="Start optional OpenAI-compatible proxy gateway.", rich_help_panel="Configure")
def proxy(
    path: str | None = typer.Option(None, help="Repository root"),
    host: str = "127.0.0.1",
    port: int = 8471,
) -> None:
    import uvicorn

    repo_root = _repo_root(path)
    config = load_config(repo_root)
    typer.echo("vaner proxy is an optional capability for non-MCP clients. MCP-first flow remains default.")
    daemon = VanerDaemon(config)
    app_instance = create_app(config, daemon.store)
    uvicorn.run(
        app_instance,
        host=host,
        port=port,
        ssl_certfile=config.proxy.ssl_certfile or None,
        ssl_keyfile=config.proxy.ssl_keyfile or None,
    )


@app.command("metrics", rich_help_panel="Inspect and debug")
def metrics_cmd(
    path: str | None = typer.Option(None, help="Repository root"),
    last: int = typer.Option(100, "--last", "-n", help="Number of recent requests to include"),
    output: str = typer.Option("summary", "--output", "-o", help="Output format: summary | json | csv"),
) -> None:
    """Show end-to-end latency and cache-hit metrics from the proxy."""
    import asyncio
    import csv
    import io

    from vaner.telemetry.metrics import MetricsStore

    repo_root = _repo_root(path)
    db_path = repo_root / ".vaner" / "metrics.db"
    if not db_path.exists():
        typer.secho("No metrics yet. Run `vaner proxy` and make some requests first.", fg=typer.colors.YELLOW)
        raise typer.Exit()

    store = MetricsStore(db_path)

    async def _load():
        await store.initialize()
        summary = await store.summary(last_n=last)
        rows = await store.recent(limit=last)
        usage = await store.mode_usage_summary()
        return summary, rows, usage

    summary, rows, usage = asyncio.run(_load())

    if output == "json":
        typer.echo(json.dumps({"summary": summary, "requests": rows, "usage_by_mode": usage}, indent=2))
        return

    if output == "csv":
        if not rows:
            typer.echo("request_id,timestamp,cache_tier,context_retrieval_ms,llm_first_token_ms,llm_total_ms,total_e2e_ms")
            return
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        typer.echo(buf.getvalue())
        return

    # Default: human-readable summary
    if not rows:
        typer.secho("No requests recorded yet.", fg=typer.colors.YELLOW)
        return

    typer.echo(f"\nVaner Proxy Metrics (last {summary['count']} requests)")
    typer.echo("=" * 50)

    tiers = summary.get("cache_tiers", {})
    total = sum(tiers.values()) or 1
    typer.echo("\nCache performance:")
    for tier, count in sorted(tiers.items()):
        pct = round(count / total * 100, 1)
        typer.echo(f"  {tier:<14} {count:>5}  ({pct}%)")

    typer.echo("\nLatency (ms):")
    for label, key in [
        ("Context retrieval", "context_retrieval_ms"),
        ("LLM first token  ", "llm_first_token_ms"),
        ("LLM total        ", "llm_total_ms"),
        ("Total E2E        ", "total_e2e_ms"),
    ]:
        data = summary.get(key, {})
        avg = data.get("avg", 0.0)
        p95 = data.get("p95", 0.0)
        if avg > 0:
            typer.echo(f"  {label}  avg={avg:>8.1f}  p95={p95:>8.1f}")

    typer.echo(f"\n  Avg context tokens:  {summary.get('avg_context_tokens', 0):.0f}")
    if usage:
        typer.echo("\nIntegration usage:")
        for mode, count in usage.items():
            typer.echo(f"  {mode:<14} {count:>5}")
    typer.echo("")


# Counter prefixes allowed into the contributed-priors export. Everything
# else is considered internal decision signal and is dropped.
# When extending, prefer transition-, hit-rate-, or category-level keys over
# fine-grained operational counters.
_CONTRIB_COUNTER_PREFIXES: tuple[str, ...] = (
    "arc_transition_",
    "prompt_macro_",
    "category_hit_",
    "next_prompt_",
    "draft_",
    "calibration_",
    "bucket_budget_",
)
_CONTRIB_EXPORT_SCHEMA_VERSION = "1"


def _hash_category(name: str) -> str:
    """SHA256 category names before export so federation contributors leak only hashes."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


@app.command("prior-export", rich_help_panel="Inspect and debug")
def prior_export(
    path: str | None = typer.Option(None, help="Repository root"),
    out: str = typer.Option("contrib.jsonl", "--out", help="Output JSONL file path"),
) -> None:
    """Export aggregate prior signals without raw prompt text.

    The export is a versioned JSONL stream suitable for ingestion by the
    community-priors aggregator. Schema:

    Line 1: ``{"kind": "header", "schema_version": "1", ...}``
    Then: ``calibration`` rows, ``quality`` rows, narrowed ``counters``,
    and ``arc_transitions`` with hashed category names.
    """
    import asyncio
    import time

    repo_root = _repo_root(path)
    db_path = repo_root / ".vaner" / "metrics.db"
    store = MetricsStore(db_path)

    async def _load() -> tuple[
        dict[str, float],
        list[dict[str, object]],
        list[dict[str, object]],
        list[dict[str, object]],
    ]:
        await store.initialize()
        counters = await store._counters_map()  # noqa: SLF001 - internal export helper
        calibration = await store.calibration_snapshot()
        quality = await store.memory_quality_snapshot()
        arc_transitions = await _load_arc_transitions(repo_root)
        return counters, calibration, [quality], arc_transitions

    counters, calibration, quality_rows, arc_transitions = asyncio.run(_load())

    # Narrow counters to the allowlist prefixes — drop internal signals.
    filtered_counters = {
        key: value for key, value in counters.items() if any(key.startswith(prefix) for prefix in _CONTRIB_COUNTER_PREFIXES)
    }

    output_path = Path(out).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "kind": "header",
        "schema_version": _CONTRIB_EXPORT_SCHEMA_VERSION,
        "vaner_version": _resolve_vaner_version(),
        "exported_at": time.time(),
    }
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, sort_keys=True) + "\n")
        handle.write(json.dumps({"kind": "counters", "counters": filtered_counters}, sort_keys=True) + "\n")
        handle.write(json.dumps({"kind": "calibration", "rows": calibration}, sort_keys=True) + "\n")
        for row in quality_rows:
            handle.write(json.dumps({"kind": "quality", "row": row}, sort_keys=True) + "\n")
        if arc_transitions:
            handle.write(json.dumps({"kind": "arc_transitions", "edges": arc_transitions}, sort_keys=True) + "\n")
    typer.echo(f"Wrote prior export: {output_path}")


async def _load_arc_transitions(repo_root: Path) -> list[dict[str, object]]:
    """Read hashed arc-transition edges from habit_transitions. Returns [] on any failure."""
    try:
        from vaner.store.artefacts import ArtefactStore

        db_path = repo_root / ".vaner" / "vaner.db"
        if not db_path.exists():
            return []
        store = ArtefactStore(db_path)
        await store.initialize()
        rows = await store.list_habit_transitions(limit=5000)
        edges: list[dict[str, object]] = []
        for row in rows:
            src = str(row.get("previous_category", ""))
            dst = str(row.get("category", ""))
            count = float(row.get("transition_count", 0) or 0)
            if not src or not dst or count <= 0:
                continue
            edges.append(
                {
                    "src_hash": _hash_category(src),
                    "dst_hash": _hash_category(dst),
                    "weight": round(count, 6),
                }
            )
        return edges
    except Exception:
        return []


def _resolve_vaner_version() -> str:
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("vaner")
    except Exception:
        return "unknown"


@app.command("impact", rich_help_panel="Inspect and debug")
def impact(
    path: str | None = typer.Option(None, help="Repository root"),
    last: int = typer.Option(500, "--last", "-n", help="Number of shadow pairs to aggregate"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show before/after impact from sampled shadow runs."""
    import asyncio

    from vaner.telemetry.metrics import MetricsStore

    repo_root = _repo_root(path)
    db_path = repo_root / ".vaner" / "metrics.db"
    if not db_path.exists():
        typer.secho("No metrics database found yet.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    store = MetricsStore(db_path)

    async def _load():
        await store.initialize()
        return await store.shadow_summary(last_n=last)

    summary = asyncio.run(_load())
    idle_path = repo_root / ".vaner" / "runtime" / "idle_usage.json"
    idle_seconds = 0.0
    if idle_path.exists():
        try:
            parsed = json.loads(idle_path.read_text(encoding="utf-8"))
            idle_seconds = float(parsed.get("idle_seconds_used", 0.0))
        except Exception:
            idle_seconds = 0.0
    summary["idle_seconds_used"] = round(idle_seconds, 3)
    if as_json:
        typer.echo(json.dumps(summary, indent=2))
        return
    if summary.get("count", 0) == 0:
        typer.echo("No shadow comparisons recorded yet. Set [gateway.shadow] rate > 0 and send traffic through `vaner proxy`.")
        return
    typer.echo("Vaner impact")
    typer.echo("=" * 40)
    typer.echo(f"pairs:            {summary['count']}")
    typer.echo(f"win rate:         {summary['win_rate'] * 100:.1f}%")
    typer.echo(f"avg latency gain: {summary['avg_latency_gain_ms']:.2f} ms")
    typer.echo(f"avg token delta:  {summary['avg_token_delta']:.2f}")
    typer.echo(f"idle seconds used:{summary['idle_seconds_used']:.2f}")


@app.command("compare", rich_help_panel="Inspect and debug")
def compare(
    prompt: str = typer.Argument(..., help="Prompt to compare with and without Vaner context"),
    path: str | None = typer.Option(None, help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Run the same prompt with and without context and print deltas."""
    import asyncio

    repo_root = _repo_root(path)
    config = load_config(repo_root)

    async def _run() -> dict[str, object]:
        base_payload = {"messages": [{"role": "user", "content": prompt}], "stream": False}
        started_plain = perf_counter()
        plain = await forward_chat_completion_with_request(config, base_payload, authorization_header=None)
        plain_ms = (perf_counter() - started_plain) * 1000.0

        package = await api.aquery(prompt, repo_root, config=config, top_n=6)
        enriched_payload = {
            "messages": [
                {"role": "system", "content": "Use provided context when relevant.\n\n" + package.injected_context},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        started_enriched = perf_counter()
        enriched = await forward_chat_completion_with_request(config, enriched_payload, authorization_header=None)
        enriched_ms = (perf_counter() - started_enriched) * 1000.0

        def _assistant_text(result: dict[str, object]) -> str:
            choices = result.get("choices", [])
            if not isinstance(choices, list) or not choices:
                return ""
            first = choices[0]
            if not isinstance(first, dict):
                return ""
            message = first.get("message", {})
            if isinstance(message, dict):
                content = message.get("content", "")
                return content if isinstance(content, str) else ""
            return ""

        enriched_text = _assistant_text(enriched)
        plain_text = _assistant_text(plain)
        return {
            "prompt": prompt,
            "context_tokens": package.token_used,
            "with_context_ms": round(enriched_ms, 2),
            "without_context_ms": round(plain_ms, 2),
            "latency_gain_ms": round(plain_ms - enriched_ms, 2),
            "with_context_chars": len(enriched_text),
            "without_context_chars": len(plain_text),
            "char_delta": len(enriched_text) - len(plain_text),
        }

    result = asyncio.run(_run())
    if as_json:
        typer.echo(json.dumps(result, indent=2))
        return
    typer.echo("Vaner compare")
    typer.echo("=" * 40)
    typer.echo(f"context tokens:   {result['context_tokens']}")
    typer.echo(f"with context:     {result['with_context_ms']} ms")
    typer.echo(f"without context:  {result['without_context_ms']} ms")
    typer.echo(f"latency gain:     {result['latency_gain_ms']} ms")
    typer.echo(f"response chars Δ: {result['char_delta']}")


@scenarios_app.command("list")
def scenarios_list(
    path: str | None = typer.Option(None, help="Repository root"),
    kind: str | None = typer.Option(None, "--kind", help="Filter by kind"),
    limit: int = typer.Option(10, "--limit", min=1, max=100, help="Maximum scenarios"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    import asyncio

    repo_root = _repo_root(path)

    async def _run() -> list[dict[str, object]]:
        store = await _scenario_store(repo_root)
        rows = await store.list_top(kind=kind, limit=limit)
        return [row.model_dump(mode="json") for row in rows]

    rows = asyncio.run(_run())
    if as_json:
        typer.echo(json.dumps({"count": len(rows), "scenarios": rows}, indent=2))
        return
    typer.echo(f"Scenarios ({len(rows)})")
    typer.echo("=" * 40)
    for row in rows:
        typer.echo(f"{row['id']} [{row['kind']}] score={row['score']:.3f} freshness={row['freshness']} entities={len(row['entities'])}")


@app.command("ls", help="Alias for `vaner scenarios list`.", rich_help_panel="Use with an agent")
def ls_alias(
    path: str | None = typer.Option(None, help="Repository root"),
    kind: str | None = typer.Option(None, "--kind", help="Filter by kind"),
    limit: int = typer.Option(10, "--limit", min=1, max=100, help="Maximum scenarios"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    scenarios_list(path=path, kind=kind, limit=limit, as_json=as_json)


@scenarios_app.command("show")
def scenarios_show(
    scenario_id: str,
    path: str | None = typer.Option(None, help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    import asyncio

    repo_root = _repo_root(path)

    async def _run() -> dict[str, object] | None:
        store = await _scenario_store(repo_root)
        row = await store.get(scenario_id)
        return row.model_dump(mode="json") if row else None

    row = asyncio.run(_run())
    if row is None:
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(row, indent=2))
        return
    typer.echo(f"{row['id']} [{row['kind']}] score={row['score']:.3f}")
    typer.echo(f"freshness={row['freshness']} cost={row['cost_to_expand']} confidence={row['confidence']}")
    typer.echo(f"entities: {', '.join(row['entities'])}")
    typer.echo("\nPrepared context\n" + "-" * 40)
    typer.echo(str(row["prepared_context"]))


@scenarios_app.command("expand")
def scenarios_expand(
    scenario_id: str,
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    import asyncio

    repo_root = _repo_root(path)
    config = load_config(repo_root)

    async def _run() -> dict[str, object] | None:
        await api.aprecompute(repo_root, config=config)
        store = await _scenario_store(repo_root)
        await store.record_expansion(scenario_id)
        row = await store.get(scenario_id)
        return row.model_dump(mode="json") if row else None

    row = asyncio.run(_run())
    if row is None:
        typer.echo(f"Scenario not found: {scenario_id}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps({"ok": True, "scenario": row}, indent=2))


@scenarios_app.command("compare")
def scenarios_compare(
    scenario_a: str,
    scenario_b: str,
    path: str | None = typer.Option(None, help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    import asyncio

    repo_root = _repo_root(path)

    async def _run() -> dict[str, object]:
        store = await _scenario_store(repo_root)
        a = await store.get(scenario_a)
        b = await store.get(scenario_b)
        if a is None or b is None:
            return {}
        set_a = set(a.entities)
        set_b = set(b.entities)
        shared = sorted(set_a & set_b)
        return {
            "shared_entities": shared,
            "a": {"id": a.id, "kind": a.kind, "score": a.score, "unique_entities": sorted(set_a - set_b)},
            "b": {"id": b.id, "kind": b.kind, "score": b.score, "unique_entities": sorted(set_b - set_a)},
            "recommended": a.id if a.score >= b.score else b.id,
        }

    payload = asyncio.run(_run())
    if not payload:
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"shared entities: {len(payload['shared_entities'])}")
    typer.echo(f"recommended: {payload['recommended']}")


@scenarios_app.command("outcome")
def scenarios_outcome(
    scenario_id: str,
    result: str = typer.Option(..., "--result", help="useful|irrelevant|partial"),
    note: str = typer.Option("", "--note", help="Optional note"),
    skill: str = typer.Option("", "--skill", help="Optional skill attribution"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    import asyncio

    if result not in {"useful", "irrelevant", "partial"}:
        typer.echo("result must be useful|irrelevant|partial")
        raise typer.Exit(code=1)

    repo_root = _repo_root(path)

    async def _run() -> None:
        store = await _scenario_store(repo_root)
        skill_name = skill.strip() or None
        await store.record_outcome(scenario_id, result, skill=skill_name, source="skill" if skill_name else None)
        metrics = MetricsStore(repo_root / ".vaner" / "metrics.db")
        await metrics.initialize()
        await metrics.record_scenario_outcome(scenario_id=scenario_id, result=result, note=note, skill=skill_name)

    asyncio.run(_run())
    typer.echo(f"Recorded outcome for {scenario_id}: {result}")


@app.command("watch", rich_help_panel="Use with an agent")
def watch(
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL"),
    as_json: bool = typer.Option(False, "--json", help="Emit raw decision events as JSON"),
    contains: str | None = typer.Option(None, "--filter", help="Substring filter against serialized decision payload"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Stop after N matching events"),
) -> None:
    """Tail live scenario events from the cockpit SSE stream."""
    stream_url = f"{cockpit_url.rstrip('/')}/scenarios/stream"
    matched = 0
    typer.echo(f"Watching decisions on {stream_url} ... (Ctrl+C to stop)")
    with httpx.Client(timeout=None) as client:
        with client.stream("GET", stream_url, headers={"Accept": "text/event-stream"}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if contains and contains not in payload:
                    continue
                event = json.loads(payload)
                if as_json:
                    typer.echo(json.dumps(event))
                else:
                    scenario_id = event.get("id", "unknown")
                    kind = event.get("kind", "unknown")
                    score = event.get("score", 0.0)
                    freshness = event.get("freshness", "unknown")
                    typer.echo(f"[{kind}] score={score:.3f} freshness={freshness} scenario {scenario_id}")
                matched += 1
                if limit is not None and matched >= limit:
                    return


@app.command("show", rich_help_panel="Use with an agent")
def show(
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL"),
) -> None:
    """Open the local web cockpit in the default browser."""
    import webbrowser

    target_url = f"{cockpit_url.rstrip('/')}/"
    opened = webbrowser.open(target_url)
    if opened:
        typer.echo(f"Opened {target_url}")
    else:
        typer.echo(f"Open this URL manually: {target_url}")


@app.command("up", help="Start daemon and cockpit together.", rich_help_panel="Get started")
def up(
    path: str | None = typer.Option(None, help="Repository root"),
    host: str = typer.Option("127.0.0.1", "--host", help="Cockpit host"),
    port: int = typer.Option(8473, "--port", help="Cockpit port"),
    interval_seconds: int = typer.Option(15, "--interval-seconds", help="Daemon loop interval"),
    open_browser: bool | None = typer.Option(
        None,
        "--open/--no-open",
        help="Open cockpit in browser. Defaults to true on TTY sessions.",
    ),
    detach: bool = typer.Option(False, "--detach", help="Start processes and return immediately."),
    force: bool = typer.Option(False, "--force", help="Allow broad/non-repo root paths."),
) -> None:
    """Start a supervised local Vaner session."""
    should_open = _console.is_terminal if open_browser is None else open_browser
    payload = run_up(
        _repo_root(path),
        host=host,
        port=port,
        mcp_sse_port=8472,
        interval_seconds=interval_seconds,
        open_browser=should_open,
        force=force,
    )
    if payload.get("reattached"):
        typer.echo(f"Vaner already running (daemon pid={payload['daemon_pid']}, cockpit pid={payload['cockpit_pid']}).")
        return
    cockpit_url = str(payload.get("cockpit_url", f"http://{host}:{port}"))
    ready = bool(payload.get("ready"))
    checklist_lines = [
        f"Daemon PID: {payload.get('daemon_pid')}",
        f"Cockpit PID: {payload.get('cockpit_pid')}",
        f"Cockpit URL: {cockpit_url}",
        "Health: ready" if ready else "Health: still starting (check `vaner logs`).",
        "Next: run `vaner status` to verify full readiness.",
    ]
    ports = payload.get("ports", {})
    if isinstance(ports, dict) and ports.get("cockpit_changed"):
        checklist_lines.append(f"Cockpit port auto-shifted to {cockpit_url} because {host}:{port} was busy.")
    inotify = payload.get("inotify", {})
    if isinstance(inotify, dict) and not inotify.get("ok", True):
        checklist_lines.append(
            f"Inotify warning: {inotify.get('detail')}. Consider `{inotify.get('fix', 'sudo sysctl fs.inotify.max_user_watches=524288')}`."
        )
    _console.print(Panel("\n".join(checklist_lines), title="Vaner up", border_style="green" if ready else "yellow"))
    if detach:
        return
    typer.echo("Press Ctrl+C to stop daemon and cockpit.")
    try:
        while True:
            import time as _time

            _time.sleep(1)
    except KeyboardInterrupt:
        down_payload = run_down(_repo_root(path))
        daemon_state = down_payload["daemon"]
        cockpit_state = down_payload["cockpit"]
        typer.echo(
            "Stopped: "
            f"daemon(pid={daemon_state['pid']}, ok={daemon_state['stopped']}), "
            f"cockpit(pid={cockpit_state['pid']}, ok={cockpit_state['stopped']})"
        )


@app.command("down", help="Stop daemon and cockpit from `vaner up`.", rich_help_panel="Get started")
def down(path: str | None = typer.Option(None, help="Repository root")) -> None:
    payload = run_down(_repo_root(path))
    daemon_state = payload["daemon"]
    cockpit_state = payload["cockpit"]
    typer.echo(
        "Stopped: "
        f"daemon(pid={daemon_state['pid']}, ok={daemon_state['stopped']}), "
        f"cockpit(pid={cockpit_state['pid']}, ok={cockpit_state['stopped']})"
    )


@app.command("logs", help="Alias for `vaner watch`.", rich_help_panel="Use with an agent")
def logs_alias(
    path: str | None = typer.Option(None, help="Repository root"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Tail runtime logs continuously."),
    events: bool = typer.Option(False, "--events", help="Show decision stream events instead of runtime logs."),
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL for --events mode."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw decision events as JSON (--events mode)."),
    contains: str | None = typer.Option(None, "--filter", help="Substring filter for --events mode."),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Stop after N matching lines/events."),
) -> None:
    if events:
        watch(cockpit_url=cockpit_url, as_json=as_json, contains=contains, limit=limit)
        return
    repo_root = _repo_root(path)
    daemon_log = log_path(repo_root, DAEMON_PROCESS)
    cockpit_log = log_path(repo_root, COCKPIT_PROCESS)
    paths = [("daemon", daemon_log), ("cockpit", cockpit_log)]
    for label, file_path in paths:
        if not file_path.exists():
            typer.echo(f"[{label}] no log file yet at {file_path}")
            continue
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-20:]
        for line in tail:
            typer.echo(f"[{label}] {line}")
    if not follow:
        return
    typer.echo("Following runtime logs... (Ctrl+C to stop)")
    offsets = {label: (path.stat().st_size if path.exists() else 0) for label, path in paths}
    emitted = 0
    try:
        while True:
            import time as _time

            for label, file_path in paths:
                if not file_path.exists():
                    continue
                with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offsets[label])
                    chunk = handle.read()
                    offsets[label] = handle.tell()
                if not chunk:
                    continue
                for line in chunk.splitlines():
                    typer.echo(f"[{label}] {line}")
                    emitted += 1
                    if limit is not None and emitted >= limit:
                        return
            _time.sleep(0.5)
    except KeyboardInterrupt:
        return


@app.command("ps", help="Alias for `vaner status`.", rich_help_panel="Get started")
def ps_alias(
    path: str | None = typer.Option(None, help="Repository root"),
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    status(path=path, cockpit_url=cockpit_url, as_json=as_json)


@app.command("status", rich_help_panel="Get started")
def status(
    path: str | None = typer.Option(None, help="Repository root"),
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show one-screen Vaner health and compute status."""
    repo_root = _repo_root(path)
    snapshot = runtime_snapshot(repo_root, cockpit_url)
    config = snapshot["config"]
    latest = api.inspect_last_decision(repo_root)
    scenario_counts = dict(snapshot["scenario_counts"])
    daemon_text = str(snapshot["daemon"]["status"])
    cockpit_ok = bool(snapshot["cockpit_reachable"])

    payload = {
        "repo_root": str(repo_root),
        "daemon": daemon_text,
        "cockpit_url": cockpit_url,
        "cockpit": {"reachable": cockpit_ok, "detail": snapshot["cockpit_detail"]},
        "mcp": config.mcp.model_dump(mode="json"),
        "compute": config.compute.model_dump(mode="json"),
        "backend": {
            "base_url": config.backend.base_url,
            "model": config.backend.model,
            "gateway_passthrough": config.gateway.passthrough_enabled,
        },
        "scenarios_ready": scenario_counts["total"],
        "scenario_counts": scenario_counts,
        "last_decision": latest.id if latest else None,
        "runtime_snapshot": {
            "daemon_pid_alive": snapshot["daemon_pid_alive"],
            "cockpit_pid_alive": snapshot["cockpit_pid_alive"],
            "backend_reachable": snapshot["backend_reachable"],
            "repo_root_sensible": snapshot["repo_root_sensible"],
            "inotify_headroom_pct": snapshot["inotify_headroom_pct"],
            "cli_up_to_date": snapshot["cli_up_to_date"],
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Vaner status")
    typer.echo("=" * 40)
    typer.echo(f"repo:     {payload['repo_root']}")
    typer.echo(f"daemon:   {payload['daemon']}")
    typer.echo(f"cockpit:  {'ok' if cockpit_ok else 'down'} ({cockpit_url})")
    typer.echo(f"mcp:      transport={config.mcp.transport} sse={config.mcp.http_host}:{config.mcp.http_port}")
    typer.echo(f"backend:  {config.backend.base_url or '(unset)'} [{config.backend.model or '(unset)'}]")
    typer.echo(
        "compute:  "
        f"device={config.compute.device} "
        f"cpu_fraction={config.compute.cpu_fraction} "
        f"gpu_fraction={config.compute.gpu_memory_fraction}"
    )
    typer.echo(f"decision: {payload['last_decision'] or 'none'}")
    typer.echo(f"scenarios:{payload['scenarios_ready']}")
    typer.echo(f"freshness: fresh={scenario_counts['fresh']} recent={scenario_counts['recent']} stale={scenario_counts['stale']}")
    if not bool(snapshot["repo_root_sensible"]):
        _console.print(
            Panel(
                str(snapshot["repo_root_fix"] or "Use a project repository path instead of your home directory."),
                title="Next best step",
                border_style="yellow",
            )
        )
    elif not bool(snapshot["daemon_pid_alive"]):
        _console.print(
            Panel(
                "Start everything with `vaner up --path .` (or run `vaner daemon start --path . --no-once`).",
                title="Next best step",
                border_style="yellow",
            )
        )
    elif not cockpit_ok:
        _console.print(
            Panel(
                "Cockpit is down. Run `vaner up --path .` or restart HTTP with `vaner daemon serve-http --path .`.",
                title="Next best step",
                border_style="yellow",
            )
        )


@app.command("doctor", rich_help_panel="Get started")
def doctor(
    path: str | None = typer.Option(None, help="Repository root"),
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Run local diagnostics and print actionable fixes."""
    repo_root = _repo_root(path)
    snapshot = runtime_snapshot(repo_root, cockpit_url)
    checks: list[dict[str, object]] = []
    config_path = repo_root / ".vaner" / "config.toml"
    checks.append(
        {
            "name": "config_exists",
            "ok": config_path.exists(),
            "detail": str(config_path),
            "fix": "Run `vaner init` in your repository." if not config_path.exists() else "",
        }
    )

    config = snapshot["config"] if config_path.exists() else None
    runtime: dict[str, object] = {"detected": False}
    if config is not None:
        has_backend = bool(config.backend.base_url.strip() and config.backend.model.strip())
        has_routes = bool(config.gateway.routes)
        checks.append(
            {
                "name": "backend_configured",
                "ok": has_backend or has_routes,
                "detail": "backend/base_url+model or gateway/routes must be set",
                "fix": "Set [backend] base_url/model or add [gateway.routes] entries in .vaner/config.toml.",
            }
        )
        checks.append(
            {
                "name": "repo_root_sensible",
                "ok": bool(snapshot["repo_root_sensible"]),
                "level": "warn" if not snapshot["repo_root_sensible"] else "pass",
                "detail": snapshot["repo_root_detail"],
                "fix": snapshot["repo_root_fix"],
            }
        )
        inotify_payload = snapshot["inotify"]
        checks.append(
            {
                "name": "inotify_headroom",
                "ok": bool(inotify_payload.get("ok", True)),
                "level": "warn" if not inotify_payload.get("ok", True) else "pass",
                "detail": inotify_payload.get("detail", ""),
                "fix": inotify_payload.get("fix", ""),
            }
        )
        checks.append(
            {
                "name": "cli_up_to_date",
                "ok": bool(snapshot["cli_up_to_date"]),
                "level": "warn" if not snapshot["cli_up_to_date"] else "pass",
                "detail": snapshot["cli_update_detail"],
                "fix": "Run `pipx upgrade vaner` (or `vaner upgrade`).",
            }
        )
        checks.append(
            {
                "name": "compute_fraction_valid",
                "ok": 0.0 < config.compute.cpu_fraction <= 1.0 and 0.0 < config.compute.gpu_memory_fraction <= 1.0,
                "detail": f"cpu_fraction={config.compute.cpu_fraction}, gpu_memory_fraction={config.compute.gpu_memory_fraction}",
                "fix": "Use `vaner config set compute.cpu_fraction 0.2` and `vaner config set compute.gpu_memory_fraction 0.5`.",
            }
        )
        runtime = _detect_local_runtime()
        checks.append(
            {
                "name": "local_runtime_detected",
                "ok": bool(runtime.get("detected")),
                "detail": runtime.get("url", "No local runtime on 11434/1234/8000"),
                "fix": "Install local runtime: curl -fsSL https://vaner.ai/install.sh | bash -s -- --with-ollama",
            }
        )
        checks.append(
            {
                "name": "exploration_llm_reachable",
                "ok": bool(runtime.get("detected")),
                "detail": runtime.get("url", "No local runtime on 11434/1234/8000"),
                "fix": "Start Ollama/LM Studio/vLLM so Vaner can precompute scenarios with local hardware.",
            }
        )
        if runtime.get("name") == "ollama" and config.backend.model.strip():
            backend_model = config.backend.model.strip()
            ollama_ok = False
            ollama_detail = "model not found"
            try:
                tags_response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=1.5)
                tags_response.raise_for_status()
                payload = tags_response.json()
                models_payload = payload.get("models", []) if isinstance(payload, dict) else []
                model_names = {
                    str(item.get("name", "")).strip()
                    for item in models_payload
                    if isinstance(item, dict) and str(item.get("name", "")).strip()
                }
                ollama_ok = backend_model in model_names
                if model_names:
                    ollama_detail = f"backend_model={backend_model} installed={', '.join(sorted(model_names)[:5])}"
                else:
                    ollama_detail = "no models returned by /api/tags"
            except Exception as exc:
                ollama_detail = str(exc)
            checks.append(
                {
                    "name": "ollama_model_pulled",
                    "ok": ollama_ok,
                    "detail": ollama_detail,
                    "fix": f"Run `ollama pull {backend_model}` to install the configured model.",
                }
            )
        is_cloud_backend = config.backend.base_url.startswith("https://")
        if is_cloud_backend and not runtime.get("detected"):
            checks.append(
                {
                    "name": "cloud_only_advisory",
                    "ok": False,
                    "detail": "Backend is remote and no local runtime detected.",
                    "fix": "Point [backend] to local Ollama/LM Studio/vLLM to use existing hardware.",
                }
            )

    checks.append(
        {
            "name": "cockpit_reachable",
            "ok": bool(snapshot["cockpit_reachable"]),
            "detail": str(snapshot["cockpit_detail"]),
            "fix": "Start everything with `vaner up --path .` (or run `vaner daemon serve-http --path .`).",
        }
    )
    checks.append(
        {
            "name": "daemon_running",
            "ok": bool(snapshot["daemon_pid_alive"]),
            "detail": str(snapshot["daemon"]["status"]),
            "fix": "Run `vaner up --path .` (or `vaner daemon start --path . --no-once`).",
        }
    )

    cursor_mcp_path = repo_root / ".cursor" / "mcp.json"
    claude_mcp_path = Path.home() / ".claude" / "claude_desktop_config.json"
    checks.append(
        {
            "name": "mcp_config_present",
            "ok": cursor_mcp_path.exists() or claude_mcp_path.exists(),
            "detail": f"cursor={cursor_mcp_path.exists()} claude={claude_mcp_path.exists()}",
            "fix": "Run `vaner init` to scaffold MCP client configuration files.",
        }
    )
    if cursor_mcp_path.exists():
        checks.append(
            {
                "name": "mcp_client_instructions",
                "ok": False,
                "level": "warn",
                "detail": "Cursor MCP config found.",
                "fix": "Reload Cursor after `vaner init` so the MCP server is picked up.",
            }
        )
    mcp_commands: list[str] = []
    for path_candidate in (cursor_mcp_path, claude_mcp_path):
        if not path_candidate.exists():
            continue
        try:
            payload = json.loads(path_candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        servers = payload.get("mcpServers", {})
        if isinstance(servers, dict):
            for server_payload in servers.values():
                if isinstance(server_payload, dict):
                    command = str(server_payload.get("command", "")).strip()
                    if command:
                        mcp_commands.append(command)
    all_commands_exist = bool(mcp_commands) and all(shutil.which(command) for command in mcp_commands)
    checks.append(
        {
            "name": "mcp_command_exists",
            "ok": all_commands_exist,
            "detail": ", ".join(mcp_commands) if mcp_commands else "No MCP commands found in client config",
            "fix": "Use `vaner init` to rewrite MCP configs with a valid command path.",
        }
    )
    if config is not None:
        config_text = config_path.read_text(encoding="utf-8")
        stale_keys = [key for key in ("exploration_model", "exploration_endpoint", "exploration_backend") if key in config_text]
        try:
            parsed = tomllib.loads(config_text)
            exploration_section = parsed.get("exploration", {})
            if isinstance(exploration_section, dict) and "embedding_device" in exploration_section:
                stale_keys.append("exploration.embedding_device")
        except Exception:
            pass
        checks.append(
            {
                "name": "config_drift",
                "ok": not stale_keys,
                "detail": "none" if not stale_keys else f"legacy keys present: {', '.join(stale_keys)}",
                "fix": "Update .vaner/config.toml to use exploration.model/endpoint/backend and compute.embedding_device.",
            }
        )
        if os.environ.get("VANER_SKIP_MCP_BOOT_PROBE", "").strip() == "1":
            checks.append(
                {
                    "name": "mcp_server_boots",
                    "ok": True,
                    "detail": "skipped (VANER_SKIP_MCP_BOOT_PROBE=1)",
                    "fix": "",
                }
            )
        else:
            try:
                boot_cmd = [shutil.which("vaner") or "vaner", "mcp", "--path", str(repo_root)]
                proc = subprocess.Popen(boot_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    proc.wait(timeout=1.5)
                    boot_ok = proc.returncode == 0
                except subprocess.TimeoutExpired:
                    boot_ok = True
                    proc.terminate()
                    proc.wait(timeout=1.0)
                checks.append(
                    {
                        "name": "mcp_server_boots",
                        "ok": boot_ok,
                        "detail": "startup probe completed",
                        "fix": "Run `vaner mcp --path .` manually to inspect startup errors.",
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "name": "mcp_server_boots",
                        "ok": False,
                        "detail": str(exc),
                        "fix": "Run `vaner mcp --path .` manually to inspect startup errors.",
                    }
                )
    try:
        import asyncio

        async def _scenario_store_check() -> dict[str, object]:
            store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
            await store.initialize()
            rows = await store.list_top(limit=1)
            return {"ok": True, "detail": f"reachable top_scenarios={len(rows)}"}

        scenario_store_check = asyncio.run(_scenario_store_check())
        checks.append(
            {
                "name": "scenario_store_reachable",
                "ok": bool(scenario_store_check["ok"]),
                "detail": str(scenario_store_check["detail"]),
                "fix": "",
            }
        )
    except Exception as exc:
        checks.append(
            {
                "name": "scenario_store_reachable",
                "ok": False,
                "detail": str(exc),
                "fix": "Run `vaner daemon start --path .` or remove a corrupted `.vaner/scenarios.db` file.",
            }
        )

    overall_ok = all(bool(item["ok"]) or item.get("level") == "warn" for item in checks)
    payload = {"ok": overall_ok, "checks": checks}
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        raise typer.Exit(code=0 if overall_ok else 1)

    _console.print("[bold]Vaner doctor[/bold]")
    _console.print("=" * 40)
    for check in checks:
        if check.get("level") == "warn":
            mark = "[yellow]WARN[/yellow]"
        else:
            mark = "[green]PASS[/green]" if check["ok"] else "[red]FAIL[/red]"
        _console.print(f"{mark} {check['name']}: {check['detail']}")
        if (not check["ok"] or check.get("level") == "warn") and check["fix"]:
            _console.print(f"       fix: {_format_fix_hint(str(check['fix']))}")
    raise typer.Exit(code=0 if overall_ok else 1)


@app.command("upgrade", help="Upgrade Vaner using pipx or pip.", rich_help_panel="Configure")
def upgrade() -> None:
    pipx_bin = shutil.which("pipx")
    if pipx_bin:
        cmd = [pipx_bin, "upgrade", "vaner"]
        typer.echo("Upgrading via pipx...")
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-U", "vaner"]
        typer.echo("Upgrading via pip...")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        _fail("Upgrade failed.", hint="Run with --verbose and inspect command output.")
    typer.echo("Upgrade complete.")


app.add_typer(daemon_app, name="daemon", rich_help_panel="Background and local")
app.add_typer(config_app, name="config", rich_help_panel="Configure")
app.add_typer(profile_app, name="profile", rich_help_panel="Background and local")
app.add_typer(scenarios_app, name="scenarios", rich_help_panel="Use with an agent")
app.add_typer(deep_run_app, name="deep-run", rich_help_panel="Background and local")
app.add_typer(guidance_app, name="guidance", rich_help_panel="Use with an agent")


def run() -> None:
    try:
        app()
    except (FileNotFoundError, PermissionError, httpx.HTTPError, aiosqlite.Error) as exc:
        typer.secho(_friendly_error_message(exc), fg=typer.colors.RED, err=True)
        if _VERBOSE:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # pragma: no cover - defensive CLI guard
        typer.secho(_friendly_error_message(exc), fg=typer.colors.RED, err=True)
        if _VERBOSE:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    run()
