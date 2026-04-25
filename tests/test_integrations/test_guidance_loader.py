# SPDX-License-Identifier: Apache-2.0

import pytest

from vaner.integrations.guidance import (
    available_variants,
    current_version,
    load_guidance,
)


def test_canonical_loads_with_expected_frontmatter() -> None:
    doc = load_guidance("canonical")
    assert doc.variant == "canonical"
    assert doc.version == 1
    assert doc.minimum_vaner_version == "0.8.5"
    # Canonical must list the core tools agents are expected to call.
    assert "vaner.predictions.active" in doc.recommended_tools
    assert "vaner.predictions.adopt" in doc.recommended_tools
    assert "vaner.resolve" in doc.recommended_tools


def test_body_contains_operational_rules() -> None:
    doc = load_guidance("canonical")
    body = doc.as_text()
    # Behavior-shaping language the spec requires.
    assert "Do not call Vaner mechanically" in body
    assert "Prefer an already-adopted Vaner package" in body
    # Trigger conditions, not marketing.
    assert "when" in body.lower()


def test_all_variants_parseable() -> None:
    for name in available_variants():
        doc = load_guidance(name)
        assert doc.variant == name
        assert doc.version >= 1
        assert doc.as_text().strip()


def test_unknown_variant_raises() -> None:
    with pytest.raises(ValueError):
        load_guidance("wobble")  # type: ignore[arg-type]


def test_current_version_returns_canonical_version() -> None:
    assert current_version() == load_guidance("canonical").version


def test_weak_variant_is_shorter_than_canonical() -> None:
    canonical = load_guidance("canonical").as_text()
    weak = load_guidance("weak").as_text()
    assert len(weak) < len(canonical), "weak variant should be a shorter/compressed guidance for minimal prompt overhead"


def test_strong_variant_mentions_adopted_package_marker() -> None:
    doc = load_guidance("strong")
    body = doc.as_text()
    # Strong is for clients that can see the injected context markers.
    assert "<VANER_ADOPTED_PACKAGE>" in body
    assert "<VANER_PREPARED_WORK_DIGEST>" in body


def test_as_dict_shape() -> None:
    doc = load_guidance("canonical")
    data = doc.as_dict()
    assert data["variant"] == "canonical"
    assert data["version"] >= 1
    assert isinstance(data["recommended_tools"], list)
    assert data["body"].strip()
