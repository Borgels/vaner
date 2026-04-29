# SPDX-License-Identifier: Apache-2.0
"""Tests for engine.observe() composer-lifecycle dispatch — 0.8.7 WS3.

Validates:

- ``observe()`` validates a ``composer_lifecycle`` payload as a
  ``DraftIntentSnapshot`` BEFORE it reaches the signal-event store; a
  malformed payload raises and is not persisted.
- A valid payload is persisted to ``signal_events`` exactly once and
  the typed snapshot is published to the composer signal pump.
- Non-composer kinds bypass the validator and reach the store
  unchanged (existing behavior preserved).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.models.signal import KIND_COMPOSER_LIFECYCLE, SignalEvent
from vaner.signals.composer import DraftIntentSnapshot

pytestmark = pytest.mark.asyncio


async def _stub_llm(_prompt: str) -> str:
    return '{"ranked_files": [], "semantic_intent": "", "confidence": 0.0, "follow_on": []}'


def _seed_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "sample.py").write_text("def hi():\n    return 'hi'\n")


def _make_engine(repo_root: Path) -> VanerEngine:
    _seed_repo(repo_root)
    engine = VanerEngine(adapter=CodeRepoAdapter(repo_root), llm=_stub_llm)
    engine.config.compute.idle_only = False
    return engine


def _valid_snapshot_payload() -> dict[str, object]:
    return {
        "session_id": "session-abc",
        "snapshot_id": "snap-001",
        "timestamp": "2026-04-25T20:14:45Z",
        "lifecycle_state": "submitted",
        "text_hash": "0" * 64,
        "length_chars": 42,
        "capabilities": {
            "level": "L0",
            "emits": ["submitted"],
            "host_app": "claude-code",
            "host_kind": "ai_chat_client",
        },
    }


def _signal(payload: dict[str, object], *, event_id: str = "evt-1") -> SignalEvent:
    return SignalEvent(
        id=event_id,
        source="composer-adapter:claude-code",
        kind=KIND_COMPOSER_LIFECYCLE,
        timestamp=0.0,
        payload=payload,
    )


async def test_valid_composer_payload_publishes_to_pump_and_persists(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path / "repo")
    seen: list[DraftIntentSnapshot] = []

    async def collector(snap: DraftIntentSnapshot) -> None:
        seen.append(snap)

    engine.composer_signal_pump.subscribe(collector)

    await engine.observe(_signal(_valid_snapshot_payload()))

    assert len(seen) == 1
    assert seen[0].lifecycle_state == "submitted"
    assert seen[0].session_id == "session-abc"
    assert seen[0].text_hash == "0" * 64
    # Persistence: exactly one row landed in the store.
    rows = await engine.store.list_signal_events(limit=10)
    assert len(rows) == 1
    assert rows[0].kind == KIND_COMPOSER_LIFECYCLE


async def test_composer_payload_creates_metadata_only_v2_prediction(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path / "repo")
    payload = _valid_snapshot_payload()
    payload["inferred_intent_label"] = "Add parser tests"
    payload["inferred_intent_confidence"] = 0.82

    await engine.observe(_signal(payload, event_id="evt-v2"))

    active = engine.get_active_predictions()
    composer = [item for item in active if item.spec.source == "composer_intent"]
    assert len(composer) == 1
    prompt = composer[0]
    assert prompt.spec.structured is not None
    assert prompt.spec.structured.readiness_mode == "evidence_ready"
    assert prompt.spec.structured.action_type == "test"
    assert prompt.artifacts.composer_metadata["composer_event_id"] == "evt-v2"
    assert "text_hash" not in prompt.artifacts.composer_metadata


async def test_malformed_composer_payload_raises_and_does_not_persist(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path / "repo")
    bad = _valid_snapshot_payload()
    bad["lifecycle_state"] = "not-a-state"  # invalid literal

    with pytest.raises(ValidationError):
        await engine.observe(_signal(bad, event_id="bad-1"))

    rows = await engine.store.list_signal_events(limit=10)
    assert rows == [], "malformed composer payload must not reach the store"


async def test_missing_required_field_raises_and_does_not_persist(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path / "repo")
    bad = _valid_snapshot_payload()
    del bad["text_hash"]

    with pytest.raises(ValidationError):
        await engine.observe(_signal(bad, event_id="bad-2"))

    rows = await engine.store.list_signal_events(limit=10)
    assert rows == []


async def test_non_composer_kind_bypasses_validator(tmp_path: Path) -> None:
    """Existing signal kinds must be unaffected by WS3's dispatch branch."""
    engine = _make_engine(tmp_path / "repo")
    seen: list[DraftIntentSnapshot] = []

    async def collector(snap: DraftIntentSnapshot) -> None:
        seen.append(snap)

    engine.composer_signal_pump.subscribe(collector)

    other = SignalEvent(
        id="evt-other",
        source="git",
        kind="commit",
        timestamp=0.0,
        payload={"sha": "abc123"},
    )
    await engine.observe(other)

    rows = await engine.store.list_signal_events(limit=10)
    assert len(rows) == 1
    assert rows[0].kind == "commit"
    assert seen == []


async def test_publish_swallows_subscriber_failures(tmp_path: Path) -> None:
    """One bad subscriber must not block other subscribers (or persistence)."""
    engine = _make_engine(tmp_path / "repo")
    delivered: list[str] = []

    async def fail(_snap: DraftIntentSnapshot) -> None:
        raise RuntimeError("intentional failure")

    async def succeed(snap: DraftIntentSnapshot) -> None:
        delivered.append(snap.snapshot_id)

    engine.composer_signal_pump.subscribe(fail)
    engine.composer_signal_pump.subscribe(succeed)

    await engine.observe(_signal(_valid_snapshot_payload()))

    assert delivered == ["snap-001"]
    rows = await engine.store.list_signal_events(limit=10)
    assert len(rows) == 1


async def test_pump_subscribe_unsubscribe(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path / "repo")
    delivered: list[str] = []

    async def collect(snap: DraftIntentSnapshot) -> None:
        delivered.append(snap.snapshot_id)

    pump = engine.composer_signal_pump
    pump.subscribe(collect)
    assert pump.subscriber_count == 1
    pump.unsubscribe(collect)
    assert pump.subscriber_count == 0

    await engine.observe(_signal(_valid_snapshot_payload()))
    assert delivered == []


async def test_pump_publishes_directly_without_engine() -> None:
    """Smoke test: pump can be exercised with no engine."""
    from vaner.signals.composer import ComposerSignalPump

    pump = ComposerSignalPump()
    delivered: list[str] = []

    async def collect(snap: DraftIntentSnapshot) -> None:
        delivered.append(snap.snapshot_id)

    pump.subscribe(collect)
    snap = DraftIntentSnapshot.model_validate(_valid_snapshot_payload())
    await pump.publish(snap)
    assert delivered == ["snap-001"]
