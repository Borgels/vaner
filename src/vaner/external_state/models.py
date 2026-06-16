# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ExternalStateFreshnessClass(StrEnum):
    STATIC_REFERENCE = "static_reference"
    SLOW_MARKET_CONTEXT = "slow_market_context"
    MARKET_SNAPSHOT = "market_snapshot"
    ACCOUNT_SNAPSHOT = "account_snapshot"
    NEWS_SNAPSHOT = "news_snapshot"
    DERIVED_ANALYSIS = "derived_analysis"


class ExternalStateSensitivity(StrEnum):
    PUBLIC_MARKET_ONLY = "public_market_only"
    USER_WATCHLIST = "user_watchlist"
    ACCOUNT_SUMMARY = "account_summary"
    POSITION_SPECIFIC = "position_specific"
    ORDER_ACTIVITY = "order_activity"
    MIXED_SENSITIVE = "mixed_sensitive"


class FinanceCapability(StrEnum):
    GET_QUOTE = "get_quote"
    GET_CHART = "get_chart"
    SEARCH_NEWS = "search_news"
    SCREEN_MARKET = "screen_market"
    GET_OPTION_CHAIN = "get_option_chain"
    LIST_OPTION_EXPIRIES = "list_option_expiries"
    ANALYZE_OPTION_STRATEGY = "analyze_option_strategy"
    SCREEN_OPTION_STRATEGIES = "screen_option_strategies"
    REVIEW_STRATEGY_POSITIONS = "review_strategy_positions"
    LIST_ACCOUNTS = "list_accounts"
    GET_BALANCE = "get_balance"
    LIST_POSITIONS = "list_positions"
    LIST_ORDERS = "list_orders"
    LIST_ALERTS = "list_alerts"


class ExternalStateSnapshot(BaseModel):
    id: str
    provider_id: str
    capability: str
    query_key: str = ""
    source_tool: str = ""
    freshness_class: ExternalStateFreshnessClass
    sensitivity_class: ExternalStateSensitivity
    captured_at: float = Field(default_factory=time.time)
    expires_at: float | None = None
    payload_fingerprint: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        provider_id: str,
        capability: str,
        freshness_class: ExternalStateFreshnessClass,
        sensitivity_class: ExternalStateSensitivity,
        payload: dict[str, Any],
        query_key: str = "",
        source_tool: str = "",
        captured_at: float | None = None,
        ttl_seconds: float | None = None,
    ) -> ExternalStateSnapshot:
        ts = time.time() if captured_at is None else float(captured_at)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        digest = hashlib.sha256(
            f"{provider_id}\n{capability}\n{query_key}\n{fingerprint}\n{ts:.6f}".encode()
        ).hexdigest()[:20]
        return cls(
            id=f"ext-{digest}",
            provider_id=provider_id,
            capability=capability,
            query_key=query_key,
            source_tool=source_tool,
            freshness_class=freshness_class,
            sensitivity_class=sensitivity_class,
            captured_at=ts,
            expires_at=(ts + float(ttl_seconds)) if ttl_seconds is not None else None,
            payload_fingerprint=fingerprint,
            payload=payload,
        )

    def is_stale(self, *, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (time.time() if now is None else float(now)) >= self.expires_at
