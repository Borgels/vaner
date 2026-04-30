# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

UsageSource = Literal[
    "provider_reported",
    "provider_generation_stats",
    "adapter_reported",
    "tokenizer_estimated",
    "char_estimated",
    "unknown",
]
PricingSource = Literal[
    "provider_reported",
    "user_config",
    "openrouter_api",
    "litellm_cost_map",
    "bundled_snapshot",
    "unknown_zero",
]
CallRole = Literal[
    "precompute",
    "draft",
    "maturation",
    "answer",
    "judge",
    "primary_forward",
    "embedding_or_retrieval",
    "rerank",
    "summarize",
    "classify",
    "tool_selection",
]
LocalOrCloud = Literal["local", "cloud", "unknown"]

COST_CALCULATION_VERSION = "vaner-cost-v1"
UNKNOWN_PRICING_SNAPSHOT_ID = "unknown-zero"


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0
    usage_source: UsageSource = "unknown"
    usage_estimated: bool = True
    provider_usage_raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fill_total(self) -> TokenUsage:
        if self.total_tokens <= 0:
            self.total_tokens = max(
                0,
                int(self.prompt_tokens)
                + int(self.completion_tokens)
                + int(self.thinking_tokens)
                + int(self.cached_input_tokens),
            )
        return self


class ModelPricing(BaseModel):
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0
    thinking_cost_per_1k: float = 0.0
    cached_input_cost_per_1k: float = 0.0
    request_cost: float = 0.0
    local_or_cloud: LocalOrCloud = "unknown"
    currency: str = "USD"
    source: PricingSource = "user_config"


class PricingSnapshot(BaseModel):
    pricing_snapshot_id: str = UNKNOWN_PRICING_SNAPSHOT_ID
    source: PricingSource = "unknown_zero"
    created_at: float = Field(default_factory=time.time)
    effective_at: float = Field(default_factory=time.time)
    content_hash: str = ""
    raw_snapshot_json: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class CostEstimate(BaseModel):
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    thinking_cost_usd: float = 0.0
    cached_input_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    pricing_source: PricingSource = "unknown_zero"
    pricing_snapshot_id: str = UNKNOWN_PRICING_SNAPSHOT_ID
    pricing_effective_at: float = 0.0
    cost_calculation_version: str = COST_CALCULATION_VERSION
    currency: str = "USD"
    estimated: bool = True
    notes: str = ""


class CostLedgerEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str = ""
    turn_id: str = ""
    prediction_id: str = ""
    cycle_id: str = ""
    client_id: str = ""
    app: str = ""
    project: str = ""
    workspace: str = ""
    provider: str = ""
    model: str = ""
    endpoint: str = ""
    call_role: CallRole = "precompute"
    local_or_cloud: LocalOrCloud = "unknown"
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: CostEstimate = Field(default_factory=CostEstimate)
    latency_ms: float = 0.0
    created_at: float = Field(default_factory=time.time)


class PredictionCostSummary(BaseModel):
    prediction_id: str
    cycle_id: str = ""
    turn_id: str = ""
    status: str = ""
    final_outcome: str = ""
    model_calls: int = 0
    local_tokens: int = 0
    cloud_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    adopted: bool = False
    ignored: bool = False
    dropped: bool = False
    invalidated: bool = False
    false_ready: bool = False
    created_at: float = Field(default_factory=time.time)
    finalized_at: float = 0.0


class TurnCostSummary(BaseModel):
    turn_id: str
    request_id: str = ""
    client_id: str = ""
    app: str = ""
    project: str = ""
    workspace: str = ""
    vaner_speculative_tokens: int = 0
    vaner_speculative_cost_usd: float = 0.0
    local_prep_tokens: int = 0
    cloud_prep_tokens: int = 0
    injected_context_tokens: int = 0
    expected_incremental_primary_cost_usd: float = 0.0
    primary_llm_input_tokens: int = 0
    primary_llm_output_tokens: int = 0
    primary_llm_thinking_tokens: int = 0
    primary_llm_cost_usd: float = 0.0
    primary_llm_usage_known: bool = False
    judge_llm_tokens: int = 0
    judge_llm_cost_usd: float = 0.0
    total_known_cloud_cost_usd: float = 0.0
    total_estimated_cloud_cost_usd: float = 0.0
    net_estimated_cloud_delta_usd: float = 0.0
    created_at: float = Field(default_factory=time.time)


class RuntimeCostRollup(BaseModel):
    cost_today_usd: float = 0.0
    cost_this_month_usd: float = 0.0
    local_tokens_today: int = 0
    cloud_tokens_today: int = 0
    speculative_tokens_today: int = 0
    injected_context_tokens_today: int = 0
    useful_prediction_rate: float = 0.0
    wasted_speculative_token_rate: float = 0.0
    average_cost_per_adopted_prediction: float = 0.0
    average_context_overhead_per_turn: float = 0.0
    cost_by_model: dict[str, float] = Field(default_factory=dict)
    cost_by_provider: dict[str, float] = Field(default_factory=dict)
    cost_by_app: dict[str, float] = Field(default_factory=dict)
    cost_by_project: dict[str, float] = Field(default_factory=dict)


def estimate_usage(
    prompt: str = "",
    completion: str = "",
    *,
    thinking: str = "",
    source: UsageSource = "char_estimated",
    provider_usage_raw: dict[str, Any] | None = None,
) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=_approx_tokens(prompt),
        completion_tokens=_approx_tokens(completion),
        thinking_tokens=_approx_tokens(thinking),
        total_tokens=_approx_tokens(prompt) + _approx_tokens(completion) + _approx_tokens(thinking),
        usage_source=source,
        usage_estimated=source in {"tokenizer_estimated", "char_estimated", "unknown"},
        provider_usage_raw=dict(provider_usage_raw or {}),
    )


def usage_from_openai_payload(
    payload: dict[str, Any],
    *,
    prompt: str = "",
    completion: str = "",
    thinking: str = "",
) -> TokenUsage:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return estimate_usage(prompt, completion, thinking=thinking, provider_usage_raw={})
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    if not isinstance(completion_details, dict):
        completion_details = {}
    thinking_tokens = int(
        usage.get("thinking_tokens")
        or usage.get("reasoning_tokens")
        or completion_details.get("reasoning_tokens")
        or completion_details.get("thinking_tokens")
        or 0
    )
    cached_input_tokens = int(prompt_details.get("cached_tokens") or prompt_details.get("cached_input_tokens") or 0)
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens + thinking_tokens + cached_input_tokens
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        thinking_tokens=thinking_tokens,
        cached_input_tokens=cached_input_tokens,
        total_tokens=total_tokens,
        usage_source="provider_reported",
        usage_estimated=False,
        provider_usage_raw=dict(usage),
    )


def usage_from_ollama_payload(
    payload: dict[str, Any],
    *,
    prompt: str = "",
    completion: str = "",
    thinking: str = "",
) -> TokenUsage:
    prompt_tokens = int(payload.get("prompt_eval_count") or 0)
    completion_tokens = int(payload.get("eval_count") or 0)
    if prompt_tokens or completion_tokens:
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            thinking_tokens=0,
            total_tokens=prompt_tokens + completion_tokens,
            usage_source="provider_generation_stats",
            usage_estimated=False,
            provider_usage_raw={
                k: payload.get(k)
                for k in (
                    "prompt_eval_count",
                    "eval_count",
                    "prompt_eval_duration",
                    "eval_duration",
                    "total_duration",
                    "load_duration",
                )
                if k in payload
            },
        )
    return estimate_usage(prompt, completion, thinking=thinking, provider_usage_raw={})


def estimate_cost(
    usage: TokenUsage,
    pricing: ModelPricing | None = None,
    *,
    pricing_source: PricingSource | None = None,
    pricing_snapshot_id: str = UNKNOWN_PRICING_SNAPSHOT_ID,
    pricing_effective_at: float = 0.0,
    notes: str = "",
) -> CostEstimate:
    if pricing is None:
        return CostEstimate(notes=notes or "pricing unknown; token usage retained with zero cost")
    source = pricing_source or pricing.source
    input_cost = (usage.prompt_tokens / 1000.0) * pricing.input_cost_per_1k
    output_cost = (usage.completion_tokens / 1000.0) * pricing.output_cost_per_1k
    thinking_cost = (usage.thinking_tokens / 1000.0) * pricing.thinking_cost_per_1k
    cached_cost = (usage.cached_input_tokens / 1000.0) * pricing.cached_input_cost_per_1k
    total = input_cost + output_cost + thinking_cost + cached_cost + pricing.request_cost
    return CostEstimate(
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        thinking_cost_usd=thinking_cost,
        cached_input_cost_usd=cached_cost,
        total_cost_usd=total,
        pricing_source=source,
        pricing_snapshot_id=pricing_snapshot_id,
        pricing_effective_at=pricing_effective_at,
        currency=pricing.currency,
        estimated=source != "provider_reported",
        notes=notes,
    )


def pricing_snapshot_from_map(source: PricingSource, raw: dict[str, Any], *, notes: str = "") -> PricingSnapshot:
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    now = time.time()
    return PricingSnapshot(
        pricing_snapshot_id=f"{source}-{digest[:16]}",
        source=source,
        created_at=now,
        effective_at=now,
        content_hash=digest,
        raw_snapshot_json=dict(raw),
        notes=notes,
    )
