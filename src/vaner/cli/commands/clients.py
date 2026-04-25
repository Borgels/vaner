# SPDX-License-Identifier: Apache-2.0
"""`vaner clients` CLI — install Vaner into MCP clients.

0.8.5 WS12: thin non-interactive surface over :mod:`vaner.cli.commands.mcp_clients`,
so desktop apps and CI pipelines can drive install/uninstall/status/doctor
through a stable contract instead of the interactive `vaner init` wizard.

The per-client adapter knowledge (config paths, JSON vs YAML vs CLI shell-out,
backup rotation, atomic writes) all lives in `mcp_clients`. This module is
just the typer-shaped entry point.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from vaner.cli.commands import mcp_clients

clients_app = typer.Typer(
    help=(
        "Install Vaner into MCP clients (Cursor, Claude Desktop, Claude Code, "
        "Cline, Continue, Zed, Windsurf, VS Code, Codex CLI, Roo). Idempotent; "
        "safe to re-run after Vaner upgrades."
    ),
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


_STATUS_GLYPH = {
    mcp_clients.ClientStatus.CONFIGURED: ("[green]✓[/green]", "Configured"),
    mcp_clients.ClientStatus.INSTALLED: ("[yellow]·[/yellow]", "Detected"),
    mcp_clients.ClientStatus.MISSING: ("[dim]✗[/dim]", "Missing"),
}


def _detected_to_dict(detected: mcp_clients.DetectedClient) -> dict[str, object]:
    return {
        "id": detected.spec.id,
        "label": detected.spec.label,
        "kind": detected.spec.kind,
        "status": detected.status.value,
        "detected": detected.status != mcp_clients.ClientStatus.MISSING,
        "configured": detected.status == mcp_clients.ClientStatus.CONFIGURED,
        "config_path": str(detected.path) if detected.path is not None else None,
        "detail": detected.detail,
    }


def _result_to_dict(result: mcp_clients.WriteResult) -> dict[str, object]:
    return {
        "client_id": result.client_id,
        "path": str(result.path) if result.path is not None else None,
        "action": result.action,
        "backup": str(result.backup) if result.backup is not None else None,
        "error": result.error,
        "manual_snippet": result.manual_snippet,
    }


def _drift_to_dict(report: mcp_clients.LauncherDrift) -> dict[str, object]:
    return {
        "client_id": report.client_id,
        "label": report.label,
        "config_path": str(report.config_path) if report.config_path is not None else None,
        "drift": report.drift,
        "current_in_config": report.current_in_config,
        "expected": report.expected,
        "detail": report.detail,
    }


def _print_detect_table(detected_list: list[mcp_clients.DetectedClient]) -> None:
    console = Console()
    table = Table(title="MCP clients", show_lines=False)
    table.add_column("Client")
    table.add_column("Status")
    table.add_column("Path")
    for detected in detected_list:
        glyph, label = _STATUS_GLYPH[detected.status]
        path = str(detected.path) if detected.path is not None else "-"
        table.add_row(detected.spec.label, f"{glyph} {label}", path)
    console.print(table)


def _print_install_results(results: list[mcp_clients.WriteResult]) -> None:
    console = Console()
    for r in results:
        if r.action == "added":
            console.print(f"[green]✓[/green] {r.client_id}: added at {r.path}")
        elif r.action == "updated":
            console.print(f"[green]✓[/green] {r.client_id}: updated at {r.path}")
        elif r.action == "skipped":
            note = f" ({r.error})" if r.error else ""
            console.print(f"[yellow]·[/yellow] {r.client_id}: skipped{note}")
        elif r.action == "failed":
            console.print(f"[red]✗[/red] {r.client_id}: failed — {r.error or 'unknown error'}")


def _resolve_repo_root(repo_root: Path | None) -> Path:
    return repo_root if repo_root is not None else Path.cwd()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@clients_app.command("detect", help="List MCP clients found on this machine and their config status.")
def detect_cmd(
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", "-C", help="Repo root for per-repo detection (default: cwd)."),
    ] = None,
    format: Annotated[
        str,
        typer.Option("--format", help="Output format: pretty (default), json."),
    ] = "pretty",
) -> None:
    root = _resolve_repo_root(repo_root)
    detected_list = mcp_clients.detect_all(root)
    if format == "json":
        typer.echo(json.dumps({"clients": [_detected_to_dict(d) for d in detected_list]}, indent=2))
        return
    if format != "pretty":
        raise typer.BadParameter(f"unknown format {format!r}. Choose from: pretty, json")
    _print_detect_table(detected_list)


@clients_app.command("install", help="Install Vaner into one or all MCP clients.")
def install_cmd(
    name: Annotated[
        str | None,
        typer.Argument(help="Client id (e.g. cursor, claude-desktop). Omit when using --all."),
    ] = None,
    install_all: Annotated[
        bool,
        typer.Option("--all", help="Install Vaner for every detected MCP client."),
    ] = False,
    server_key: Annotated[
        str,
        typer.Option(
            "--server-key",
            help="Override the JSON `mcpServers` key (Claude Desktop uses `vaner-<reponame>` by default).",
        ),
    ] = "vaner",
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", "-C", help="Repo root (default: cwd)."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change; do not write any files."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-write the entry even when it already matches."),
    ] = False,
    format: Annotated[
        str,
        typer.Option("--format", help="Output format: pretty (default), json."),
    ] = "pretty",
) -> None:
    if not install_all and name is None:
        raise typer.BadParameter("specify a client id or pass --all")
    if install_all and name is not None:
        raise typer.BadParameter("--all and a client name are mutually exclusive")

    root = _resolve_repo_root(repo_root)
    detected_list = mcp_clients.detect_all(root)
    launcher_cmd, launcher_args = mcp_clients.resolve_launcher(root)

    if install_all:
        targets = [d for d in detected_list if d.status != mcp_clients.ClientStatus.MISSING]
    else:
        targets = [d for d in detected_list if d.spec.id == name]
        if not targets:
            ids = ", ".join(d.spec.id for d in detected_list)
            raise typer.BadParameter(f"unknown client id {name!r}. Known: {ids}")
        if targets[0].status == mcp_clients.ClientStatus.MISSING:
            typer.secho(
                f"warning: {name} not detected on this machine — writing config anyway",
                fg=typer.colors.YELLOW,
                err=True,
            )

    results: list[mcp_clients.WriteResult] = []
    for detected in targets:
        # Per the existing wizard convention, Claude Desktop uses
        # `vaner-<reponame>` so multiple repos can register independently.
        key = server_key
        if key == "vaner" and detected.spec.id == "claude-desktop":
            key = f"vaner-{root.name}"
        result = mcp_clients.write_client(
            detected,
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
            server_key=key,
            dry_run=dry_run,
            force=force,
        )
        results.append(result)

    if format == "json":
        typer.echo(json.dumps({"results": [_result_to_dict(r) for r in results]}, indent=2))
        return
    if format != "pretty":
        raise typer.BadParameter(f"unknown format {format!r}. Choose from: pretty, json")
    _print_install_results(results)
    if any(r.action == "failed" for r in results):
        raise typer.Exit(code=1)


@clients_app.command("uninstall", help="Remove the Vaner entry from one or all MCP clients.")
def uninstall_cmd(
    name: Annotated[
        str | None,
        typer.Argument(help="Client id; omit with --all."),
    ] = None,
    uninstall_all: Annotated[
        bool,
        typer.Option("--all", help="Remove Vaner from every configured MCP client."),
    ] = False,
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", "-C", help="Repo root (default: cwd)."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change; do not delete."),
    ] = False,
    format: Annotated[
        str,
        typer.Option("--format", help="Output format: pretty (default), json."),
    ] = "pretty",
) -> None:
    if not uninstall_all and name is None:
        raise typer.BadParameter("specify a client id or pass --all")
    if uninstall_all and name is not None:
        raise typer.BadParameter("--all and a client name are mutually exclusive")

    root = _resolve_repo_root(repo_root)
    detected_list = mcp_clients.detect_all(root)
    if uninstall_all:
        targets = [d for d in detected_list if d.status == mcp_clients.ClientStatus.CONFIGURED]
    else:
        targets = [d for d in detected_list if d.spec.id == name]
        if not targets:
            ids = ", ".join(d.spec.id for d in detected_list)
            raise typer.BadParameter(f"unknown client id {name!r}. Known: {ids}")

    results = [mcp_clients.remove_client(d, dry_run=dry_run) for d in targets]
    if format == "json":
        typer.echo(json.dumps({"results": [_result_to_dict(r) for r in results]}, indent=2))
        return
    if format != "pretty":
        raise typer.BadParameter(f"unknown format {format!r}. Choose from: pretty, json")
    for r in results:
        if r.action == "updated":
            typer.echo(f"✓ {r.client_id}: removed from {r.path}")
        elif r.action == "skipped":
            typer.echo(f"· {r.client_id}: nothing to remove" + (f" ({r.error})" if r.error else ""))
        else:
            typer.secho(f"✗ {r.client_id}: {r.error or 'failed'}", fg=typer.colors.RED, err=True)


@clients_app.command("status", help="Show install/configured matrix for every supported MCP client.")
def status_cmd(
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", "-C"),
    ] = None,
    format: Annotated[
        str,
        typer.Option("--format", help="Output format: pretty (default), json."),
    ] = "pretty",
) -> None:
    detect_cmd(repo_root=repo_root, format=format)


@clients_app.command(
    "doctor",
    help="Detect launcher path drift after Vaner is reinstalled / moved.",
)
def doctor_cmd(
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", "-C"),
    ] = None,
    format: Annotated[
        str,
        typer.Option("--format", help="Output format: pretty (default), json."),
    ] = "pretty",
) -> None:
    root = _resolve_repo_root(repo_root)
    detected_list = mcp_clients.detect_all(root)
    reports = [mcp_clients.launcher_drift(d) for d in detected_list]
    drifted = [r for r in reports if r.drift]

    if format == "json":
        typer.echo(
            json.dumps(
                {
                    "drift": [_drift_to_dict(r) for r in reports],
                    "drift_count": len(drifted),
                    "fix_command": "vaner clients install --all --force",
                },
                indent=2,
            )
        )
        if drifted:
            sys.exit(1)
        return

    if format != "pretty":
        raise typer.BadParameter(f"unknown format {format!r}. Choose from: pretty, json")

    console = Console()
    if not drifted:
        console.print("[green]All configured MCP clients use the current `vaner` binary.[/green]")
        return
    table = Table(title="Launcher drift", show_lines=False)
    table.add_column("Client")
    table.add_column("Configured")
    table.add_column("Expected")
    for r in drifted:
        table.add_row(r.label, r.current_in_config or "-", r.expected)
    console.print(table)
    console.print()
    console.print("[yellow]Fix:[/yellow] run `vaner clients install --all --force`")
    raise typer.Exit(code=1)
