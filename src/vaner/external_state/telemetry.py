# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

from vaner.external_state.models import ExternalStateSnapshot


def redacted_snapshot_telemetry(snapshot: ExternalStateSnapshot, *, status: str, latency_ms: float) -> dict[str, Any]:
    """Return telemetry-safe metadata for an external-state snapshot."""

    return {
        "provider_id": snapshot.provider_id,
        "capability": snapshot.capability,
        "status": status,
        "latency_ms": round(max(0.0, float(latency_ms)), 3),
        "sensitivity_class": snapshot.sensitivity_class.value,
        "freshness_class": snapshot.freshness_class.value,
        "captured_at": snapshot.captured_at,
        "expires_at": snapshot.expires_at,
        "payload_fingerprint": snapshot.payload_fingerprint,
    }
