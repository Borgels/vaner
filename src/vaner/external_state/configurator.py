# SPDX-License-Identifier: Apache-2.0

"""User-facing configuration helpers for external-state MCP providers."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vaner.external_state.models import ExternalStateSensitivity, FinanceCapability
from vaner.integrations.mcp_consumer.client import discover_mcp_tools
from vaner.integrations.mcp_consumer.safety import McpToolDescriptor, ReadOnlyToolPolicy
from vaner.models.config import ExternalStateConfig, McpConsumerConfig, VanerConfig
from vaner.setup.config_io import update_toml_section

_PROVIDER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_ACCOUNT_CAPABILITIES = {
    FinanceCapability.LIST_ACCOUNTS.value,
    FinanceCapability.GET_BALANCE.value,
    FinanceCapability.LIST_POSITIONS.value,
    FinanceCapability.LIST_ORDERS.value,
    FinanceCapability.LIST_ALERTS.value,
    FinanceCapability.REVIEW_STRATEGY_POSITIONS.value,
}

_CAPABILITY_PATTERNS: tuple[tuple[FinanceCapability, tuple[str, ...]], ...] = (
    (FinanceCapability.ANALYZE_OPTION_STRATEGY, ("analyze_option_strategy", "analyse_option_strategy", "option_strategy_analy")),
    (FinanceCapability.SCREEN_OPTION_STRATEGIES, ("screen_option_strateg", "scan_option_strateg", "find_option_strateg")),
    (FinanceCapability.GET_OPTION_CHAIN, ("option_chain", "options_chain", "chain_options", "get_chain")),
    (FinanceCapability.LIST_OPTION_EXPIRIES, ("option_expir", "option_expiries", "expiration", "expiry")),
    (FinanceCapability.LIST_POSITIONS, ("list_positions", "positions", "holdings", "portfolio_positions")),
    (FinanceCapability.LIST_ORDERS, ("list_orders", "orders", "order_activity")),
    (FinanceCapability.GET_BALANCE, ("balance", "balances", "cash", "margin")),
    (FinanceCapability.LIST_ALERTS, ("alerts", "watch_alerts")),
    (FinanceCapability.LIST_ACCOUNTS, ("accounts", "account_summary")),
    (FinanceCapability.GET_CHART, ("chart", "candles", "candle", "ohlc", "bars")),
    (
        FinanceCapability.SEARCH_NEWS,
        ("search_news", "market_news", "news_search", "news_sentiment", "web_search", "search_web", "headlines"),
    ),
    (FinanceCapability.SCREEN_MARKET, ("screen_market", "market_screen", "scanner", "scan_market", "search_market", "gainers", "losers")),
    (FinanceCapability.GET_QUOTE, ("quote", "quotes", "infoprice", "info_price", "market_price", "snapshot_price")),
)


class DiscoveredExternalTool(BaseModel):
    name: str
    annotations: dict[str, Any] = Field(default_factory=dict)
    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    open_world_hint: bool | None = None
    provider_risk: str | None = None
    safety_allowed: bool
    safety_reason: str
    proposed_capability: str | None = None
    sensitivity_class: str = ExternalStateSensitivity.PUBLIC_MARKET_ONLY.value
    access_scope: str = "market_data"
    auto_selected: bool = False


class ExternalStateProviderPayload(BaseModel):
    id: str
    transport: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    env_keys: list[str] = Field(default_factory=list)
    timeout_ms: int
    allowed_tools: list[str] = Field(default_factory=list)
    tool_risks: dict[str, str] = Field(default_factory=dict)
    capability_tools: dict[str, str] = Field(default_factory=dict)


class ExternalStateSettingsPayload(BaseModel):
    enabled: bool
    max_calls_per_cycle: int
    max_cycle_ms: int
    providers: list[ExternalStateProviderPayload] = Field(default_factory=list)
    finance: dict[str, Any] = Field(default_factory=dict)
    model_native_search: dict[str, Any] = Field(default_factory=dict)


class ProviderDiscoveryPayload(BaseModel):
    provider_id: str
    tools: list[DiscoveredExternalTool]
    applied: bool = False
    applied_allowed_tools: list[str] = Field(default_factory=list)
    applied_capability_tools: dict[str, str] = Field(default_factory=dict)
    blocked_count: int = 0


@dataclass(frozen=True, slots=True)
class ProviderConfigInput:
    provider_id: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] | None = None
    url: str = ""
    env: dict[str, str] | None = None
    timeout_ms: int = 10000


def validate_provider_id(provider_id: str) -> str:
    provider_id = provider_id.strip()
    if not _PROVIDER_ID_RE.match(provider_id):
        raise ValueError("provider id must match ^[a-zA-Z0-9_-]{1,64}$")
    return provider_id


def external_state_payload(config: VanerConfig) -> ExternalStateSettingsPayload:
    finance = config.external_state.finance
    providers: list[ExternalStateProviderPayload] = []
    for provider_id, consumer in sorted(config.external_state.consumers.items()):
        capabilities = {
            capability: tool
            for capability, tool in finance.capability_tools.items()
            if tool in set(consumer.allowed_tools) or provider_id == finance.provider
        }
        providers.append(
            ExternalStateProviderPayload(
                id=provider_id,
                transport=consumer.transport,
                command=consumer.command,
                args=list(consumer.args),
                url=consumer.url,
                env_keys=sorted(consumer.env),
                timeout_ms=consumer.timeout_ms,
                allowed_tools=list(consumer.allowed_tools),
                tool_risks=dict(consumer.tool_risks),
                capability_tools=capabilities,
            )
        )
    return ExternalStateSettingsPayload(
        enabled=config.external_state.enabled,
        max_calls_per_cycle=config.external_state.max_calls_per_cycle,
        max_cycle_ms=config.external_state.max_cycle_ms,
        providers=providers,
        finance=finance.model_dump(mode="json"),
        model_native_search=config.external_state.model_native_search.model_dump(mode="json"),
    )


def provider_input_from_config(config: VanerConfig, provider_id: str) -> ProviderConfigInput:
    provider_id = validate_provider_id(provider_id)
    consumer = config.external_state.consumers.get(provider_id)
    if consumer is None:
        raise KeyError(provider_id)
    return ProviderConfigInput(
        provider_id=provider_id,
        transport=consumer.transport,
        command=consumer.command,
        args=list(consumer.args),
        url=consumer.url,
        env=dict(consumer.env),
        timeout_ms=consumer.timeout_ms,
    )


async def discover_provider(
    config: VanerConfig,
    provider_id: str,
    *,
    apply: bool = False,
    trust_unknown_read: bool = False,
) -> ProviderDiscoveryPayload:
    provider_id = validate_provider_id(provider_id)
    consumer = config.external_state.consumers.get(provider_id)
    if consumer is None:
        raise KeyError(provider_id)
    descriptors = await discover_mcp_tools(
        transport=consumer.transport,
        command=consumer.command,
        args=list(consumer.args),
        url=consumer.url,
        env=dict(consumer.env),
        timeout_ms=consumer.timeout_ms,
    )
    payload = build_discovery_payload(
        provider_id,
        descriptors,
        consumer,
        finance_market_enabled=config.external_state.finance.market_data_enabled,
        finance_account_enabled=config.external_state.finance.account_state_enabled,
        trust_unknown_read=trust_unknown_read,
    )
    if apply:
        apply_discovery(config.repo_root, config.external_state, provider_id, payload, trust_unknown_read=trust_unknown_read)
        payload.applied = True
    return payload


def discover_provider_sync(
    config: VanerConfig,
    provider_id: str,
    *,
    apply: bool = False,
    trust_unknown_read: bool = False,
) -> ProviderDiscoveryPayload:
    return asyncio.run(discover_provider(config, provider_id, apply=apply, trust_unknown_read=trust_unknown_read))


def build_discovery_payload(
    provider_id: str,
    descriptors: list[McpToolDescriptor],
    consumer: McpConsumerConfig,
    *,
    finance_market_enabled: bool,
    finance_account_enabled: bool,
    trust_unknown_read: bool = False,
) -> ProviderDiscoveryPayload:
    names = [descriptor.name for descriptor in descriptors]
    policy_tool_risks = dict(consumer.tool_risks)
    if trust_unknown_read:
        for descriptor in descriptors:
            if _infer_finance_capability(descriptor.name) is not None:
                policy_tool_risks.setdefault(descriptor.name, "read")
    policy = ReadOnlyToolPolicy(allowed_tools=set(names), tool_risks=policy_tool_risks)
    tools: list[DiscoveredExternalTool] = []
    applied_allowed: list[str] = []
    applied_caps: dict[str, str] = {}
    applied_cap_scores: dict[str, int] = {}
    for descriptor in sorted(descriptors, key=lambda item: item.name):
        descriptor = McpToolDescriptor(
            name=descriptor.name,
            annotations=dict(descriptor.annotations),
            provider_risk=policy_tool_risks.get(descriptor.name),
        )
        decision = policy.validate(descriptor)
        capability = _infer_finance_capability(descriptor.name)
        scope = _capability_scope(capability.value if capability is not None else None)
        # A discovery/apply action is itself the explicit market-data opt-in.
        # Account-state tools remain separately gated because positions,
        # balances, orders, and alerts are materially more sensitive.
        scope_enabled = finance_account_enabled if scope == "account_state" else True
        auto_selected = bool(decision.allowed and capability is not None and scope_enabled)
        sensitivity = _capability_sensitivity(capability.value if capability is not None else None)
        if auto_selected:
            applied_allowed.append(descriptor.name)
            score = _capability_tool_score(capability, descriptor.name)
            if score > applied_cap_scores.get(capability.value, -1):
                applied_caps[capability.value] = descriptor.name
                applied_cap_scores[capability.value] = score
        tools.append(
            DiscoveredExternalTool(
                name=descriptor.name,
                annotations=dict(descriptor.annotations),
                read_only_hint=_bool_or_none(descriptor.annotations.get("readOnlyHint")),
                destructive_hint=_bool_or_none(descriptor.annotations.get("destructiveHint")),
                open_world_hint=_bool_or_none(descriptor.annotations.get("openWorldHint")),
                provider_risk=descriptor.provider_risk,
                safety_allowed=decision.allowed,
                safety_reason=decision.reason,
                proposed_capability=capability.value if capability is not None else None,
                sensitivity_class=sensitivity,
                access_scope=scope,
                auto_selected=auto_selected,
            )
        )
    return ProviderDiscoveryPayload(
        provider_id=provider_id,
        tools=tools,
        applied_allowed_tools=applied_allowed,
        applied_capability_tools=applied_caps,
        blocked_count=len([tool for tool in tools if not tool.safety_allowed]),
    )


def save_provider_config(repo_root: Path, provider: ProviderConfigInput) -> Path:
    provider_id = validate_provider_id(provider.provider_id)
    config_path = _ensure_config(repo_root)
    text = config_path.read_text(encoding="utf-8")
    text = update_toml_section(
        text,
        f"external_state.consumers.{provider_id}",
        {
            "transport": provider.transport,
            "command": provider.command,
            "args": list(provider.args or []),
            "url": provider.url,
            "env": dict(provider.env or {}),
            "timeout_ms": int(provider.timeout_ms),
        },
    )
    config_path.write_text(text, encoding="utf-8")
    return config_path


def save_external_state_enabled(
    repo_root: Path,
    *,
    enabled: bool | None = None,
    max_calls_per_cycle: int | None = None,
    max_cycle_ms: int | None = None,
) -> Path:
    config_path = _ensure_config(repo_root)
    values: dict[str, object] = {}
    if enabled is not None:
        values["enabled"] = bool(enabled)
    if max_calls_per_cycle is not None:
        values["max_calls_per_cycle"] = int(max_calls_per_cycle)
    if max_cycle_ms is not None:
        values["max_cycle_ms"] = int(max_cycle_ms)
    text = update_toml_section(config_path.read_text(encoding="utf-8"), "external_state", values)
    config_path.write_text(text, encoding="utf-8")
    return config_path


def save_finance_settings(
    repo_root: Path,
    *,
    enabled: bool | None = None,
    provider: str | None = None,
    market_data_enabled: bool | None = None,
    account_state_enabled: bool | None = None,
    capability_tools: dict[str, str] | None = None,
) -> Path:
    if provider:
        provider = validate_provider_id(provider)
    config_path = _ensure_config(repo_root)
    values: dict[str, object] = {}
    if enabled is not None:
        values["enabled"] = bool(enabled)
    if provider is not None:
        values["provider"] = provider
    if market_data_enabled is not None:
        values["market_data_enabled"] = bool(market_data_enabled)
    if account_state_enabled is not None:
        values["account_state_enabled"] = bool(account_state_enabled)
    if capability_tools is not None:
        values["capability_tools"] = dict(capability_tools)
    text = update_toml_section(config_path.read_text(encoding="utf-8"), "external_state.finance", values)
    config_path.write_text(text, encoding="utf-8")
    return config_path


def save_model_native_search_settings(
    repo_root: Path,
    *,
    enabled: bool | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    max_results: int | None = None,
    timeout_seconds: float | None = None,
) -> Path:
    config_path = _ensure_config(repo_root)
    values: dict[str, object] = {}
    if enabled is not None:
        values["enabled"] = bool(enabled)
    if provider is not None:
        if provider not in {"ollama", "brave"}:
            raise ValueError("model-native search provider must be 'ollama' or 'brave'")
        values["provider"] = provider
        if api_key_env is None and provider == "brave":
            values["api_key_env"] = "BRAVE_SEARCH_API_KEY"
        if base_url is None and provider == "brave":
            values["base_url"] = "https://api.search.brave.com/res/v1"
        if api_key_env is None and provider == "ollama":
            values["api_key_env"] = "OLLAMA_API_KEY"
        if base_url is None and provider == "ollama":
            values["base_url"] = "https://ollama.com/api"
    if base_url is not None:
        values["base_url"] = base_url
    if api_key_env is not None:
        values["api_key_env"] = api_key_env
    if max_results is not None:
        values["max_results"] = int(max_results)
    if timeout_seconds is not None:
        values["timeout_seconds"] = float(timeout_seconds)
    text = update_toml_section(config_path.read_text(encoding="utf-8"), "external_state.model_native_search", values)
    config_path.write_text(text, encoding="utf-8")
    return config_path


def apply_discovery(
    repo_root: Path,
    external_state: ExternalStateConfig,
    provider_id: str,
    discovery: ProviderDiscoveryPayload,
    *,
    trust_unknown_read: bool = False,
) -> Path:
    provider_id = validate_provider_id(provider_id)
    consumer = external_state.consumers.get(provider_id, McpConsumerConfig())
    allowed_tools = sorted(set(discovery.applied_allowed_tools))
    tool_risks = dict(consumer.tool_risks)
    if trust_unknown_read:
        for tool in discovery.tools:
            if tool.auto_selected and tool.provider_risk == "read":
                tool_risks[tool.name] = "read"
    config_path = _ensure_config(repo_root)
    text = config_path.read_text(encoding="utf-8")
    text = update_toml_section(
        text,
        f"external_state.consumers.{provider_id}",
        {
            "transport": consumer.transport,
            "command": consumer.command,
            "args": list(consumer.args),
            "url": consumer.url,
            "env": dict(consumer.env),
            "timeout_ms": int(consumer.timeout_ms),
            "allowed_tools": allowed_tools,
            "tool_risks": tool_risks,
        },
    )
    capability_tools = dict(external_state.finance.capability_tools)
    capability_tools.update(discovery.applied_capability_tools)
    applied_market_data = any(_capability_scope(capability) == "market_data" for capability in discovery.applied_capability_tools)
    applied_primary_finance = any(
        capability != FinanceCapability.SEARCH_NEWS.value and _capability_scope(capability) == "market_data"
        for capability in discovery.applied_capability_tools
    ) or any(_capability_scope(capability) == "account_state" for capability in discovery.applied_capability_tools)
    finance_provider = (
        provider_id
        if not external_state.finance.provider or applied_primary_finance
        else external_state.finance.provider
    )
    text = update_toml_section(
        text,
        "external_state",
        {"enabled": True},
    )
    text = update_toml_section(
        text,
        "external_state.finance",
        {
            "enabled": True,
            "provider": finance_provider,
            "market_data_enabled": bool(external_state.finance.market_data_enabled or applied_market_data),
            "account_state_enabled": bool(external_state.finance.account_state_enabled),
            "capability_tools": capability_tools,
        },
    )
    config_path.write_text(text, encoding="utf-8")
    return config_path


def _ensure_config(repo_root: Path) -> Path:
    config_path = repo_root / ".vaner" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text("", encoding="utf-8")
    return config_path


def _infer_finance_capability(tool_name: str) -> FinanceCapability | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", tool_name.lower()).strip("_")
    if "alert" in normalized or "user_settings" in normalized:
        return None
    if "spread_quote" in normalized:
        return None
    if "stock_strateg" in normalized:
        return None
    for capability, patterns in _CAPABILITY_PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            return capability
    return None


def _capability_scope(capability: str | None) -> str:
    if capability in _ACCOUNT_CAPABILITIES:
        return "account_state"
    return "market_data"


def _capability_sensitivity(capability: str | None) -> str:
    if capability == FinanceCapability.LIST_ORDERS.value:
        return ExternalStateSensitivity.ORDER_ACTIVITY.value
    if capability in {FinanceCapability.LIST_POSITIONS.value, FinanceCapability.REVIEW_STRATEGY_POSITIONS.value}:
        return ExternalStateSensitivity.POSITION_SPECIFIC.value
    if capability in {FinanceCapability.LIST_ACCOUNTS.value, FinanceCapability.GET_BALANCE.value}:
        return ExternalStateSensitivity.ACCOUNT_SUMMARY.value
    if capability == FinanceCapability.LIST_ALERTS.value:
        return ExternalStateSensitivity.USER_WATCHLIST.value
    return ExternalStateSensitivity.PUBLIC_MARKET_ONLY.value


def _capability_tool_score(capability: FinanceCapability, tool_name: str) -> int:
    normalized = re.sub(r"[^a-z0-9]+", "_", tool_name.lower()).strip("_")
    exact_suffix = capability.value
    if normalized.endswith(exact_suffix):
        return 100
    if capability == FinanceCapability.LIST_POSITIONS:
        if "closed" in normalized:
            return 10
        if "net_positions" in normalized:
            return 80
        if "review_strategy_positions" in normalized:
            return 20
        if "list_positions" in normalized:
            return 100
    if capability == FinanceCapability.LIST_OPTION_EXPIRIES and "standard_option_expiries" in normalized:
        return 80
    return 50


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
