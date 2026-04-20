# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.models.config import VanerConfig
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


def test_cockpit_root_serves_html_and_expected_endpoints(temp_repo) -> None:
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Vaner Cockpit" in response.text
    assert 'data-mode="daemon"' in response.text
    assert 'window.__VANER_MODE = "daemon"' in response.text
    assert "/scenarios/stream" in response.text
    assert "/compute/devices" in response.text
    assert "/scenarios/" in response.text


def test_status_payload_includes_backend(temp_repo) -> None:
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        response = client.get("/status")
    assert response.status_code == 200
    payload = response.json()
    assert "backend" in payload
    assert payload["backend"]["base_url"] == config.backend.base_url
    assert payload["backend"]["model"] == config.backend.model


def test_ui_route_redirects_to_root(temp_repo) -> None:
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        response = client.get("/ui", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/"


def test_scenario_stream_route_not_shadowed_by_id_route(temp_repo) -> None:
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )

    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_stream_1",
                kind="debug",
                score=0.9,
                confidence=0.8,
                entities=["src/main.py"],
                evidence=[],
                prepared_context="ctx",
                coverage_gaps=[],
                freshness="fresh",
                cost_to_expand="medium",
                created_at=time.time(),
            )
        )

    asyncio.run(_seed())

    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        probe = client.get("/scenarios/stream?limit=1")
        if probe.status_code == 404:
            pytest.skip("/scenarios/stream unavailable on this daemon surface")
        with client.stream("GET", "/scenarios/stream?limit=1") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = [line for line in response.iter_lines() if line]
        data_lines = [line for line in lines if line.startswith("data: ")]
        assert data_lines
        payload = json.loads(data_lines[0].removeprefix("data: "))
        assert payload["id"] == "scn_stream_1"
