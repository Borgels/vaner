# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from vaner.external_state.models import (
    ExternalStateFreshnessClass,
    ExternalStateSensitivity,
    ExternalStateSnapshot,
    FinanceCapability,
)
from vaner.models.config import ExternalStateConfig


class ModelNativeSearchProvider(Protocol):
    """Runtime hook for model/client-native search tools.

    Vaner core deliberately does not know whether the implementation is a
    hosted model tool, a browser/search MCP server, or a local search gateway.
    The runtime may inject an implementation when that capability is available.
    """

    async def search_news(
        self,
        query: str,
        *,
        topics: list[str],
        lookback_days: int,
        limit: int,
    ) -> ExternalStateSnapshot | None:
        ...


def build_model_search_news_snapshot(
    *,
    provider_id: str,
    query: str,
    payload: dict[str, Any],
    source_tool: str = "model_native_search",
    ttl_seconds: float = 900.0,
) -> ExternalStateSnapshot:
    return ExternalStateSnapshot.build(
        provider_id=provider_id,
        capability=FinanceCapability.SEARCH_NEWS.value,
        query_key=f"news:{query[:200]}",
        source_tool=source_tool,
        freshness_class=ExternalStateFreshnessClass.NEWS_SNAPSHOT,
        sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
        payload=payload,
        captured_at=time.time(),
        ttl_seconds=ttl_seconds,
    )


class OllamaWebSearchProvider:
    """Adapter for Ollama's web search API.

    This uses Ollama's provider-side search endpoint, not the local model chat
    endpoint. It is opt-in and requires an API key configured through env.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://ollama.com/api",
        max_results: int = 5,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_results = max(1, min(int(max_results), 10))
        self.timeout_seconds = float(timeout_seconds)

    async def search_news(
        self,
        query: str,
        *,
        topics: list[str],
        lookback_days: int,
        limit: int,
    ) -> ExternalStateSnapshot | None:
        search_query = _search_query(query, topics=topics, lookback_days=lookback_days)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/web_search",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"query": search_query, "max_results": min(int(limit), self.max_results)},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            payload = {"result": payload}
        return build_model_search_news_snapshot(
            provider_id="ollama_web_search",
            query=query,
            source_tool="ollama.web_search",
            payload={
                "query": search_query,
                "topics": topics,
                "lookback_days": lookback_days,
                "results": payload.get("results", payload),
            },
        )


class BraveWebSearchProvider:
    """Adapter for Brave Search's web endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.search.brave.com/res/v1",
        max_results: int = 5,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_results = max(1, min(int(max_results), 10))
        self.timeout_seconds = float(timeout_seconds)

    async def search_news(
        self,
        query: str,
        *,
        topics: list[str],
        lookback_days: int,
        limit: int,
    ) -> ExternalStateSnapshot | None:
        search_query = _search_query(query, topics=topics, lookback_days=lookback_days)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                params={
                    "q": search_query,
                    "count": min(int(limit), self.max_results),
                    "search_lang": "en",
                    "spellcheck": 1,
                },
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            payload = {"result": payload}
        return build_model_search_news_snapshot(
            provider_id="brave_search",
            query=query,
            source_tool="brave.web_search",
            payload={
                "query": search_query,
                "topics": topics,
                "lookback_days": lookback_days,
                "results": _brave_results(payload),
            },
        )


def model_search_provider_from_config(config: ExternalStateConfig) -> ModelNativeSearchProvider | None:
    search = config.model_native_search
    if not search.enabled:
        return None
    provider = search.provider
    api_key_env = _api_key_env(provider, search.api_key_env)
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        return None
    if provider == "ollama":
        base_url = search.base_url or "https://ollama.com/api"
        if _hostname(base_url) == "api.search.brave.com":
            base_url = "https://ollama.com/api"
        return OllamaWebSearchProvider(
            api_key=api_key,
            base_url=base_url,
            max_results=search.max_results,
            timeout_seconds=search.timeout_seconds,
        )
    if provider == "brave":
        base_url = search.base_url or "https://api.search.brave.com/res/v1"
        if _hostname(base_url) == "ollama.com":
            base_url = "https://api.search.brave.com/res/v1"
        return BraveWebSearchProvider(
            api_key=api_key,
            base_url=base_url,
            max_results=search.max_results,
            timeout_seconds=search.timeout_seconds,
        )
    return None


def _api_key_env(provider: str, configured: str) -> str:
    if provider == "brave" and (not configured or configured == "OLLAMA_API_KEY"):
        return "BRAVE_SEARCH_API_KEY"
    if provider == "ollama" and not configured:
        return "OLLAMA_API_KEY"
    return configured


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _brave_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for bucket in ("news", "web"):
        section = payload.get(bucket)
        if not isinstance(section, dict):
            continue
        items = section.get("results")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "type": bucket,
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "description": item.get("description"),
                    "age": item.get("age"),
                    "page_age": item.get("page_age"),
                }
            )
    return results or [payload]


def _search_query(query: str, *, topics: list[str], lookback_days: int) -> str:
    terms = ", ".join(topic for topic in topics if topic)
    suffix = f" recent {terms} news last {max(1, int(lookback_days))} days".strip()
    return f"{query[:300]} {suffix}".strip()[:380]
