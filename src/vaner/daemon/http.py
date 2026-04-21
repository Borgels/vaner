# SPDX-License-Identifier: Apache-2.0
"""Daemon FastAPI surface.

Historically this module owned the Vaner cockpit routes directly. Those
routes now live in :mod:`vaner.ui.server` so the daemon, proxy, and MCP
surfaces share a single implementation. This module is a thin shim that
constructs the shared app with ``mode="daemon"``.
"""

from __future__ import annotations

from fastapi import FastAPI

from vaner.models.config import VanerConfig
from vaner.ui.server import build_cockpit_app


def create_daemon_http_app(config: VanerConfig) -> FastAPI:
    return build_cockpit_app(config, mode="daemon")
