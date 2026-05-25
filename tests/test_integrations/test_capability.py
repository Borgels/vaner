# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace

import pytest

from vaner.integrations.capability import (
    ClientCapabilityTier,
    ClientFamily,
    classify_client,
    current_client_family,
    current_tier,
    detect_tier,
    is_terminal_host,
    record_tier,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset_between_tests() -> None:
    reset_cache()
    yield
    reset_cache()


def _params(
    *,
    experimental: dict | None = None,
    roots: object | None = None,
    sampling: object | None = None,
    client_name: str | None = "test-client",
    client_version: str | None = "0.0.0",
):
    caps = SimpleNamespace(
        experimental=experimental,
        roots=roots,
        sampling=sampling,
    )
    info = SimpleNamespace(name=client_name, version=client_version)
    return SimpleNamespace(clientInfo=info, capabilities=caps)


def test_unknown_when_params_missing() -> None:
    assert detect_tier(None).tier is ClientCapabilityTier.UNKNOWN


def test_tier_1_when_capabilities_absent() -> None:
    params = SimpleNamespace(
        clientInfo=SimpleNamespace(name="x", version="1"),
        capabilities=None,
    )
    assert detect_tier(params).tier is ClientCapabilityTier.TIER_1


def test_tier_1_when_no_markers() -> None:
    params = _params(experimental={})
    assert detect_tier(params).tier is ClientCapabilityTier.TIER_1


def test_tier_2_when_roots_present() -> None:
    params = _params(roots=SimpleNamespace(listChanged=True))
    assert detect_tier(params).tier is ClientCapabilityTier.TIER_2


def test_tier_2_when_sampling_present() -> None:
    params = _params(sampling=SimpleNamespace())
    assert detect_tier(params).tier is ClientCapabilityTier.TIER_2


def test_tier_3_when_injection_extension_present() -> None:
    params = _params(experimental={"vaner.context_injection": {"version": 1}})
    detection = detect_tier(params)
    assert detection.tier is ClientCapabilityTier.TIER_3
    assert detection.reason == "vaner_injection_extension_advertised"


def test_tier_4_when_ui_extension_present() -> None:
    params = _params(experimental={"io.modelcontextprotocol/ui": {}})
    detection = detect_tier(params)
    assert detection.tier is ClientCapabilityTier.TIER_4


def test_tier_4_wins_over_tier_2_markers() -> None:
    params = _params(
        experimental={"io.modelcontextprotocol/ui": {}},
        roots=SimpleNamespace(),
        sampling=SimpleNamespace(),
    )
    assert detect_tier(params).tier is ClientCapabilityTier.TIER_4


def test_detection_carries_client_name() -> None:
    params = _params(
        experimental={"io.modelcontextprotocol/ui": {}},
        client_name="Claude Desktop",
        client_version="0.9.0",
    )
    d = detect_tier(params)
    assert d.client_name == "Claude Desktop"
    assert d.client_version == "0.9.0"


def test_session_cache_round_trip() -> None:
    class _Session:
        pass

    s = _Session()
    assert current_tier(s) is ClientCapabilityTier.UNKNOWN
    record_tier(s, detect_tier(_params(experimental={"io.modelcontextprotocol/ui": {}})))
    assert current_tier(s) is ClientCapabilityTier.TIER_4


def test_session_cache_is_per_instance() -> None:
    class _Session:
        pass

    s1 = _Session()
    s2 = _Session()
    record_tier(s1, detect_tier(_params(roots=SimpleNamespace())))
    assert current_tier(s1) is ClientCapabilityTier.TIER_2
    assert current_tier(s2) is ClientCapabilityTier.UNKNOWN


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("claude-code", ClientFamily.CLAUDE_CODE),
        ("Claude Code", ClientFamily.CLAUDE_CODE),
        ("claude-code-cli/1.2.3", ClientFamily.CLAUDE_CODE),
        ("Claude Desktop", ClientFamily.CLAUDE_DESKTOP),
        ("claude-desktop", ClientFamily.CLAUDE_DESKTOP),
        ("claude.ai", ClientFamily.CLAUDE_WEB),
        ("ChatGPT", ClientFamily.CHATGPT),
        ("chatgpt-mcp/0.1", ClientFamily.CHATGPT),
        ("codex-cli", ClientFamily.CODEX),
        ("Cursor", ClientFamily.CURSOR),
        ("cursor", ClientFamily.CURSOR),
        ("vscode", ClientFamily.VSCODE),
        ("Visual Studio Code", ClientFamily.VSCODE),
        ("github-copilot", ClientFamily.VSCODE),
        ("Zed", ClientFamily.ZED),
        ("Goose", ClientFamily.GOOSE),
        ("Windsurf", ClientFamily.WINDSURF),
        ("Cline", ClientFamily.CLINE),
        ("continue.dev", ClientFamily.CONTINUE),
    ],
)
def test_classify_client_recognises_known_hosts(name: str, expected: ClientFamily) -> None:
    assert classify_client(name) is expected


@pytest.mark.parametrize("name", [None, "", "   ", "some-random-thing", "openai/gpt-4o"])
def test_classify_client_returns_unknown_for_unrecognised(name) -> None:
    assert classify_client(name) is ClientFamily.UNKNOWN


def test_is_terminal_host_groups_cli_clients() -> None:
    assert is_terminal_host(ClientFamily.CLAUDE_CODE)
    assert is_terminal_host(ClientFamily.CODEX)
    assert is_terminal_host(ClientFamily.CLINE)
    # GUI hosts should not be flagged terminal.
    assert not is_terminal_host(ClientFamily.CLAUDE_DESKTOP)
    assert not is_terminal_host(ClientFamily.CLAUDE_WEB)
    assert not is_terminal_host(ClientFamily.CHATGPT)
    assert not is_terminal_host(ClientFamily.UNKNOWN)


def test_current_client_family_returns_unknown_when_session_unseen() -> None:
    class _Session:
        pass

    assert current_client_family(_Session()) is ClientFamily.UNKNOWN


def test_current_client_family_reflects_recorded_clientinfo() -> None:
    class _Session:
        pass

    s = _Session()
    record_tier(s, detect_tier(_params(client_name="claude-code", roots=SimpleNamespace())))
    assert current_client_family(s) is ClientFamily.CLAUDE_CODE
    # Tier resolution still works alongside.
    assert current_tier(s) is ClientCapabilityTier.TIER_2
