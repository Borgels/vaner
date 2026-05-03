from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.daemon.precompute_worker import read_worker_control
from vaner.models.config import VanerConfig
from vaner.models.signal import SignalEvent
from vaner.store.artefacts import ArtefactStore


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
    assert paused["worker_control"]["action"] == "pause"
    stored_control = read_worker_control(config.repo_root)
    assert stored_control is not None
    assert stored_control["action"] == "pause"
    assert stored_control["drain_queue"] is True
    assert jobs["jobs"][0]["status"] == "deferred"


def test_focus_pause_all_and_paused_mode_request_worker_cancellation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_daemon_http_app(config)

    with TestClient(app) as client:
        pause_all = client.post("/focus/pause-all")
        paused_mode = client.post("/focus/mode", json={"mode": "paused"})

    assert pause_all.status_code == 200
    assert pause_all.json()["worker_control"]["action"] == "pause_all"
    assert pause_all.json()["worker_control"]["drain_queue"] is True
    assert paused_mode.status_code == 200
    assert paused_mode.json()["worker_control"]["reason"] == "focus mode set to paused"
    stored_control = read_worker_control(config.repo_root)
    assert stored_control is not None
    assert stored_control["action"] == "pause_all"


def test_jobs_cancel_requests_active_worker_interrupt(tmp_path: Path) -> None:
    config = _config(tmp_path)
    app = create_daemon_http_app(config)

    with TestClient(app) as client:
        response = client.post("/jobs/precompute-one/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["worker_control"]["action"] == "cancel_active"
    assert body["worker_control"]["job_id"] == "precompute-one"
    stored_control = read_worker_control(config.repo_root)
    assert stored_control is not None
    assert stored_control["job_id"] == "precompute-one"


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


def test_recent_activity_endpoint_returns_codex_query_history(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async def _seed() -> None:
        store = ArtefactStore(config.repo_root / ".vaner" / "artefacts.db")
        await store.initialize()
        await store.insert_query_history(
            session_id="codex-session",
            query_text="show focus routing in the web ui",
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
            source="codex_prompt",
            host_app="codex-cli",
            prompt_hash="a" * 64,
            turn_id="turn-1",
        )

    asyncio.run(_seed())
    app = create_daemon_http_app(config)

    with TestClient(app) as client:
        response = client.get("/activity/recent", params={"host_app": "codex-cli", "limit": 5})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["host_app"] == "codex-cli"
    assert body["items"][0]["turn_id"] == "turn-1"
    assert body["items"][0]["source"] == "codex_prompt"


def test_recent_events_endpoint_replays_persisted_activity(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async def _seed() -> None:
        store = ArtefactStore(config.repo_root / ".vaner" / "artefacts.db")
        await store.initialize()
        now = time.time()
        await store.insert_signal_event(
            SignalEvent(
                id="sig-1",
                source="git",
                kind="changed",
                timestamp=now,
                payload={"corpus_id": "repo", "path": "src/app.py"},
            )
        )
        await store.insert_query_history(
            session_id="codex-session",
            query_text="wire recent events into the cockpit",
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
            corpus_id="repo",
            source="codex_prompt",
            host_app="codex-cli",
            prompt_hash="b" * 64,
            turn_id="turn-2",
            timestamp=now + 1,
        )

    asyncio.run(_seed())
    app = create_daemon_http_app(config)

    with TestClient(app) as client:
        response = client.get("/events/recent", params={"limit": 5, "corpus_id": "repo"})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    kinds = {item["kind"] for item in body["events"]}
    assert kinds == {"signal.ingest"}
    assert body["events"][0]["payload"]["host_app"] == "codex-cli"
    assert body["events"][1]["path"] == "src/app.py"


def test_artefacts_endpoint_refreshes_repo_local_codex_plans(tmp_path: Path) -> None:
    config = _config(tmp_path)
    plan_dir = config.repo_root / ".codex" / "plans"
    plan_dir.mkdir(parents=True)
    (plan_dir / "focus.md").write_text(
        "# Focus UI Plan\n\n"
        "## Tasks\n"
        "- [ ] replay recent signal events in the cockpit activity lane\n"
        "- [ ] connect Codex prompt activity to the current workspace focus route\n"
        "- [ ] show which prediction inputs came from local plan artefacts\n"
        "- [ ] verify that stale scenarios do not hide historical context\n",
        encoding="utf-8",
    )
    app = create_daemon_http_app(config)

    with TestClient(app) as client:
        response = client.get("/artefacts", params={"connector": "local_plan", "limit": 5})

    assert response.status_code == 200
    body = response.json()
    assert body["artefacts"]
    assert body["artefacts"][0]["connector"] == "local_plan"
    assert body["artefacts"][0]["title"] == "Focus UI Plan"
