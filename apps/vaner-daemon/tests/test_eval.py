"""Tests for EvalSignal persistence and detect_reprompt heuristic."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from vaner_runtime.eval import (
    EvalSignal,
    detect_reprompt,
    load_signals,
    record_signal,
)


# ---------------------------------------------------------------------------
# detect_reprompt
# ---------------------------------------------------------------------------

class TestDetectReprompt:
    def test_high_overlap_returns_true(self):
        history = ["How do I fix the broken authentication module?"]
        current = "How do I fix broken authentication module issues?"
        assert detect_reprompt(current, history) is True

    def test_low_overlap_returns_false(self):
        history = ["What is the weather like today?"]
        current = "Explain quantum entanglement in simple terms."
        assert detect_reprompt(current, history) is False

    def test_empty_history_returns_false(self):
        assert detect_reprompt("anything", []) is False

    def test_empty_current_returns_false(self):
        assert detect_reprompt("", ["some previous prompt"]) is False

    def test_stopwords_only_current_returns_false(self):
        # After stopword removal cur is empty
        assert detect_reprompt("the a an is it", ["the a an is it"]) is False

    def test_window_respected(self):
        # Only looks at last 3 prompts; old match beyond window is ignored
        history = ["Fix the auth bug"] + ["unrelated stuff"] * 3
        current = "Fix auth bug now"
        # "Fix the auth bug" is outside window=3 (index 0, window covers [-3:])
        assert detect_reprompt(current, history, window=3) is False

    def test_window_within_range_matches(self):
        history = ["unrelated", "unrelated", "Fix the authentication bug please"]
        current = "Fix authentication bug"
        assert detect_reprompt(current, history, window=3) is True

    def test_exact_repeat_returns_true(self):
        prompt = "How can I deploy the new model?"
        assert detect_reprompt(prompt, [prompt]) is True

    def test_partial_overlap_below_threshold(self):
        history = ["configure the server networking proxy settings manually"]
        current = "write unit tests for the payment gateway"
        assert detect_reprompt(current, history) is False


# ---------------------------------------------------------------------------
# record_signal + load_signals roundtrip
# ---------------------------------------------------------------------------

class TestSignalPersistence:
    def test_roundtrip(self, tmp_path):
        db = tmp_path / "eval.db"
        sig = EvalSignal(
            session_id="sess-1",
            prompt_hash="abc123",
            injected=True,
            reprompted=False,
            helpfulness=0.75,
            model_referenced=True,
            timestamp=time.time(),
        )
        record_signal(sig, db)
        loaded = load_signals(db, since_days=7)
        assert len(loaded) == 1
        s = loaded[0]
        assert s.session_id == "sess-1"
        assert s.prompt_hash == "abc123"
        assert s.injected is True
        assert s.reprompted is False
        assert s.helpfulness == pytest.approx(0.75)
        assert s.model_referenced is True

    def test_multiple_signals_ordered_desc(self, tmp_path):
        db = tmp_path / "eval.db"
        t_old = time.time() - 100
        t_new = time.time()
        for i, ts in enumerate([t_old, t_new]):
            record_signal(
                EvalSignal(
                    session_id=f"sess-{i}",
                    prompt_hash=f"hash-{i}",
                    injected=False,
                    reprompted=False,
                    helpfulness=None,
                    model_referenced=False,
                    timestamp=ts,
                ),
                db,
            )
        loaded = load_signals(db, since_days=7)
        assert len(loaded) == 2
        # Newest first
        assert loaded[0].session_id == "sess-1"
        assert loaded[1].session_id == "sess-0"

    def test_load_empty_when_db_missing(self, tmp_path):
        db = tmp_path / "nonexistent.db"
        assert load_signals(db) == []

    def test_since_days_filters_old_records(self, tmp_path):
        db = tmp_path / "eval.db"
        old_ts = time.time() - 10 * 86400  # 10 days ago
        record_signal(
            EvalSignal(
                session_id="old",
                prompt_hash="old_hash",
                injected=False,
                reprompted=False,
                helpfulness=None,
                model_referenced=False,
                timestamp=old_ts,
            ),
            db,
        )
        record_signal(
            EvalSignal(
                session_id="new",
                prompt_hash="new_hash",
                injected=True,
                reprompted=True,
                helpfulness=0.5,
                model_referenced=True,
                timestamp=time.time(),
            ),
            db,
        )
        loaded = load_signals(db, since_days=7)
        assert len(loaded) == 1
        assert loaded[0].session_id == "new"

    def test_null_helpfulness_persists(self, tmp_path):
        db = tmp_path / "eval.db"
        sig = EvalSignal(
            session_id="s",
            prompt_hash="h",
            injected=False,
            reprompted=False,
            helpfulness=None,
            model_referenced=False,
        )
        record_signal(sig, db)
        loaded = load_signals(db, since_days=7)
        assert loaded[0].helpfulness is None

    def test_db_parent_created_automatically(self, tmp_path):
        db = tmp_path / "nested" / "deep" / "eval.db"
        sig = EvalSignal(
            session_id="s",
            prompt_hash="h",
            injected=False,
            reprompted=False,
            helpfulness=None,
            model_referenced=False,
        )
        record_signal(sig, db)
        assert db.exists()
