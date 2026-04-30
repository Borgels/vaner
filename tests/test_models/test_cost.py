# SPDX-License-Identifier: Apache-2.0

from vaner.models.cost import (
    ModelPricing,
    estimate_cost,
    estimate_usage,
    usage_from_ollama_payload,
    usage_from_openai_payload,
)


def test_openai_usage_parses_reasoning_and_cached_tokens() -> None:
    usage = usage_from_openai_payload(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 12},
                "completion_tokens_details": {"reasoning_tokens": 10},
            }
        }
    )

    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 40
    assert usage.thinking_tokens == 10
    assert usage.cached_input_tokens == 12
    assert usage.total_tokens == 150
    assert usage.usage_source == "provider_reported"
    assert usage.usage_estimated is False


def test_ollama_usage_prefers_generation_stats() -> None:
    usage = usage_from_ollama_payload({"prompt_eval_count": 11, "eval_count": 7, "total_duration": 123})

    assert usage.prompt_tokens == 11
    assert usage.completion_tokens == 7
    assert usage.total_tokens == 18
    assert usage.usage_source == "provider_generation_stats"
    assert usage.usage_estimated is False


def test_unknown_pricing_retains_tokens_with_zero_cost() -> None:
    usage = estimate_usage("hello world", "answer")
    cost = estimate_cost(usage, None)

    assert usage.total_tokens > 0
    assert cost.total_cost_usd == 0.0
    assert cost.pricing_source == "unknown_zero"
    assert cost.estimated is True


def test_configured_pricing_computes_input_output_thinking_costs() -> None:
    usage = usage_from_openai_payload(
        {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "completion_tokens_details": {"reasoning_tokens": 250},
            }
        }
    )
    cost = estimate_cost(
        usage,
        ModelPricing(input_cost_per_1k=1.0, output_cost_per_1k=2.0, thinking_cost_per_1k=4.0),
    )

    assert cost.input_cost_usd == 1.0
    assert cost.output_cost_usd == 1.0
    assert cost.thinking_cost_usd == 1.0
    assert cost.total_cost_usd == 3.0
