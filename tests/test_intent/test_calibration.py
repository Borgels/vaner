# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

import pytest

from vaner.intent.calibration import IsotonicCalibrator

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_requires_at_least_one_knot():
    with pytest.raises(ValueError):
        IsotonicCalibrator(knots=[])


def test_single_knot_all_values_map_to_y():
    cal = IsotonicCalibrator(knots=[(0.5, 0.8)])
    assert cal.transform(0.1) == pytest.approx(0.8)
    assert cal.transform(0.5) == pytest.approx(0.8)
    assert cal.transform(0.9) == pytest.approx(0.8)


def test_knot_sorting_is_robust():
    cal = IsotonicCalibrator(knots=[(1.0, 0.9), (0.0, 0.1), (0.5, 0.5)])
    assert cal.transform(0.0) == pytest.approx(0.1)
    assert cal.transform(1.0) == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# transform()
# ---------------------------------------------------------------------------


def _sample_cal() -> IsotonicCalibrator:
    # Monotonic non-decreasing isotonic curve
    return IsotonicCalibrator(knots=[(0.0, 0.02), (0.25, 0.18), (0.5, 0.5), (0.75, 0.82), (1.0, 0.98)])


def test_transform_below_first_knot_clamps():
    cal = _sample_cal()
    assert cal.transform(-1.0) == pytest.approx(0.02)


def test_transform_above_last_knot_clamps():
    cal = _sample_cal()
    assert cal.transform(2.0) == pytest.approx(0.98)


def test_transform_interpolates_linearly():
    cal = _sample_cal()
    # Midpoint between (0.25, 0.18) and (0.5, 0.5)
    assert cal.transform(0.375) == pytest.approx(0.34)


def test_transform_at_knot_returns_exact_y():
    cal = _sample_cal()
    assert cal.transform(0.5) == pytest.approx(0.5)


def test_transform_clamps_to_unit_interval():
    cal = IsotonicCalibrator(knots=[(0.0, -0.5), (1.0, 1.5)])
    # Extremes forced into [0, 1]
    assert cal.transform(-10.0) == 0.0
    assert cal.transform(10.0) == 1.0


def test_transform_monotonic_non_decreasing():
    cal = _sample_cal()
    xs = [i / 100.0 for i in range(101)]
    ys = [cal.transform(x) for x in xs]
    for earlier, later in zip(ys, ys[1:], strict=False):
        assert later >= earlier - 1e-9


# ---------------------------------------------------------------------------
# load() — disk roundtrip
# ---------------------------------------------------------------------------


def _write_curve(tmp_path, payload):
    p = tmp_path / "calibration_curve.json"
    p.write_text(json.dumps(payload))
    return p


def test_load_missing_file_returns_none(tmp_path):
    assert IsotonicCalibrator.load(tmp_path / "no_such.json") is None


def test_load_malformed_json_returns_none(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not: valid json")
    assert IsotonicCalibrator.load(p) is None


def test_load_non_dict_returns_none(tmp_path):
    p = _write_curve(tmp_path, ["not", "a", "dict"])
    assert IsotonicCalibrator.load(p) is None


def test_load_missing_knots_returns_none(tmp_path):
    p = _write_curve(tmp_path, {"schema_version": "1", "method": "isotonic"})
    assert IsotonicCalibrator.load(p) is None


def test_load_empty_knots_returns_none(tmp_path):
    p = _write_curve(tmp_path, {"knots": []})
    assert IsotonicCalibrator.load(p) is None


def test_load_malformed_knot_pair_returns_none(tmp_path):
    p = _write_curve(tmp_path, {"knots": [[0.0]]})  # missing y
    assert IsotonicCalibrator.load(p) is None


def test_load_valid_curve_roundtrip(tmp_path):
    payload = {
        "schema_version": "1",
        "method": "isotonic",
        "knots": [[0.0, 0.1], [0.5, 0.4], [1.0, 0.9]],
        "fit_metadata": {"n_samples": 1000, "ece_before": 0.2, "ece_after": 0.05},
    }
    p = _write_curve(tmp_path, payload)
    cal = IsotonicCalibrator.load(p)
    assert cal is not None
    assert cal.knot_count == 3
    assert cal.transform(0.5) == pytest.approx(0.4)
    assert cal.metadata["n_samples"] == 1000


def test_load_ignores_bad_metadata(tmp_path):
    p = _write_curve(tmp_path, {"knots": [[0.0, 0.0], [1.0, 1.0]], "fit_metadata": "not a dict"})
    cal = IsotonicCalibrator.load(p)
    assert cal is not None
    assert cal.metadata == {}
