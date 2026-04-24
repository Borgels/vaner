# SPDX-License-Identifier: Apache-2.0
"""Tests for the reasoning_mode enum on the OpenAI/Ollama structured adapters.

Verifies the four reasoning_mode values produce correct request shaping and
response-validation behaviour:

- ``off``: inject ``enable_thinking=false`` (openai) or ``/no_think`` (ollama);
  reject responses that still emit a thinking preamble.
- ``allowed``: thinking preamble permitted; adapter strips it.
- ``required``: provider must emit a thinking preamble; error otherwise.
- ``provider_default``: trust the adapter/provider default (no added opinion).
"""

from __future__ import annotations

import json

import httpx
import pytest

from vaner.clients.openai import openai_llm_structured

_real_async_client = httpx.AsyncClient


def _stub(handler):
    def _factory(**_kwargs):
        return _real_async_client(transport=httpx.MockTransport(handler))

    return _factory


def _openai_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# ---------------------------------------------------------------------------
# allowed — thinking captured, content stripped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_allowed_captures_and_strips_thinking(monkeypatch):
    raw = '<thinking>why this</thinking>\n{"ok":true}'

    def _handler(_req):
        return httpx.Response(200, json=_openai_response(raw))

    monkeypatch.setattr(httpx, "AsyncClient", _stub(_handler))

    call = openai_llm_structured(
        model="claude-style",
        api_key="EMPTY",
        base_url="http://localhost",
        reasoning_mode="allowed",
    )
    result = await call("hi")
    assert result.thinking == "why this"
    assert result.content == '{"ok":true}'


# ---------------------------------------------------------------------------
# required — must see a preamble
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_required_accepts_response_with_thinking(monkeypatch):
    raw = "<think>I reason</think>\n[]"

    def _handler(_req):
        return httpx.Response(200, json=_openai_response(raw))

    monkeypatch.setattr(httpx, "AsyncClient", _stub(_handler))

    call = openai_llm_structured(
        model="deepseek-style",
        api_key="EMPTY",
        base_url="http://localhost",
        reasoning_mode="required",
    )
    result = await call("hi")
    assert result.thinking == "I reason"


@pytest.mark.asyncio
async def test_mode_required_errors_without_thinking(monkeypatch):
    raw = '{"straight": "json"}'

    def _handler(_req):
        return httpx.Response(200, json=_openai_response(raw))

    monkeypatch.setattr(httpx, "AsyncClient", _stub(_handler))

    call = openai_llm_structured(
        model="any",
        api_key="EMPTY",
        base_url="http://localhost",
        reasoning_mode="required",
    )
    with pytest.raises(ValueError, match="required"):
        await call("hi")


# ---------------------------------------------------------------------------
# off — suppress and enforce absence of thinking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_off_accepts_bare_json(monkeypatch):
    def _handler(_req):
        return httpx.Response(200, json=_openai_response('{"ok": true}'))

    monkeypatch.setattr(httpx, "AsyncClient", _stub(_handler))

    call = openai_llm_structured(
        model="qwen3",
        api_key="EMPTY",
        base_url="http://localhost",
        reasoning_mode="off",
    )
    result = await call("hi")
    assert result.thinking == ""
    assert result.content == '{"ok": true}'


@pytest.mark.asyncio
async def test_mode_off_errors_when_thinking_still_present(monkeypatch):
    raw = '<thinking>still reasoning</thinking>\n{"v":1}'

    def _handler(_req):
        return httpx.Response(200, json=_openai_response(raw))

    monkeypatch.setattr(httpx, "AsyncClient", _stub(_handler))

    call = openai_llm_structured(
        model="qwen3",
        api_key="EMPTY",
        base_url="http://localhost",
        reasoning_mode="off",
    )
    with pytest.raises(ValueError, match="off"):
        await call("hi")


# ---------------------------------------------------------------------------
# provider_default — pass-through, no validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_provider_default_passes_thinking_through(monkeypatch):
    raw = '<thinking>unbothered</thinking>\n{"done":true}'

    def _handler(_req):
        return httpx.Response(200, json=_openai_response(raw))

    monkeypatch.setattr(httpx, "AsyncClient", _stub(_handler))

    call = openai_llm_structured(
        model="any",
        api_key="EMPTY",
        base_url="http://localhost",
        reasoning_mode="provider_default",
    )
    result = await call("hi")
    assert result.thinking == "unbothered"
    assert result.content == '{"done":true}'


@pytest.mark.asyncio
async def test_mode_provider_default_accepts_bare_json(monkeypatch):
    def _handler(_req):
        return httpx.Response(200, json=_openai_response('{"bare":1}'))

    monkeypatch.setattr(httpx, "AsyncClient", _stub(_handler))

    call = openai_llm_structured(
        model="any",
        api_key="EMPTY",
        base_url="http://localhost",
        reasoning_mode="provider_default",
    )
    result = await call("hi")
    assert result.thinking == ""
    assert result.content == '{"bare":1}'


# ---------------------------------------------------------------------------
# Outgoing-request shape differs per mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_off_vs_allowed_differ_in_outgoing_request(monkeypatch):
    captured: list[dict] = []

    def _handler(req):
        captured.append(json.loads(req.content or b"{}"))
        return httpx.Response(200, json=_openai_response('{"ok":true}'))

    monkeypatch.setattr(httpx, "AsyncClient", _stub(_handler))

    for mode in ("off", "allowed"):
        call = openai_llm_structured(
            model="qwen3",
            api_key="EMPTY",
            base_url="http://localhost",
            reasoning_mode=mode,  # type: ignore[arg-type]
        )
        await call("hi")

    off_body, allowed_body = captured
    off_tpl = off_body.get("chat_template_kwargs", {})
    allowed_tpl = allowed_body.get("chat_template_kwargs", {})
    assert off_tpl.get("enable_thinking") is False
    assert "enable_thinking" not in allowed_tpl
