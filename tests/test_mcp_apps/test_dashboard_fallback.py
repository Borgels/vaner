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
_prepared_work_fallback_text = _server._prepared_work_fallback_text


def _card(
    *,
    label: str,
    readiness: str = "ready",
    readiness_label: str = "Ready",
    eta_bucket_label: str | None = "Ready now",
    adoptable: bool = True,
    suppression_reason: str | None = None,
    id: str = "pred_test",
) -> dict:
    return {
        "id": id,
        "label": label,
        "readiness": readiness,
        "readiness_label": readiness_label,
        "eta_bucket_label": eta_bucket_label,
        "adoptable": adoptable,
        "suppression_reason": suppression_reason,
    }


def test_empty_cards_render_empty_state() -> None:
    text = _dashboard_fallback_text([])
    assert "preparing likely next work" in text
    assert "No prepared context" in text


def test_ready_card_renders_adoptable_marker_in_markdown() -> None:
    text = _dashboard_fallback_text([_card(label="Draft the project update", id="pred_xyz")])
    assert "**Draft the project update**" in text
    assert text.startswith("Vaner has 1 prepared context item(s):")
    assert "**Ready**" in text
    assert '`vaner.predictions.adopt id="pred_xyz"`' in text
    assert "`vaner.suggest`" in text


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
    assert "**Still gathering**" in text
    assert "~1 min" in text
    assert "`not_ready_yet`" in text
    # No adopt command for non-adoptable cards.
    assert "vaner.predictions.adopt" not in text


def test_multiple_cards_numbered_with_adopt_commands() -> None:
    text = _dashboard_fallback_text(
        [
            _card(label="First", id="pred_1"),
            _card(label="Second", adoptable=False, suppression_reason="stale", id="pred_2"),
            _card(label="Third", id="pred_3"),
        ]
    )
    assert "1. **Ready**" in text
    assert "2." in text and "**Second**" in text
    assert "3. **Ready**" in text
    # Only adoptable cards get an adopt command.
    assert '`vaner.predictions.adopt id="pred_1"`' in text
    assert '`vaner.predictions.adopt id="pred_3"`' in text
    assert '`vaner.predictions.adopt id="pred_2"`' not in text


def test_card_without_eta_label_does_not_crash() -> None:
    text = _dashboard_fallback_text([_card(label="No ETA", eta_bucket_label=None)])
    assert "**No ETA**" in text


def _pw_card(
    *,
    title: str,
    source_id: str = "wp_test",
    source_type: str = "work_product",
    kind: str = "draft",
    badge: str | None = None,
    confidence_label: str | None = None,
    freshness_label: str | None = None,
    target_label: str | None = None,
) -> dict:
    return {
        "id": "card_" + source_id,
        "source_id": source_id,
        "source_type": source_type,
        "kind": kind,
        "title": title,
        "badge": badge,
        "confidence_label": confidence_label,
        "freshness_label": freshness_label,
        "target_label": target_label,
    }


def test_prepared_work_empty_cards_render_empty_state() -> None:
    text = _prepared_work_fallback_text([])
    assert "no prepared work" in text.lower()


def test_prepared_work_card_includes_inspect_command() -> None:
    text = _prepared_work_fallback_text(
        [
            _pw_card(
                title="Draft release notes",
                source_id="wp_42",
                source_type="work_product",
                badge="Draft",
                freshness_label="fresh",
            )
        ]
    )
    assert "**Draft release notes**" in text
    assert "**Draft**" in text
    assert "fresh" in text
    assert '`vaner.work_products.inspect id="wp_42"`' in text


def test_prepared_work_prediction_card_uses_adopt_command() -> None:
    text = _prepared_work_fallback_text(
        [
            _pw_card(
                title="Draft answer for follow-up question",
                source_id="pred_99",
                source_type="prediction",
                kind="prediction",
                badge="Ready",
            )
        ]
    )
    assert '`vaner.predictions.adopt id="pred_99"`' in text
    # Per-card row should not surface an inspect command for predictions
    # (the trailing footer mentions inspect/export/feedback in general).
    assert "Inspect: " not in text


def test_prepared_work_multiple_cards_numbered() -> None:
    text = _prepared_work_fallback_text(
        [
            _pw_card(title="First", source_id="wp_1"),
            _pw_card(title="Second", source_id="wp_2"),
        ]
    )
    assert "1." in text and "**First**" in text
    assert "2." in text and "**Second**" in text
