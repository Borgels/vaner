# SPDX-License-Identifier: Apache-2.0

"""Thin CLI dispatcher.

Command implementations live in ``vaner.cli.commands`` modules; this entrypoint
preserves the historical ``vaner.cli.main:run`` target.
"""

from __future__ import annotations

import ipaddress

import typer

from vaner.cli.commands.app import app, run

__all__ = ["app", "run"]


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost"}


def _require_safe_proxy_exposure(host: str, token: str) -> None:
    if _is_loopback_host(host):
        return
    if not token.strip():
        raise typer.BadParameter("Proxy on non-loopback host requires an auth token.")


def _require_safe_mcp_sse_exposure(host: str) -> None:
    if not _is_loopback_host(host):
        raise typer.BadParameter("MCP SSE transport only supports loopback hosts by default.")


if __name__ == "__main__":
    run()
