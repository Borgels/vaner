# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.intent.bundles import bundle_public_summary, bundle_rejection_reason

bundle_app = typer.Typer(help="Install and inspect trained Vaner bundles", no_args_is_help=True)


@bundle_app.command("inspect")
def inspect_bundle(
    bundle_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True)],
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    summary = bundle_public_summary(bundle_dir)
    if as_json:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Bundle: {summary['bundle_id']}")
    typer.echo(f"Records: {summary.get('records_used')}")
    typer.echo(f"Backend: {summary.get('scorer_backend')}")
    typer.echo(f"Trained: {summary.get('trained')}")
    if summary.get("rejection_reason"):
        typer.secho(f"Rejected: {summary['rejection_reason']}", fg=typer.colors.YELLOW)
    else:
        typer.secho("Installable: yes", fg=typer.colors.GREEN)


@bundle_app.command("install")
def install_bundle(
    bundle_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True)],
    path: Annotated[Path, typer.Option("--path", "-p", help="Workspace/repository root to install into.")] = Path("."),
    allow_rejected: Annotated[
        bool,
        typer.Option("--allow-rejected", help="Install a rejected bundle for local diagnostics."),
    ] = False,
    allow_experimental_data: Annotated[
        bool,
        typer.Option(
            "--allow-experimental-data",
            help="Install a locally trained bundle whose data is not release-eligible but passed training and temporal gates.",
        ),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
) -> None:
    reason = bundle_rejection_reason(bundle_dir, allow_experimental_data=allow_experimental_data)
    if reason and not allow_rejected:
        payload = {"installed": False, "reason": reason, "bundle": bundle_public_summary(bundle_dir)}
        if as_json:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            typer.secho(f"Refusing rejected bundle: {reason}", fg=typer.colors.RED, err=True)
            typer.echo("Use --allow-rejected only for local diagnostics.")
        raise typer.Exit(code=2)

    async def _install() -> bool:
        engine = VanerEngine(adapter=CodeRepoAdapter(path.resolve()))
        return await engine.load_bundle(
            bundle_dir,
            allow_rejected=allow_rejected,
            allow_experimental_data=allow_experimental_data,
        )

    installed = asyncio.run(_install())
    payload = {
        "installed": installed,
        "experimental_data": bool(allow_experimental_data and not allow_rejected),
        "bundle": bundle_public_summary(bundle_dir),
    }
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        raise typer.Exit(code=0 if installed else 1)
    if not installed:
        typer.secho("Bundle was not installed; no compatible scorer model was loaded.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho("Bundle installed.", fg=typer.colors.GREEN)
