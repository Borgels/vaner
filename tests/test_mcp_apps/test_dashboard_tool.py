# SPDX-License-Identifier: Apache-2.0

"""End-to-end tests for vaner.predictions.dashboard via the MCP server."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
    pytest.skip("mcp package is unavailable in this test environment", allow_module_level=True)

from vaner.integrations.capability import detect_tier, record_tier, reset_cache
from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)


@pytest.fixture(autouse=True)
def _reset_capability_cache() -> None:
    reset_cache()
    yield
    reset_cache()


def _prompt(
    *,
    label: str,
    readiness: str = "ready",
    briefing: str | None = "prepared summary",
    draft: str | None = None,
    confidence: float = 0.75,
) -> PredictedPrompt:
    spec = PredictionSpec(
        id=prediction_id("arc", "anchor", label),
        label=label,
        description=f"suspected {label}",
        source="arc",
        anchor="anchor",
        confidence=confidence,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    run = PredictionRun(
        weight=0.5,
        token_budget=2048,
        tokens_used=500,
        scenarios_spawned=4,
        scenarios_complete=3,
        readiness=readiness,  # type: ignore[arg-type]
        updated_at=0.0,
    )
    artifacts = PredictionArtifacts(prepared_briefing=briefing, draft_answer=draft)
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


class _StubEngine:
    """Minimal engine stub — the dashboard handler only calls get_active_predictions()."""

    def __init__(self, prompts: list[PredictedPrompt]) -> None:
        self._prompts = prompts
        self.prediction_registry = None

    def get_active_predictions(self) -> list[PredictedPrompt]:
        return list(self._prompts)


def _json_payload_from_result(result) -> dict:
    for item in result.root.content:
        text = getattr(item, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError("tool result did not include a JSON text payload")


def _call_result(server, name: str, arguments: dict | None = None):
    async def _do():
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = server.request_handlers[CallToolRequest]
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments or {}),
        )
        return await handler(req)

    return asyncio.run(_do())


def _call(server, name: str, arguments: dict | None = None) -> dict:
    return _json_payload_from_result(_call_result(server, name, arguments))


def _build(tmp_path: Path, prompts: list[PredictedPrompt]):
    (tmp_path / ".vaner").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    from vaner.mcp.server import build_server

    return build_server(tmp_path, engine=_StubEngine(prompts))


def test_dashboard_returns_cards_with_ui_available_false_by_default(tmp_path: Path) -> None:
    server = _build(
        tmp_path,
        [
            _prompt(label="Ready alpha", readiness="ready"),
            _prompt(label="Drafting beta", readiness="drafting", draft="d"),
            _prompt(label="Queued gamma", readiness="queued", briefing=None),
        ],
    )
    payload = _call(server, "vaner.predictions.dashboard")
    assert payload["ui_available"] is False  # no Tier-4 client advertised
    assert payload["source"] == "engine"
    assert len(payload["predictions"]) == 3
    # Adoptable ordering: ready alpha first.
    assert payload["predictions"][0]["label"] == "Ready alpha"
    assert payload["predictions"][0]["rank"] == 1


def test_dashboard_fallback_path_sets_structured_content(tmp_path: Path) -> None:
    server = _build(tmp_path, [_prompt(label="one")])
    result = _call_result(server, "vaner.predictions.dashboard")
    payload = _json_payload_from_result(result)
    assert result.root.structuredContent == payload


def test_dashboard_resource_link_path_sets_structured_content(tmp_path: Path) -> None:
    from mcp.server.lowlevel.server import RequestContext, request_ctx

    class _Session:
        pass

    session = _Session()
    session.client_params = SimpleNamespace(
        clientInfo=SimpleNamespace(name="tier4-client", version="1.0"),
        capabilities=SimpleNamespace(
            experimental={"io.modelcontextprotocol/ui": {}},
            roots=SimpleNamespace(),
            sampling=None,
        ),
    )
    record_tier(session, detect_tier(session.client_params))
    token = request_ctx.set(RequestContext(request_id="test", meta=None, session=session, lifespan_context=None))
    try:
        server = _build(tmp_path, [_prompt(label="one")])
        result = _call_result(server, "vaner.predictions.dashboard")
    finally:
        request_ctx.reset(token)

    payload = _json_payload_from_result(result)
    assert payload["ui_available"] is True
    assert result.root.structuredContent == payload
    assert any(getattr(item, "type", None) == "resource_link" for item in result.root.content)


def test_dashboard_respects_limit(tmp_path: Path) -> None:
    prompts = [_prompt(label=f"p{i}") for i in range(8)]
    server = _build(tmp_path, prompts)
    payload = _call(server, "vaner.predictions.dashboard", {"limit": 3})
    assert len(payload["predictions"]) == 3
    for i, card in enumerate(payload["predictions"], start=1):
        assert card["rank"] == i


def test_dashboard_min_readiness_filter(tmp_path: Path) -> None:
    prompts = [
        _prompt(label="queued one", readiness="queued", briefing=None),
        _prompt(label="ready one", readiness="ready"),
    ]
    server = _build(tmp_path, prompts)
    payload = _call(server, "vaner.predictions.dashboard", {"min_readiness": "drafting"})
    labels = [c["label"] for c in payload["predictions"]]
    assert "ready one" in labels
    assert "queued one" not in labels


def test_dashboard_empty_engine_renders_empty_state(tmp_path: Path) -> None:
    server = _build(tmp_path, [])
    payload = _call(server, "vaner.predictions.dashboard")
    assert payload["predictions"] == []
    assert "preparing likely next steps" in payload["fallback_text"]


def test_dashboard_fallback_text_matches_card_count(tmp_path: Path) -> None:
    prompts = [_prompt(label=f"p{i}") for i in range(4)]
    server = _build(tmp_path, prompts)
    payload = _call(server, "vaner.predictions.dashboard", {"limit": 2})
    assert "2 active prediction(s)" in payload["fallback_text"]


def test_dashboard_include_details_default_false_strips_description(tmp_path: Path) -> None:
    server = _build(tmp_path, [_prompt(label="one")])
    payload = _call(server, "vaner.predictions.dashboard")
    card = payload["predictions"][0]
    assert "description" not in card


def test_dashboard_include_details_true_keeps_description(tmp_path: Path) -> None:
    server = _build(tmp_path, [_prompt(label="one")])
    payload = _call(server, "vaner.predictions.dashboard", {"include_details": True})
    assert payload["predictions"][0]["description"] == "suspected one"
