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


class ClientFamily(IntEnum):
    """Normalised host family derived from ``clientInfo.name``.

    Tier detection answers *what the host can render*. ClientFamily answers
    *which host is rendering it* — useful for tiny per-host UX tweaks
    (markdown vs plain, slash-command hints, dashboard wording) without
    branching on capability flags. ``UNKNOWN`` covers everything we don't
    have a tested heuristic for; treat it the same as a generic Tier-aware
    host.
    """

    UNKNOWN = 0
    CLAUDE_CODE = 1
    CLAUDE_DESKTOP = 2
    CLAUDE_WEB = 3
    CHATGPT = 4
    CODEX = 5
    CURSOR = 6
    VSCODE = 7
    ZED = 8
    GOOSE = 9
    WINDSURF = 10
    CLINE = 11
    CONTINUE = 12


_UI_EXT_KEY = "io.modelcontextprotocol/ui"
_INJECTION_EXT_KEY = "vaner.context_injection"


def classify_client(name: str | None) -> ClientFamily:
    """Map a ``clientInfo.name`` string into a :class:`ClientFamily`.

    Matching is case-insensitive and substring-tolerant because hosts are
    inconsistent ("claude-code" vs "Claude Code" vs "claude-code-cli"). New
    hosts default to ``UNKNOWN`` — adding one is intentional, not automatic,
    so we can document the heuristic per host.
    """

    if not isinstance(name, str) or not name.strip():
        return ClientFamily.UNKNOWN
    raw = name.strip().lower()
    # Normalise whitespace and underscores to hyphens so "Claude Code",
    # "claude_code", and "claude-code" all classify identically.
    n = raw.replace("_", "-")
    n_compact = " ".join(n.split())
    n = n_compact.replace(" ", "-")
    # Order matters: more specific prefixes first.
    if "claude-code" in n:
        return ClientFamily.CLAUDE_CODE
    if "claude-desktop" in n:
        return ClientFamily.CLAUDE_DESKTOP
    if n in {"claude.ai", "claude-ai", "claude-web", "claudeai"}:
        return ClientFamily.CLAUDE_WEB
    if n.startswith("chatgpt") or "openai-chatgpt" in n:
        return ClientFamily.CHATGPT
    if "codex" in n:
        return ClientFamily.CODEX
    if "cursor" in n:
        return ClientFamily.CURSOR
    if "vscode" in n or "vs-code" in n or "visual-studio-code" in n or "copilot" in n:
        return ClientFamily.VSCODE
    if n.startswith("zed"):
        return ClientFamily.ZED
    if "goose" in n:
        return ClientFamily.GOOSE
    if "windsurf" in n:
        return ClientFamily.WINDSURF
    if "cline" in n:
        return ClientFamily.CLINE
    if n.startswith("continue") or "continue.dev" in n:
        return ClientFamily.CONTINUE
    return ClientFamily.UNKNOWN


def is_terminal_host(family: ClientFamily) -> bool:
    """Return True for hosts that render output as terminal text (no iframes)."""

    return family in {
        ClientFamily.CLAUDE_CODE,
        ClientFamily.CODEX,
        ClientFamily.ZED,
        ClientFamily.CLINE,
        ClientFamily.CONTINUE,
        ClientFamily.WINDSURF,
    }


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


def current_client_family(session: Any) -> ClientFamily:
    """Return the :class:`ClientFamily` for *session*, or UNKNOWN if not seen."""

    detection = _CACHE.get(session)
    if detection is None:
        return ClientFamily.UNKNOWN
    return classify_client(detection.client_name)


def reset_cache() -> None:
    """Test-only helper — clear the per-session cache."""
    _CACHE._by_id.clear()
    _CACHE._refs.clear()
