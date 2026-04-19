# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time
import uuid

from vaner.daemon.engine.planner import plan_targets
from vaner.models.signal import SignalEvent


def _signal(path: str) -> SignalEvent:
    return SignalEvent(
        id=str(uuid.uuid4()),
        source="test",
        kind="file_seen",
        timestamp=time.time(),
        payload={"path": path},
    )


def test_planner_respects_excluded_patterns(temp_repo):
    secret = temp_repo / "secret.env"
    secret.write_text("k=v\n", encoding="utf-8")
    targets = plan_targets(temp_repo, [_signal("secret.env")], ["**"], ["*.env"])
    assert targets == []


def test_planner_respects_allowed_paths(temp_repo):
    src = temp_repo / "src"
    src.mkdir()
    allowed_file = src / "ok.py"
    blocked_file = temp_repo / "blocked.py"
    allowed_file.write_text("x=1\n", encoding="utf-8")
    blocked_file.write_text("x=2\n", encoding="utf-8")
    signals = [_signal("src/ok.py"), _signal("blocked.py")]
    targets = plan_targets(temp_repo, signals, ["src/**"], [])
    assert targets == [allowed_file]
