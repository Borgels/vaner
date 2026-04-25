# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import platform

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.models.config import VanerConfig

if platform.system().lower().startswith("win"):
    pytest.skip("daemon http TestClient is flaky on Windows runners", allow_module_level=True)


def _client(temp_repo) -> TestClient:
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    return TestClient(create_daemon_http_app(config))


def test_guidance_endpoint_returns_canonical_body(temp_repo) -> None:
    with _client(temp_repo) as client:
        resp = client.get("/integrations/guidance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["variant"] == "canonical"
        assert data["version"] == 1
        assert "Do not call Vaner mechanically" in data["body"]


def test_guidance_endpoint_supports_markdown_format(temp_repo) -> None:
    with _client(temp_repo) as client:
        resp = client.get("/integrations/guidance?format=markdown")
        assert resp.status_code == 200
        data = resp.json()
        assert data["markdown"].startswith("---")
        assert "guidance_version: 1" in data["markdown"]


def test_guidance_endpoint_supports_json_format(temp_repo) -> None:
    with _client(temp_repo) as client:
        resp = client.get("/integrations/guidance?format=json&variant=weak")
        assert resp.status_code == 200
        data = resp.json()
        assert data["variant"] == "weak"
        assert "body" in data
        assert isinstance(data["recommended_tools"], list)


def test_guidance_endpoint_rejects_unknown_variant(temp_repo) -> None:
    with _client(temp_repo) as client:
        resp = client.get("/integrations/guidance?variant=bogus")
        assert resp.status_code == 400
        assert resp.json()["code"] == "invalid_variant"


def test_guidance_endpoint_rejects_unknown_format(temp_repo) -> None:
    with _client(temp_repo) as client:
        resp = client.get("/integrations/guidance?format=bogus")
        assert resp.status_code == 400
        assert resp.json()["code"] == "invalid_format"
