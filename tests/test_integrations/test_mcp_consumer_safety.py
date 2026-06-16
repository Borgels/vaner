# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.integrations.mcp_consumer.safety import McpToolDescriptor, ReadOnlyToolPolicy


def test_read_only_policy_allows_explicit_annotated_read_tool() -> None:
    policy = ReadOnlyToolPolicy(allowed_tools={"provider_get_quote"})
    decision = policy.validate(
        McpToolDescriptor(
            name="provider_get_quote",
            annotations={"readOnlyHint": True, "destructiveHint": False},
            provider_risk="read",
        )
    )

    assert decision.allowed is True
    assert decision.reason == "read_only_allowlisted"


def test_read_only_policy_blocks_missing_annotation_for_normal_users() -> None:
    policy = ReadOnlyToolPolicy(allowed_tools={"provider_get_quote"})
    decision = policy.validate(McpToolDescriptor(name="provider_get_quote"))

    assert decision.allowed is False
    assert decision.reason == "missing_positive_readonly_classification"


def test_read_only_policy_allows_configured_provider_read_risk() -> None:
    policy = ReadOnlyToolPolicy(
        allowed_tools={"provider_get_quote"},
        tool_risks={"provider_get_quote": "read"},
    )
    decision = policy.validate(McpToolDescriptor(name="provider_get_quote"))

    assert decision.allowed is True
    assert decision.reason == "read_only_allowlisted"


def test_read_only_policy_blocks_provider_write_risk() -> None:
    policy = ReadOnlyToolPolicy(allowed_tools={"provider_get_quote"})
    decision = policy.validate(
        McpToolDescriptor(
            name="provider_get_quote",
            annotations={"readOnlyHint": True},
            provider_risk="write",
        )
    )

    assert decision.allowed is False
    assert decision.reason == "provider_risk_not_read:write"


def test_read_only_policy_blocks_deny_pattern_even_when_allowlisted() -> None:
    policy = ReadOnlyToolPolicy(allowed_tools={"provider_precheck_order"})
    decision = policy.validate(
        McpToolDescriptor(
            name="provider_precheck_order",
            annotations={"readOnlyHint": True},
            provider_risk="read",
        )
    )

    assert decision.allowed is False
    assert decision.reason == "tool_name_matches_deny_pattern"


def test_read_only_policy_blocks_tools_outside_allowlist() -> None:
    policy = ReadOnlyToolPolicy(allowed_tools={"provider_get_quote"})
    decision = policy.validate(
        McpToolDescriptor(
            name="provider_get_chart",
            annotations={"readOnlyHint": True},
            provider_risk="read",
        )
    )

    assert decision.allowed is False
    assert decision.reason == "tool_not_in_allowlist"
