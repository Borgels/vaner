# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from vaner.cli.commands.config import load_config
from vaner.clients.daemon import VanerDaemonClient, VanerDaemonUnavailable, daemon_base_url_from_env
from vaner.sources_permissions import apply_sources_permissions, artefact_counts_by_connector, build_sources_permissions, dump_json

sources_app = typer.Typer(help="Source permissions and ingestion controls", no_args_is_help=True)
_console = Console()


def _repo_root(path: Path | None) -> Path:
    return path.resolve() if path is not None else Path.cwd().resolve()


async def _daemon_client_for_repo(repo_root: Path) -> VanerDaemonClient:
    client = VanerDaemonClient(base_url=daemon_base_url_from_env())
    status = await client.get_status()
    daemon_repo = status.get("repo_root") if isinstance(status, dict) else None
    if not daemon_repo or Path(str(daemon_repo)).expanduser().resolve() != repo_root.resolve():
        raise VanerDaemonUnavailable("daemon is serving a different repository")
    return client


async def _local_permissions(repo_root: Path) -> dict[str, Any]:
    config = load_config(repo_root)
    counts = await artefact_counts_by_connector(repo_root)
    payload = build_sources_permissions(config, artefact_counts=counts)
    payload["source"] = "local_fallback"
    return payload


def _print_permissions(payload: dict[str, Any]) -> None:
    _console.print("[bold]Source permissions[/bold]")
    _console.print(str(payload.get("workspace") or ""))
    table = Table()
    table.add_column("Source")
    table.add_column("Enabled")
    table.add_column("Accepted")
    table.add_column("Skipped")
    table.add_column("Boundary")
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    for source in sources.values():
        if not isinstance(source, dict):
            continue
        table.add_row(
            str(source.get("id") or ""),
            "yes" if source.get("enabled") else "no",
            str(source.get("accepted_count") or source.get("ingested_count") or 0),
            str(source.get("skipped_count") or 0),
            str(source.get("privacy_zone") or ""),
        )
    _console.print(table)


@sources_app.command("list")
def list_sources(
    repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = _repo_root(repo_root)
    try:
        client = asyncio.run(_daemon_client_for_repo(root))
        payload = asyncio.run(client.get_sources_permissions())
    except VanerDaemonUnavailable:
        payload = asyncio.run(_local_permissions(root))
    typer.echo(dump_json(payload)) if json_output else _print_permissions(payload)


@sources_app.command("enable")
def enable_source(
    source_id: Annotated[str, typer.Argument(help="Source id, e.g. global_client_plans.")],
    repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = _repo_root(repo_root)
    payload = _source_patch(source_id, True)
    try:
        client = asyncio.run(_daemon_client_for_repo(root))
        result = asyncio.run(client.update_sources_permissions(payload))
    except VanerDaemonUnavailable:
        apply_sources_permissions(root, payload)
        result = asyncio.run(_local_permissions(root))
    typer.echo(dump_json(result)) if json_output else _print_permissions(result)


@sources_app.command("disable")
def disable_source(
    source_id: Annotated[str, typer.Argument(help="Source id, e.g. global_client_plans.")],
    repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = _repo_root(repo_root)
    payload = _source_patch(source_id, False)
    try:
        client = asyncio.run(_daemon_client_for_repo(root))
        result = asyncio.run(client.update_sources_permissions(payload))
    except VanerDaemonUnavailable:
        apply_sources_permissions(root, payload)
        result = asyncio.run(_local_permissions(root))
    typer.echo(dump_json(result)) if json_output else _print_permissions(result)


@sources_app.command("refresh")
def refresh_sources(
    repo_root: Annotated[Path | None, typer.Option("--repo-root", "-C")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    root = _repo_root(repo_root)
    try:
        client = asyncio.run(_daemon_client_for_repo(root))
        result = asyncio.run(client.refresh_sources())
    except VanerDaemonUnavailable:
        from vaner.intent.source_refresh import refresh_intent_artefacts_from_sources
        from vaner.store.artefacts import ArtefactStore

        async def _refresh() -> dict[str, Any]:
            config = load_config(root)
            store = ArtefactStore(root / ".vaner" / "artefacts.db")
            await store.initialize()
            return {"ok": True, "accepted": await refresh_intent_artefacts_from_sources(config, store), "source": "local_fallback"}

        result = asyncio.run(_refresh())
    typer.echo(dump_json(result)) if json_output else _console.print(result)


def _source_patch(source_id: str, enabled: bool) -> dict[str, Any]:
    if source_id != "global_client_plans":
        raise typer.BadParameter("Only global_client_plans is currently user-toggleable.")
    return {"global_client_plans": {"enabled": enabled}}

