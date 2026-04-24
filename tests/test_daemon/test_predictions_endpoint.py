# SPDX-License-Identifier: Apache-2.0
"""Tests for the Phase C /predictions/* HTTP surface."""

from __future__ import annotations

import platform
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_registry import PredictionRegistry
from vaner.models.config import VanerConfig

if platform.system().lower().startswith("win"):
    pytest.skip("daemon http TestClient is flaky on Windows runners", allow_module_level=True)


@dataclass
class _StubEngine:
    """Minimal engine shim exposing just what the predictions HTTP surface
    needs. Lets us exercise the endpoints without spinning up a real engine.
    """

    prediction_registry: PredictionRegistry

    def get_active_predictions(self):
        return self.prediction_registry.active()


def _enroll_stub(reg: PredictionRegistry) -> str:
    spec = PredictionSpec(
        id=prediction_id("arc", "anchor", "Write the next test"),
        label="Write the next test",
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


def _make_config(tmp_path):
    return VanerConfig(
        repo_root=tmp_path,
        store_path=tmp_path / ".vaner" / "store.db",
        telemetry_path=tmp_path / ".vaner" / "telemetry.db",
    )


def test_predictions_active_empty_when_no_engine(temp_repo):
    config = _make_config(temp_repo)
    app = create_daemon_http_app(config)  # no engine
    with TestClient(app) as client:
        response = client.get("/predictions/active")
    assert response.status_code == 200
    assert response.json() == {"predictions": []}


def test_predictions_active_returns_live_predictions(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll_stub(registry)
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.get("/predictions/active")

    assert response.status_code == 200
    data = response.json()
    assert len(data["predictions"]) == 1
    row = data["predictions"][0]
    assert row["id"] == pid
    assert row["spec"]["label"] == "Write the next test"
    assert row["spec"]["source"] == "arc"
    assert row["run"]["readiness"] == "queued"
    assert "token_budget" in row["run"]
    assert row["artifacts"]["has_draft"] is False


def test_predictions_single_returns_404_when_registry_absent(temp_repo):
    config = _make_config(temp_repo)
    app = create_daemon_http_app(config)  # no engine
    with TestClient(app) as client:
        response = client.get("/predictions/some-id")
    assert response.status_code == 404


def test_predictions_single_returns_prediction_when_present(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll_stub(registry)
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.get(f"/predictions/{pid}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == pid
    assert body["spec"]["label"] == "Write the next test"


def test_predictions_single_returns_404_for_unknown_id(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    _enroll_stub(registry)
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)
    with TestClient(app) as client:
        response = client.get("/predictions/does-not-exist")
    assert response.status_code == 404


def test_predictions_active_excludes_stale(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll_stub(registry)
    registry.transition(pid, "stale")
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.get("/predictions/active")

    assert response.status_code == 200
    assert response.json() == {"predictions": []}


def test_adopt_returns_resolution_with_prepared_package(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll_stub(registry)
    registry.attach_artifact(
        pid,
        draft="Here is a suggested answer.",
        briefing="## Context\nfoo.py: bar()\n",
    )
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.post(f"/predictions/{pid}/adopt")

    assert response.status_code == 200
    body = response.json()
    assert body["adopted_from_prediction_id"] == pid
    assert body["resolution_id"].startswith("adopt-")
    # WS9: the adopt path now routes through BriefingAssembler, which wraps
    # the prediction's attached briefing in summary + Prepared evidence +
    # Provenance sections. The original text still appears verbatim inside
    # the Prepared evidence section.
    assert body["prepared_briefing"] is not None
    assert "## Context" in body["prepared_briefing"]
    assert "foo.py: bar()" in body["prepared_briefing"]
    assert "## Provenance" in body["prepared_briefing"]
    assert body["predicted_response"] == "Here is a suggested answer."
    assert body["intent"] == "Write the next test"
    assert body["provenance"]["mode"] == "predictive_hit"


def test_adopt_returns_404_for_unknown_id(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    _enroll_stub(registry)
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.post("/predictions/does-not-exist/adopt")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"


def test_adopt_returns_409_when_registry_absent(temp_repo):
    config = _make_config(temp_repo)
    app = create_daemon_http_app(config)  # no engine

    with TestClient(app) as client:
        response = client.post("/predictions/any/adopt")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "engine_unavailable"


def test_adopt_returns_400_for_whitespace_id(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    _enroll_stub(registry)
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.post("/predictions/%20/adopt")

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_input"


def test_events_stream_includes_predictions_stage(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    _enroll_stub(registry)
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        # Limit=1 ensures we get at most one frame and return, so the test
        # doesn't block waiting on the SSE loop.
        response = client.get("/events/stream", params={"stages": "predictions", "limit": 1})

    assert response.status_code == 200
    body = response.text
    assert '"stage": "predictions"' in body


# ---------------------------------------------------------------------------
# WS3.b — ?include= surface for GET /predictions/{id}
# ---------------------------------------------------------------------------


def test_predictions_one_default_omits_artifact_content(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll_stub(registry)
    registry.attach_artifact(pid, draft="secret draft", briefing="confidential briefing", thinking="private reasoning")
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)
    with TestClient(app) as client:
        response = client.get(f"/predictions/{pid}")
    body = response.json()
    # Summary flags only — raw content is NOT returned by default.
    assert body["artifacts"]["has_draft"] is True
    assert body["artifacts"]["has_briefing"] is True
    assert "artifacts_content" not in body


def test_predictions_one_include_draft_returns_content(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll_stub(registry)
    registry.attach_artifact(pid, draft="My speculative answer.", briefing="### Brief\n")
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)
    with TestClient(app) as client:
        response = client.get(f"/predictions/{pid}", params={"include": "draft"})
    body = response.json()
    assert body["artifacts_content"]["draft_answer"] == "My speculative answer."
    assert "prepared_briefing" not in body["artifacts_content"]


def test_predictions_one_include_multiple_returns_all_requested(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll_stub(registry)
    registry.attach_artifact(pid, draft="D", briefing="B", thinking="T")
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)
    with TestClient(app) as client:
        response = client.get(f"/predictions/{pid}", params={"include": "draft,briefing,thinking"})
    content = response.json()["artifacts_content"]
    assert content["draft_answer"] == "D"
    assert content["prepared_briefing"] == "B"
    assert content["thinking_traces"] == ["T"]


# ---------------------------------------------------------------------------
# WS3.d — adopt Resolution includes real evidence for attached scenarios
# ---------------------------------------------------------------------------


def test_adopt_resolution_lists_evidence_for_attached_scenarios(temp_repo):
    config = _make_config(temp_repo)
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll_stub(registry)
    registry.attach_scenario(pid, "scen-alpha")
    registry.attach_scenario(pid, "scen-beta")
    registry.attach_artifact(pid, draft="draft text", briefing="briefing text")
    engine = _StubEngine(prediction_registry=registry)
    app = create_daemon_http_app(config, engine=engine)

    with TestClient(app) as client:
        response = client.post(f"/predictions/{pid}/adopt")

    assert response.status_code == 200
    body = response.json()
    evidence_ids = {e["id"] for e in body["evidence"]}
    assert evidence_ids == {"scen-alpha", "scen-beta"}
    for e in body["evidence"]:
        assert e["kind"] == "record"
        assert e["locator"]["prediction_id"] == pid
    # briefing_token_used is nonzero for a non-empty briefing.
    assert body["briefing_token_used"] > 0
