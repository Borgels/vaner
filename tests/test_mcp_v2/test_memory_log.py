from __future__ import annotations

import re

from vaner.mcp.memory_log import append_log, memory_dir, tail_log, write_index
from vaner.models.scenario import Scenario

LOG_RE = re.compile(r"^## \[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] [a-z._]+ \| .+ \| [^|]+ \| [a-z_-]+ \| [a-z_-]+$")


def test_append_log_writes_grep_able_line(tmp_path) -> None:
    append_log(tmp_path, tool="vaner.status", label="status", decision_id=None, provenance_mode=None, memory_state=None)
    lines = tail_log(tmp_path, 1)
    assert len(lines) == 1
    assert LOG_RE.match(lines[0]) is not None


def test_tail_log_returns_last_n(tmp_path) -> None:
    for idx in range(10):
        append_log(tmp_path, tool="vaner.status", label=f"s{idx}", decision_id=None, provenance_mode=None, memory_state="candidate")
    lines = tail_log(tmp_path, 3)
    assert len(lines) == 3
    assert "s9" in lines[-1]


def test_write_index_groups_by_memory_state(tmp_path) -> None:
    scenarios = [
        Scenario(id="a", kind="change", memory_state="trusted"),
        Scenario(id="b", kind="change", memory_state="candidate"),
        Scenario(id="c", kind="change", memory_state="stale"),
    ]
    path = write_index(tmp_path, scenarios)
    text = path.read_text(encoding="utf-8")
    assert "## Trusted" in text
    assert "## Candidate" in text
    assert "## Stale" in text


def test_log_does_not_carry_semantic_meaning(tmp_path) -> None:
    text = memory_dir(tmp_path) / "log.md"
    append_log(tmp_path, tool="vaner.inspect", label="x", decision_id=None, provenance_mode=None, memory_state="trusted")
    assert text.exists()
