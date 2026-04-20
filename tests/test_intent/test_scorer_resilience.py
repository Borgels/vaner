# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent import scorer as scorer_module
from vaner.intent.scorer import IntentScorer


def test_load_model_gracefully_handles_backend_decode_errors(tmp_path, monkeypatch) -> None:
    model_path = tmp_path / "intent-model.json"
    model_path.write_text("{}", encoding="utf-8")

    class _BrokenBooster:
        def load_model(self, _path: str) -> None:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    class _FakeXgb:
        @staticmethod
        def Booster():
            return _BrokenBooster()

    monkeypatch.setattr(scorer_module, "xgb", _FakeXgb())
    scorer = IntentScorer()
    loaded = scorer.load_model(model_path, backend="xgboost")
    assert loaded is False
