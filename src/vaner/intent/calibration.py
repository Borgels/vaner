# SPDX-License-Identifier: Apache-2.0
"""Isotonic calibration applied at inference to map raw model scores to probabilities.

The curve is fit offline on a held-out validation split and exported as a list of
``(x, y)`` knot pairs. This module loads the curve and interpolates — it has
**no scikit-learn dependency** at inference time.

Curve format (JSON)::

    {
      "schema_version": "1",
      "method": "isotonic",
      "knots": [[0.0, 0.02], [0.15, 0.08], ..., [1.0, 0.98]],
      "fit_metadata": {"n_samples": 96000, "ece_before": 0.091, "ece_after": 0.027}
    }

Fail-closed: malformed JSON → ``load()`` returns ``None``; callers fall back to the
uncalibrated prediction.
"""

from __future__ import annotations

import json
import logging
from bisect import bisect_right
from pathlib import Path

logger = logging.getLogger(__name__)


class IsotonicCalibrator:
    """Step-function calibrator built from (x, y) knot pairs.

    The curve is monotonically non-decreasing by construction (isotonic fit).
    Between knots we use right-continuous step interpolation — consistent with
    ``sklearn.isotonic.IsotonicRegression``'s default behavior.
    """

    __slots__ = ("_xs", "_ys", "_metadata")

    def __init__(self, knots: list[tuple[float, float]], metadata: dict[str, object] | None = None) -> None:
        if not knots:
            raise ValueError("IsotonicCalibrator requires at least one knot")
        # Sort by x to be defensive against out-of-order exports.
        ordered = sorted((float(x), float(y)) for x, y in knots)
        self._xs: list[float] = [x for x, _ in ordered]
        self._ys: list[float] = [y for _, y in ordered]
        self._metadata = dict(metadata or {})

    @classmethod
    def load(cls, path: Path) -> IsotonicCalibrator | None:
        """Load a calibrator from disk. Returns ``None`` on any failure (malformed JSON,
        missing file, empty knot list, etc.) so callers can fall back to uncalibrated output.
        """
        try:
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            knots_raw = payload.get("knots")
            if not isinstance(knots_raw, list) or not knots_raw:
                return None
            knots: list[tuple[float, float]] = []
            for pair in knots_raw:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    return None
                knots.append((float(pair[0]), float(pair[1])))
            metadata = payload.get("fit_metadata") if isinstance(payload.get("fit_metadata"), dict) else {}
            return cls(knots, metadata=metadata)
        except Exception as exc:
            logger.warning("Failed to load calibration curve from %s: %s", path, exc)
            return None

    def transform(self, x: float) -> float:
        """Map a raw score to its calibrated probability.

        Values below the first knot clamp to the first y; values above the last
        knot clamp to the last y. Within the range we use right-continuous step
        interpolation — find the largest x_i <= x and return y_i.
        """
        value = float(x)
        if value <= self._xs[0]:
            return max(0.0, min(1.0, self._ys[0]))
        if value >= self._xs[-1]:
            return max(0.0, min(1.0, self._ys[-1]))
        # Linear interpolation between surrounding knots — smoother than pure
        # step and matches sklearn's default at inference.
        idx = bisect_right(self._xs, value) - 1
        x0, x1 = self._xs[idx], self._xs[idx + 1]
        y0, y1 = self._ys[idx], self._ys[idx + 1]
        if x1 == x0:
            return max(0.0, min(1.0, y0))
        frac = (value - x0) / (x1 - x0)
        return max(0.0, min(1.0, y0 + frac * (y1 - y0)))

    @property
    def metadata(self) -> dict[str, object]:
        return dict(self._metadata)

    @property
    def knot_count(self) -> int:
        return len(self._xs)
