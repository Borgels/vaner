# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import platform
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.models.config import VanerConfig
from vaner.models.scenario import Scenario

if platform.system().lower().startswith("win"):
    pytest.skip("daemon http TestClient is flaky on Windows runners", allow_module_level=True)


def _config(temp_repo: Path) -> VanerConfig:
    return VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )


def _write_config_toml(temp_repo: Path) -> None:
    """Create a minimal ``.vaner/config.toml`` so ``vaner config set`` helpers work."""

    vaner_dir = temp_repo / ".vaner"
    vaner_dir.mkdir(parents=True, exist_ok=True)
    (vaner_dir / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen2.5-coder:7b"

[mcp]
transport = "stdio"
http_host = "127.0.0.1"
http_port = 8472

[limits]
max_context_tokens = 4096
""".strip(),
        encoding="utf-8",
    )


def _seed_scenario(app, scenario_id: str = "scn_http_1") -> None:
    asyncio.run(app.state.scenario_store.initialize())
    asyncio.run(
        app.state.scenario_store.upsert(
            Scenario(
                id=scenario_id,
                kind="debug",
                score=0.91,
                confidence=0.72,
                entities=["src/vaner/router/proxy.py"],
                evidence=[],
                prepared_context='{"file_summary":"proxy route"}',
                coverage_gaps=["recent diff overlap"],
                freshness="fresh",
                cost_to_expand="medium",
                created_at=time.time(),
            )
        )
    )


def test_cockpit_root_serves_built_spa(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))
    dist_dir = Path("src/vaner/daemon/cockpit_assets/dist")
    if not (dist_dir / "index.html").exists():
        pytest.skip("cockpit bundle not built")

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Vaner Cockpit" in response.text
        assert "window.__VANER_MODE__ = 'daemon'" in response.text or 'window.__VANER_MODE__ = "daemon"' in response.text

        asset_files = list((dist_dir / "assets").glob("*"))
        assert asset_files, "expected built assets in cockpit dist"
        asset_response = client.get(f"/assets/{asset_files[0].name}")
        assert asset_response.status_code == 200


def test_scenarios_payload_has_enriched_fields(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))
    _seed_scenario(app)

    with TestClient(app) as client:
        response = client.get("/scenarios?limit=1")

    assert response.status_code == 200
    payload = response.json()
    scenario = payload["scenarios"][0]
    assert scenario["title"] == "proxy.py"
    assert scenario["path"] == "src/vaner/router/proxy.py"
    assert scenario["decision_state"] == "pending"
    assert scenario["reason"] == "recent diff overlap"
    assert scenario["pinned"] is False


def test_scenario_pin_toggle(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))
    _seed_scenario(app)

    with TestClient(app) as client:
        response = client.post("/scenarios/scn_http_1/pin", json={"pinned": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario"]["pinned"] is True
    assert payload["scenario"]["decision_state"] == "chosen"


def test_pinned_facts_crud(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))

    with TestClient(app) as client:
        created = client.post("/pinned-facts", json={"text": "prefer python 3.11 typing"})
        assert created.status_code == 201
        fact_id = created.json()["fact"]["id"]

        listed = client.get("/pinned-facts")
        assert listed.status_code == 200
        assert listed.json()["facts"][0]["text"] == "prefer python 3.11 typing"

        deleted = client.delete(f"/pinned-facts/{fact_id}")
        assert deleted.status_code == 204

        relisted = client.get("/pinned-facts")
        assert relisted.status_code == 200
        assert relisted.json()["facts"] == []


def test_bootstrap_includes_cockpit_sha(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.get("/cockpit/bootstrap.json")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "daemon"
    assert "cockpit_sha" in payload
    assert "version" in payload


def test_backend_presets_endpoint(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.get("/backend/presets")
    assert response.status_code == 200
    presets = response.json()["presets"]
    assert isinstance(presets, list)
    assert presets, "expected at least one backend preset"
    assert {"name", "base_url", "default_model", "api_key_env"} <= set(presets[0])


def test_update_backend_rejects_unknown_keys(temp_repo: Path) -> None:
    _write_config_toml(temp_repo)
    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        ok = client.post("/backend", json={"model": "qwen2.5-coder:7b"})
        bad = client.post("/backend", json={"nonsense": "nope"})
    assert ok.status_code == 200
    assert ok.json()["backend"]["model"] == "qwen2.5-coder:7b"
    assert bad.status_code == 400


def test_update_context_persists_limits(temp_repo: Path) -> None:
    _write_config_toml(temp_repo)
    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.post("/context", json={"max_context_tokens": 32000})
        status = client.get("/status").json()
    assert response.status_code == 200
    assert response.json()["limits"]["max_context_tokens"] == 32000
    assert status["limits"]["max_context_tokens"] == 32000


def test_update_mcp_validates_transport(temp_repo: Path) -> None:
    _write_config_toml(temp_repo)
    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        ok = client.post("/mcp", json={"transport": "sse", "http_host": "127.0.0.1", "http_port": 8472})
        bad = client.post("/mcp", json={"transport": "raw"})
    assert ok.status_code == 200
    assert ok.json()["mcp"]["transport"] == "sse"
    assert bad.status_code == 400


def test_skill_nudge_persists(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        first = client.post("/skills/vaner-research/nudge", json={"delta": 0.1})
        assert first.status_code == 200
        first_weight = first.json()["weight"]
        second = client.get("/skills").json()
    assert any(skill["name"] == "vaner-research" and skill["weight"] == first_weight for skill in second["skills"])
    app2 = create_daemon_http_app(_config(temp_repo))
    with TestClient(app2) as client:
        listing = client.get("/skills").json()
    assert any(skill["name"] == "vaner-research" and skill["weight"] == first_weight for skill in listing["skills"])


def test_compute_accepts_extended_fields(temp_repo: Path) -> None:
    _write_config_toml(temp_repo)
    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.post("/compute", json={"max_cycle_seconds": 42, "max_session_minutes": 15})
    assert response.status_code == 200
    compute = response.json()["compute"]
    assert compute["max_cycle_seconds"] == 42
    assert compute["max_session_minutes"] == 15


def test_events_stream_emits_on_upsert(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))

    def publish() -> None:
        time.sleep(0.1)
        _seed_scenario(app, "scn_stream_1")

    with TestClient(app) as client:
        thread = threading.Thread(target=publish, daemon=True)
        thread.start()
        with client.stream("GET", "/events/stream?limit=1") as response:
            assert response.status_code == 200
            lines = [line for line in response.iter_lines() if line]
        thread.join(timeout=1)

    data_lines = [line for line in lines if line.startswith("data: ")]
    assert data_lines
    payload = json.loads(data_lines[0].removeprefix("data: "))
    assert payload["scn"] == "scn_stream_1"
    assert payload["tag"] in {"expand", "score"}
    # New structured fields must travel alongside the legacy envelope.
    assert payload["stage"] == "scenarios"
    assert payload["kind"] in {"expand", "score"}


def test_events_stream_filters_by_stage(temp_repo: Path) -> None:
    from vaner.events import publish as publish_event
    from vaner.events import reset_bus

    reset_bus()
    app = create_daemon_http_app(_config(temp_repo))

    def publish_non_matching() -> None:
        time.sleep(0.05)
        # Wrong stage - should be filtered out.
        publish_event("scenarios", "expand", {"msg": "ignored"}, scn="scn_ignored")

    def publish_matching() -> None:
        time.sleep(0.15)
        publish_event(
            "model",
            "llm.request",
            {"msg": "hi", "model": "qwen"},
            path="src/app.py",
        )

    with TestClient(app) as client:
        ignored_thread = threading.Thread(target=publish_non_matching, daemon=True)
        matching_thread = threading.Thread(target=publish_matching, daemon=True)
        ignored_thread.start()
        matching_thread.start()
        with client.stream("GET", "/events/stream?limit=1&stages=model") as response:
            assert response.status_code == 200
            lines = [line for line in response.iter_lines() if line]
        ignored_thread.join(timeout=1)
        matching_thread.join(timeout=1)

    data_lines = [line for line in lines if line.startswith("data: ")]
    assert data_lines
    payload = json.loads(data_lines[0].removeprefix("data: "))
    assert payload["stage"] == "model"
    assert payload["kind"] == "llm.request"
    assert payload["path"] == "src/app.py"
