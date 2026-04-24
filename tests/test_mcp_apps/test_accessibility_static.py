# SPDX-License-Identifier: Apache-2.0

"""Static accessibility smoke for the UI bundle.

A full axe-core / pa11y pass needs a headless browser. That's deferred
to 0.8.6 polish. What we *can* pin today: the HTML must include the
structural markers we rely on (aria-live region, aria-label on the list,
button labels, semantic list + headings) so the bundle never regresses
to a keyboard-unreachable / screen-reader-hostile state.
"""

from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
    pytest.skip("mcp package is unavailable in this test environment", allow_module_level=True)

from vaner.mcp.apps import ACTIVE_PREDICTIONS_HTML


def test_has_live_region_for_status() -> None:
    # Status updates (adopt success, refresh errors) need a live region
    # so screen readers announce them without user action.
    assert 'aria-live="polite"' in ACTIVE_PREDICTIONS_HTML
    assert 'role="status"' in ACTIVE_PREDICTIONS_HTML


def test_has_labelled_list() -> None:
    # The card list uses aria-labelledby pointing at the heading so the
    # list has accessible context.
    assert 'aria-labelledby="heading"' in ACTIVE_PREDICTIONS_HTML
    assert 'id="heading"' in ACTIVE_PREDICTIONS_HTML


def test_adopt_buttons_are_labelled() -> None:
    # Every dynamically-rendered adopt button is given an aria-label that
    # names the prediction so a screen-reader user understands which one
    # they'd adopt.
    assert 'aria-label="Adopt prediction:' in ACTIVE_PREDICTIONS_HTML


def test_uses_semantic_heading_and_list() -> None:
    # Real H1/OL, not div soup — matters for rotor navigation.
    assert "<h1" in ACTIVE_PREDICTIONS_HTML
    assert "<ol" in ACTIVE_PREDICTIONS_HTML
    # Cards are rendered as <li> elements inside the ol (dynamic render
    # uses document.createElement("li") + className = "card"); the marker
    # to match the class lives in the JS render path.
    assert 'className = "card"' in ACTIVE_PREDICTIONS_HTML


def test_focus_styles_defined() -> None:
    # Keyboard users need a visible focus ring. We define :focus-within
    # on the card and :focus on the Adopt button.
    assert ":focus-within" in ACTIVE_PREDICTIONS_HTML
    assert "button.adopt:focus" in ACTIVE_PREDICTIONS_HTML


def test_dark_mode_styles_defined() -> None:
    # High-contrast + dark-mode adaptation for low-vision users.
    assert "prefers-color-scheme: dark" in ACTIVE_PREDICTIONS_HTML
    assert "color-scheme: light dark" in ACTIVE_PREDICTIONS_HTML


def test_disabled_state_is_signaled_textually_not_just_color() -> None:
    # When Adopt is not available, the button text changes (not just the
    # color fades). Ensures color-only signaling is avoided.
    assert "disabled ? readinessLabel : adoptLabel" in ACTIVE_PREDICTIONS_HTML


def test_no_inline_tabindex_traps() -> None:
    # tabindex="-1" or tabindex=999 can create keyboard traps. We allow
    # no inline tabindex on the card grid — default tab order suffices.
    assert 'tabindex="-1"' not in ACTIVE_PREDICTIONS_HTML
    assert "tabindex=999" not in ACTIVE_PREDICTIONS_HTML
