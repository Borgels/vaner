# SPDX-License-Identifier: Apache-2.0
"""`vaner guidance` CLI — prints the canonical Vaner guidance asset."""

from __future__ import annotations

import json

import typer

from vaner.integrations.guidance import (
    available_variants,
    load_guidance,
)

guidance_app = typer.Typer(
    help="Print the Vaner guidance asset for embedding in agent prompts.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@guidance_app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    variant: str = typer.Option(
        "canonical",
        "--variant",
        "-v",
        help=f"Guidance variant to print. One of: {', '.join(available_variants())}.",
    ),
    fmt: str = typer.Option(
        "body",
        "--format",
        "-f",
        help="Output format: body (default), markdown (frontmatter+body), json.",
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _print(variant=variant, fmt=fmt)


@guidance_app.command("print", help="Print the guidance asset.")
def print_cmd(
    variant: str = typer.Option("canonical", "--variant", "-v"),
    fmt: str = typer.Option("body", "--format", "-f"),
) -> None:
    _print(variant=variant, fmt=fmt)


@guidance_app.command("versions", help="List available guidance variants and versions.")
def versions_cmd() -> None:
    for name in available_variants():
        doc = load_guidance(name)
        typer.echo(f"{name}\tv{doc.version}\t{doc.minimum_vaner_version}\tupdated {doc.updated_at}")


def _print(*, variant: str, fmt: str) -> None:
    if variant not in available_variants():
        raise typer.BadParameter(f"unknown variant {variant!r}. Choose from: {', '.join(available_variants())}")
    doc = load_guidance(variant)  # type: ignore[arg-type]
    if fmt == "body":
        typer.echo(doc.as_text())
    elif fmt == "markdown":
        typer.echo(doc.as_markdown())
    elif fmt == "json":
        typer.echo(json.dumps(doc.as_dict(), indent=2, ensure_ascii=False))
    else:
        raise typer.BadParameter(f"unknown format {fmt!r}. Choose from: body, markdown, json")
