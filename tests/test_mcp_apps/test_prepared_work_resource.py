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
    assert "<script>" in PREPARED_WORK_HTML
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


def test_prepared_work_bundle_has_no_external_runtime_network() -> None:
    assert CSP_RESOURCE_DOMAINS == ()
    for bad in ("127.0.0.1", "localhost", "0.0.0.0", "unpkg.com", "https://", "http://"):
        assert bad not in PREPARED_WORK_HTML


def test_prepared_work_tool_meta_references_resource_uri() -> None:
    meta = prepared_work_tool_meta()
    assert meta["ui"]["resourceUri"] == PREPARED_WORK_URI
    assert meta["ui/resourceUri"] == PREPARED_WORK_URI


def test_prepared_work_sha256_is_stable_hex_digest() -> None:
    assert re.match(r"^[0-9a-f]{64}$", PREPARED_WORK_SHA256)
