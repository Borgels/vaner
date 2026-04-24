# SPDX-License-Identifier: Apache-2.0

"""WS8 metric plumbing tests.

The counters are incremented via MCP handlers; rather than drive the
full server we unit-test the helpers + verify the metric store gets
written when the handler path is exercised through the stub-engine
dashboard test already pinned in test_dashboard_tool.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
    pytest.skip("mcp package is unavailable in this test environment", allow_module_level=True)

from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)
from vaner.telemetry.metrics import MetricsStore


def _prompt(label: str, readiness: str = "ready") -> PredictedPrompt:
    return PredictedPrompt(
        spec=PredictionSpec(
            id=prediction_id("arc", "anchor", label),
            label=label,
            description=label,
            source="arc",
            anchor="anchor",
            confidence=0.7,
            hypothesis_type="likely_next",
            specificity="concrete",
            created_at=0.0,
        ),
        run=PredictionRun(
            weight=0.5,
            token_budget=1024,
            tokens_used=100,
            scenarios_spawned=2,
            scenarios_complete=1,
            readiness=readiness,  # type: ignore[arg-type]
            updated_at=0.0,
        ),
        artifacts=PredictionArtifacts(prepared_briefing="briefing"),
    )


class _StubEngine:
    def __init__(self, prompts: list[PredictedPrompt]) -> None:
        self._prompts = prompts
        self.prediction_registry = None

    def get_active_predictions(self) -> list[PredictedPrompt]:
        return list(self._prompts)


def _build(tmp_path: Path, prompts: list[PredictedPrompt]):
    (tmp_path / ".vaner").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    from vaner.mcp.server import build_server

    return build_server(tmp_path, engine=_StubEngine(prompts))


def _call(server, name: str, arguments: dict | None = None) -> dict:
    async def _do() -> dict:
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = server.request_handlers[CallToolRequest]
        result = await handler(
            CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(name=name, arguments=arguments or {}),
            )
        )
        return json.loads(result.root.content[0].text)

    return asyncio.run(_do())


def _counter(repo_root: Path, name: str) -> float:
    async def _get() -> float:
        store = MetricsStore(repo_root / ".vaner" / "metrics.db")
        await store.initialize()
        counters = await store._counters_map()
        return float(counters.get(name, 0.0))

    return asyncio.run(_get())


def test_dashboard_tool_increments_called_counter(tmp_path: Path) -> None:
    server = _build(tmp_path, [_prompt("first")])
    _call(server, "vaner.predictions.dashboard")
    assert _counter(tmp_path, "mcp_dashboard_called") >= 1


class _MockRegistry:
    """Minimal registry stub — matches the interface the adopt handler calls."""

    def __init__(self, prompt: PredictedPrompt) -> None:
        self._prompt = prompt
        self.lock = asyncio.Lock()
        self.adoptions: list[str] = []

    def get(self, pid: str) -> PredictedPrompt | None:
        return self._prompt if pid == self._prompt.spec.id else None

    def record_adoption(self, pid: str) -> None:
        self.adoptions.append(pid)


class _EngineWithRegistry:
    def __init__(self, prompt: PredictedPrompt) -> None:
        self.prediction_registry = _MockRegistry(prompt)
        self._prompt = prompt

    def get_active_predictions(self) -> list[PredictedPrompt]:
        return [self._prompt]


def _build_with_registry(tmp_path: Path, prompt: PredictedPrompt):
    (tmp_path / ".vaner").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    from vaner.mcp.server import build_server

    return build_server(tmp_path, engine=_EngineWithRegistry(prompt))


def test_adopt_from_mcp_app_increments_apps_clicked(tmp_path: Path) -> None:
    p = _prompt("adoptable")
    server = _build_with_registry(tmp_path, p)
    _call(
        server,
        "vaner.predictions.adopt",
        {"prediction_id": p.spec.id, "source": "mcp_app"},
    )
    assert _counter(tmp_path, "mcp_apps_adopt_clicked") >= 1
    assert _counter(tmp_path, "mcp_adopt_source_mcp_app") >= 1


def test_adopt_source_default_does_not_increment_apps_clicked(tmp_path: Path) -> None:
    p = _prompt("adoptable2")
    server = _build_with_registry(tmp_path, p)
    _call(server, "vaner.predictions.adopt", {"prediction_id": p.spec.id})
    assert _counter(tmp_path, "mcp_apps_adopt_clicked") == 0
