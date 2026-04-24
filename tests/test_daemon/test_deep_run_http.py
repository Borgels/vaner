# SPDX-License-Identifier: Apache-2.0
"""WS4 — Daemon HTTP /deep-run/* endpoint tests (0.8.3)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from vaner.cli.commands.config import load_config
from vaner.cli.commands.init import init_repo
from vaner.daemon.http import create_daemon_http_app
from vaner.intent.deep_run_gates import (
    reset_cost_gate,
    set_active_session_for_routing,
)


@pytest.fixture(autouse=True)
def _isolate_singletons():
    set_active_session_for_routing(None)
    reset_cost_gate(None)
    yield
    set_active_session_for_routing(None)
    reset_cost_gate(None)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def client(repo_root: Path) -> TestClient:
    init_repo(repo_root)
    config = load_config(repo_root)
    app = create_daemon_http_app(config)
    return TestClient(app)


def test_status_when_no_session(client: TestClient) -> None:
    resp = client.get("/deep-run/status")
    assert resp.status_code == 200
    assert resp.json() == {"session": None}


def test_lifecycle_start_status_list_show_stop(client: TestClient) -> None:
    # start
    start = client.post(
        "/deep-run/start",
        json={
            "ends_at": time.time() + 3600,
            "preset": "balanced",
            "locality": "local_only",
        },
    )
    assert start.status_code == 200, start.text
    started = start.json()
    assert started["status"] == "active"
    assert started["preset"] == "balanced"

    # status
    status_resp = client.get("/deep-run/status")
    assert status_resp.status_code == 200
    payload = status_resp.json()
    assert payload["session"]["id"] == started["id"]

    # list
    listed = client.get("/deep-run/sessions").json()
    assert any(s["id"] == started["id"] for s in listed["sessions"])

    # show
    shown = client.get(f"/deep-run/sessions/{started['id']}").json()
    assert shown["id"] == started["id"]

    # stop
    stop = client.post("/deep-run/stop", json={})
    assert stop.status_code == 200
    summary = stop.json()["summary"]
    assert summary["session_id"] == started["id"]
    assert summary["final_status"] == "ended"
    # Four-counter honesty preserved across the surface
    for k in ("matured_kept", "matured_discarded", "matured_rolled_back", "matured_failed"):
        assert k in summary


def test_start_missing_ends_at_returns_400(client: TestClient) -> None:
    resp = client.post("/deep-run/start", json={})
    assert resp.status_code == 400


def test_double_start_returns_409(client: TestClient) -> None:
    client.post("/deep-run/start", json={"ends_at": time.time() + 60})
    second = client.post("/deep-run/start", json={"ends_at": time.time() + 60})
    assert second.status_code == 409


def test_show_unknown_session_returns_404(client: TestClient) -> None:
    resp = client.get("/deep-run/sessions/ffffffffffffffff")
    assert resp.status_code == 404


def test_stop_with_kill_records_killed(client: TestClient) -> None:
    client.post("/deep-run/start", json={"ends_at": time.time() + 60})
    resp = client.post("/deep-run/stop", json={"kill": True, "reason": "user_kill"})
    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["final_status"] == "killed"
    assert summary["cancelled_reason"] == "user_kill"


def test_list_limit_bounds_clamped(client: TestClient) -> None:
    # limit=999 should be silently clamped to 200, not rejected
    resp = client.get("/deep-run/sessions?limit=999")
    assert resp.status_code == 200
