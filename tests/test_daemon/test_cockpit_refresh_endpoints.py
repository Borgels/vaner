# SPDX-License-Identifier: Apache-2.0
"""Tests for the cockpit-refresh HTTP routes.

Covers /goals, /artefacts, /artefacts/{id}, /learning/recent, the
include_all variant of /predictions/active, and the latest_invalidation_signal
field on /scenarios/{id}.
"""

from __future__ import annotations

import json
import platform
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.daemon.precompute_worker import prediction_snapshot_path, worker_status_path
from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_registry import PredictionRegistry
from vaner.models.config import VanerConfig
from vaner.models.signal import SignalEvent
from vaner.plan_drafts import latest_plan_draft, list_plan_drafts, record_plan_draft
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
        related_files_json='["src/auth.py"]',
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


def test_plan_draft_endpoint_surfaces_active_work_and_prepared_card(temp_repo: Path) -> None:
    config = _config(temp_repo)
    app = create_daemon_http_app(config)
    plan = """# Live Work Visibility

- Add plan draft ingestion.
- Show active work in the cockpit.
- Keep speculative prep in Vaner runtime storage.
"""
    with TestClient(app) as client:
        created = client.post("/plans/draft", json={"text": plan, "source_client": "codex-cli", "session_id": "s1"})
        assert created.status_code == 200
        draft = created.json()["draft"]

        active = client.get("/work/active")
        assert active.status_code == 200
        active_body = active.json()
        assert active_body["plan_draft"]["id"] == draft["id"]
        assert "Live Work Visibility" in active_body["summary"]

        prepared = client.get("/prepared-work?surface=cockpit&limit=5")
        assert prepared.status_code == 200
        cards = prepared.json()["prepared_work"]
        assert cards[0]["id"] == f"plan:{draft['id']}"
        assert cards[0]["badge"] == "Plan draft"


def test_completed_plan_draft_stops_driving_active_work(temp_repo: Path) -> None:
    config = _config(temp_repo)
    app = create_daemon_http_app(config)
    plan = """# Finish Lifecycle

- Add lifecycle status.
- Verify active work drops completed plans.
"""
    with TestClient(app) as client:
        created = client.post("/plans/draft", json={"text": plan, "source_client": "codex-cli", "session_id": "s1"})
        assert created.status_code == 200
        draft = created.json()["draft"]

        completed = client.post(f"/plans/drafts/{draft['id']}/complete")
        assert completed.status_code == 200
        assert completed.json()["draft"]["status"] == "implemented"
        assert completed.json()["draft"]["accepted"] is True

        active = client.get("/work/active")
        assert active.status_code == 200
        active_body = active.json()
        assert active_body["plan_draft"] is None
        assert "Finish Lifecycle" not in active_body["summary"]

        prepared = client.get("/prepared-work?surface=cockpit&limit=5")
        assert prepared.status_code == 200
        assert all(card["id"] != f"plan:{draft['id']}" for card in prepared.json()["prepared_work"])

        predictions = client.get("/predictions/active?include_all=true")
        assert predictions.status_code == 200
        queued = predictions.json().get("by_state", {}).get("queued", [])
        assert all(row.get("id") != f"plan-draft-{draft['id']}" for row in queued)

        default_list = client.get("/plans/drafts")
        assert default_list.status_code == 200
        assert default_list.json()["drafts"] == []

        full_list = client.get("/plans/drafts?include_inactive=true")
        assert full_list.status_code == 200
        assert full_list.json()["drafts"][0]["id"] == draft["id"]


def test_codex_activity_extracts_proposed_plan_block(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))
    body = {
        "hook_event_name": "Stop",
        "session_id": "s2",
        "message": "<proposed_plan>\\n# Captured Plan\\n- Prepare one thing\\n</proposed_plan>",
    }
    with TestClient(app) as client:
        response = client.post("/signals/codex/activity", json=body)

    assert response.status_code == 200
    drafts = response.json()["plan_drafts"]
    assert len(drafts) == 1
    assert drafts[0]["title"] == "Captured Plan"
    assert drafts[0]["tasks"] == ["Prepare one thing"]


def test_codex_activity_ignores_proposed_plan_regex_literals(temp_repo: Path) -> None:
    app = create_daemon_http_app(_config(temp_repo))
    body = {
        "hook_event_name": "Stop",
        "session_id": "s2",
        "message": r'_PLAN_BLOCK_RE = re.compile(r"<proposed_plan>\s*(.*?)\s*</proposed_plan>")',
    }
    with TestClient(app) as client:
        response = client.post("/signals/codex/activity", json=body)
        active = client.get("/work/active")

    assert response.status_code == 200
    assert response.json()["plan_drafts"] == []
    assert active.json()["plan_draft"] is None


def test_plan_drafts_prefer_actionable_structure_over_incidental_newer_capture(temp_repo: Path) -> None:
    record_plan_draft(
        temp_repo,
        text="""# Setup Plan

- Fix the model recommendation config.
- Rebuild the desktop app.
- Verify cockpit prepared work.
""",
        now=100.0,
    )
    record_plan_draft(
        temp_repo,
        text="# Captured Plan\n- Prepare one thing",
        now=200.0,
    )

    latest = latest_plan_draft(temp_repo)

    assert latest is not None
    assert latest.title == "Setup Plan"


def test_plan_drafts_filter_raw_trailing_backslash_captures(temp_repo: Path) -> None:
    record_plan_draft(temp_repo, text="# Valid Plan\n- Verify cockpit state", now=100.0)
    record_plan_draft(temp_repo, text="# Captured Plan\n- Prepare one thing", now=200.0)
    store_path = temp_repo / ".vaner" / "runtime" / "plan_drafts.json"
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    payload["drafts"][0]["title"] = "Captured Plan\\"
    payload["drafts"][0]["summary"] = "Captured Plan\\: 1 planned step ready for shadow preparation."
    payload["drafts"][0]["tasks"] = ["Prepare one thing\\"]
    store_path.write_text(json.dumps(payload), encoding="utf-8")

    drafts = list_plan_drafts(temp_repo)

    assert [draft.title for draft in drafts] == ["Valid Plan"]


def test_plan_drafts_filter_generic_placeholder_captures_from_active_default(temp_repo: Path) -> None:
    record_plan_draft(temp_repo, text="# Captured Plan\n- Prepare one thing", now=100.0)
    concrete = record_plan_draft(temp_repo, text="# Captured Plan\n- Verify cockpit state", now=200.0)

    active = list_plan_drafts(temp_repo)
    all_drafts = list_plan_drafts(temp_repo, include_inactive=True)

    assert [draft.id for draft in active] == [concrete.id]
    assert len(all_drafts) == 2
    assert {draft.title for draft in all_drafts} == {"Captured Plan"}


def test_prepared_work_includes_worker_snapshot_predictions_without_engine(temp_repo: Path) -> None:
    path = prediction_snapshot_path(temp_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    payload = {
        "predictions": [
            {
                "id": "pred-1",
                "readiness_label": "Ready",
                "source_label": "Worker snapshot",
                "ui_summary": "Prepared from worker-owned prediction state.",
                "artifacts": {"scenario_ids": ["scn-1"], "has_draft": False},
                "spec": {
                    "label": "Implement active work UI",
                    "description": "Prepared from worker-owned prediction state.",
                    "source": "plan_draft",
                    "anchor": "active work UI",
                    "confidence": 0.82,
                    "created_at": now,
                },
                "run": {"readiness": "ready", "updated_at": now},
            }
        ],
        "by_state": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.get("/prepared-work?surface=cockpit&limit=5")

    assert response.status_code == 200
    cards = response.json()["prepared_work"]
    assert any(card["id"] == "prediction:pred-1" for card in cards)


def test_active_work_uses_compact_predictions_and_marks_stale_worker(temp_repo: Path) -> None:
    now = time.time()
    prediction_path = prediction_snapshot_path(temp_repo)
    prediction_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_path.write_text(
        json.dumps(
            {
                "predictions": [
                    {
                        "id": "pred-private",
                        "readiness_label": "Ready",
                        "source_label": "Recent work",
                        "ui_summary": (
                            "Recent queries clustered by shared domain vocabulary:\n"
                            "- private query sample?\n- another private query sample?"
                        ),
                        "spec": {
                            "label": "Goal: Privacy",
                            "description": "private query sample? another private query sample?",
                            "source": "goal",
                            "anchor": "private-query-anchor",
                            "confidence": 0.7,
                            "structured": {"semantic_hint": "private query sample? another private query sample?"},
                        },
                        "run": {"readiness": "ready", "updated_at": now},
                        "artifacts": {"scenario_ids": ["s1"], "has_draft": False, "has_briefing": True},
                    }
                ],
                "by_state": {},
            }
        ),
        encoding="utf-8",
    )
    worker_status_path(temp_repo).write_text(
        json.dumps(
            {
                "worker": {
                    "state": "running",
                    "phase": "prediction_precompute",
                    "pid": 99999999,
                    "last_heartbeat_at": now - 600,
                },
                "jobs": [{"id": "stale-job", "status": "running"}],
                "queue": {"size": 1},
            }
        ),
        encoding="utf-8",
    )

    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.get("/work/active")

    assert response.status_code == 200
    payload = response.json()
    assert payload["worker"]["state"] == "stale"
    assert payload["phase"] == "idle"
    assert all(job.get("id") != "stale-job" for job in payload["jobs"])
    row = payload["predictions"][0]
    assert "spec" not in row
    assert "structured" not in row
    assert "private query" not in json.dumps(row).lower()


@pytest.mark.asyncio
async def test_predictions_active_include_all_overlays_live_prompt_signal(temp_repo: Path) -> None:
    store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
    await store.initialize()
    await store.insert_query_history(
        session_id="session-1",
        query_text="prioritize prompt signal scheduling",
        selected_paths=[],
        hit_precomputed=False,
        token_used=0,
        timestamp=time.time(),
        source="codex_prompt",
        host_app="codex-cli",
        turn_id="turn-1",
        prompt_hash="b" * 64,
        capture_policy="local_raw_redacted",
    )

    app = create_daemon_http_app(_config(temp_repo))
    with TestClient(app) as client:
        response = client.get("/predictions/active?include_all=true")

    assert response.status_code == 200
    queued = response.json()["by_state"]["queued"]
    live = queued[0]
    assert live["source_label"] == "Live prompt signal"
    assert live["run"]["readiness"] == "queued"
    assert live["adoptable"] is False
    assert live["spec"]["anchor"] == "prioritize prompt signal scheduling"


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
