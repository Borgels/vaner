# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from vaner.cli.commands.config import load_config, set_compute_value, set_config_value
from vaner.clients.daemon import VanerDaemonClient, VanerDaemonUnavailable, daemon_base_url_from_env
from vaner.focus import FocusManager

focus_app = typer.Typer(help="Auto Focus controls", no_args_is_help=True)
route_app = typer.Typer(help="Workspace routing controls", no_args_is_help=True)
resources_app = typer.Typer(help="Local resource status", no_args_is_help=True)
jobs_app = typer.Typer(help="Background job status", no_args_is_help=True)

_console = Console()


def _repo_root(path: Path | None) -> Path:
    return path.resolve() if path is not None else Path.cwd().resolve()


def _json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


async def _daemon_client_for_repo(repo_root: Path) -> VanerDaemonClient:
    client = VanerDaemonClient(base_url=daemon_base_url_from_env())
    status = await client.get_status()
    daemon_repo = status.get("repo_root") if isinstance(status, dict) else None
    if not daemon_repo or Path(str(daemon_repo)).expanduser().resolve() != repo_root.resolve():
        raise VanerDaemonUnavailable("daemon is serving a different repository")
    return client


async def _daemon_get(kind: str, repo_root: Path) -> dict[str, Any]:
    client = await _daemon_client_for_repo(repo_root)
    if kind == "focus":
        return await client.get_focus()
    if kind == "route":
        return await client.get_focus_route()
    if kind == "resources":
        return await client.get_resources()
    if kind == "jobs":
        return await client.get_jobs()
    raise ValueError(kind)


async def _daemon_focus_action(repo_root: Path, action: str, **kwargs: Any) -> dict[str, Any]:
    client = await _daemon_client_for_repo(repo_root)
    return await client.focus_action(action, **kwargs)


async def _daemon_route_set(repo_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    client = await _daemon_client_for_repo(repo_root)
    return await client.set_focus_route(payload)


async def _daemon_cancel_job(job_id: str) -> dict[str, Any]:
    return await VanerDaemonClient(base_url=daemon_base_url_from_env()).cancel_job(job_id)


def _local_focus(repo_root: Path) -> FocusManager:
    return FocusManager(load_config(repo_root))


def _print_focus(payload: dict[str, Any]) -> None:
    status = payload.get("status", "unknown")
    explanation = payload.get("explanation", "")
    _console.print(f"[bold]Status:[/bold] {status}")
    if explanation:
        _console.print(explanation)
    why_not = payload.get("why_not") or []
    if why_not:
        _console.print()
        for reason in why_not:
            if isinstance(reason, dict):
                _console.print(f"- {reason.get('message') or reason.get('reason_code')}")
    table = Table(title="Workspaces")
    table.add_column("Workspace")
    table.add_column("Tier")
    table.add_column("Confidence")
    table.add_column("State")
    for ws in payload.get("workspaces", []) or []:
        if not isinstance(ws, dict):
            continue
        state = "pinned" if ws.get("pinned") else ("paused" if ws.get("paused") else "")
        table.add_row(
            str(ws.get("display_name") or ws.get("id")),
            str(ws.get("tier") or ""),
            f"{float(ws.get('focus_confidence') or 0):.2f}",
            state,
        )
    if payload.get("workspaces"):
        _console.print(table)


def _print_resources(payload: dict[str, Any]) -> None:
    _console.print(f"[bold]Resource mode:[/bold] {payload.get('resource_mode', 'balanced')}")
    _console.print(str(payload.get("explanation") or ""))
    runtimes = payload.get("runtimes") or []
    if runtimes:
        table = Table(title="Runtimes")
        table.add_column("Runtime")
        table.add_column("Endpoint")
        table.add_column("Local")
        table.add_column("Healthy")
        for runtime in runtimes:
            if isinstance(runtime, dict):
                table.add_row(
                    str(runtime.get("kind") or runtime.get("id")),
                    str(runtime.get("endpoint") or "-"),
                    str(runtime.get("local")),
                    str(runtime.get("healthy")),
                )
        _console.print(table)


def _print_route(payload: dict[str, Any]) -> None:
    route = payload.get("effective_route") if isinstance(payload.get("effective_route"), dict) else {}
    workspace = route.get("workspace") if isinstance(route.get("workspace"), dict) else {}
    client = route.get("client") if isinstance(route.get("client"), dict) else {}
    backend = route.get("backend") if isinstance(route.get("backend"), dict) else {}
    _console.print(f"[bold]Route:[/bold] {payload.get('explanation') or 'No route selected.'}")
    _console.print(f"Workspace: {workspace.get('display_name') or 'auto'}")
    _console.print(f"Client: {client.get('display_name') or 'auto'}")
    _console.print(f"Selection: {route.get('selected_by') or route.get('workspace_policy') or 'auto'}")
    _console.print(f"Compute: {route.get('resource_mode') or 'balanced'} · {route.get('device') or 'auto'}")
    _console.print(f"Model: {backend.get('name') or 'backend'} / {backend.get('model') or 'unset'}")


def _print_jobs(payload: dict[str, Any]) -> None:
    table = Table(title="Jobs")
    table.add_column("Job")
    table.add_column("Status")
    table.add_column("Reason")
    table.add_column("Explanation")
    for job in payload.get("jobs", []) or []:
        if isinstance(job, dict):
            table.add_row(
                str(job.get("id")),
                str(job.get("status")),
                str(job.get("defer_reason") or "-"),
                str(job.get("explanation") or ""),
            )
    _console.print(table)


@focus_app.command("status")
def focus_status(
    repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    root = _repo_root(repo_root)
    try:
        payload = asyncio.run(_daemon_get("focus", root))
    except VanerDaemonUnavailable:
        payload = _local_focus(root).build_state().model_dump(mode="json")
        payload["source"] = "local_fallback"
    if json_output:
        _json(payload)
    else:
        _print_focus(payload)


@route_app.command("status")
def route_status(
    repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    root = _repo_root(repo_root)
    try:
        payload = asyncio.run(_daemon_get("route", root))
    except VanerDaemonUnavailable:
        payload = _local_focus(root).route_state().model_dump(mode="json")
        payload["source"] = "local_fallback"
    _json(payload) if json_output else _print_route(payload)


@route_app.command("set")
def route_set(
    workspace_policy: Annotated[str | None, typer.Option("--workspace-policy", help="auto, work_here, or pinned.")] = None,
    workspace: Annotated[Path | None, typer.Option("--workspace", help="Workspace path for work_here or pinned.")] = None,
    client: Annotated[str | None, typer.Option("--client", help="Preferred client id, or auto.")] = None,
    resource_mode: Annotated[str | None, typer.Option("--resource-mode", help="low_power, balanced, or performance.")] = None,
    device: Annotated[str | None, typer.Option("--device", help="Compute device, e.g. auto, cpu, cuda, mps.")] = None,
    backend_name: Annotated[str | None, typer.Option("--backend-name")] = None,
    backend_base_url: Annotated[str | None, typer.Option("--backend-base-url")] = None,
    backend_model: Annotated[str | None, typer.Option("--backend-model")] = None,
    backend_api_key_env: Annotated[str | None, typer.Option("--backend-api-key-env")] = None,
    ttl_seconds: Annotated[int | None, typer.Option("--ttl-seconds")] = None,
    repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = _repo_root(repo_root)
    if backend_api_key_env is not None:
        raise typer.BadParameter("Use `vaner config set backend.api_key_env <ENV_VAR>` to change backend credentials.")
    payload: dict[str, Any] = {}
    if workspace_policy is not None:
        payload["workspace_policy"] = workspace_policy
    if workspace is not None:
        payload["workspace_path"] = str(workspace.expanduser().resolve())
    if client is not None:
        payload["client_id"] = None if client == "auto" else client
    if resource_mode is not None:
        payload["resource_mode"] = resource_mode
    if device is not None:
        payload["compute_device"] = device
    backend = {
        "name": backend_name,
        "base_url": backend_base_url,
        "model": backend_model,
    }
    backend = {key: value for key, value in backend.items() if value is not None}
    if backend:
        payload["backend"] = backend
    if ttl_seconds is not None:
        payload["ttl_seconds"] = ttl_seconds
    try:
        result = asyncio.run(_daemon_route_set(root, payload))
    except VanerDaemonUnavailable:
        manager = _local_focus(root)
        manager.set_route_preferences(
            workspace_policy=workspace_policy,
            workspace_path=workspace,
            client_id=(None if client == "auto" else client) if client is not None else ...,
            resource_mode=resource_mode,
            ttl_seconds=ttl_seconds,
        )
        if device is not None:
            set_compute_value(root, "device", device)
        if backend:
            for key, value in backend.items():
                set_config_value(root, "backend", key, value)
        result = FocusManager(load_config(root)).route_state().model_dump(mode="json")
        result["source"] = "local_fallback"
    _json(result) if json_output else _print_route(result)


@focus_app.command("work-here")
def focus_work_here(
    path: Annotated[Path | None, typer.Argument(help="Workspace path. Defaults to cwd.")] = None,
    ttl_seconds: Annotated[int, typer.Option("--ttl-seconds", help="Override lifetime.")] = 1800,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    target = str(_repo_root(path))
    root = Path(target)
    try:
        payload = asyncio.run(_daemon_focus_action(root, "work-here", path=target, ttl_seconds=ttl_seconds))
    except VanerDaemonUnavailable:
        payload = _local_focus(Path(target)).work_here(Path(target), ttl_seconds=ttl_seconds).model_dump(mode="json")
        payload["source"] = "local_fallback"
    _json(payload) if json_output else _print_focus(payload)


@focus_app.command("pin")
def focus_pin(path: Annotated[Path | None, typer.Argument(help="Workspace path. Defaults to cwd.")] = None) -> None:
    target = str(_repo_root(path))
    root = Path(target)
    try:
        payload = asyncio.run(_daemon_focus_action(root, "pin", path=target))
    except VanerDaemonUnavailable:
        payload = _local_focus(Path(target)).pin(Path(target)).model_dump(mode="json")
    _print_focus(payload)


@focus_app.command("unpin")
def focus_unpin(repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None) -> None:
    root = _repo_root(repo_root)
    try:
        payload = asyncio.run(_daemon_focus_action(root, "unpin", path=str(root)))
    except VanerDaemonUnavailable:
        payload = _local_focus(root).unpin().model_dump(mode="json")
    _print_focus(payload)


@focus_app.command("pause")
def focus_pause(
    path: Annotated[Path | None, typer.Argument(help="Workspace path. Defaults to cwd.")] = None,
    all_workspaces: Annotated[bool, typer.Option("--all", help="Pause all Auto Focus work.")] = False,
) -> None:
    root = _repo_root(path)
    try:
        payload = asyncio.run(_daemon_focus_action(root, "pause-all" if all_workspaces else "pause", path=str(root)))
    except VanerDaemonUnavailable:
        manager = _local_focus(root)
        payload = manager.pause_all().model_dump(mode="json") if all_workspaces else manager.pause(root).model_dump(mode="json")
    _print_focus(payload)


@focus_app.command("resume")
def focus_resume(path: Annotated[Path | None, typer.Argument(help="Workspace path. Defaults to cwd.")] = None) -> None:
    root = _repo_root(path)
    try:
        payload = asyncio.run(_daemon_focus_action(root, "resume", path=str(root)))
    except VanerDaemonUnavailable:
        payload = _local_focus(root).resume(root).model_dump(mode="json")
    _print_focus(payload)


@focus_app.command("mode")
def focus_mode(
    mode: Annotated[str, typer.Argument(help="auto, manual-only, or paused.")],
    resource_mode: Annotated[str | None, typer.Option("--resource-mode", help="balanced, low_power, or performance.")] = None,
) -> None:
    try:
        root = Path.cwd().resolve()
        payload = asyncio.run(_daemon_focus_action(root, "mode", mode=mode, resource_mode=resource_mode))
    except VanerDaemonUnavailable:
        payload = _local_focus(root).set_mode(mode, resource_mode=resource_mode).model_dump(mode="json")  # type: ignore[arg-type]
    _print_focus(payload)


focus_app.add_typer(route_app, name="route")


@resources_app.command("status")
def resources_status(
    repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = _repo_root(repo_root)
    try:
        payload = asyncio.run(_daemon_get("resources", root))
    except VanerDaemonUnavailable:
        payload = _local_focus(root).resources_state().model_dump(mode="json")
        payload["source"] = "local_fallback"
    _json(payload) if json_output else _print_resources(payload)


@jobs_app.command("status")
def jobs_status(
    repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = _repo_root(repo_root)
    try:
        payload = asyncio.run(_daemon_get("jobs", root))
    except VanerDaemonUnavailable:
        payload = _local_focus(root).jobs_state()
        payload["source"] = "local_fallback"
    _json(payload) if json_output else _print_jobs(payload)


@jobs_app.command("cancel")
def jobs_cancel(job_id: Annotated[str, typer.Argument(help="Job id.")]) -> None:
    try:
        payload = asyncio.run(_daemon_cancel_job(job_id))
    except VanerDaemonUnavailable:
        payload = {
            "ok": True,
            "job_id": job_id,
            "status": "cancelled",
            "reason_code": "local_fallback",
            "explanation": "Daemon was unavailable; matching cancellable jobs will be skipped by focus gates when the daemon reads state.",
        }
    _json(payload)
