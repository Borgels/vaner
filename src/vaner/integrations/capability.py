# SPDX-License-Identifier: Apache-2.0
"""Client capability tiers.

Vaner ships a single engine but the right delivery surface depends on what
the connected client can do. We classify the client into one of four tiers
during MCP ``initialize`` and pick defaults accordingly.

Tier definitions (mirror the 0.8.5 plan):

* Tier 1 — MCP only. Tools work; nothing else.
* Tier 2 — MCP + prompt guidance. Accepts a canonical guidance block.
* Tier 3 — MCP + prompt guidance + context mediation. Accepts injected
  digest / adopted-package blocks.
* Tier 4 — MCP Apps UI capable. Renders ``ui://`` resources inline.
"""

from __future__ import annotations

import logging
import weakref
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class ClientCapabilityTier(IntEnum):
    UNKNOWN = 0
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4


_UI_EXT_KEY = "io.modelcontextprotocol/ui"
_INJECTION_EXT_KEY = "vaner.context_injection"


@dataclass(frozen=True)
class TierDetection:
    """Explainable tier classification — includes the signals used."""

    tier: ClientCapabilityTier
    client_name: str | None = None
    client_version: str | None = None
    reason: str = "heuristic"


def detect_tier(client_params: Any | None) -> TierDetection:
    """Classify *client_params* (an MCP ``InitializeRequestParams``) into a tier.

    Returns :class:`TierDetection` so callers can log why a tier was chosen.
    Degrades to :attr:`ClientCapabilityTier.UNKNOWN` on missing/malformed
    input rather than raising.
    """
    if client_params is None:
        return TierDetection(tier=ClientCapabilityTier.UNKNOWN, reason="no_client_params")

    name = _safe_get(client_params, "clientInfo", "name")
    version = _safe_get(client_params, "clientInfo", "version")
    caps = _safe_attr(client_params, "capabilities")
    if caps is None:
        return TierDetection(
            tier=ClientCapabilityTier.TIER_1,
            client_name=name,
            client_version=version,
            reason="capabilities_absent",
        )

    experimental = _safe_attr(caps, "experimental") or {}
    if isinstance(experimental, dict) and _UI_EXT_KEY in experimental:
        return TierDetection(
            tier=ClientCapabilityTier.TIER_4,
            client_name=name,
            client_version=version,
            reason="ui_extension_advertised",
        )
    if isinstance(experimental, dict) and _INJECTION_EXT_KEY in experimental:
        return TierDetection(
            tier=ClientCapabilityTier.TIER_3,
            client_name=name,
            client_version=version,
            reason="vaner_injection_extension_advertised",
        )
    if _safe_attr(caps, "roots") is not None or _safe_attr(caps, "sampling") is not None:
        return TierDetection(
            tier=ClientCapabilityTier.TIER_2,
            client_name=name,
            client_version=version,
            reason="roots_or_sampling_present",
        )
    return TierDetection(
        tier=ClientCapabilityTier.TIER_1,
        client_name=name,
        client_version=version,
        reason="no_known_tier_markers",
    )


def _safe_attr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name, None)
    except Exception:  # pragma: no cover - defensive
        return None


def _safe_get(obj: Any, *path: str) -> Any:
    cur: Any = obj
    for part in path:
        cur = _safe_attr(cur, part)
        if cur is None:
            return None
    return cur


# ---------------------------------------------------------------------------
# Session cache
# ---------------------------------------------------------------------------


class _SessionTierCache:
    """Cache tier detection per live MCP session.

    Sessions are short-lived; we key by ``id(session)`` and hold a weak
    reference so we don't pin memory. If the SDK drops the session the
    entry disappears automatically.
    """

    def __init__(self) -> None:
        self._by_id: dict[int, TierDetection] = {}
        self._refs: dict[int, weakref.ReferenceType[Any]] = {}

    def get(self, session: Any) -> TierDetection | None:
        key = id(session)
        if key not in self._by_id:
            return None
        ref = self._refs.get(key)
        if ref is None or ref() is None:
            self._by_id.pop(key, None)
            self._refs.pop(key, None)
            return None
        return self._by_id[key]

    def put(self, session: Any, detection: TierDetection) -> None:
        key = id(session)

        def _cleanup(_ref: Any, k: int = key) -> None:
            self._by_id.pop(k, None)
            self._refs.pop(k, None)

        self._by_id[key] = detection
        try:
            self._refs[key] = weakref.ref(session, _cleanup)
        except TypeError:
            # Session not weakly-referenceable (e.g. a dict stub in tests);
            # fall back to a strong reference in the dict but no-op cleanup.
            self._refs[key] = weakref.ref(_DummyKeeper(session), _cleanup)


class _DummyKeeper:
    """Weakref target for objects that don't support weakref natively."""

    __slots__ = ("__weakref__", "inner")

    def __init__(self, inner: Any) -> None:
        self.inner = inner


_CACHE = _SessionTierCache()


def record_tier(session: Any, detection: TierDetection) -> None:
    _CACHE.put(session, detection)
    logger.info(
        "vaner.integrations.tier_detected",
        extra={
            "tier": int(detection.tier),
            "tier_name": detection.tier.name,
            "client_name": detection.client_name,
            "client_version": detection.client_version,
            "reason": detection.reason,
        },
    )


def current_tier(session: Any) -> ClientCapabilityTier:
    detection = _CACHE.get(session)
    return detection.tier if detection is not None else ClientCapabilityTier.UNKNOWN


def current_detection(session: Any) -> TierDetection | None:
    return _CACHE.get(session)


def reset_cache() -> None:
    """Test-only helper — clear the per-session cache."""
    _CACHE._by_id.clear()
    _CACHE._refs.clear()
