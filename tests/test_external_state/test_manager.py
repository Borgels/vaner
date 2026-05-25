# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.external_state import (
    ExternalStateFreshnessClass,
    ExternalStateManager,
    ExternalStateSensitivity,
    ExternalStateSnapshot,
    FinanceCapability,
)
from vaner.integrations.mcp_consumer.client import McpToolCallResult
from vaner.models.config import ExternalStateConfig, FinanceExternalStateConfig, McpConsumerConfig


class _FakeClient:
    def __init__(self, *, payload: dict | None = None, payloads: list[dict] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.payload = payload
        self.payloads = list(payloads or [])

    async def call_tool(self, tool_name: str, args: dict) -> McpToolCallResult:
        self.calls.append((tool_name, args))
        if self.payloads:
            return McpToolCallResult(ok=True, payload=self.payloads.pop(0))
        return McpToolCallResult(ok=True, payload=self.payload or {"tool": tool_name, "args": args})


class _FakeModelSearchProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def search_news(
        self,
        query: str,
        *,
        topics: list[str],
        lookback_days: int,
        limit: int,
    ) -> ExternalStateSnapshot | None:
        self.calls.append(
            {
                "query": query,
                "topics": topics,
                "lookback_days": lookback_days,
                "limit": limit,
            }
        )
        return ExternalStateSnapshot.build(
            provider_id="model_search",
            capability=FinanceCapability.SEARCH_NEWS.value,
            query_key="model-news",
            source_tool="web_search",
            freshness_class=ExternalStateFreshnessClass.NEWS_SNAPSHOT,
            sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
            payload={"items": [{"title": "market catalyst"}]},
            ttl_seconds=900.0,
        )


class _FailingModelSearchProvider:
    async def search_news(self, *args, **kwargs) -> ExternalStateSnapshot | None:
        raise RuntimeError("search unavailable")


async def test_manager_maps_finance_capability_to_readonly_tool_snapshot() -> None:
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            max_calls_per_cycle=2,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                capability_tools={FinanceCapability.SCREEN_MARKET.value: "provider_screen_market"},
            ),
        )
    )
    client = _FakeClient()
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshot = await manager.call_finance_capability(
        FinanceCapability.SCREEN_MARKET,
        {"query": "options"},
        freshness_class=ExternalStateFreshnessClass.MARKET_SNAPSHOT,
        sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
    )

    assert snapshot is not None
    assert snapshot.capability == FinanceCapability.SCREEN_MARKET.value
    assert snapshot.source_tool == "provider_screen_market"
    assert client.calls == [("provider_screen_market", {"query": "options"})]


async def test_manager_blocks_account_capability_without_account_permission() -> None:
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                account_state_enabled=False,
                capability_tools={FinanceCapability.LIST_POSITIONS.value: "provider_list_positions"},
            ),
        )
    )
    client = _FakeClient()
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshot = await manager.call_finance_capability(FinanceCapability.LIST_POSITIONS)

    assert snapshot is None
    assert client.calls == []


async def test_manager_treats_mcp_tool_error_payload_as_no_snapshot() -> None:
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                capability_tools={FinanceCapability.SCREEN_MARKET.value: "provider_screen_market"},
            ),
        )
    )
    client = _FakeClient(payload={"isError": True, "content": [{"type": "text", "text": "provider error"}]})
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshot = await manager.call_finance_capability(FinanceCapability.SCREEN_MARKET, {"preset": "top_gainers"})

    assert snapshot is None
    assert client.calls == [("provider_screen_market", {"preset": "top_gainers"})]


async def test_collect_finance_snapshots_uses_structured_screening_criteria() -> None:
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            max_calls_per_cycle=2,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                capability_tools={FinanceCapability.SCREEN_MARKET.value: "provider_screen_market"},
            ),
        )
    )
    client = _FakeClient()
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshots = await manager.collect_finance_snapshots(["prepare a Denmark premarket losers finance brief"])

    assert len(snapshots) == 1
    assert snapshots[0].capability == FinanceCapability.SCREEN_MARKET.value
    assert snapshots[0].freshness_class == ExternalStateFreshnessClass.MARKET_SNAPSHOT
    assert snapshots[0].sensitivity_class == ExternalStateSensitivity.PUBLIC_MARKET_ONLY
    assert client.calls == [
        (
            "provider_screen_market",
            {
                "preset": "premarket_losers",
                "market": "denmark",
                "assetType": "Stock",
                "limit": 10,
                "maxInstruments": 100,
            },
        )
    ]


async def test_collect_finance_snapshots_rotates_trading_exploration_branches(monkeypatch) -> None:
    monkeypatch.setattr("vaner.external_state.manager.time.time", lambda: 120.0)
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            max_calls_per_cycle=4,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                capability_tools={FinanceCapability.SCREEN_MARKET.value: "provider_screen_market"},
            ),
        )
    )
    client = _FakeClient()
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshots = await manager.collect_finance_snapshots(["explore trading setup branches for US stocks"])

    assert [snapshot.capability for snapshot in snapshots] == [
        FinanceCapability.SCREEN_MARKET.value,
        FinanceCapability.SCREEN_MARKET.value,
        FinanceCapability.SCREEN_MARKET.value,
    ]
    assert [args["preset"] for _, args in client.calls] == ["top_gainers", "premarket_gainers", "premarket_losers"]
    assert [args["market"] for _, args in client.calls] == ["us", "us", "us"]


async def test_collect_finance_snapshots_explores_option_strategy_screen_when_available() -> None:
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            max_calls_per_cycle=4,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                account_state_enabled=True,
                capability_tools={
                    FinanceCapability.SCREEN_MARKET.value: "provider_screen_market",
                    FinanceCapability.LIST_ACCOUNTS.value: "provider_list_accounts",
                    FinanceCapability.SCREEN_OPTION_STRATEGIES.value: "provider_screen_option_strategies",
                },
            ),
        )
    )
    client = _FakeClient(
        payloads=[
            {"tool": "provider_screen_market"},
            {"content": [{"type": "text", "text": '{"Data":[{"AccountKey":"account-1"}]}'}]},
            {"tool": "provider_screen_option_strategies"},
        ]
    )
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshots = await manager.collect_finance_snapshots(["screen option spread candidates for US"])

    assert [snapshot.capability for snapshot in snapshots] == [
        FinanceCapability.SCREEN_MARKET.value,
        FinanceCapability.LIST_ACCOUNTS.value,
        FinanceCapability.SCREEN_OPTION_STRATEGIES.value,
    ]
    assert client.calls[0][0] == "provider_screen_market"
    assert client.calls[1] == ("provider_list_accounts", {})
    assert client.calls[2][0] == "provider_screen_option_strategies"
    assert client.calls[2][1]["accountKey"] == "account-1"
    assert client.calls[2][1]["underlyingPreset"] == "top_gainers"
    assert snapshots[2].freshness_class == ExternalStateFreshnessClass.DERIVED_ANALYSIS
    assert snapshots[2].sensitivity_class == ExternalStateSensitivity.MIXED_SENSITIVE


async def test_collect_finance_snapshots_skips_option_strategy_without_account_permission() -> None:
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            max_calls_per_cycle=4,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                account_state_enabled=False,
                capability_tools={
                    FinanceCapability.SCREEN_MARKET.value: "provider_screen_market",
                    FinanceCapability.LIST_ACCOUNTS.value: "provider_list_accounts",
                    FinanceCapability.SCREEN_OPTION_STRATEGIES.value: "provider_screen_option_strategies",
                },
            ),
        )
    )
    client = _FakeClient()
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshots = await manager.collect_finance_snapshots(["screen option spread candidates for US"])

    assert [snapshot.capability for snapshot in snapshots] == [FinanceCapability.SCREEN_MARKET.value]
    assert client.calls == [
        (
            "provider_screen_market",
            {
                "preset": "top_gainers",
                "market": "us",
                "assetType": "Stock",
                "limit": 10,
                "maxInstruments": 100,
            },
        )
    ]


async def test_collect_finance_snapshots_routes_news_to_non_primary_provider() -> None:
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            max_calls_per_cycle=4,
            consumers={
                "market": McpConsumerConfig(allowed_tools=["provider_screen_market"]),
                "search": McpConsumerConfig(allowed_tools=["web_search"]),
            },
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="market",
                market_data_enabled=True,
                capability_tools={
                    FinanceCapability.SCREEN_MARKET.value: "provider_screen_market",
                    FinanceCapability.SEARCH_NEWS.value: "web_search",
                },
            ),
        )
    )
    market_client = _FakeClient()
    search_client = _FakeClient()
    manager._clients["market"] = market_client  # type: ignore[assignment]
    manager._clients["search"] = search_client  # type: ignore[assignment]

    snapshots = await manager.collect_finance_snapshots(["earnings news catalyst for US stocks"])

    assert [snapshot.capability for snapshot in snapshots] == [
        FinanceCapability.SCREEN_MARKET.value,
        FinanceCapability.SEARCH_NEWS.value,
    ]
    assert market_client.calls == [
        (
            "provider_screen_market",
            {
                "preset": "top_gainers",
                "market": "us",
                "assetType": "Stock",
                "limit": 10,
                "maxInstruments": 100,
            },
        )
    ]
    assert search_client.calls == [
        (
            "web_search",
            {
                "query": "earnings news catalyst for US stocks",
                "topics": ["markets", "finance"],
                "lookback_days": 7,
                "limit": 10,
            },
        )
    ]


async def test_collect_finance_snapshots_uses_model_search_fallback_when_structured_news_missing() -> None:
    model_search = _FakeModelSearchProvider()
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            max_calls_per_cycle=3,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                capability_tools={FinanceCapability.SCREEN_MARKET.value: "provider_screen_market"},
            ),
        ),
        model_search_provider=model_search,
    )
    client = _FakeClient()
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshots = await manager.collect_finance_snapshots(["macro news catalyst for options"])

    assert [snapshot.capability for snapshot in snapshots] == [
        FinanceCapability.SCREEN_MARKET.value,
        FinanceCapability.SEARCH_NEWS.value,
    ]
    assert snapshots[1].provider_id == "model_search"
    assert snapshots[1].freshness_class == ExternalStateFreshnessClass.NEWS_SNAPSHOT
    assert snapshots[1].sensitivity_class == ExternalStateSensitivity.PUBLIC_MARKET_ONLY
    assert model_search.calls == [
        {
            "query": "macro news catalyst for options",
            "topics": ["markets", "finance"],
            "lookback_days": 7,
            "limit": 10,
        }
    ]


async def test_collect_finance_snapshots_prefers_structured_news_over_model_search_fallback() -> None:
    model_search = _FakeModelSearchProvider()
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            max_calls_per_cycle=3,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                capability_tools={
                    FinanceCapability.SCREEN_MARKET.value: "provider_screen_market",
                    FinanceCapability.SEARCH_NEWS.value: "provider_search_news",
                },
            ),
        ),
        model_search_provider=model_search,
    )
    client = _FakeClient()
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshots = await manager.collect_finance_snapshots(["market headlines and sentiment"])

    assert [snapshot.provider_id for snapshot in snapshots] == ["provider", "provider"]
    assert client.calls[1][0] == "provider_search_news"
    assert model_search.calls == []


async def test_model_search_fallback_respects_external_state_opt_in() -> None:
    model_search = _FakeModelSearchProvider()
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=False,
            max_calls_per_cycle=3,
            finance=FinanceExternalStateConfig(
                enabled=True,
                market_data_enabled=True,
            ),
        ),
        model_search_provider=model_search,
    )

    snapshots = await manager.collect_finance_snapshots(["macro news catalyst"])

    assert snapshots == []
    assert model_search.calls == []


async def test_model_search_failure_downgrades_to_no_news_snapshot() -> None:
    manager = ExternalStateManager(
        ExternalStateConfig(
            enabled=True,
            max_calls_per_cycle=3,
            finance=FinanceExternalStateConfig(
                enabled=True,
                provider="provider",
                market_data_enabled=True,
                capability_tools={FinanceCapability.SCREEN_MARKET.value: "provider_screen_market"},
            ),
        ),
        model_search_provider=_FailingModelSearchProvider(),
    )
    client = _FakeClient()
    manager._clients["provider"] = client  # type: ignore[assignment]

    snapshots = await manager.collect_finance_snapshots(["macro news catalyst"])

    assert [snapshot.capability for snapshot in snapshots] == [FinanceCapability.SCREEN_MARKET.value]
