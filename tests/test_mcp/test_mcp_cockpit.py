# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mcp", reason="MCP extra not installed in this environment")


def test_mcp_cockpit_bootstrap_reports_mcp_mode(temp_repo: Path) -> None:
    from vaner.mcp.server import build_cockpit

    app = build_cockpit(temp_repo)
    with TestClient(app) as client:
        payload = client.get("/cockpit/bootstrap.json").json()
    assert payload["mode"] == "mcp"
    assert "cockpit_sha" in payload


def test_mcp_cockpit_shares_routes_with_daemon(temp_repo: Path) -> None:
    from vaner.mcp.server import build_cockpit

    app = build_cockpit(temp_repo)
    with TestClient(app) as client:
        assert client.get("/status").status_code == 200
        assert client.get("/backend/presets").status_code == 200
        assert client.get("/compute/devices").status_code == 200
        assert client.get("/skills").status_code == 200
