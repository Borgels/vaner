# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.cli.commands.config import load_config
from vaner.external_state.configurator import (
    ProviderConfigInput,
    apply_discovery,
    build_discovery_payload,
    external_state_payload,
    save_finance_settings,
    save_model_native_search_settings,
    save_provider_config,
)
from vaner.integrations.mcp_consumer.safety import McpToolDescriptor
from vaner.models.config import McpConsumerConfig


def test_discovery_auto_selects_annotated_market_tools_only() -> None:
    consumer = McpConsumerConfig()
    payload = build_discovery_payload(
        "provider",
        [
            McpToolDescriptor(name="provider_get_quote", annotations={"readOnlyHint": True}),
            McpToolDescriptor(name="provider_list_positions", annotations={"readOnlyHint": True}),
            McpToolDescriptor(name="provider_place_order", annotations={"readOnlyHint": True}),
            McpToolDescriptor(name="provider_get_chart"),
        ],
        consumer,
        finance_market_enabled=True,
        finance_account_enabled=False,
    )

    selected = {tool.name for tool in payload.tools if tool.auto_selected}

    assert selected == {"provider_get_quote"}
    assert payload.applied_capability_tools == {"get_quote": "provider_get_quote"}
    assert {tool.name: tool.safety_reason for tool in payload.tools}["provider_place_order"] == "tool_name_matches_deny_pattern"
    assert {tool.name: tool.safety_reason for tool in payload.tools}["provider_get_chart"] == "missing_positive_readonly_classification"


def test_discovery_can_include_account_tools_when_permission_enabled() -> None:
    payload = build_discovery_payload(
        "provider",
        [McpToolDescriptor(name="provider_list_positions", annotations={"readOnlyHint": True})],
        McpConsumerConfig(),
        finance_market_enabled=True,
        finance_account_enabled=True,
    )

    assert payload.applied_allowed_tools == ["provider_list_positions"]
    assert payload.applied_capability_tools == {"list_positions": "provider_list_positions"}
    assert payload.tools[0].sensitivity_class == "position_specific"


def test_discovery_prefers_live_positions_over_history_when_mapping_capability() -> None:
    payload = build_discovery_payload(
        "provider",
        [
            McpToolDescriptor(name="provider_list_closed_positions", annotations={"readOnlyHint": True}),
            McpToolDescriptor(name="provider_list_net_positions", annotations={"readOnlyHint": True}),
            McpToolDescriptor(name="provider_list_positions", annotations={"readOnlyHint": True}),
            McpToolDescriptor(name="provider_review_strategy_positions", annotations={"readOnlyHint": True}),
        ],
        McpConsumerConfig(),
        finance_market_enabled=True,
        finance_account_enabled=True,
    )

    assert payload.applied_capability_tools["list_positions"] == "provider_list_positions"
    assert set(payload.applied_allowed_tools) == {
        "provider_list_closed_positions",
        "provider_list_net_positions",
        "provider_list_positions",
        "provider_review_strategy_positions",
    }


def test_external_state_payload_redacts_env_values(temp_repo) -> None:
    save_provider_config(
        temp_repo,
        ProviderConfigInput(
            provider_id="provider",
            command="provider-mcp",
            args=["--stdio"],
            env={"TOKEN": "secret"},
        ),
    )
    save_finance_settings(temp_repo, enabled=True, provider="provider", market_data_enabled=True)
    config = load_config(temp_repo)

    payload = external_state_payload(config)

    assert payload.providers[0].env_keys == ["TOKEN"]
    assert "secret" not in payload.model_dump_json()


def test_model_native_search_settings_are_exposed_without_api_key_values(temp_repo, monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "secret")
    save_model_native_search_settings(
        temp_repo,
        enabled=True,
        provider="ollama",
        base_url="https://ollama.com/api",
        api_key_env="OLLAMA_API_KEY",
        max_results=7,
    )
    config = load_config(temp_repo)

    payload = external_state_payload(config)

    assert payload.model_native_search["enabled"] is True
    assert payload.model_native_search["provider"] == "ollama"
    assert payload.model_native_search["api_key_env"] == "OLLAMA_API_KEY"
    assert payload.model_native_search["max_results"] == 7
    assert "secret" not in payload.model_dump_json()


def test_model_native_search_settings_support_brave_provider(temp_repo, monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret")

    save_model_native_search_settings(temp_repo, enabled=True, provider="brave", max_results=6)
    config = load_config(temp_repo)

    assert config.external_state.model_native_search.provider == "brave"
    assert config.external_state.model_native_search.api_key_env == "BRAVE_SEARCH_API_KEY"
    assert config.external_state.model_native_search.base_url == "https://api.search.brave.com/res/v1"
    assert config.external_state.model_native_search.max_results == 6
    assert "secret" not in external_state_payload(config).model_dump_json()


def test_discovery_maps_generic_web_search_to_news_capability() -> None:
    payload = build_discovery_payload(
        "search",
        [McpToolDescriptor(name="web_search", annotations={"readOnlyHint": True})],
        McpConsumerConfig(),
        finance_market_enabled=True,
        finance_account_enabled=False,
    )

    assert payload.applied_allowed_tools == ["web_search"]
    assert payload.applied_capability_tools == {"search_news": "web_search"}
    assert payload.tools[0].sensitivity_class == "public_market_only"
    assert payload.tools[0].access_scope == "market_data"


def test_apply_news_only_discovery_preserves_primary_finance_provider(temp_repo) -> None:
    save_provider_config(temp_repo, ProviderConfigInput(provider_id="market", command="market-mcp"))
    save_provider_config(temp_repo, ProviderConfigInput(provider_id="search", command="search-mcp"))
    save_finance_settings(
        temp_repo,
        enabled=True,
        provider="market",
        market_data_enabled=True,
        capability_tools={"screen_market": "provider_screen_market"},
    )
    config = load_config(temp_repo)
    discovery = build_discovery_payload(
        "search",
        [McpToolDescriptor(name="web_search", annotations={"readOnlyHint": True})],
        config.external_state.consumers["search"],
        finance_market_enabled=True,
        finance_account_enabled=False,
    )

    apply_discovery(temp_repo, config.external_state, "search", discovery)
    updated = load_config(temp_repo)

    assert updated.external_state.finance.provider == "market"
    assert updated.external_state.finance.capability_tools["screen_market"] == "provider_screen_market"
    assert updated.external_state.finance.capability_tools["search_news"] == "web_search"
    assert updated.external_state.consumers["search"].allowed_tools == ["web_search"]
