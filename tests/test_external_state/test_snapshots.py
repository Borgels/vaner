# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.external_state import (
    ExternalStateFreshnessClass,
    ExternalStateSensitivity,
    ExternalStateSnapshot,
    redacted_snapshot_telemetry,
)
from vaner.store.artefacts import ArtefactStore


def test_snapshot_fingerprint_is_stable_and_telemetry_redacts_payload() -> None:
    snapshot = ExternalStateSnapshot.build(
        provider_id="provider",
        capability="get_quote",
        freshness_class=ExternalStateFreshnessClass.MARKET_SNAPSHOT,
        sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
        payload={"symbol": "REDACTME", "price": 123.45},
        ttl_seconds=30,
        captured_at=10.0,
    )
    same = ExternalStateSnapshot.build(
        provider_id="provider",
        capability="get_quote",
        freshness_class=ExternalStateFreshnessClass.MARKET_SNAPSHOT,
        sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
        payload={"price": 123.45, "symbol": "REDACTME"},
        ttl_seconds=30,
        captured_at=10.0,
    )

    assert snapshot.payload_fingerprint == same.payload_fingerprint
    telemetry = redacted_snapshot_telemetry(snapshot, status="ok", latency_ms=12.3456)
    assert telemetry["payload_fingerprint"] == snapshot.payload_fingerprint
    assert "REDACTME" not in str(telemetry)
    assert "payload" not in telemetry


async def test_external_state_snapshot_persists_and_filters_stale(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = ArtefactStore(tmp_path / "artefacts.db")
    await store.initialize()
    fresh = ExternalStateSnapshot.build(
        provider_id="provider",
        capability="get_quote",
        freshness_class=ExternalStateFreshnessClass.MARKET_SNAPSHOT,
        sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
        payload={"quote": "redacted by callers"},
        captured_at=10.0,
        ttl_seconds=100.0,
    )
    stale = ExternalStateSnapshot.build(
        provider_id="provider",
        capability="get_quote",
        freshness_class=ExternalStateFreshnessClass.MARKET_SNAPSHOT,
        sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
        payload={"quote": "old"},
        captured_at=1.0,
        ttl_seconds=1.0,
    )
    await store.upsert_external_state_snapshot(fresh)
    await store.upsert_external_state_snapshot(stale)

    saved = await store.get_external_state_snapshot(fresh.id)
    assert saved is not None
    assert saved.payload == fresh.payload

    rows = await store.list_external_state_snapshots(capability="get_quote", now=20.0)
    assert [row.id for row in rows] == [fresh.id]
    rows_with_stale = await store.list_external_state_snapshots(capability="get_quote", include_stale=True, now=20.0)
    assert {row.id for row in rows_with_stale} == {fresh.id, stale.id}
