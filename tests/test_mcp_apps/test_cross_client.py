# SPDX-License-Identifier: Apache-2.0

"""Cross-client tier simulation.

The real clients (Claude Desktop, Claude Code, Cursor, ChatGPT, VS Code
Copilot, plain stdio) each advertise a different capability shape during
MCP initialize. We can't spin them up in unit tests, but we *can*
simulate each one by writing the expected `client_params` shape and
asking :func:`detect_tier` what it sees. If the tier mapping drifts, a
real-client regression becomes obvious here without waiting for a
downstream user to file a bug.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vaner.integrations.capability import ClientCapabilityTier, detect_tier


def _caps(**kw) -> SimpleNamespace:
    return SimpleNamespace(
        experimental=kw.get("experimental"),
        roots=kw.get("roots"),
        sampling=kw.get("sampling"),
    )


def _params(name: str, version: str, caps: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        clientInfo=SimpleNamespace(name=name, version=version),
        capabilities=caps,
    )


# (client_name, expected_tier, caps_kwargs)
_CASES = [
    # Tier 4 — MCP Apps host.
    (
        "claude_desktop_ui",
        ClientCapabilityTier.TIER_4,
        {"experimental": {"io.modelcontextprotocol/ui": {}}, "roots": SimpleNamespace()},
    ),
    (
        "chatgpt_ui",
        ClientCapabilityTier.TIER_4,
        {
            "experimental": {"io.modelcontextprotocol/ui": {"version": 1}},
            "sampling": SimpleNamespace(),
        },
    ),
    # Tier 3 — context-mediation host (future hosts that opt in).
    (
        "custom_proxy_with_injection",
        ClientCapabilityTier.TIER_3,
        {"experimental": {"vaner.context_injection": {}}, "roots": SimpleNamespace()},
    ),
    # Tier 2 — prompt-guidance capable (most real MCP clients).
    (
        "claude_code",
        ClientCapabilityTier.TIER_2,
        {"roots": SimpleNamespace(listChanged=True)},
    ),
    # Cursor pre-2.6 — MCP-aware but no UI extension. Tier 2.
    (
        "cursor_pre_2_6",
        ClientCapabilityTier.TIER_2,
        {"sampling": SimpleNamespace()},
    ),
    # Cursor 2.6+ ships MCP Apps support. Detection is value-driven (we
    # match on the experimental UI key, not the client name) so the same
    # capability-detection code automatically promotes Cursor to Tier-4
    # the moment it advertises the extension. This case pins the contract.
    (
        "cursor_2_6_ui",
        ClientCapabilityTier.TIER_4,
        {
            "experimental": {"io.modelcontextprotocol/ui": {"version": 1}},
            "sampling": SimpleNamespace(),
        },
    ),
    (
        "vscode_copilot",
        ClientCapabilityTier.TIER_2,
        {"roots": SimpleNamespace()},
    ),
    # Tier 1 — bare stdio.
    (
        "plain_stdio_client",
        ClientCapabilityTier.TIER_1,
        {"experimental": {}},
    ),
    (
        "minimal_mcp_client",
        ClientCapabilityTier.TIER_1,
        {},  # no roots/sampling/experimental
    ),
]


@pytest.mark.parametrize(("name", "expected_tier", "caps_kwargs"), _CASES)
def test_client_tier_matches_expectation(name: str, expected_tier: ClientCapabilityTier, caps_kwargs: dict) -> None:
    params = _params(name, "1.0.0", _caps(**caps_kwargs))
    detection = detect_tier(params)
    assert detection.tier is expected_tier, (
        f"client {name!r} expected {expected_tier.name}, got {detection.tier.name} (reason={detection.reason!r})"
    )


def test_tier_4_ui_extension_supersedes_lower_markers() -> None:
    # A Tier-4 client will also advertise roots/sampling; the UI flag
    # must win the classification regardless.
    params = _params(
        "claude_desktop",
        "0.9",
        _caps(
            experimental={"io.modelcontextprotocol/ui": {}},
            roots=SimpleNamespace(),
            sampling=SimpleNamespace(),
        ),
    )
    assert detect_tier(params).tier is ClientCapabilityTier.TIER_4


def test_unknown_future_experimental_does_not_demote_tier_2() -> None:
    # Future clients may advertise experimental keys we don't know about.
    # We must still classify them at Tier-2 if they advertise roots.
    params = _params(
        "future_client",
        "2.0",
        _caps(
            experimental={"com.example.new": {}},
            roots=SimpleNamespace(),
        ),
    )
    assert detect_tier(params).tier is ClientCapabilityTier.TIER_2


def test_detection_preserves_client_name_for_logging() -> None:
    # The detection object carries the name + version so the per-session
    # log line can attribute the tier to the right client. If this drifts,
    # logs become uninformative.
    params = _params("claude_code", "1.2.3", _caps(roots=SimpleNamespace()))
    d = detect_tier(params)
    assert d.client_name == "claude_code"
    assert d.client_version == "1.2.3"
