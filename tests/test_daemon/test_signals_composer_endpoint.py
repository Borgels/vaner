# SPDX-License-Identifier: Apache-2.0
"""Tests for POST /signals/composer — 0.8.7 WS7.

Validates the daemon endpoint that ingests composer-lifecycle events
from registered ComposerAdapters (e.g. the Claude Code UserPromptSubmit
hook).
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.models.config import VanerConfig
from vaner.models.signal import KIND_COMPOSER_LIFECYCLE, SignalEvent

if platform.system().lower().startswith("win"):
    pytest.skip("daemon http TestClient is flaky on Windows runners", allow_module_level=True)


@dataclass
class _CapturingEngine:
    """Engine shim that captures every observe() call for assertion."""

    observed: list[SignalEvent] = field(default_factory=list)

    async def observe(self, event: SignalEvent) -> None:
        self.observed.append(event)


def _make_config(tmp_path: Path) -> VanerConfig:
    return VanerConfig(
        repo_root=tmp_path,
        store_path=tmp_path / ".vaner" / "store.db",
        telemetry_path=tmp_path / ".vaner" / "telemetry.db",
    )


def _valid_l0_payload() -> dict[str, Any]:
    return {
        "session_id": "session-abc",
        "snapshot_id": "snap-001",
        "timestamp": "2026-04-25T20:14:45Z",
        "lifecycle_state": "submitted",
        "text_hash": "0" * 64,
        "length_chars": 42,
        "capabilities": {
            "level": "L0",
            "emits": ["submitted"],
            "host_app": "claude-code",
            "host_kind": "ai_chat_client",
        },
    }


def test_unavailable_engine_returns_409(temp_repo):
    config = _make_config(temp_repo)
    app = create_daemon_http_app(config)  # no engine
    with TestClient(app) as client:
        response = client.post("/signals/composer", json=_valid_l0_payload())
    assert response.status_code == 409
    assert response.json()["code"] == "engine_unavailable"


def test_valid_payload_publishes_to_engine_and_returns_event_id(temp_repo):
    config = _make_config(temp_repo)
    engine = _CapturingEngine()
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.post("/signals/composer", json=_valid_l0_payload())

    assert response.status_code == 200
    body = response.json()
    assert "composer_event_id" in body
    assert isinstance(body["composer_event_id"], str)
    assert len(body["composer_event_id"]) > 0

    # Engine saw exactly one composer_lifecycle event with the right shape.
    assert len(engine.observed) == 1
    event = engine.observed[0]
    assert event.kind == KIND_COMPOSER_LIFECYCLE
    assert event.source == "composer-adapter:claude-code"
    assert event.payload["session_id"] == "session-abc"
    assert event.payload["lifecycle_state"] == "submitted"
    # Critical privacy invariant: only hash + length, no raw text key.
    assert "prompt" not in event.payload
    assert "text" not in event.payload
    assert "draft_text" not in event.payload


def test_malformed_json_returns_400(temp_repo):
    config = _make_config(temp_repo)
    engine = _CapturingEngine()
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.post(
            "/signals/composer",
            content=b"not-json{",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"
    assert engine.observed == []


def test_missing_required_field_returns_400(temp_repo):
    config = _make_config(temp_repo)
    engine = _CapturingEngine()
    app = create_daemon_http_app(config, engine=engine)
    bad = _valid_l0_payload()
    del bad["text_hash"]

    with TestClient(app) as client:
        response = client.post("/signals/composer", json=bad)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"
    assert engine.observed == []


def test_capability_violation_returns_400(temp_repo):
    """An L0 adapter declaring emits=['submitted'] cannot send 'actionable'."""
    config = _make_config(temp_repo)
    engine = _CapturingEngine()
    app = create_daemon_http_app(config, engine=engine)
    payload = _valid_l0_payload()
    payload["lifecycle_state"] = "actionable"  # not in capabilities.emits

    with TestClient(app) as client:
        response = client.post("/signals/composer", json=payload)

    assert response.status_code == 400
    assert response.json()["code"] == "capability_violation"
    assert engine.observed == []


def test_response_event_id_is_unique_per_call(temp_repo):
    config = _make_config(temp_repo)
    engine = _CapturingEngine()
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        a = client.post("/signals/composer", json=_valid_l0_payload()).json()
        b = client.post("/signals/composer", json=_valid_l0_payload()).json()

    assert a["composer_event_id"] != b["composer_event_id"]
    # Both events reached the engine.
    assert len(engine.observed) == 2
