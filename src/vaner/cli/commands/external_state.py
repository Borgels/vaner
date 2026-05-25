# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from vaner.cli.commands.config import load_config
from vaner.external_state.configurator import (
    ProviderConfigInput,
    discover_provider_sync,
    external_state_payload,
    save_external_state_enabled,
    save_finance_settings,
    save_provider_config,
)

external_state_app = typer.Typer(help="External data provider controls")
providers_app = typer.Typer(help="Manage MCP external-state providers")
finance_app = typer.Typer(help="Finance external-state permissions")
external_state_app.add_typer(providers_app, name="providers")
external_state_app.add_typer(finance_app, name="finance")
console = Console()


def _repo_root(path: str | None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    env_path = os.environ.get("VANER_PATH", "").strip()
    return Path(env_path).expanduser().resolve() if env_path else Path.cwd()


def _parse_args(raw: list[str]) -> list[str]:
    if len(raw) == 1:
        return shlex.split(raw[0])
    return list(raw)


def _parse_env(values: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise typer.BadParameter("--env values must use KEY=VALUE")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter("--env key must not be empty")
        env[key] = value
    return env


@external_state_app.command("show")
def show_external_state(
    repo: Annotated[str | None, typer.Option("--repo", help="Repository root")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """Show configured external-state providers without exposing env values."""

    config = load_config(_repo_root(repo))
    payload = external_state_payload(config)
    if as_json:
        console.print_json(json.dumps(payload.model_dump(mode="json")))
        return
    table = Table("Provider", "Transport", "Command/URL", "Tools", "Capabilities")
    for provider in payload.providers:
        endpoint = provider.url if provider.transport == "streamable_http" else " ".join([provider.command, *provider.args]).strip()
        table.add_row(
            provider.id,
            provider.transport,
            endpoint or "-",
            str(len(provider.allowed_tools)),
            str(len(provider.capability_tools)),
        )
    console.print(f"external_state: {'enabled' if payload.enabled else 'disabled'}")
    finance = payload.finance
    console.print(
        "finance: "
        f"{'enabled' if finance.get('enabled') else 'disabled'}, "
        f"market_data={'on' if finance.get('market_data_enabled') else 'off'}, "
        f"account_state={'on' if finance.get('account_state_enabled') else 'off'}, "
        f"provider={finance.get('provider') or '-'}"
    )
    console.print(table)


@external_state_app.command("enable")
def enable_external_state(
    repo: Annotated[str | None, typer.Option("--repo", help="Repository root")] = None,
) -> None:
    save_external_state_enabled(_repo_root(repo), enabled=True)
    console.print("External-state access enabled.")


@external_state_app.command("disable")
def disable_external_state(
    repo: Annotated[str | None, typer.Option("--repo", help="Repository root")] = None,
) -> None:
    save_external_state_enabled(_repo_root(repo), enabled=False)
    console.print("External-state access disabled.")


@providers_app.command("add")
def add_provider(
    provider_id: Annotated[str, typer.Argument(help="Provider id, e.g. local_broker")],
    command: Annotated[str, typer.Option("--command", "-c", help="MCP server command")] = "",
    arg: Annotated[list[str] | None, typer.Option("--arg", help="MCP server argument. Repeat or pass a quoted arg string.")] = None,
    transport: Annotated[str, typer.Option("--transport", help="stdio or streamable_http")] = "stdio",
    url: Annotated[str, typer.Option("--url", help="streamable_http URL")] = "",
    env: Annotated[list[str] | None, typer.Option("--env", help="Environment override KEY=VALUE. Repeatable.")] = None,
    timeout_ms: Annotated[int, typer.Option("--timeout-ms", min=100)] = 10000,
    repo: Annotated[str | None, typer.Option("--repo", help="Repository root")] = None,
) -> None:
    """Register a provider connection. Discovery happens separately."""

    save_provider_config(
        _repo_root(repo),
        ProviderConfigInput(
            provider_id=provider_id,
            transport=transport,
            command=command,
            args=_parse_args(arg or []),
            url=url,
            env=_parse_env(env or []),
            timeout_ms=timeout_ms,
        ),
    )
    console.print(f"Provider `{provider_id}` saved. Run `vaner external-state providers discover {provider_id}`.")


@providers_app.command("discover")
def discover_provider_command(
    provider_id: Annotated[str, typer.Argument(help="Configured provider id")],
    apply: Annotated[bool, typer.Option("--apply", help="Persist safe discovered read tools and capability mappings")] = False,
    trust_unknown_read: Annotated[
        bool,
        typer.Option(
            "--trust-unknown-read",
            help="Treat finance-looking tools with missing annotations as read-only provider metadata.",
        ),
    ] = False,
    repo: Annotated[str | None, typer.Option("--repo", help="Repository root")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
) -> None:
    """List provider tools, classify safety, and propose finance capabilities."""

    config = load_config(_repo_root(repo))
    payload = discover_provider_sync(config, provider_id, apply=apply, trust_unknown_read=trust_unknown_read)
    if as_json:
        console.print_json(json.dumps(payload.model_dump(mode="json")))
        return
    table = Table("Tool", "Safety", "Capability", "Scope", "Selected")
    for tool in payload.tools:
        table.add_row(
            tool.name,
            tool.safety_reason,
            tool.proposed_capability or "-",
            tool.access_scope,
            "yes" if tool.auto_selected else "",
        )
    console.print(table)
    if apply:
        console.print(
            f"Applied {len(payload.applied_allowed_tools)} tools and "
            f"{len(payload.applied_capability_tools)} finance capability mappings."
        )


@finance_app.command("enable")
def enable_finance(
    provider: Annotated[str, typer.Option("--provider", "-p", help="Provider id to use for finance")] = "",
    market_data: Annotated[bool, typer.Option("--market-data/--no-market-data")] = True,
    account_state: Annotated[bool, typer.Option("--account-state/--no-account-state")] = False,
    repo: Annotated[str | None, typer.Option("--repo", help="Repository root")] = None,
) -> None:
    """Enable finance recognition plus opt-in external finance data access."""

    repo_root = _repo_root(repo)
    save_external_state_enabled(repo_root, enabled=True)
    save_finance_settings(
        repo_root,
        enabled=True,
        provider=provider or None,
        market_data_enabled=market_data,
        account_state_enabled=account_state,
    )
    console.print(
        "Finance external data enabled "
        f"(market_data={'on' if market_data else 'off'}, account_state={'on' if account_state else 'off'})."
    )


@finance_app.command("disable")
def disable_finance(
    repo: Annotated[str | None, typer.Option("--repo", help="Repository root")] = None,
) -> None:
    save_finance_settings(_repo_root(repo), enabled=False, market_data_enabled=False, account_state_enabled=False)
    console.print("Finance external data disabled.")
