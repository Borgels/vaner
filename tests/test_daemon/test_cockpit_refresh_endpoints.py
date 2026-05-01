# SPDX-License-Identifier: Apache-2.0
"""Tests for the cockpit-refresh HTTP routes.

Covers /goals, /artefacts, /artefacts/{id}, /learning/recent, the
include_all variant of /predictions/active, and the latest_invalidation_signal
field on /scenarios/{id}.
"""

from __future__ import annotations

import platform
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_registry import PredictionRegistry
from vaner.models.config import VanerConfig
from vaner.models.signal import SignalEvent
from vaner.store.artefacts import ArtefactStore

if platform.system().lower().startswith("win"):
    pytest.skip("daemon http TestClient is flaky on Windows runners", allow_module_level=True)


def _config(repo: Path) -> VanerConfig:
    return VanerConfig(
        repo_root=repo,
        store_path=repo / ".vaner" / "store.db",
        telemetry_path=repo / ".vaner" / "telemetry.db",
    )


@dataclass
class _StubEngine:
    prediction_registry: PredictionRegistry

    def get_active_predictions(self):
        return self.prediction_registry.active()


def _enroll_prediction(reg: PredictionRegistry) -> str:
    spec = PredictionSpec(
        id=prediction_id("arc", "anchor", "Plan the next refactor"),
        label="Plan the next refactor",
        description="Predicted follow-up",
        source="arc",
        anchor="anchor",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    reg.enroll(spec, initial_weight=1.0)
    return spec.id


@pytest.mark.asyncio
async def test_goals_endpoint_returns_workspace_goals(temp_repo: Path) -> None:
    store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
    await store.initialize()
    await store.upsert_workspace_goal(
        id="goal-1",
        title="Clean up the auth middleware",
        description="Remove the legacy session token paths",
        source="user_declared",
        confidence=1.0,
        status="active",
        evidence_json="[]",
        related_files_json="[\"src/auth.py\"]",
    )

    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.get("/goals")

    assert response.status_code == 200
    data = response.json()
    assert len(data["goals"]) == 1
    goal = data["goals"][0]
    assert goal["id"] == "goal-1"
    assert goal["title"] == "Clean up the auth middleware"
    assert goal["related_files"] == ["src/auth.py"]
    # Raw JSON columns should be parsed away.
    assert "related_files_json" not in goal
    assert "evidence_json" not in goal


@pytest.mark.asyncio
async def test_artefacts_endpoint_returns_artefacts_and_detail(temp_repo: Path) -> None:
    store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
    await store.initialize()
    artefact_id = "artefact-1"
    snapshot_id = "snap-1"
    now = time.time()
    await store.upsert_intent_artefact(
        id=artefact_id,
        source_uri="docs/auth.md",
        source_tier="local",
        connector="local",
        kind="doc",
        title="Design doc draft",
        status="active",
        confidence=0.8,
        created_at=now,
        last_observed_at=now,
        last_reconciled_at=None,
        latest_snapshot=snapshot_id,
        linked_goals_json='["goal-1"]',
        linked_files_json='["src/auth.py"]',
        supersedes=None,
    )

    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        listing = client.get("/artefacts")
        assert listing.status_code == 200
        artefacts = listing.json()["artefacts"]
        assert len(artefacts) == 1
        assert artefacts[0]["linked_goals"] == ["goal-1"]
        assert "linked_files_json" not in artefacts[0]

        detail = client.get(f"/artefacts/{artefact_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["artefact"]["id"] == artefact_id
        assert body["snapshot_id"] == snapshot_id
        assert isinstance(body["recent_outcomes"], list)


@pytest.mark.asyncio
async def test_artefacts_endpoint_404_on_unknown_id(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.get("/artefacts/missing")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_learning_recent_returns_feedback_and_state(temp_repo: Path) -> None:
    store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
    await store.initialize()
    await store.insert_feedback_event(
        query_id="query-1",
        cache_tier="hot",
        similarity=0.91,
        quality_lift=0.12,
        latency_ms=180.0,
        metadata={"feedback_kind": "useful", "scenario_id": "s-1"},
    )
    await store.upsert_learning_state(
        key="skill_weights",
        value={"tests": 0.6, "refactor": 0.3},
    )

    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.get("/learning/recent?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["feedback_events"]) == 1
    assert payload["learning_state"]["skill_weights"]["tests"] == 0.6


def test_predictions_active_include_all_groups_by_state(temp_repo: Path) -> None:
    config = _config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll_prediction(registry)
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.get("/predictions/active?include_all=true")

    assert response.status_code == 200
    payload = response.json()
    assert "by_state" in payload
    grouped = payload["by_state"]
    assert isinstance(grouped, dict)
    # The single enrolled prediction lands in some readiness lane.
    flat = [row for items in grouped.values() for row in items]
    assert any(row["id"] == pid for row in flat)


def test_predictions_active_default_is_back_compat(temp_repo: Path) -> None:
    config = _config(temp_repo)
    app = create_daemon_http_app(config)
    with TestClient(app) as client:
        response = client.get("/predictions/active")
    assert response.status_code == 200
    payload = response.json()
    # The default response stays exactly { predictions: [] } so existing
    # MCP / desktop clients keep parsing successfully.
    assert payload == {"predictions": []}


@pytest.mark.asyncio
async def test_scenarios_detail_emits_invalidation_signal_for_stale(temp_repo: Path) -> None:
    # Seed a scenarios.db row marked recent and a signal_events row to be
    # surfaced on /scenarios/{id}.
    from vaner.models.scenario import Scenario
    from vaner.store.scenarios import ScenarioStore

    scenario_store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
    await scenario_store.initialize()
    await scenario_store.upsert(
        Scenario(
            id="scn-stale",
            kind="research",
            score=0.4,
            confidence=0.5,
            freshness="recent",
        )
    )

    artefact_store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
    await artefact_store.initialize()
    await artefact_store.insert_signal_event(
        SignalEvent(
            id=str(uuid.uuid4()),
            source="git",
            kind="commit",
            timestamp=time.time(),
            payload={"sha": "abcdef"},
        )
    )

    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.get("/scenarios/scn-stale")

    assert response.status_code == 200
    body = response.json()
    invalidation = body.get("latest_invalidation_signal")
    assert invalidation is not None
    assert invalidation["kind"] == "commit"
    assert invalidation["source"] == "git"
