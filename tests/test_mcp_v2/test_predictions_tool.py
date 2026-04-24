# SPDX-License-Identifier: Apache-2.0
"""Tests for vaner.predictions.active and vaner.predictions.adopt MCP tools."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pytest

from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_registry import PredictionRegistry

from .conftest import call_tool, parse_content


@dataclass
class _StubEngine:
    prediction_registry: PredictionRegistry

    def get_active_predictions(self):
        return self.prediction_registry.active()


def _enroll(reg: PredictionRegistry, *, label: str = "Write the next test") -> str:
    spec = PredictionSpec(
        id=prediction_id("arc", "anchor", label),
        label=label,
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


def _make_server(temp_repo: Path, engine):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    return build_server(temp_repo, engine=engine)


# ---------------------------------------------------------------------------
# vaner.predictions.active
# ---------------------------------------------------------------------------


def test_predictions_active_without_engine_returns_unavailable_flag(temp_repo):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    server = build_server(temp_repo)  # no engine
    result = call_tool(server, "vaner.predictions.active")
    body = parse_content(result)
    assert body["engine_unavailable"] is True
    assert body["predictions"] == []


def test_predictions_active_with_engine_returns_live_rows(temp_repo):
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll(registry)
    engine = _StubEngine(prediction_registry=registry)
    server = _make_server(temp_repo, engine=engine)

    result = call_tool(server, "vaner.predictions.active")
    body = parse_content(result)

    assert body.get("engine_unavailable") is not True
    assert len(body["predictions"]) == 1
    row = body["predictions"][0]
    assert row["id"] == pid
    assert row["label"] == "Write the next test"
    assert row["source"] == "arc"
    assert row["readiness"] == "queued"
    assert row["has_draft"] is False


def test_predictions_active_excludes_stale(temp_repo):
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll(registry)
    registry.transition(pid, "stale")
    engine = _StubEngine(prediction_registry=registry)
    server = _make_server(temp_repo, engine=engine)

    result = call_tool(server, "vaner.predictions.active")
    body = parse_content(result)
    assert body["predictions"] == []


# ---------------------------------------------------------------------------
# vaner.predictions.adopt
# ---------------------------------------------------------------------------


def test_adopt_errors_on_empty_prediction_id(temp_repo):
    """Empty string in prediction_id is our handler's own guard (schema can't
    catch an empty string, only a missing key). The schema-level missing-key
    case is rejected by MCP before the handler runs."""
    registry = PredictionRegistry(cycle_token_pool=1_000)
    _enroll(registry)
    engine = _StubEngine(prediction_registry=registry)
    server = _make_server(temp_repo, engine=engine)

    result = call_tool(server, "vaner.predictions.adopt", {"prediction_id": "   "})
    body = parse_content(result)
    assert body["code"] == "invalid_input"


def test_adopt_returns_engine_unavailable_without_engine(temp_repo):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    server = build_server(temp_repo)

    result = call_tool(server, "vaner.predictions.adopt", {"prediction_id": "any"})
    body = parse_content(result)
    assert body["code"] == "engine_unavailable"


def test_adopt_returns_not_found_for_unknown_id(temp_repo):
    registry = PredictionRegistry(cycle_token_pool=1_000)
    _enroll(registry)
    engine = _StubEngine(prediction_registry=registry)
    server = _make_server(temp_repo, engine=engine)

    result = call_tool(server, "vaner.predictions.adopt", {"prediction_id": "nope"})
    body = parse_content(result)
    assert body["code"] == "not_found"


def test_adopt_returns_resolution_with_prediction_provenance(temp_repo):
    registry = PredictionRegistry(cycle_token_pool=1_000)
    pid = _enroll(registry)
    # Attach prepared artifacts so the Resolution carries content.
    registry.attach_artifact(
        pid,
        draft="Here is a suggested answer.",
        briefing="## Context\nfoo.py: bar()\n",
    )
    engine = _StubEngine(prediction_registry=registry)
    server = _make_server(temp_repo, engine=engine)

    result = call_tool(server, "vaner.predictions.adopt", {"prediction_id": pid})
    body = parse_content(result)

    # Resolution-shaped response
    assert body["adopted_from_prediction_id"] == pid
    assert body["resolution_id"].startswith("adopt-")
    # WS9: the MCP adopt path routes through BriefingAssembler, which wraps
    # the prediction's attached briefing in summary + evidence + provenance
    # sections. The attached text still appears inside the Prepared evidence
    # section; we just no longer assume it's at position 0.
    assert body["prepared_briefing"] is not None
    assert "## Context" in body["prepared_briefing"]
    assert "foo.py: bar()" in body["prepared_briefing"]
    # Provenance section is always last and cites the prediction's source.
    assert "## Provenance" in body["prepared_briefing"]
    assert "source: arc" in body["prepared_briefing"]
    assert body["predicted_response"] == "Here is a suggested answer."
    assert body["intent"] == "Write the next test"
    assert body["provenance"]["mode"] == "predictive_hit"


# ---------------------------------------------------------------------------
# WS3.5 — MCP tool forwards through the injected daemon_client
# ---------------------------------------------------------------------------


def test_mcp_forwards_to_injected_daemon_client_for_predictions_active(temp_repo):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    import httpx

    from vaner.clients.daemon import DEFAULT_BASE_URL, VanerDaemonClient
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/predictions/active"
        return httpx.Response(
            200,
            json={"predictions": [{"id": "mock-pid", "spec": {"label": "Mock prediction"}}]},
        )

    transport = httpx.MockTransport(_handler)
    httpx_client = httpx.AsyncClient(transport=transport, base_url=DEFAULT_BASE_URL)
    daemon_client = VanerDaemonClient(client=httpx_client)
    server = build_server(temp_repo, daemon_client=daemon_client)

    result = call_tool(server, "vaner.predictions.active")
    body = parse_content(result)
    assert body["predictions"][0]["id"] == "mock-pid"


def test_mcp_forwards_adopt_via_injected_daemon_client(temp_repo):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    import httpx

    from vaner.clients.daemon import DEFAULT_BASE_URL, VanerDaemonClient
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={
                "intent": "Forwarded adopt",
                "confidence": 0.9,
                "summary": "ok",
                "evidence": [],
                "provenance": {"mode": "predictive_hit"},
                "resolution_id": "adopt-mock",
                "prepared_briefing": "## Brief",
                "adopted_from_prediction_id": "mock-pid",
            },
        )

    transport = httpx.MockTransport(_handler)
    httpx_client = httpx.AsyncClient(transport=transport, base_url=DEFAULT_BASE_URL)
    daemon_client = VanerDaemonClient(client=httpx_client)
    server = build_server(temp_repo, daemon_client=daemon_client)

    result = call_tool(server, "vaner.predictions.adopt", {"prediction_id": "mock-pid"})
    body = parse_content(result)
    assert body["adopted_from_prediction_id"] == "mock-pid"
    assert body["resolution_id"] == "adopt-mock"
