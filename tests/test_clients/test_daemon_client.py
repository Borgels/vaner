# SPDX-License-Identifier: Apache-2.0
"""WS3.5 — tests for the shared VanerDaemonClient HTTP contract.

Uses httpx.MockTransport to exercise every endpoint + error path without
standing up a real daemon.
"""

from __future__ import annotations

import httpx
import pytest

from vaner.clients.daemon import (
    DEFAULT_BASE_URL,
    VanerDaemonClient,
    VanerDaemonNotFound,
    VanerDaemonUnavailable,
)


def _client(handler) -> VanerDaemonClient:
    """Build a VanerDaemonClient backed by a MockTransport handler."""
    transport = httpx.MockTransport(handler)
    httpx_client = httpx.AsyncClient(transport=transport, base_url=DEFAULT_BASE_URL)
    return VanerDaemonClient(client=httpx_client)


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_returns_body():
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/status"
        return httpx.Response(200, json={"health": "ok", "mcp": {}, "compute": {}})

    client = _client(_handler)
    body = await client.get_status()
    assert body["health"] == "ok"


@pytest.mark.asyncio
async def test_get_status_raises_unavailable_on_5xx():
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client = _client(_handler)
    with pytest.raises(VanerDaemonUnavailable):
        await client.get_status()


# ---------------------------------------------------------------------------
# get_predictions_active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_predictions_active_returns_body():
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"predictions": [{"id": "pred-1", "spec": {"label": "x"}}]},
        )

    client = _client(_handler)
    body = await client.get_predictions_active()
    assert len(body["predictions"]) == 1
    assert body["predictions"][0]["id"] == "pred-1"


@pytest.mark.asyncio
async def test_get_predictions_active_404_raises_unavailable():
    """Pre-Phase-4 daemon returning 404 should surface as Unavailable (not a
    generic HTTPStatusError)."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = _client(_handler)
    with pytest.raises(VanerDaemonUnavailable, match="predictions/active"):
        await client.get_predictions_active()


@pytest.mark.asyncio
async def test_get_predictions_active_transport_error_raises_unavailable():
    def _handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client(_handler)
    with pytest.raises(VanerDaemonUnavailable, match="unreachable"):
        await client.get_predictions_active()


# ---------------------------------------------------------------------------
# get_prediction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_prediction_without_include_omits_query_param():
    def _handler(request: httpx.Request) -> httpx.Response:
        assert "include" not in request.url.params
        return httpx.Response(200, json={"id": "p"})

    client = _client(_handler)
    body = await client.get_prediction("p")
    assert body["id"] == "p"


@pytest.mark.asyncio
async def test_get_prediction_include_draft_briefing_sets_query_param():
    seen: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["include"] = request.url.params.get("include", "")
        return httpx.Response(200, json={"id": "p"})

    client = _client(_handler)
    await client.get_prediction("p", include=["draft", "briefing"])
    assert seen["include"] == "draft,briefing"


@pytest.mark.asyncio
async def test_get_prediction_404_raises_not_found():
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "nope"})

    client = _client(_handler)
    with pytest.raises(VanerDaemonNotFound):
        await client.get_prediction("missing")


# ---------------------------------------------------------------------------
# adopt_prediction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adopt_prediction_returns_parsed_resolution():
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/predictions/pid-1/adopt"
        return httpx.Response(
            200,
            json={
                "intent": "Do the thing",
                "confidence": 0.8,
                "summary": "adopted",
                "evidence": [],
                "provenance": {"mode": "predictive_hit"},
                "resolution_id": "adopt-pid-1",
                "prepared_briefing": "## Brief\nfoo",
                "adopted_from_prediction_id": "pid-1",
            },
        )

    client = _client(_handler)
    resolution = await client.adopt_prediction("pid-1")
    assert resolution.intent == "Do the thing"
    assert resolution.adopted_from_prediction_id == "pid-1"
    assert resolution.prepared_briefing is not None


@pytest.mark.asyncio
async def test_adopt_prediction_400_raises_value_error():
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "invalid_input", "message": "bad id"})

    client = _client(_handler)
    with pytest.raises(ValueError, match="bad id"):
        await client.adopt_prediction("   ")


@pytest.mark.asyncio
async def test_adopt_prediction_404_raises_not_found():
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "not_found", "message": "nope"})

    client = _client(_handler)
    with pytest.raises(VanerDaemonNotFound):
        await client.adopt_prediction("ghost")


@pytest.mark.asyncio
async def test_adopt_prediction_409_raises_unavailable():
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"code": "engine_unavailable", "message": "no engine yet"},
        )

    client = _client(_handler)
    with pytest.raises(VanerDaemonUnavailable, match="no engine yet"):
        await client.adopt_prediction("pid-1")


@pytest.mark.asyncio
async def test_adopt_prediction_5xx_raises_unavailable():
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    client = _client(_handler)
    with pytest.raises(VanerDaemonUnavailable):
        await client.adopt_prediction("pid-1")


# ---------------------------------------------------------------------------
# Client construction defaults
# ---------------------------------------------------------------------------


def test_default_base_url_is_localhost_8473():
    client = VanerDaemonClient()
    assert client._base == "http://127.0.0.1:8473"


def test_base_url_trailing_slash_is_stripped():
    client = VanerDaemonClient(base_url="http://example.com/")
    assert client._base == "http://example.com"
