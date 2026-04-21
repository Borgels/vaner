# SPDX-License-Identifier: Apache-2.0
"""Proxy emits decision.recorded + llm.request/llm.response to the bus."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from vaner.events import get_bus, reset_bus
from vaner.models.config import BackendConfig, ProxyConfig, VanerConfig
from vaner.router import proxy as proxy_module
from vaner.router.proxy import create_app
from vaner.store.artefacts import ArtefactStore

_TEST_BACKEND = BackendConfig(base_url="http://127.0.0.1:11434/v1", model="test-model")


@pytest.fixture(autouse=True)
def _fresh_bus() -> None:
    reset_bus()


def _make_config(temp_repo) -> VanerConfig:
    return VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
        backend=_TEST_BACKEND,
        proxy=ProxyConfig(max_requests_per_minute=10),
    )


class _Package:
    injected_context = "context"
    cache_tier = "miss"
    partial_similarity = 0.0
    token_used = 7


async def _fake_aquery(prompt, repo_root, config=None, top_n=6):
    return _Package()


async def _fake_forward(config, payload, *, authorization_header=None):
    return {
        "id": "chatcmpl-1",
        "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }


def _drain(queue: asyncio.Queue, limit: int = 20) -> list:
    async def _run() -> list:
        events: list = []
        for _ in range(limit):
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.05)
            except TimeoutError:
                break
            events.append(event)
        return events

    return asyncio.run(_run())


def test_proxy_emits_llm_request_and_response_events(temp_repo, monkeypatch):
    monkeypatch.setattr(proxy_module, "aquery", _fake_aquery)
    monkeypatch.setattr(proxy_module, "forward_chat_completion_with_request", _fake_forward)

    config = _make_config(temp_repo)
    app = create_app(config, ArtefactStore(config.store_path))
    bus = get_bus()
    queue = bus.subscribe()

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200

    events = _drain(queue)
    kinds = [(event.stage, event.kind) for event in events]

    assert ("model", "llm.request") in kinds
    assert ("model", "llm.response") in kinds

    request_event = next(event for event in events if event.kind == "llm.request")
    response_event = next(event for event in events if event.kind == "llm.response")
    assert request_event.payload["model"] == "test-model"
    assert response_event.payload["ok"] is True
    assert response_event.payload["request_id"] == request_event.id
    assert response_event.payload["latency_ms"] >= 0


def test_proxy_emits_decision_recorded_when_decision_present(temp_repo, monkeypatch):
    monkeypatch.setattr(proxy_module, "aquery", _fake_aquery)
    monkeypatch.setattr(proxy_module, "forward_chat_completion_with_request", _fake_forward)

    class _FakeDecision:
        id = "dec_test_1"
        selections = [{"a": 1}, {"b": 2}]

        def model_dump(self, mode=None):
            return {"id": self.id, "selections": self.selections}

    monkeypatch.setattr(proxy_module.DecisionRecord, "read_latest", staticmethod(lambda repo_root: _FakeDecision()))

    config = _make_config(temp_repo)
    app = create_app(config, ArtefactStore(config.store_path))
    bus = get_bus()
    queue = bus.subscribe()

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200

    events = _drain(queue)
    decision_events = [event for event in events if event.kind == "decision.recorded"]
    assert decision_events, f"expected decision.recorded event, got {[(event.stage, event.kind) for event in events]}"
    decision_event = decision_events[0]
    assert decision_event.stage == "decisions"
    assert decision_event.payload["decision_id"] == "dec_test_1"
    assert decision_event.payload["selection_count"] == 2
