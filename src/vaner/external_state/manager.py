# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from vaner.external_state.model_search import ModelNativeSearchProvider
from vaner.external_state.models import (
    ExternalStateFreshnessClass,
    ExternalStateSensitivity,
    ExternalStateSnapshot,
    FinanceCapability,
)
from vaner.integrations.mcp_consumer.client import McpConsumerClient
from vaner.integrations.mcp_consumer.safety import ReadOnlyToolPolicy
from vaner.models.config import ExternalStateConfig

_ACCOUNT_CAPABILITIES = {
    FinanceCapability.LIST_ACCOUNTS.value,
    FinanceCapability.GET_BALANCE.value,
    FinanceCapability.LIST_POSITIONS.value,
    FinanceCapability.LIST_ORDERS.value,
    FinanceCapability.LIST_ALERTS.value,
    FinanceCapability.REVIEW_STRATEGY_POSITIONS.value,
}

_NEWS_TERMS = {
    "catalyst",
    "earnings",
    "fed",
    "headline",
    "headlines",
    "macro",
    "news",
    "sentiment",
}

_OPTION_TERMS = {
    "call",
    "covered call",
    "delta",
    "expiry",
    "greek",
    "iv",
    "option",
    "premium",
    "put",
    "spread",
    "strike",
    "theta",
    "vega",
    "volatility",
}

_TRADING_EXPLORATION_TERMS = {
    "branch",
    "branches",
    "explore",
    "setup",
    "setups",
    "trade",
    "trades",
    "trading",
}

_MARKET_EXPLORATION_BRANCHES: tuple[dict[str, Any], ...] = (
    {"preset": "top_gainers", "market": "us", "assetType": "Stock", "limit": 10, "maxInstruments": 100},
    {"preset": "top_losers", "market": "us", "assetType": "Stock", "limit": 10, "maxInstruments": 100},
    {"preset": "premarket_gainers", "market": "us", "assetType": "Stock", "limit": 10, "maxInstruments": 100},
    {"preset": "premarket_losers", "market": "us", "assetType": "Stock", "limit": 10, "maxInstruments": 100},
    {"preset": "top_gainers", "market": "us_nasdaq", "assetType": "Stock", "limit": 10, "maxInstruments": 100},
    {"preset": "top_losers", "market": "us_nasdaq", "assetType": "Stock", "limit": 10, "maxInstruments": 100},
    {"preset": "top_gainers", "market": "us_nyse", "assetType": "Stock", "limit": 10, "maxInstruments": 100},
    {"preset": "top_losers", "market": "us_nyse", "assetType": "Stock", "limit": 10, "maxInstruments": 100},
)


class ExternalStateManager:
    """Owns configured external-state MCP consumers for one Vaner engine."""

    def __init__(self, config: ExternalStateConfig, *, model_search_provider: ModelNativeSearchProvider | None = None) -> None:
        self.config = config
        self._model_search_provider = model_search_provider
        self._clients: dict[str, McpConsumerClient] = {}
        self._cycle_calls = 0

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    async def start(self) -> None:
        if not self.enabled:
            return
        for name, consumer in self.config.consumers.items():
            policy = ReadOnlyToolPolicy(allowed_tools=set(consumer.allowed_tools), tool_risks=dict(consumer.tool_risks))
            client = McpConsumerClient(
                server_name=name,
                transport=consumer.transport,
                command=consumer.command,
                args=consumer.args,
                url=consumer.url,
                env=consumer.env,
                timeout_ms=consumer.timeout_ms,
                policy=policy,
            )
            await client.start()
            self._clients[name] = client

    async def stop(self) -> None:
        for client in list(self._clients.values()):
            await client.stop()
        self._clients.clear()

    def reset_cycle_budget(self) -> None:
        self._cycle_calls = 0

    def _check_cycle_budget(self) -> bool:
        if self.config.max_calls_per_cycle <= 0:
            return False
        if self._cycle_calls >= self.config.max_calls_per_cycle:
            return False
        self._cycle_calls += 1
        return True

    async def call_finance_capability(
        self,
        capability: FinanceCapability | str,
        args: dict[str, Any] | None = None,
        *,
        query_key: str = "",
        freshness_class: ExternalStateFreshnessClass = ExternalStateFreshnessClass.MARKET_SNAPSHOT,
        sensitivity_class: ExternalStateSensitivity = ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
        ttl_seconds: float | None = 300.0,
    ) -> ExternalStateSnapshot | None:
        if not self.enabled or not self.config.finance.enabled:
            return None
        capability_name = capability.value if isinstance(capability, FinanceCapability) else str(capability)
        if capability_name in _ACCOUNT_CAPABILITIES and not self.config.finance.account_state_enabled:
            return None
        if capability_name not in _ACCOUNT_CAPABILITIES and not self.config.finance.market_data_enabled:
            return None
        tool_name = self.config.finance.capability_tools.get(capability_name, "")
        if not tool_name:
            return None
        provider = self._provider_for_tool(tool_name)
        if not provider or provider not in self._clients:
            return None
        if not self._check_cycle_budget():
            return None
        result = await self._clients[provider].call_tool(tool_name, args or {})
        if not result.ok:
            return None
        payload = _payload_to_jsonable(result.payload)
        if _payload_is_error(payload):
            return None
        return ExternalStateSnapshot.build(
            provider_id=provider,
            capability=capability_name,
            query_key=query_key,
            source_tool=tool_name,
            freshness_class=freshness_class,
            sensitivity_class=sensitivity_class,
            payload=payload,
            captured_at=time.time(),
            ttl_seconds=ttl_seconds,
        )

    async def collect_finance_snapshots(self, recent_queries: list[str]) -> list[ExternalStateSnapshot]:
        query_text = " ".join(recent_queries[-5:]).strip()
        if not query_text:
            return []
        snapshots: list[ExternalStateSnapshot] = []
        screen_criteria = _screen_market_criteria(query_text)
        screen = await self.call_finance_capability(
            FinanceCapability.SCREEN_MARKET,
            screen_criteria,
            query_key=_safe_query_key(query_text),
            freshness_class=ExternalStateFreshnessClass.MARKET_SNAPSHOT,
            sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
            ttl_seconds=300.0,
        )
        if screen is not None:
            snapshots.append(screen)
        for branch_index, branch_criteria in enumerate(_market_exploration_branches(query_text, screen_criteria)):
            branch = await self.call_finance_capability(
                FinanceCapability.SCREEN_MARKET,
                branch_criteria,
                query_key=_safe_query_key(f"market-branch:{branch_index}:{query_text}"),
                freshness_class=ExternalStateFreshnessClass.MARKET_SNAPSHOT,
                sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
                ttl_seconds=300.0,
            )
            if branch is not None:
                snapshots.append(branch)
        if _looks_option_related(query_text) and FinanceCapability.SCREEN_OPTION_STRATEGIES.value in self.config.finance.capability_tools:
            account_key: str | None = None
            if self.config.finance.account_state_enabled:
                accounts = await self.call_finance_capability(
                    FinanceCapability.LIST_ACCOUNTS,
                    {},
                    query_key="account_accounts",
                    freshness_class=ExternalStateFreshnessClass.ACCOUNT_SNAPSHOT,
                    sensitivity_class=ExternalStateSensitivity.ACCOUNT_SUMMARY,
                    ttl_seconds=120.0,
                )
                if accounts is not None:
                    snapshots.append(accounts)
                    account_key = _first_account_key(accounts.payload)
            if account_key is None:
                return snapshots
            strategies = await self.call_finance_capability(
                FinanceCapability.SCREEN_OPTION_STRATEGIES,
                _option_strategy_criteria(query_text, account_key=account_key),
                query_key=_safe_query_key(f"option-strategies:{query_text}"),
                freshness_class=ExternalStateFreshnessClass.DERIVED_ANALYSIS,
                sensitivity_class=ExternalStateSensitivity.MIXED_SENSITIVE,
                ttl_seconds=300.0,
            )
            if strategies is not None:
                snapshots.append(strategies)
        news = await self._collect_news_snapshot(query_text)
        if news is not None:
            snapshots.append(news)
        if self.config.finance.account_state_enabled:
            positions = await self.call_finance_capability(
                FinanceCapability.LIST_POSITIONS,
                {},
                query_key="account_positions",
                freshness_class=ExternalStateFreshnessClass.ACCOUNT_SNAPSHOT,
                sensitivity_class=ExternalStateSensitivity.POSITION_SPECIFIC,
                ttl_seconds=120.0,
            )
            if positions is not None:
                snapshots.append(positions)
        return snapshots

    async def _collect_news_snapshot(self, query_text: str) -> ExternalStateSnapshot | None:
        if not _looks_news_related(query_text):
            return None
        if not self.enabled or not self.config.finance.enabled or not self.config.finance.market_data_enabled:
            return None
        criteria = _news_search_criteria(query_text)
        if FinanceCapability.SEARCH_NEWS.value in self.config.finance.capability_tools:
            news = await self.call_finance_capability(
                FinanceCapability.SEARCH_NEWS,
                criteria,
                query_key=_safe_query_key(f"news:{query_text}"),
                freshness_class=ExternalStateFreshnessClass.NEWS_SNAPSHOT,
                sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
                ttl_seconds=900.0,
            )
            if news is not None:
                return news
        if self._model_search_provider is None or not self._check_cycle_budget():
            return None
        try:
            return await self._model_search_provider.search_news(
                str(criteria["query"]),
                topics=list(criteria["topics"]),
                lookback_days=int(criteria["lookback_days"]),
                limit=int(criteria["limit"]),
            )
        except Exception:
            return None

    def _provider_for_tool(self, tool_name: str) -> str:
        preferred = self.config.finance.provider
        if preferred and preferred in self._clients:
            consumer = self.config.consumers.get(preferred)
            if consumer is None or tool_name in set(consumer.allowed_tools):
                return preferred
        for provider, consumer in self.config.consumers.items():
            if provider in self._clients and tool_name in set(consumer.allowed_tools):
                return provider
        return preferred


def _payload_to_jsonable(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(mode="json")
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {"result": repr(payload)}
    return data if isinstance(data, dict) else {"result": data}


def _payload_is_error(payload: dict[str, Any]) -> bool:
    return payload.get("isError") is True


def _first_account_key(payload: dict[str, Any]) -> str | None:
    data = _payload_data(payload)
    queue: list[Any] = [data]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for key in ("AccountKey", "accountKey", "account_key"):
                value = current.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return None


def _payload_data(payload: dict[str, Any]) -> Any:
    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                text = item["text"].strip()
                if not text:
                    continue
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return payload
    return payload


def _safe_query_key(query: str) -> str:
    return hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]  # noqa: S324


def _looks_option_related(query: str) -> bool:
    text = query.lower()
    return any(term in text for term in _OPTION_TERMS)


def _looks_news_related(query: str) -> bool:
    text = query.lower()
    return any(term in text for term in _NEWS_TERMS)


def _looks_trading_exploration_related(query: str) -> bool:
    text = query.lower()
    return any(term in text for term in _TRADING_EXPLORATION_TERMS)


def _market_exploration_branches(query: str, primary: dict[str, Any]) -> list[dict[str, Any]]:
    if not _looks_trading_exploration_related(query):
        return []
    primary_key = (str(primary.get("preset") or ""), str(primary.get("market") or ""))
    candidates = [
        dict(branch)
        for branch in _MARKET_EXPLORATION_BRANCHES
        if (str(branch.get("preset") or ""), str(branch.get("market") or "")) != primary_key
    ]
    if not candidates:
        return []
    # Rotate the exploration branch every cycle instead of replaying the same
    # branch set forever. This keeps a stable refresh path while adding novelty
    # pressure across adjacent market regimes.
    bucket = int(time.time() // 120)
    offset = bucket % len(candidates)
    rotated = candidates[offset:] + candidates[:offset]
    return rotated[:2]


def _screen_market_criteria(query: str) -> dict[str, Any]:
    """Build provider-neutral structured criteria for the market-screen capability."""

    text = query.lower()
    premarket = "premarket" in text or "pre-market" in text
    downside = any(term in text for term in ("decliner", "decliners", "down", "loser", "losers", "selloff"))
    if premarket:
        preset = "premarket_losers" if downside else "premarket_gainers"
    else:
        preset = "top_losers" if downside else "top_gainers"

    market = "us"
    market_terms = (
        ("denmark", ("denmark", "danish", "copenhagen")),
        ("nordics", ("nordic", "nordics")),
        ("sweden", ("sweden", "swedish", "stockholm")),
        ("norway", ("norway", "norwegian", "oslo")),
        ("finland", ("finland", "finnish", "helsinki")),
        ("europe", ("europe", "european")),
        ("us_nasdaq", ("nasdaq",)),
        ("us_nyse", ("nyse",)),
    )
    for candidate, terms in market_terms:
        if any(term in text for term in terms):
            market = candidate
            break

    return {
        "preset": preset,
        "market": market,
        "assetType": "Stock",
        "limit": 10,
        "maxInstruments": 100,
    }


def _news_search_criteria(query: str) -> dict[str, Any]:
    return {
        "query": query[:300],
        "topics": ["markets", "finance"],
        "lookback_days": 7,
        "limit": 10,
    }


def _option_strategy_criteria(query: str, *, account_key: str) -> dict[str, Any]:
    screen = _screen_market_criteria(query)
    market = screen["market"]
    if market not in {"us", "us_nasdaq", "us_nyse"}:
        market = "us"
    text = query.lower()
    strategies: list[str]
    playbook = "income_30_60d"
    if "condor" in text:
        strategies = ["iron_condor"]
    elif any(term in text for term in ("bear", "downside", "put spread", "credit spread")):
        strategies = ["put_credit_spread", "call_credit_spread"]
    elif any(term in text for term in ("leap", "long call", "debit")):
        strategies = ["long_call", "debit_spread"]
        playbook = "long_term_directional"
    else:
        strategies = ["cash_secured_put", "put_credit_spread", "iron_condor"]
    return {
        "accountKey": account_key,
        "market": market,
        "underlyingUniverse": "auto",
        "underlyingPreset": screen["preset"],
        "playbook": playbook,
        "riskProfile": "balanced",
        "strategies": strategies,
        "maxUnderlyings": 10,
        "maxUnderlyingScan": 120,
        "maxSymbolsToPlan": 3,
        "maxPlans": 6,
        "includeAccountContext": True,
        "includeTechnicalContext": True,
        "includeVolatilityContext": True,
        "includeNewsContext": False,
        "requireGreeks": False,
        "riskBudgetPercent": 1,
    }
