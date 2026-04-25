# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vaner.integrations.injection.handoff import (
    DEFAULT_TTL_SECONDS,
    consume_handoff,
    handoff_path,
    read_handoff,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fresh_payload(stashed_at: float = 1_777_000_000.0) -> dict:
    return {
        "intent": "Draft the project update",
        "resolution_id": "adopt-pred-xyz",
        "adopted_from_prediction_id": "pred-xyz",
        "prepared_briefing": "The team finished evidence gathering yesterday.",
        "predicted_response": "Here's a draft you asked for.",
        "provenance": {"mode": "predictive_hit"},
        "stashed_at": stashed_at,
    }


def test_read_returns_none_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "pending-adopt.json"
    assert read_handoff(path=missing) is None


def test_read_happy_path(tmp_path: Path) -> None:
    target = tmp_path / "pending-adopt.json"
    _write(target, _fresh_payload(stashed_at=1_777_000_000.0))

    res = read_handoff(path=target, now=1_777_000_030.0)
    assert res is not None
    assert res.intent == "Draft the project update"
    assert res.adopted_from_prediction_id == "pred-xyz"
    assert res.age_seconds == pytest.approx(30.0)
    assert res.path == target


def test_stale_drop_ignored(tmp_path: Path) -> None:
    target = tmp_path / "pending-adopt.json"
    _write(target, _fresh_payload(stashed_at=1_777_000_000.0))

    # 10 min + 1s older than the drop → over default TTL.
    res = read_handoff(
        path=target,
        now=1_777_000_000.0 + DEFAULT_TTL_SECONDS + 1.0,
    )
    assert res is None


def test_custom_ttl_extends_freshness(tmp_path: Path) -> None:
    target = tmp_path / "pending-adopt.json"
    _write(target, _fresh_payload(stashed_at=1_777_000_000.0))

    res = read_handoff(
        path=target,
        now=1_777_000_000.0 + 1_000.0,
        ttl_seconds=2_000,
    )
    assert res is not None


def test_missing_stashed_at_ignored(tmp_path: Path) -> None:
    target = tmp_path / "pending-adopt.json"
    payload = _fresh_payload()
    del payload["stashed_at"]
    _write(target, payload)

    assert read_handoff(path=target) is None


def test_malformed_json_ignored(tmp_path: Path) -> None:
    target = tmp_path / "pending-adopt.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")

    assert read_handoff(path=target) is None


def test_non_object_payload_ignored(tmp_path: Path) -> None:
    target = tmp_path / "pending-adopt.json"
    _write(target.parent / "x.json", {"foo": "bar"})
    target.write_text("[1, 2, 3]", encoding="utf-8")

    assert read_handoff(path=target) is None


def test_consume_deletes_file(tmp_path: Path) -> None:
    target = tmp_path / "pending-adopt.json"
    _write(target, _fresh_payload(stashed_at=1_777_000_000.0))

    res = consume_handoff(path=target, now=1_777_000_030.0)
    assert res is not None
    assert not target.exists(), "consume should remove the handoff file after read"


def test_consume_leaves_stale_file_alone(tmp_path: Path) -> None:
    target = tmp_path / "pending-adopt.json"
    _write(target, _fresh_payload(stashed_at=1_777_000_000.0))

    res = consume_handoff(
        path=target,
        now=1_777_000_000.0 + DEFAULT_TTL_SECONDS + 1.0,
    )
    assert res is None
    # Stale file is not our business to sweep — the desktop may rotate it.
    assert target.exists()


def test_handoff_path_platform_is_absolute() -> None:
    # We can't portably check the full path, but it must be absolute and
    # end with `pending-adopt.json` on every supported platform.
    p = handoff_path()
    assert p.is_absolute()
    assert p.name == "pending-adopt.json"


def test_linux_respects_xdg_state_home(monkeypatch) -> None:
    if not os.sys.platform.startswith("linux"):
        pytest.skip("XDG_STATE_HOME applies on Linux only")
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/xdg-state")
    p = handoff_path()
    assert str(p).startswith("/tmp/xdg-state/vaner/")
