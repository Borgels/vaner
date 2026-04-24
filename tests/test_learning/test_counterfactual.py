# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

from vaner.learning.counterfactual import CounterfactualAnalyzer, CounterfactualRecord, _infer_root_cause

# ---------------------------------------------------------------------------
# _infer_root_cause
# ---------------------------------------------------------------------------


def test_infer_root_cause_abstain_too_eager():
    cause = _infer_root_cause("cold_miss", [], [], abstain_was_active=True)
    assert cause == "abstain_too_eager"


def test_infer_root_cause_cold_miss_no_helpful():
    cause = _infer_root_cause("cold_miss", [], ["wasted.py"], abstain_was_active=False)
    assert cause == "taxonomy"


def test_infer_root_cause_cold_miss_no_wasted():
    cause = _infer_root_cause("cold_miss", ["helpful.py"], [], abstain_was_active=False)
    assert cause == "retrieval"


def test_infer_root_cause_cold_miss_both():
    cause = _infer_root_cause("cold_miss", ["helpful.py"], ["wasted.py"], abstain_was_active=False)
    assert cause == "ranking"


def test_infer_root_cause_warm_start():
    cause = _infer_root_cause("warm_start", [], [], abstain_was_active=False)
    assert cause == "timing"


def test_infer_root_cause_taxonomy_miss():
    cause = _infer_root_cause("taxonomy_miss", [], [], abstain_was_active=False)
    assert cause == "taxonomy"


def test_infer_root_cause_retrieval_miss():
    cause = _infer_root_cause("retrieval_miss", [], [], abstain_was_active=False)
    assert cause == "retrieval"


def test_infer_root_cause_unknown_falls_back():
    cause = _infer_root_cause("completely_unknown", [], [], abstain_was_active=False)
    assert cause == "retrieval"


# ---------------------------------------------------------------------------
# CounterfactualAnalyzer.analyze()
# ---------------------------------------------------------------------------


def test_analyze_returns_record(tmp_path):
    analyzer = CounterfactualAnalyzer(tmp_path / "decisions")
    record = analyzer.analyze(
        prompt="fix the bug in the auth module",
        miss_type="cold_miss",
        helpful_paths=["src/auth/policy.py"],
        wasted_paths=[],
    )
    assert isinstance(record, CounterfactualRecord)
    assert record.root_cause == "retrieval"
    assert record.prompt_snippet.startswith("fix the bug")


def test_analyze_writes_json_file(tmp_path):
    decisions_dir = tmp_path / "decisions"
    analyzer = CounterfactualAnalyzer(decisions_dir)
    record = analyzer.analyze(
        prompt="implement new feature",
        miss_type="warm_start",
        helpful_paths=[],
        wasted_paths=[],
    )
    files = list(decisions_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["record_id"] == record.record_id
    assert payload["root_cause"] == "timing"


def test_analyze_truncates_long_prompt(tmp_path):
    analyzer = CounterfactualAnalyzer(tmp_path / "d")
    long_prompt = "a" * 500
    record = analyzer.analyze(prompt=long_prompt, miss_type="cold_miss", helpful_paths=[], wasted_paths=[])
    assert len(record.prompt_snippet) <= 120


def test_analyze_truncates_paths(tmp_path):
    analyzer = CounterfactualAnalyzer(tmp_path / "d")
    many_paths = [f"file_{i}.py" for i in range(50)]
    record = analyzer.analyze(
        prompt="test",
        miss_type="cold_miss",
        helpful_paths=many_paths,
        wasted_paths=many_paths,
    )
    assert len(record.helpful_paths) <= 20
    assert len(record.wasted_paths) <= 20


def test_analyze_abstain_was_active(tmp_path):
    analyzer = CounterfactualAnalyzer(tmp_path / "d")
    record = analyzer.analyze(
        prompt="anything",
        miss_type="cold_miss",
        helpful_paths=[],
        wasted_paths=[],
        abstain_was_active=True,
    )
    assert record.root_cause == "abstain_too_eager"


# ---------------------------------------------------------------------------
# _write() silently survives bad path
# ---------------------------------------------------------------------------


def test_write_survives_bad_path(tmp_path):
    # Point analyzer at a path that cannot be created (file exists at parent).
    bad_parent = tmp_path / "blocker.txt"
    bad_parent.write_text("block", encoding="utf-8")
    analyzer = CounterfactualAnalyzer(bad_parent / "decisions")
    record = CounterfactualRecord(
        record_id="test-id",
        ts=0.0,
        miss_type="cold_miss",
        prompt_snippet="x",
        helpful_paths=[],
        wasted_paths=[],
        root_cause="retrieval",
        metadata={},
    )
    # Should not raise
    analyzer._write(record)
