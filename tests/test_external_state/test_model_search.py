# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

import httpx
import pytest

from vaner.external_state import FinanceCapability, model_search_provider_from_config
from vaner.external_state.model_search import BraveWebSearchProvider, OllamaWebSearchProvider
from vaner.models.config import ExternalStateConfig, ModelNativeSearchConfig

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _stub_async_client(handler):
    def _factory(**_kwargs):
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

    return _factory


@pytest.mark.asyncio
async def test_ollama_web_search_provider_builds_news_snapshot(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["body"] = json.loads(req.content or b"{}")
        captured["auth"] = req.headers.get("Authorization")
        return httpx.Response(200, json={"results": [{"title": "headline", "url": "https://example.test"}]})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))
    provider = OllamaWebSearchProvider(api_key="secret", base_url="https://ollama.com/api", max_results=4)

    snapshot = await provider.search_news("macro catalyst", topics=["markets", "finance"], lookback_days=7, limit=10)

    assert snapshot.capability == FinanceCapability.SEARCH_NEWS.value
    assert snapshot.provider_id == "ollama_web_search"
    assert snapshot.source_tool == "ollama.web_search"
    assert captured["path"] == "/api/web_search"
    assert captured["auth"] == "Bearer secret"
    assert captured["body"] == {
        "query": "macro catalyst recent markets, finance news last 7 days",
        "max_results": 4,
    }


@pytest.mark.asyncio
async def test_brave_web_search_provider_builds_news_snapshot(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["params"] = dict(req.url.params)
        captured["token"] = req.headers.get("X-Subscription-Token")
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "market headline",
                            "url": "https://example.test/news",
                            "description": "fresh market context",
                            "age": "2 hours ago",
                        }
                    ]
                }
            },
        )

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))
    provider = BraveWebSearchProvider(api_key="secret", base_url="https://api.search.brave.com/res/v1", max_results=3)

    snapshot = await provider.search_news("earnings news", topics=["markets", "finance"], lookback_days=3, limit=10)

    assert snapshot.capability == FinanceCapability.SEARCH_NEWS.value
    assert snapshot.provider_id == "brave_search"
    assert snapshot.source_tool == "brave.web_search"
    assert captured["path"] == "/res/v1/web/search"
    assert captured["token"] == "secret"
    assert captured["params"]["count"] == "3"
    assert captured["params"]["q"] == "earnings news recent markets, finance news last 3 days"
    assert snapshot.payload["results"] == [
        {
            "type": "web",
            "title": "market headline",
            "url": "https://example.test/news",
            "description": "fresh market context",
            "age": "2 hours ago",
            "page_age": None,
        }
    ]


def test_model_search_factory_uses_brave_env_without_exposing_key(monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret")
    config = ExternalStateConfig(
        model_native_search=ModelNativeSearchConfig(
            enabled=True,
            provider="brave",
            api_key_env="BRAVE_SEARCH_API_KEY",
            max_results=8,
        )
    )

    provider = model_search_provider_from_config(config)

    assert isinstance(provider, BraveWebSearchProvider)
    assert provider.max_results == 8


def test_model_search_factory_returns_none_without_key(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    config = ExternalStateConfig(model_native_search=ModelNativeSearchConfig(enabled=True, provider="brave"))

    assert model_search_provider_from_config(config) is None


@pytest.mark.asyncio
async def test_brave_web_search_caps_long_queries(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["q"] = req.url.params["q"]
        return httpx.Response(200, json={"web": {"results": []}})

    monkeypatch.setattr(httpx, "AsyncClient", _stub_async_client(_handler))
    provider = BraveWebSearchProvider(api_key="secret", base_url="https://api.search.brave.com/res/v1", max_results=3)

    await provider.search_news("very long prompt " * 80, topics=["markets", "finance"], lookback_days=7, limit=10)

    assert len(str(captured["q"])) <= 380
