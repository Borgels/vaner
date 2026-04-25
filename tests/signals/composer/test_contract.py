# SPDX-License-Identifier: Apache-2.0
"""Tests for the ComposerAdapter contract — 0.8.7 WS2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vaner.signals.composer import (
    ComposerAdapterCapabilities,
    ComposerEvent,
    DraftIntentSnapshot,
)
from vaner.signals.composer.schema import ARTIFACT_RELATIVE, render_schema


def _l0_capabilities() -> ComposerAdapterCapabilities:
    return ComposerAdapterCapabilities(
        level="L0",
        emits=("submitted",),
        host_app="claude-code",
        host_kind="ai_chat_client",
    )


def _valid_l0_snapshot() -> DraftIntentSnapshot:
    return DraftIntentSnapshot(
        session_id="session-abc",
        snapshot_id="snap-001",
        timestamp="2026-04-25T20:14:45Z",
        lifecycle_state="submitted",
        text_hash="9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        length_chars=42,
        capabilities=_l0_capabilities(),
        workspace_id="/home/me/repos/example",
        field_role="agent_prompt",
    )


class TestSnapshotRoundTrip:
    def test_minimal_l0_snapshot_round_trips(self) -> None:
        snap = _valid_l0_snapshot()
        encoded = snap.model_dump_json()
        decoded = DraftIntentSnapshot.model_validate_json(encoded)
        assert decoded == snap

    def test_l1_snapshot_with_all_lifecycle_states(self) -> None:
        l1_caps = ComposerAdapterCapabilities(
            level="L1",
            emits=(
                "observing",
                "tentative",
                "stabilizing",
                "actionable",
                "submitted",
                "cleared",
                "abandoned",
            ),
            host_app="future-l1-host",
            host_kind="ide",
        )
        for state in l1_caps.emits:
            snap = DraftIntentSnapshot(
                session_id="s1",
                snapshot_id=f"snap-{state}",
                timestamp="2026-04-25T20:14:45Z",
                lifecycle_state=state,
                text_hash="0" * 64,
                length_chars=0,
                capabilities=l1_caps,
            )
            assert snap.lifecycle_state == state

    def test_envelope_wraps_snapshot(self) -> None:
        event = ComposerEvent(snapshot=_valid_l0_snapshot())
        encoded = event.model_dump_json()
        decoded = ComposerEvent.model_validate_json(encoded)
        assert decoded.snapshot == event.snapshot


class TestValidation:
    def test_negative_length_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DraftIntentSnapshot(
                session_id="s",
                snapshot_id="x",
                timestamp="t",
                lifecycle_state="submitted",
                text_hash="h",
                length_chars=-1,
                capabilities=_l0_capabilities(),
            )

    def test_unknown_lifecycle_state_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DraftIntentSnapshot(
                session_id="s",
                snapshot_id="x",
                timestamp="t",
                lifecycle_state="not-a-state",  # type: ignore[arg-type]
                text_hash="h",
                length_chars=0,
                capabilities=_l0_capabilities(),
            )

    def test_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DraftIntentSnapshot(
                session_id="s",
                snapshot_id="x",
                timestamp="t",
                lifecycle_state="submitted",
                text_hash="h",
                length_chars=0,
                capabilities=_l0_capabilities(),
                inferred_intent_confidence=1.5,
            )

    def test_unknown_capability_level_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ComposerAdapterCapabilities(
                level="L9",  # type: ignore[arg-type]
                emits=("submitted",),
                host_app="x",
                host_kind="ai_chat_client",
            )

    def test_unknown_host_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ComposerAdapterCapabilities(
                level="L0",
                emits=("submitted",),
                host_app="x",
                host_kind="phone",  # type: ignore[arg-type]
            )


class TestPrivacyContract:
    """The text_hash field is the only draft-derived data in 0.8.7.

    Adapters must not be allowed to smuggle raw text through other
    fields by accident — the contract has no field for it. These
    tests pin the privacy invariant in place.
    """

    def test_no_field_named_text_or_preview(self) -> None:
        fields = set(DraftIntentSnapshot.model_fields)
        for forbidden in ("text", "draft_text", "raw_text", "preview", "text_preview"):
            assert forbidden not in fields, (
                f"DraftIntentSnapshot must not expose a {forbidden!r} field — raw draft text never crosses the adapter boundary in 0.8.7."
            )

    def test_no_field_named_local_text_ref(self) -> None:
        # local_text_ref is in the longer product spec but deferred until
        # a redaction module exists. v0.8.7 must not ship it.
        assert "local_text_ref" not in DraftIntentSnapshot.model_fields


class TestSchemaArtifact:
    """The committed JSON Schema artifact must match what pydantic produces.

    CI runs the regeneration script and `git diff --exit-code` to enforce
    no drift. This test catches drift locally before committing.
    """

    def test_committed_artifact_matches_pydantic_output(self) -> None:
        repo_root = _find_repo_root()
        committed = (repo_root / ARTIFACT_RELATIVE).read_text(encoding="utf-8")
        regenerated = render_schema()
        assert committed == regenerated, "JSON Schema artifact is stale. Re-run `vaner-composer-schema` and commit the result."

    def test_artifact_is_valid_json(self) -> None:
        repo_root = _find_repo_root()
        text = (repo_root / ARTIFACT_RELATIVE).read_text(encoding="utf-8")
        # Will raise if not valid JSON.
        json.loads(text)

    def test_console_script_writes_deterministically(self, tmp_path: Path) -> None:
        # Two consecutive runs produce byte-identical output.
        first = render_schema()
        second = render_schema()
        assert first == second


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("repo root not found")
