# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import re

import pytest

if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
    pytest.skip("mcp package is unavailable in this test environment", allow_module_level=True)

from vaner.mcp.apps import (
    CSP_RESOURCE_DOMAINS,
    PREPARED_WORK_HTML,
    PREPARED_WORK_MIME,
    PREPARED_WORK_SHA256,
    PREPARED_WORK_URI,
    prepared_work_tool_meta,
)


def test_prepared_work_uri_and_mime_are_mcp_app_profile() -> None:
    assert PREPARED_WORK_URI == "ui://vaner/prepared-work"
    assert PREPARED_WORK_MIME == "text/html;profile=mcp-app"


def test_prepared_work_html_bundle_shape() -> None:
    assert "<!doctype html>" in PREPARED_WORK_HTML.lower()
    assert "Prepared work" in PREPARED_WORK_HTML
    assert "<style>" in PREPARED_WORK_HTML
    # The bundle now imports the vendored ext-apps SDK as a module script.
    assert "<script type=\"module\">" in PREPARED_WORK_HTML
    assert 'id="cards"' in PREPARED_WORK_HTML


def test_prepared_work_bundle_renders_user_facing_card_fields_and_actions() -> None:
    for marker in (
        "card.title",
        "card.summary",
        "card.why_prepared",
        "card.action_note",
        "card.confidence_label",
        "card.freshness_label",
        "card.target_label",
        "card.evidence_count",
        "card.primary_action",
        "card.secondary_actions",
        "button.dataset.kind",
        'setAttribute("aria-label"',
        'aria-label="Prepared work actions"',
    ):
        assert marker in PREPARED_WORK_HTML
    for internal in ("adoptability", "self_eval", "lifecycle", "raw_score"):
        assert internal not in PREPARED_WORK_HTML


def test_prepared_work_bundle_wires_actions_to_mcp_tools() -> None:
    """Action buttons must dispatch real ``vaner.*`` tools via App.callTool."""
    # The bundle uses the vendored ext-apps SDK and instantiates an App.
    assert "const App = gc;" in PREPARED_WORK_HTML
    assert "new App({" in PREPARED_WORK_HTML
    # callTool dispatch path exists.
    assert ".callTool(tool, args)" in PREPARED_WORK_HTML
    # Default tool routing covers the five PreparedWorkActionKind values.
    for tool in (
        "vaner.work_products.inspect",
        "vaner.work_products.export",
        "vaner.work_products.dismiss",
        "vaner.work_products.feedback",
        "vaner.predictions.adopt",
    ):
        assert tool in PREPARED_WORK_HTML
    # The bundle re-queries the dashboard on a refresh interval so the panel
    # stays alive when state changes.
    assert "vaner.prepared_work.dashboard" in PREPARED_WORK_HTML
    assert "REFRESH_INTERVAL_MS" in PREPARED_WORK_HTML


def test_prepared_work_bundle_has_no_external_runtime_network() -> None:
    """The bundle must not reach any concrete external host at runtime.

    The vendored ext-apps SDK contains URL-pattern strings inside its
    Zod validators (mirrors the active_predictions.html exemption); we
    assert against domain references rather than the bare ``http://``
    substring.
    """
    assert CSP_RESOURCE_DOMAINS == ()
    for bad in ("127.0.0.1", "localhost", "0.0.0.0", "unpkg.com"):
        assert bad not in PREPARED_WORK_HTML


def test_prepared_work_tool_meta_references_resource_uri() -> None:
    meta = prepared_work_tool_meta()
    assert meta["ui"]["resourceUri"] == PREPARED_WORK_URI
    assert meta["ui/resourceUri"] == PREPARED_WORK_URI


def test_prepared_work_sha256_is_stable_hex_digest() -> None:
    assert re.match(r"^[0-9a-f]{64}$", PREPARED_WORK_SHA256)
