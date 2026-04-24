# SPDX-License-Identifier: Apache-2.0
"""Tests that structured-output and token-budget parameters propagate through
the LLM adapters into the outgoing HTTP request.

Uses httpx's MockTransport so we can assert request bodies without hitting a
real server.
"""

from __future__ import annotations

import json

import httpx
import pytest

from vaner.clients.ollama import ollama_llm_structured
from vaner.clients.openai import openai_llm_structured

_real_async_client = httpx.AsyncClient


def _stub_async_client(handler):
    """Factory returning a stub AsyncClient that ignores its kwargs and routes
    requests through ``handler``. Use with monkeypatch to intercept outgoing
    HTTP without hitting a real server.
    """

    def _factory(**_kwargs):
        return _real_async_client(transport=httpx.MockTransport(handler))

    return _factory


# ---------------------------------------------------------------------------
# OpenAI adapter — parameter propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_response_format_is_forwarded(monkeypatch):
    captured: dict[str, dict] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content or b"{}")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))

    call = openai_llm_structured(
        model="gpt-4o",
        api_key="EMPTY",
        base_url="http://localhost",
        max_tokens=256,
        response_format={"type": "json_object"},
    )
    await call("hi")

    assert captured["body"]["model"] == "gpt-4o"
    assert captured["body"]["max_tokens"] == 256
    assert captured["body"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_extra_body_is_merged(monkeypatch):
    captured: dict[str, dict] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content or b"{}")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))

    call = openai_llm_structured(
        model="qwen3",
        api_key="EMPTY",
        base_url="http://localhost",
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    await call("hi")
    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_openai_reasoning_off_injects_enable_thinking_false(monkeypatch):
    captured: dict[str, dict] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content or b"{}")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))

    call = openai_llm_structured(
        model="qwen3",
        api_key="EMPTY",
        base_url="http://localhost",
        reasoning_mode="off",
    )
    await call("hi")
    chat_tpl = captured["body"].get("chat_template_kwargs", {})
    assert chat_tpl.get("enable_thinking") is False


# ---------------------------------------------------------------------------
# Ollama adapter — translation to Ollama's idiom
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_max_tokens_translates_to_num_predict(monkeypatch):
    captured: dict[str, dict] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content or b"{}")
        return httpx.Response(200, json={"response": "{}"})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))

    call = ollama_llm_structured(
        model="qwen2.5:7b",
        base_url="http://localhost:11434",
        max_tokens=128,
    )
    await call("hi")
    assert captured["body"]["options"]["num_predict"] == 128


@pytest.mark.asyncio
async def test_ollama_response_format_translates_to_format_json(monkeypatch):
    captured: dict[str, dict] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content or b"{}")
        return httpx.Response(200, json={"response": "{}"})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))

    call = ollama_llm_structured(
        model="qwen2.5:7b",
        base_url="http://localhost:11434",
        response_format={"type": "json_object"},
    )
    await call("hi")
    assert captured["body"]["format"] == "json"


@pytest.mark.asyncio
async def test_ollama_reasoning_off_appends_no_think(monkeypatch):
    captured: dict[str, dict] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content or b"{}")
        return httpx.Response(200, json={"response": "{}"})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))

    call = ollama_llm_structured(
        model="qwen3:8b",
        base_url="http://localhost:11434",
        reasoning_mode="off",
    )
    await call("hello")
    assert "/no_think" in captured["body"]["prompt"]


# ---------------------------------------------------------------------------
# End-to-end: response parsing back into LLMResponse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_returns_llmresponse_with_thinking_split(monkeypatch):
    raw = '<thinking>reason</thinking>\n{"v": 1}'

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))

    call = openai_llm_structured(
        model="gpt-4o",
        api_key="EMPTY",
        base_url="http://localhost",
    )
    result = await call("hi")
    assert result.thinking == "reason"
    assert result.content == '{"v": 1}'
    assert result.raw == raw
