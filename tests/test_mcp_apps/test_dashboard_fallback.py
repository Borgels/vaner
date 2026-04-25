# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the dashboard text-fallback formatter.

These exercise `_dashboard_fallback_text` directly so the text-only path
(Tier 1/2 MCP clients) is pinned without needing a full MCP server build.
"""

from __future__ import annotations

import importlib
import importlib.util

import pytest

if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
    pytest.skip("mcp package is unavailable in this test environment", allow_module_level=True)

_server = importlib.import_module("vaner.mcp.server")
_dashboard_fallback_text = _server._dashboard_fallback_text


def _card(
    *,
    label: str,
    readiness: str = "ready",
    readiness_label: str = "Ready",
    eta_bucket_label: str | None = "Ready now",
    adoptable: bool = True,
    suppression_reason: str | None = None,
) -> dict:
    return {
        "label": label,
        "readiness": readiness,
        "readiness_label": readiness_label,
        "eta_bucket_label": eta_bucket_label,
        "adoptable": adoptable,
        "suppression_reason": suppression_reason,
    }


def test_empty_cards_render_empty_state() -> None:
    text = _dashboard_fallback_text([])
    assert "preparing likely next steps" in text
    assert "No adoptable predictions" in text


def test_ready_card_renders_adoptable_marker() -> None:
    text = _dashboard_fallback_text([_card(label="Draft the project update")])
    assert '"Draft the project update"' in text
    assert text.startswith("Vaner has 1 active prediction(s):")
    assert "Use vaner.predictions.adopt" in text


def test_non_adoptable_card_shows_suppression_reason() -> None:
    text = _dashboard_fallback_text(
        [
            _card(
                label="Still gathering",
                readiness="evidence_gathering",
                readiness_label="Gathering evidence",
                eta_bucket_label="~1 min",
                adoptable=False,
                suppression_reason="not_ready_yet",
            )
        ]
    )
    assert '"Still gathering"' in text
    assert "~1 min" in text
    assert "not_ready_yet" in text


def test_multiple_cards_numbered() -> None:
    text = _dashboard_fallback_text(
        [
            _card(label="First"),
            _card(label="Second", adoptable=False, suppression_reason="stale"),
            _card(label="Third"),
        ]
    )
    assert "1. Ready" in text
    assert "2." in text and '"Second"' in text
    assert "3. Ready" in text


def test_card_without_eta_label_does_not_crash() -> None:
    text = _dashboard_fallback_text([_card(label="No ETA", eta_bucket_label=None)])
    assert '"No ETA"' in text
