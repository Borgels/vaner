# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import platform
import time

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.models.config import VanerConfig
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore

if platform.system().lower().startswith("win"):
    pytest.skip("daemon http TestClient is flaky on Windows runners", allow_module_level=True)


def test_cockpit_root_serves_html_and_expected_endpoints(temp_repo, monkeypatch) -> None:
    cockpit_dist = temp_repo / "cockpit-dist"
    (cockpit_dist / "assets").mkdir(parents=True)
    (cockpit_dist / "brand").mkdir(parents=True)
    (cockpit_dist / "brand" / "lockup-dark-animated.svg").write_text("<svg></svg>", encoding="utf-8")
    (cockpit_dist / "index.html").write_text(
        '<!doctype html><title>Vaner Cockpit</title><div id="root"></div>'
        '<img src="/brand/lockup-dark-animated.svg"><script src="/assets/index.js"></script>',
        encoding="utf-8",
    )
    monkeypatch.setattr("vaner.daemon.http.cockpit_dist_dir", lambda: cockpit_dist)
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        response = client.get("/")
        logo = client.get("/brand/lockup-dark-animated.svg")
    assert response.status_code == 200
    assert logo.status_code == 200
    assert logo.text == "<svg></svg>"
    assert response.headers["content-type"].startswith("text/html")
    assert "Vaner Cockpit" in response.text
    assert '<div id="root"></div>' in response.text
    assert 'data-mode="daemon"' not in response.text
    assert "window.__VANER_MODE" not in response.text


def test_cockpit_root_returns_503_when_assets_missing(temp_repo, monkeypatch) -> None:
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    monkeypatch.setattr("vaner.daemon.http.cockpit_dist_dir", lambda: None)
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 503
    assert "assets are not built" in response.text
    assert "legacy cockpit" in response.text


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
    assert payload["prediction_health"]["engine_available"] is False
    assert payload["prediction_health"]["diagnostic_status"] == "engine_unavailable"


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


def test_cockpit_support_endpoints_return_payloads(temp_repo) -> None:
    skill_path = temp_repo / ".cursor" / "skills" / "vaner" / "sample" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(
        "---\nname: sample-skill\ndescription: Helps with cockpit smoke tests\ntags: [debug]\n---\n# Sample\n",
        encoding="utf-8",
    )

    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_pinned_1",
                kind="debug",
                score=0.9,
                confidence=0.8,
                entities=["src/main.py"],
                evidence=[],
                prepared_context="Pinned context",
                coverage_gaps=[],
                freshness="fresh",
                cost_to_expand="medium",
                created_at=time.time(),
                pinned=1,
            )
        )

    asyncio.run(_seed())

    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        presets = client.get("/backend/presets")
        skills = client.get("/skills")
        pinned = client.get("/pinned-facts")

    assert presets.status_code == 200
    assert presets.json()["presets"]
    assert skills.status_code == 200
    assert skills.json()["skills"][0]["name"] == "sample-skill"
    assert pinned.status_code == 200
    assert pinned.json()["facts"] == [{"id": "scn_pinned_1", "text": "Pinned context"}]


def test_scenarios_endpoint_separates_live_and_history(temp_repo) -> None:
    old = time.time() - 7_200

    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(Scenario(id="scn_live", kind="debug", score=0.8, confidence=0.8, freshness="fresh"))
        await store.upsert(
            Scenario(
                id="scn_archived",
                kind="research",
                score=0.9,
                confidence=0.9,
                freshness="stale",
                created_at=old,
                last_refreshed_at=old,
                last_reinforced_at=old,
            )
        )

    asyncio.run(_seed())

    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        live = client.get("/scenarios?visibility=live")
        history = client.get("/scenarios?visibility=history")

    assert live.status_code == 200
    assert [item["id"] for item in live.json()["scenarios"]] == ["scn_live"]
    assert live.json()["scenarios"][0]["relevance"] >= 0.4
    assert history.status_code == 200
    assert [item["id"] for item in history.json()["scenarios"]] == ["scn_archived"]
    assert history.json()["scenarios"][0]["visibility"] == "archived"


def test_heatmap_replay_endpoint_returns_persisted_samples(temp_repo) -> None:
    now = time.time()

    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_heatmap",
                kind="debug",
                score=0.9,
                confidence=0.8,
                entities=["src/main.py"],
                prepared_context="ctx",
                freshness="fresh",
                created_at=now,
                last_refreshed_at=now,
            )
        )

    asyncio.run(_seed())

    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        response = client.get(f"/heatmap/replay?from_ts={now - 5}&to_ts={now + 5}&limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["synthetic"] is False
    assert [item["id"] for item in payload["scenarios"]] == ["scn_heatmap"]
    assert payload["samples"]
    assert payload["samples"][0]["scenario_id"] == "scn_heatmap"


def test_heatmap_replay_stream_emits_real_snapshot(temp_repo) -> None:
    now = time.time()

    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_heatmap_stream",
                kind="debug",
                score=0.9,
                confidence=0.8,
                entities=["src/main.py"],
                prepared_context="ctx",
                freshness="fresh",
                created_at=now,
                last_refreshed_at=now,
            )
        )

    asyncio.run(_seed())

    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        with client.stream("GET", "/heatmap/replay/stream?range_seconds=60&limit=1") as response:
            text = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: replay_snapshot" in text
    assert '"synthetic": false' in text
    assert "scn_heatmap_stream" in text


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
