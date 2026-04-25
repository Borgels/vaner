# SPDX-License-Identifier: Apache-2.0

"""POST /integrations/handoff/check (0.8.5 WS13)."""

from __future__ import annotations

import json
import platform
import time
from pathlib import Path

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


@pytest.fixture
def stub_handoff(tmp_path, monkeypatch) -> Path:
    """Redirect `handoff_path()` to a tmp file the test owns."""
    target = tmp_path / "pending-adopt.json"
    monkeypatch.setattr("vaner.integrations.injection.handoff.handoff_path", lambda: target)
    return target


def _payload(stashed_at: float) -> dict:
    return {
        "intent": "Draft the project update",
        "resolution_id": "adopt-pred-xyz",
        "adopted_from_prediction_id": "pred-xyz",
        "prepared_briefing": "...",
        "predicted_response": "...",
        "stashed_at": stashed_at,
    }


def test_returns_null_when_no_handoff_present(temp_repo, stub_handoff: Path) -> None:
    with _client(temp_repo) as client:
        resp = client.post("/integrations/handoff/check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["adopted_package"] is None
    assert body["fresh"] is False
    assert body["age_seconds"] is None
    assert body["path"] == str(stub_handoff)


def test_returns_payload_when_fresh_handoff_present(temp_repo, stub_handoff: Path) -> None:
    stub_handoff.parent.mkdir(parents=True, exist_ok=True)
    stub_handoff.write_text(json.dumps(_payload(time.time())), encoding="utf-8")
    with _client(temp_repo) as client:
        resp = client.post("/integrations/handoff/check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fresh"] is True
    assert body["adopted_package"]["intent"] == "Draft the project update"
    assert body["adopted_package"]["adopted_from_prediction_id"] == "pred-xyz"
    assert isinstance(body["age_seconds"], (int, float))
    assert body["age_seconds"] >= 0
    # File must NOT be deleted by a check (this is the read-only mirror of
    # the MCP `vaner.resolve` handoff path which DOES consume).
    assert stub_handoff.exists()


def test_stale_handoff_returns_null(temp_repo, stub_handoff: Path) -> None:
    stub_handoff.parent.mkdir(parents=True, exist_ok=True)
    # 1 hour old — way past the 600s default TTL.
    stub_handoff.write_text(json.dumps(_payload(time.time() - 3600)), encoding="utf-8")
    with _client(temp_repo) as client:
        resp = client.post("/integrations/handoff/check")
    body = resp.json()
    assert body["fresh"] is False
    assert body["adopted_package"] is None


def test_custom_ttl_extends_freshness(temp_repo, stub_handoff: Path) -> None:
    stub_handoff.parent.mkdir(parents=True, exist_ok=True)
    # 1 hour old; default TTL says stale, but we override.
    stub_handoff.write_text(json.dumps(_payload(time.time() - 3600)), encoding="utf-8")
    with _client(temp_repo) as client:
        resp = client.post("/integrations/handoff/check", json={"ttl_seconds": 7200})
    body = resp.json()
    assert body["fresh"] is True


def test_invalid_json_body_falls_back_to_default_ttl(temp_repo, stub_handoff: Path) -> None:
    stub_handoff.parent.mkdir(parents=True, exist_ok=True)
    stub_handoff.write_text(json.dumps(_payload(time.time())), encoding="utf-8")
    with _client(temp_repo) as client:
        # Send malformed body; endpoint should still return a fresh result.
        resp = client.post(
            "/integrations/handoff/check",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 200
    assert resp.json()["fresh"] is True


def test_negative_ttl_falls_back_to_default(temp_repo, stub_handoff: Path) -> None:
    stub_handoff.parent.mkdir(parents=True, exist_ok=True)
    # Fresh payload; if the endpoint accepted a negative TTL it would mark stale.
    stub_handoff.write_text(json.dumps(_payload(time.time())), encoding="utf-8")
    with _client(temp_repo) as client:
        resp = client.post("/integrations/handoff/check", json={"ttl_seconds": -1})
    body = resp.json()
    # Negative TTL is rejected silently and the default is used.
    assert body["fresh"] is True
