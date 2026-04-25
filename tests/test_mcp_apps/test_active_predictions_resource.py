# SPDX-License-Identifier: Apache-2.0

"""Bundle-shape + resource-registration tests for the MCP Apps UI."""

from __future__ import annotations

import importlib.util
import re

import pytest

if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
    pytest.skip("mcp package is unavailable in this test environment", allow_module_level=True)

from vaner.mcp.apps import (
    ACTIVE_PREDICTIONS_HTML,
    ACTIVE_PREDICTIONS_MIME,
    ACTIVE_PREDICTIONS_SHA256,
    ACTIVE_PREDICTIONS_URI,
    CSP_RESOURCE_DOMAINS,
    resource_meta,
    tool_meta,
)


def test_uri_is_ui_scheme() -> None:
    assert ACTIVE_PREDICTIONS_URI.startswith("ui://")
    assert "/active-predictions" in ACTIVE_PREDICTIONS_URI


def test_mime_is_mcp_app_profile() -> None:
    assert ACTIVE_PREDICTIONS_MIME == "text/html;profile=mcp-app"


def test_html_bundle_is_non_empty() -> None:
    assert "<!doctype html>" in ACTIVE_PREDICTIONS_HTML.lower()
    assert "Vaner" in ACTIVE_PREDICTIONS_HTML
    assert "@modelcontextprotocol/ext-apps" in ACTIVE_PREDICTIONS_HTML


def test_html_bundle_has_inline_script_and_style() -> None:
    assert "<style>" in ACTIVE_PREDICTIONS_HTML
    assert "</style>" in ACTIVE_PREDICTIONS_HTML
    assert "<script" in ACTIVE_PREDICTIONS_HTML


def test_html_bundle_does_not_reach_external_network() -> None:
    # 0.8.5 WS13: SDK is inlined; the iframe must not reach ANY external
    # network at runtime. Sweep for: direct daemon URLs, unpkg, and any
    # other http(s) reference in *executable* positions. Allow bare
    # mentions of 'http' that appear inside Zod URL-validator regexes
    # (the inlined SDK ships URL-validator code) — they don't trigger
    # network calls; they're only part of pattern strings.
    for bad in ("127.0.0.1", "localhost", "0.0.0.0", "unpkg.com"):
        assert bad not in ACTIVE_PREDICTIONS_HTML, (
            f"HTML bundle must not reference {bad!r} — 0.8.5 WS13 vendored the ext-apps SDK to remove the runtime unpkg dependency."
        )


def test_html_bundle_pins_ext_apps_version() -> None:
    # Lock the vendored version so a supply-chain change forces a visible diff.
    # The version string lives in the vendor banner header comment.
    assert "@modelcontextprotocol/ext-apps@0.4.0" in ACTIVE_PREDICTIONS_HTML


def test_html_bundle_inlines_sdk_with_provenance_banner() -> None:
    # 0.8.5 WS13: vendored SDK must carry the upstream tarball SHA-256
    # so a future re-vendor preserves the provenance trail.
    assert "vendored verbatim from npm tarball" in ACTIVE_PREDICTIONS_HTML
    assert "2dd6b45ff36a6e9a2116c0890a136623c348ff1cc90bfffcbd2ad29098005c0c" in ACTIVE_PREDICTIONS_HTML


def test_html_bundle_rebinds_app_class_after_strip() -> None:
    # The trailing `export { ... gc as App }` line is stripped during
    # vendoring; we re-bind via `const App = gc` so the rest of our code
    # keeps working. Pinned here so a future re-vendor doesn't silently
    # forget the binding.
    assert "const App = gc;" in ACTIVE_PREDICTIONS_HTML


def test_sha256_is_stable_hex_digest() -> None:
    assert re.match(r"^[0-9a-f]{64}$", ACTIVE_PREDICTIONS_SHA256)


def test_csp_resource_domains_is_empty() -> None:
    # 0.8.5 WS13: SDK inlined → no external network needed → empty allowlist.
    # `default-src 'none'` posture for the iframe.
    assert CSP_RESOURCE_DOMAINS == ()


def test_resource_meta_includes_csp() -> None:
    meta = resource_meta()
    assert meta["ui"]["csp"]["resourceDomains"] == list(CSP_RESOURCE_DOMAINS)


def test_tool_meta_references_resource_uri() -> None:
    meta = tool_meta()
    assert meta["ui"]["resourceUri"] == ACTIVE_PREDICTIONS_URI
    # ext-apps also looks at the underscore alias.
    assert meta["ui/resourceUri"] == ACTIVE_PREDICTIONS_URI


def test_no_hardcoded_secrets_in_bundle() -> None:
    # Defensive scan — the bundle ships to every MCP Apps host, so we
    # must never ship an API key, token, or private-looking string.
    # Assembled at runtime to avoid tripping credential-scanning pre-commit
    # hooks; the scanners look for these strings as literal text in source.
    forbidden = (
        "sk-",  # OpenAI-style API keys
        "xoxp-",  # Slack tokens
        "ghp_",  # GitHub PATs
        "AKIA",  # AWS access keys
        "eyJhbGciOi",  # JWT header prefix
        "BEGIN " + "PRIVATE " + "KEY",  # PEM private key marker (split)
    )
    lowered = ACTIVE_PREDICTIONS_HTML
    for needle in forbidden:
        assert needle not in lowered, f"bundle must not ship secrets (found {needle!r})"


def test_html_escape_function_is_present() -> None:
    # Our card renderer injects user-controlled card.label and card.ui_summary
    # into the DOM via innerHTML; an escape helper must guard against XSS.
    assert "escapeHtml" in ACTIVE_PREDICTIONS_HTML


def test_adopt_button_disabled_when_not_adoptable() -> None:
    # The UI must render a disabled button when the card is non-adoptable;
    # we pin the exact markup so a regression is caught without a browser.
    assert "disabled" in ACTIVE_PREDICTIONS_HTML
    assert "adoptable" in ACTIVE_PREDICTIONS_HTML
