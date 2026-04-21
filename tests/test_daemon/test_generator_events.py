# SPDX-License-Identifier: Apache-2.0
"""LLM wrapper emits ``llm.request`` + ``llm.response`` with latency."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from vaner.daemon.engine.generator import _llm_summarize
from vaner.events import get_bus, reset_bus
from vaner.models.config import GenerationConfig, VanerConfig


@pytest.fixture(autouse=True)
def _fresh_bus() -> None:
    reset_bus()


def _config(temp_repo) -> VanerConfig:
    return VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
        generation=GenerationConfig(use_llm=True, generation_model="test-model"),
    )


class _FakeResponse:
    def __init__(self, status_code: int, data: dict) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.mark.asyncio
async def test_llm_summarize_success_emits_request_and_response(temp_repo, monkeypatch):
    config = _config(temp_repo)
    bus = get_bus()
    queue = bus.subscribe()

    fake = _FakeResponse(
        200,
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
    )
    monkeypatch.setattr(
        "vaner.daemon.engine.generator.httpx.AsyncClient",
        lambda timeout=None: _FakeClient(fake),
    )

    result = await _llm_summarize("body", "prompt", config, "src/foo.py")
    assert result == "ok"

    events = []
    for _ in range(2):
        events.append(await asyncio.wait_for(queue.get(), timeout=0.5))

    by_kind = {event.kind: event for event in events}
    assert set(by_kind) == {"llm.request", "llm.response"}

    request_event = by_kind["llm.request"]
    assert request_event.stage == "model"
    assert request_event.path == "src/foo.py"
    assert request_event.payload["model"] == "test-model"
    assert request_event.payload["prompt_chars"] == len("prompt")

    response_event = by_kind["llm.response"]
    assert response_event.stage == "model"
    assert response_event.payload["ok"] is True
    assert response_event.payload["request_id"] == request_event.id
    assert response_event.payload["latency_ms"] >= 0
    assert response_event.payload["usage"] == {"prompt_tokens": 3, "completion_tokens": 2}


@pytest.mark.asyncio
async def test_llm_summarize_failure_still_emits_response_with_error(temp_repo, monkeypatch):
    config = _config(temp_repo)
    bus = get_bus()
    queue = bus.subscribe()

    monkeypatch.setattr(
        "vaner.daemon.engine.generator.httpx.AsyncClient",
        lambda timeout=None: _FakeClient(RuntimeError("connection refused")),
    )

    result = await _llm_summarize("body", "prompt", config, "src/bar.py")
    assert result is None

    events = []
    for _ in range(2):
        events.append(await asyncio.wait_for(queue.get(), timeout=0.5))

    by_kind = {event.kind: event for event in events}
    assert set(by_kind) == {"llm.request", "llm.response"}
    response_event = by_kind["llm.response"]
    assert response_event.payload["ok"] is False
    assert "connection refused" in response_event.payload["error"]
