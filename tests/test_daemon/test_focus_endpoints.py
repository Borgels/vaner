from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.models.config import VanerConfig


def _config(tmp_path: Path) -> VanerConfig:
    repo = tmp_path / "repo"
    (repo / ".vaner").mkdir(parents=True)
    (repo / ".vaner" / "config.toml").write_text("", encoding="utf-8")
    return VanerConfig(repo_root=repo, store_path=repo / ".vaner" / "store.db", telemetry_path=repo / ".vaner" / "telemetry.db")


def test_focus_endpoint_reports_explainable_state(tmp_path: Path) -> None:
    app = create_daemon_http_app(_config(tmp_path))

    with TestClient(app) as client:
        response = client.get("/focus")

    assert response.status_code == 200
    body = response.json()
    assert "focus_epoch" in body
    assert body["explanation"]
    assert isinstance(body["why_not"], list)


def test_focus_work_here_and_pause_round_trip(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_daemon_http_app(config)

    with TestClient(app) as client:
        active = client.post("/focus/workspaces/current/work-here", json={"path": str(config.repo_root)}).json()
        paused = client.post("/focus/workspaces/current/pause", json={"path": str(config.repo_root)}).json()
        jobs = client.get("/jobs").json()

    assert active["status"] == "active"
    assert paused["focus_epoch"] > active["focus_epoch"]
    assert jobs["jobs"][0]["status"] == "deferred"


def test_focus_route_reports_and_updates_route(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_daemon_http_app(config)

    with TestClient(app) as client:
        initial = client.get("/focus/route")
        updated = client.post(
            "/focus/route",
            json={
                "workspace_policy": "pinned",
                "workspace_path": str(config.repo_root),
                "client_id": "cursor",
                "resource_mode": "performance",
                "compute_device": "cpu",
                "backend": {"name": "ollama", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen3.5:8b"},
            },
        )

    assert initial.status_code == 200
    assert "effective_route" in initial.json()
    assert updated.status_code == 200
    route = updated.json()["effective_route"]
    assert route["workspace_policy"] == "pinned"
    assert route["client"]["id"] == "cursor"
    assert route["resource_mode"] == "performance"
    assert route["device"] == "cpu"
    assert route["backend"]["name"] == "ollama"
    assert route["backend"]["model"] == "qwen3.5:8b"


def test_focus_route_rejects_invalid_workspace_and_client(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_daemon_http_app(config)

    with TestClient(app) as client:
        bad_workspace = client.post("/focus/route", json={"workspace_policy": "pinned", "workspace_path": "relative"})
        bad_client = client.post("/focus/route", json={"client_id": "unknown-client"})

    assert bad_workspace.status_code == 400
    assert bad_workspace.json()["code"] == "invalid_workspace"
    assert bad_client.status_code == 400
    assert bad_client.json()["code"] == "invalid_route"


def test_focus_action_rejects_workspace_id_mismatch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_daemon_http_app(config)

    with TestClient(app) as client:
        response = client.post("/focus/workspaces/not-current/pin", json={"path": str(config.repo_root)})

    assert response.status_code == 404
    assert response.json()["code"] == "workspace_id_mismatch"


def test_focus_mode_rejects_invalid_json_and_modes(tmp_path: Path) -> None:
    app = create_daemon_http_app(_config(tmp_path))

    with TestClient(app) as client:
        invalid_json = client.post("/focus/mode", content="{", headers={"content-type": "application/json"})
        invalid_mode = client.post("/focus/mode", json={"mode": "manual"})

    assert invalid_json.status_code == 400
    assert invalid_json.json()["code"] == "invalid_input"
    assert invalid_mode.status_code == 400
    assert invalid_mode.json()["code"] == "invalid_mode"


def test_resources_endpoint_is_read_only_shape(tmp_path: Path) -> None:
    app = create_daemon_http_app(_config(tmp_path))

    with TestClient(app) as client:
        response = client.get("/resources")

    assert response.status_code == 200
    body = response.json()
    assert body["cloud_execution_policy"] == "disabled_v1"
    assert "runtimes" in body
    assert "devices" in body
