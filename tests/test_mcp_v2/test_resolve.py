# SPDX-License-Identifier: Apache-2.0
"""Tests for the v0.8.1 MCP ``vaner.resolve`` convergence.

Pre-0.8.1 the handler ran its own scenario-store + memory-state + conflict-
detection pipeline, parallel to ``VanerEngine.resolve_query``. Since 0.8.1
the handler is a thin shim: it delegates to an injected engine (tests +
in-process embedders) or HTTP-forwards to the daemon via
:class:`VanerDaemonClient`. These tests exercise the new contract.

What scenario-store-specific behaviour was deliberately dropped:
- ``Abstain(reason="memory_conflict")`` — memory-conflict detection was a
  scenario-store concept that the engine does not replicate.
- ``gaps: ["memory_conflict"]`` — same.
- ``memory`` sub-object on ``provenance`` — scenario-store-specific.

What survives:
- ``Abstain(reason="low_confidence")`` — MCP-surface concern, still
  applied against the engine's Resolution.confidence output.
- ``include_briefing`` / ``include_predicted_response`` / ``include_metrics``
  opt-in flags.
- ``resolution_id``, ``provenance.mode``, token accounting, evidence list.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from vaner.clients.daemon import VanerDaemonUnavailable
from vaner.mcp.contracts import (
    EvidenceItem,
    Provenance,
    Resolution,
)

from .conftest import call_tool, parse_content


@dataclass
class _StubEngine:
    """Minimal engine stub — only needs resolve_query for this test file."""

    resolution: Resolution
    calls: list[dict] = field(default_factory=list)

    async def resolve_query(
        self,
        query: str,
        *,
        context: dict | None = None,
        include_briefing: bool = True,
        include_predicted_response: bool = True,
    ) -> Resolution:
        self.calls.append(
            {
                "query": query,
                "context": context,
                "include_briefing": include_briefing,
                "include_predicted_response": include_predicted_response,
            }
        )
        return self.resolution


def _build_resolution(**overrides) -> Resolution:
    base = {
        "intent": "Explain auth flow",
        "confidence": 0.85,
        "summary": "Authentication lives in middleware",
        "evidence": [
            EvidenceItem(
                id="ev-1",
                source="heuristic",
                kind="file",
                locator={"path": "src/auth.py"},
                reason="top heuristic pick",
            ),
        ],
        "provenance": Provenance(mode="predictive_hit", cache="warm", freshness="fresh"),
        "resolution_id": "resolve-test-123",
        "prepared_briefing": None,
        "predicted_response": None,
        "briefing_token_used": 0,
        "briefing_token_budget": 0,
    }
    base.update(overrides)
    return Resolution(**base)


def _make_server(temp_repo: Path, *, engine=None, daemon_client=None):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    return build_server(temp_repo, engine=engine, daemon_client=daemon_client)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_resolve_rejects_empty_query(temp_repo) -> None:
    engine = _StubEngine(resolution=_build_resolution())
    server = _make_server(temp_repo, engine=engine)
    result = parse_content(call_tool(server, "vaner.resolve", {"query": "   "}))
    assert result["code"] == "invalid_input"
    assert engine.calls == []  # never reached the engine


# ---------------------------------------------------------------------------
# Engine delegation (injected engine — tests + in-process)
# ---------------------------------------------------------------------------


def test_resolve_delegates_to_injected_engine(temp_repo) -> None:
    engine = _StubEngine(
        resolution=_build_resolution(
            prepared_briefing="## Context\nauth in middleware",
            briefing_token_used=42,
            briefing_token_budget=500,
        ),
    )
    server = _make_server(temp_repo, engine=engine)
    payload = parse_content(
        call_tool(
            server,
            "vaner.resolve",
            {"query": "explain auth", "include_briefing": True},
        )
    )
    assert payload["resolution_id"] == "resolve-test-123"
    assert payload["confidence"] == 0.85
    assert payload["prepared_briefing"] == "## Context\nauth in middleware"
    assert payload["briefing_token_used"] == 42
    assert payload["provenance"]["mode"] == "predictive_hit"
    assert len(engine.calls) == 1
    assert engine.calls[0]["query"] == "explain auth"
    assert engine.calls[0]["include_briefing"] is True


def test_resolve_forwards_opt_in_flags_to_engine(temp_repo) -> None:
    engine = _StubEngine(resolution=_build_resolution())
    server = _make_server(temp_repo, engine=engine)
    call_tool(
        server,
        "vaner.resolve",
        {
            "query": "refactor the auth path",
            "include_briefing": False,
            "include_predicted_response": True,
        },
    )
    assert engine.calls[0]["include_briefing"] is False
    assert engine.calls[0]["include_predicted_response"] is True


def test_resolve_abstains_on_low_confidence(temp_repo) -> None:
    """The engine doesn't produce Abstain — the MCP shim applies the
    0.35 threshold as a surface-level policy."""
    engine = _StubEngine(resolution=_build_resolution(confidence=0.2))
    server = _make_server(temp_repo, engine=engine)
    payload = parse_content(call_tool(server, "vaner.resolve", {"query": "obscure corner"}))
    assert payload["abstained"] is True
    assert payload["reason"] == "low_confidence"


def test_resolve_high_confidence_does_not_abstain(temp_repo) -> None:
    engine = _StubEngine(resolution=_build_resolution(confidence=0.9))
    server = _make_server(temp_repo, engine=engine)
    payload = parse_content(call_tool(server, "vaner.resolve", {"query": "clear question"}))
    assert payload.get("abstained") is not True
    assert payload["resolution_id"] == "resolve-test-123"


# ---------------------------------------------------------------------------
# Metrics opt-in (wraps the engine's Resolution with runtime economics)
# ---------------------------------------------------------------------------


def test_resolve_default_omits_metrics(temp_repo) -> None:
    engine = _StubEngine(resolution=_build_resolution())
    server = _make_server(temp_repo, engine=engine)
    payload = parse_content(call_tool(server, "vaner.resolve", {"query": "explain auth"}))
    assert payload.get("metrics") is None


def test_resolve_include_metrics_returns_runtime_economics(temp_repo) -> None:
    engine = _StubEngine(
        resolution=_build_resolution(
            prepared_briefing="## Context\nauth in middleware\nmore detail",
            briefing_token_used=50,
            briefing_token_budget=500,
        ),
    )
    server = _make_server(temp_repo, engine=engine)
    payload = parse_content(
        call_tool(
            server,
            "vaner.resolve",
            {
                "query": "auth flow",
                "include_briefing": True,
                "include_metrics": True,
                "estimated_cost_per_1k_tokens": 2.50,
            },
        )
    )
    metrics = payload.get("metrics")
    assert isinstance(metrics, dict)
    assert metrics["briefing_tokens"] == 50
    assert metrics["total_context_tokens"] >= 50
    assert metrics["cache_tier"] in {"cold", "warm", "hot"}
    assert metrics["freshness"] in {"fresh", "recent", "stale"}
    assert metrics["elapsed_ms"] >= 0.0
    assert metrics["estimated_cost_per_1k_tokens"] == 2.50
    expected_cost = (metrics["total_context_tokens"] / 1000.0) * 2.50
    assert abs(metrics["estimated_cost_usd"] - expected_cost) < 1e-6


# ---------------------------------------------------------------------------
# Daemon-forward path (no injected engine)
# ---------------------------------------------------------------------------


class _StubDaemonClient:
    """Records calls + returns the injected Resolution (or raises)."""

    def __init__(self, *, resolution: Resolution | None = None, error: Exception | None = None) -> None:
        self._resolution = resolution
        self._error = error
        self.calls: list[dict] = []

    async def resolve(
        self,
        query: str,
        *,
        context: dict | None = None,
        include_briefing: bool = False,
        include_predicted_response: bool = False,
    ) -> Resolution:
        self.calls.append(
            {
                "query": query,
                "context": context,
                "include_briefing": include_briefing,
                "include_predicted_response": include_predicted_response,
            }
        )
        if self._error is not None:
            raise self._error
        assert self._resolution is not None
        return self._resolution


def test_resolve_forwards_to_injected_daemon_client_when_no_engine(temp_repo) -> None:
    """When no engine is injected, the MCP shim HTTP-forwards through
    the provided VanerDaemonClient. Tests can inject a stub to verify
    the forward happens without a live daemon."""
    resolution = _build_resolution(
        resolution_id="resolve-via-daemon-789",
        prepared_briefing="briefing text",
        briefing_token_used=20,
        briefing_token_budget=200,
    )
    daemon = _StubDaemonClient(resolution=resolution)
    server = _make_server(temp_repo, daemon_client=daemon)
    payload = parse_content(
        call_tool(
            server,
            "vaner.resolve",
            {"query": "delegated question", "include_briefing": True},
        )
    )
    assert payload["resolution_id"] == "resolve-via-daemon-789"
    assert daemon.calls == [
        {
            "query": "delegated question",
            "context": {},
            "include_briefing": True,
            "include_predicted_response": False,
        }
    ]


def test_resolve_returns_engine_unavailable_when_daemon_down(temp_repo) -> None:
    daemon = _StubDaemonClient(error=VanerDaemonUnavailable("daemon connection refused"))
    server = _make_server(temp_repo, daemon_client=daemon)
    payload = parse_content(call_tool(server, "vaner.resolve", {"query": "q"}))
    assert payload["code"] == "engine_unavailable"
    assert "daemon connection refused" in payload["message"] or "serve-http" in payload["message"]


def test_resolve_returns_invalid_input_when_daemon_rejects(temp_repo) -> None:
    daemon = _StubDaemonClient(error=ValueError("query is required"))
    server = _make_server(temp_repo, daemon_client=daemon)
    payload = parse_content(call_tool(server, "vaner.resolve", {"query": "q"}))
    assert payload["code"] == "invalid_input"
    assert "query is required" in payload["message"]
