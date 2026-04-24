# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server import NotificationOptions, Server
    from mcp.server.models import InitializationOptions
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

try:
    from mcp.server import NotificationOptions, Server  # type: ignore[import-not-found]
    from mcp.server.models import InitializationOptions  # type: ignore[import-not-found]
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool  # type: ignore[import-not-found]

    _MCP_IMPORT_ERROR: ModuleNotFoundError | None = None
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency path
    _MCP_IMPORT_ERROR = exc

    class NotificationOptions:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    class Server:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise ImportError("import error in vaner.mcp.server: No module named 'mcp'") from _MCP_IMPORT_ERROR

    class InitializationOptions(dict[str, Any]):
        pass

    class CallToolResult(dict[str, Any]):
        pass

    class ListToolsResult(dict[str, Any]):
        pass

    class TextContent(dict[str, Any]):
        pass

    class Tool(dict[str, Any]):
        pass


from vaner.api import aprecompute
from vaner.cli.commands.config import load_config
from vaner.cli.commands.init import init_repo
from vaner.intent.briefing import BriefingAssembler
from vaner.learning.reward import RewardInput, compute_reward
from vaner.mcp.contracts import (
    Abstain,
    EvidenceItem,
    Provenance,
    Resolution,
    ResolutionMetrics,
)
from vaner.mcp.lint import run_lint
from vaner.mcp.memory_log import append_log, write_index
from vaner.memory.policy import (
    NegativeFeedbackContext,
    PromotionContext,
    decide_on_negative_feedback,
    decide_promotion,
    evidence_fingerprint,
)
from vaner.models.decision import DecisionRecord
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore

logger = logging.getLogger(__name__)

BACKEND_NOT_CONFIGURED_MESSAGE = (
    "No LLM backend configured for Vaner.\n"
    "Fix it with one command:\n"
    "  vaner init --backend-preset ollama          # local, free\n"
    "  vaner init --backend-preset openrouter --backend-api-key-env OPENROUTER_API_KEY\n"
    "Docs: https://docs.vaner.ai/mcp"
)

_SUGGESTION_CACHE_CAPACITY = 128
_MANAGED_PATH_MARKERS = (
    ".cursor/mcp.json",
    ".cursor/skills/vaner/vaner-feedback/skill.md",
    ".claude/skills/vaner/vaner-feedback/skill.md",
)
_MANAGED_QUERY_HINTS = {"mcp", "cursor", "claude", "skill", "skills", "feedback", "config", "prompt"}


def _make_text(content: str) -> list[TextContent]:
    return [TextContent(type="text", text=content)]


def _json_result(payload: dict[str, Any], *, is_error: bool = False) -> CallToolResult:
    data = json.dumps(payload, indent=2)
    return CallToolResult(content=_make_text(data), isError=is_error, structuredContent=payload)


def _ensure_backend(config: Any) -> bool:
    backend = getattr(config, "backend", None)
    base_url = str(getattr(backend, "base_url", "") or "").strip()
    model = str(getattr(backend, "model", "") or "").strip()
    return bool(base_url and model)


def _tokenize(text: str) -> set[str]:
    return {tok for tok in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split() if len(tok) > 2}


def _scenario_fingerprints(scenario: Scenario) -> list[str]:
    return [evidence_fingerprint(e.source_path, {"key": e.key}, e.excerpt[:128], e.weight) for e in scenario.evidence]


def _scenario_to_summary(scenario: Scenario) -> dict[str, Any]:
    return {
        "id": scenario.id,
        "kind": scenario.kind,
        "score": scenario.score,
        "confidence": scenario.confidence,
        "entities": scenario.entities[:8],
        "freshness": scenario.freshness,
        "memory_state": scenario.memory_state,
        "memory_confidence": scenario.memory_confidence,
        "cost_to_expand": scenario.cost_to_expand,
        "prepared_context_preview": scenario.prepared_context[:220],
    }


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip().lower()


def _scenario_paths(scenario: Scenario) -> set[str]:
    paths = {_normalize_path(entity) for entity in scenario.entities if entity}
    paths.update(_normalize_path(ref.source_path) for ref in scenario.evidence if ref.source_path)
    return {path for path in paths if path}


def _query_targets_managed_files(query_tokens: set[str]) -> bool:
    return bool(query_tokens & _MANAGED_QUERY_HINTS)


def _is_managed_path(path: str) -> bool:
    normalized = _normalize_path(path)
    return any(normalized.endswith(marker) for marker in _MANAGED_PATH_MARKERS)


def _scenario_penalty(scenario: Scenario, query_tokens: set[str]) -> float:
    if _query_targets_managed_files(query_tokens):
        return 0.0
    if any(_is_managed_path(path) for path in _scenario_paths(scenario)):
        return 0.35
    return 0.0


# Phase 4 / WS3.c: daemon-forwarding for MCP → daemon bridge.
# The MCP server runs as a subprocess (stdio transport) and does not hold an
# engine directly. When invoked without an injected engine, the predictions
# tools HTTP-forward to a daemon on 127.0.0.1:8473 via VanerDaemonClient.
# The client is the shared contract layer Vaner's own surfaces use; the
# fresh-per-call construction keeps the MCP subprocess stateless. Tests can
# inject a pre-configured client via the ``daemon_client`` kwarg on
# :func:`build_server`.


def _serialize_prediction_for_mcp(prompt: Any) -> dict[str, Any]:
    """Render a PredictedPrompt into the shape returned by vaner.predictions.active."""
    spec = prompt.spec
    run = prompt.run
    artifacts = prompt.artifacts
    return {
        "id": spec.id,
        "label": spec.label,
        "description": spec.description,
        "source": spec.source,
        "confidence": spec.confidence,
        "hypothesis_type": spec.hypothesis_type,
        "specificity": spec.specificity,
        "readiness": run.readiness,
        "weight": run.weight,
        "token_budget": run.token_budget,
        "tokens_used": run.tokens_used,
        "scenarios_complete": run.scenarios_complete,
        "evidence_score": artifacts.evidence_score,
        "has_draft": artifacts.draft_answer is not None,
        "has_briefing": artifacts.prepared_briefing is not None,
    }


_ADOPT_BRIEFING_ASSEMBLER = BriefingAssembler()
"""Module-level assembler for the adopt path.

Kept here (not per-request) so the approximation-warning latch fires at
most once per server lifetime. No per-process tokenizer is attached
today; when the resolve path gets a structured LLM client (WS8), the
same assembler instance can be reconfigured via
``_ADOPT_BRIEFING_ASSEMBLER._tokenizer = ...``.
"""


def _build_adopt_resolution(prompt: Any) -> Resolution:
    """Assemble a Resolution from a PredictedPrompt's prepared artifacts.

    Uses the same Resolution shape ``vaner.resolve`` returns so downstream
    agents can treat adopt results interchangeably.
    ``adopted_from_prediction_id`` carries the source prediction's id for
    provenance.

    WS3.d: evidence is populated from the prediction's attached scenarios (one
    EvidenceItem per scenario_id pointing at the scenario's file set).

    WS9: briefing assembly routes through the shared
    :class:`BriefingAssembler` so the rendering matches what the engine
    produced in-cycle. When the prediction already carries a
    ``prepared_briefing``, the assembler incorporates it as the
    evidence section; otherwise a minimal summary + provenance briefing
    is synthesised. Token counts come from the assembler — which in turn
    delegates to a real tokenizer when one is registered, or falls back
    to the four-char heuristic with a one-time warning.
    """
    spec = prompt.spec
    artifacts = prompt.artifacts
    run = prompt.run
    briefing_obj = _ADOPT_BRIEFING_ASSEMBLER.from_prediction(prompt)
    provenance = Provenance(mode="predictive_hit", cache="warm", freshness="fresh")
    evidence: list[EvidenceItem] = [
        EvidenceItem(
            id=scenario_id,
            source=spec.source,
            kind="record",
            locator={"prediction_id": spec.id, "scenario_id": scenario_id},
            reason=f"scenario explored under prediction {spec.label!r}",
        )
        for scenario_id in artifacts.scenario_ids
    ]
    return Resolution(
        intent=spec.label,
        confidence=float(spec.confidence),
        summary=spec.description or spec.label,
        evidence=evidence,
        provenance=provenance,
        resolution_id=f"adopt-{spec.id}",
        prepared_briefing=briefing_obj.text or None,
        predicted_response=artifacts.draft_answer,
        briefing_token_used=briefing_obj.token_count,
        briefing_token_budget=run.token_budget,
        adopted_from_prediction_id=spec.id,
    )


def build_server(
    repo_root: Path,
    *,
    engine: Any | None = None,
    daemon_client: Any | None = None,
) -> Server:
    """Construct the MCP server.

    ``engine`` is an optional live VanerEngine reference. When supplied,
    ``vaner.predictions.*`` tools operate on the engine's in-memory
    PredictionRegistry directly.

    ``daemon_client`` is an optional :class:`VanerDaemonClient` injected for
    testing. When absent the server constructs a default client on each
    prediction-tool invocation pointing at ``127.0.0.1:8473``. This is the
    shared HTTP contract Vaner's own surfaces (cockpit, desktop, CLI, and
    this MCP subprocess) use to reach the daemon.
    """
    # Lazy import keeps MCP server import-free of pydantic/httpx when the
    # tools surface is unused (e.g. CLI --help).
    from vaner.clients.daemon import (
        VanerDaemonClient,
        VanerDaemonNotFound,
        VanerDaemonUnavailable,
    )

    def _daemon() -> Any:
        return daemon_client if daemon_client is not None else VanerDaemonClient()

    if not (repo_root / ".vaner" / "config.toml").exists():
        init_repo(repo_root)
    suggestion_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
    promotion_ring: list[dict[str, str]] = []
    server: Server = Server("vaner")

    def _put_suggestion(suggestion_id: str, payload: dict[str, Any]) -> None:
        suggestion_cache[suggestion_id] = payload
        suggestion_cache.move_to_end(suggestion_id)
        while len(suggestion_cache) > _SUGGESTION_CACHE_CAPACITY:
            suggestion_cache.popitem(last=False)

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name="vaner.status",
                    description="Return Vaner readiness, freshness, and memory quality health.",
                    inputSchema={"type": "object", "properties": {"scope": {"type": "object"}}},
                ),
                Tool(
                    name="vaner.suggest",
                    description="Return lightweight intent suggestions before resolution.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "context": {"type": "object"},
                            "limit": {"type": "integer", "default": 5},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="vaner.resolve",
                    description="Resolve the best context package with confidence, evidence, and provenance.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "suggestion_id": {"type": "string"},
                            "context": {"type": "object"},
                            "budget": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
                            "max_evidence_items": {"type": "integer", "default": 8},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="vaner.expand",
                    description="Expand a scenario/evidence branch without recomputing everything.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "target_id": {"type": "string"},
                            "mode": {
                                "type": "string",
                                "enum": ["details", "neighbors", "dependencies", "timeline", "related"],
                                "default": "details",
                            },
                            "budget": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
                        },
                        "required": ["target_id"],
                    },
                ),
                Tool(
                    name="vaner.search",
                    description="Fallback retrieval when predictive confidence is weak.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "mode": {
                                "type": "string",
                                "enum": ["semantic", "lexical", "hybrid", "symbol", "path"],
                                "default": "hybrid",
                            },
                            "limit": {"type": "integer", "default": 10},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="vaner.explain",
                    description="Explain why a package was selected and where uncertainty remains.",
                    inputSchema={"type": "object", "properties": {"resolution_id": {"type": "string"}, "target_id": {"type": "string"}}},
                ),
                Tool(
                    name="vaner.feedback",
                    description="Record usefulness feedback and apply gated memory learning.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "resolution_id": {"type": "string"},
                            "rating": {"type": "string", "enum": ["useful", "partial", "wrong", "irrelevant"]},
                            "correction": {"type": "string"},
                            "preferred_items": {"type": "array", "items": {"type": "string"}},
                            "rejected_items": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["rating"],
                    },
                ),
                Tool(
                    name="vaner.warm",
                    description="Hint Vaner to precompute around selected targets.",
                    inputSchema={
                        "type": "object",
                        "properties": {"targets": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
                        "required": ["targets"],
                    },
                ),
                Tool(
                    name="vaner.inspect",
                    description="Read a normalized scenario/evidence item by id.",
                    inputSchema={"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"]},
                ),
                Tool(
                    name="vaner.debug.trace",
                    description="Debug trace of recent decision and memory quality state.",
                    inputSchema={"type": "object", "properties": {"resolution_id": {"type": "string"}}},
                ),
                Tool(
                    name="vaner.predictions.active",
                    description=(
                        "List active PredictedPrompts from the current precompute cycle. "
                        "Each entry carries a human-readable label, readiness state, and "
                        "per-prediction compute contract so callers can pick the most "
                        "advanced prediction to adopt."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="vaner.predictions.adopt",
                    description=(
                        "Adopt a specific prediction as the user's next intent. Returns a "
                        "Resolution with the prepared briefing + draft answer + evidence "
                        "(same shape as vaner.resolve) plus adopted_from_prediction_id "
                        "populated for provenance."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {"prediction_id": {"type": "string"}},
                        "required": ["prediction_id"],
                    },
                ),
                Tool(
                    name="vaner.goals.list",
                    description=(
                        "List workspace goals. Goals are long-horizon workspace "
                        'aspirations ("implement JWT migration") that seed '
                        "predictions and bias scenario scoring. Filter by status "
                        "('active'/'paused'/'abandoned'/'achieved')."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["active", "paused", "abandoned", "achieved"],
                            },
                            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                        },
                    },
                ),
                Tool(
                    name="vaner.goals.declare",
                    description=(
                        "Declare a new workspace goal. Confidence is fixed at 1.0 for "
                        "user-declared goals (the user is authoritative). The goal "
                        "begins in 'active' status and starts seeding predictions on "
                        "the next precompute cycle."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "related_files": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["title"],
                    },
                ),
                Tool(
                    name="vaner.goals.update_status",
                    description=(
                        "Update a goal's status. When set to 'achieved' / 'abandoned', "
                        "goal-anchored predictions for this goal will be invalidated "
                        "on the next cycle."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "goal_id": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["active", "paused", "abandoned", "achieved"],
                            },
                        },
                        "required": ["goal_id", "status"],
                    },
                ),
                Tool(
                    name="vaner.goals.delete",
                    description=("Delete a goal. Prefer update_status over delete — deletion loses the goal's evidence trail."),
                    inputSchema={
                        "type": "object",
                        "properties": {"goal_id": {"type": "string"}},
                        "required": ["goal_id"],
                    },
                ),
                Tool(
                    name="vaner.artefacts.list",
                    description=(
                        "List intent-bearing artefacts Vaner has ingested "
                        "(plans, outlines, task lists, briefs, roadmaps, "
                        "runbooks). Filter by status, connector, or source "
                        "tier. 0.8.2 WS1."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["active", "dormant", "stale", "superseded", "archived"],
                            },
                            "connector": {"type": "string"},
                            "source_tier": {
                                "type": "string",
                                "enum": ["T1", "T2", "T3", "T4"],
                            },
                            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                        },
                    },
                ),
                Tool(
                    name="vaner.artefacts.inspect",
                    description=(
                        "Return full detail on one intent-bearing artefact: "
                        "metadata, current snapshot items (state + text + "
                        "section_path + related_files), and classifier "
                        "confidence. 0.8.2 WS1."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {"artefact_id": {"type": "string"}},
                        "required": ["artefact_id"],
                    },
                ),
                Tool(
                    name="vaner.artefacts.set_status",
                    description=(
                        "Manually set an artefact's lifecycle status. Typical use: "
                        "archive an artefact the user no longer wants Vaner to "
                        "treat as active intent. User-override; bypasses "
                        "reconciliation heuristics."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "artefact_id": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["active", "dormant", "stale", "superseded", "archived"],
                            },
                        },
                        "required": ["artefact_id", "status"],
                    },
                ),
                Tool(
                    name="vaner.artefacts.influence",
                    description=(
                        "Show an artefact's downstream influence: which "
                        "WorkspaceGoals it backs, which active PredictionSpecs "
                        "are anchored to its items, and the most recent "
                        "ReconciliationOutcome ids that touched either. Hard "
                        "requirement from the 0.8.2 spec (§MCP inspectability) "
                        "— shape stable across 0.8.2 WS1/WS2/WS3."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {"artefact_id": {"type": "string"}},
                        "required": ["artefact_id"],
                    },
                ),
                Tool(
                    name="vaner.sources.status",
                    description=(
                        "Per-source status for the intent-artefact ingestion "
                        "pipeline: tier policy, enabled flag, repo / path "
                        "allowlist, and ingest counts by connector. 0.8.2 WS1."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]
        )

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> CallToolResult:
        args = arguments or {}
        started = time.perf_counter()
        active_repo_root = repo_root
        if not (active_repo_root / ".vaner" / "config.toml").exists():
            init_repo(active_repo_root)
        config = load_config(active_repo_root)
        metrics_store = MetricsStore(active_repo_root / ".vaner" / "metrics.db")
        scenario_store = ScenarioStore(active_repo_root / ".vaner" / "scenarios.db")
        await metrics_store.initialize()
        await scenario_store.initialize()

        async def _record(status: str, *, tool_name: str | None = None, scenario_id: str | None = None) -> None:
            latency_ms = (time.perf_counter() - started) * 1000.0
            try:
                await metrics_store.increment_mode_usage("mcp")
                await metrics_store.increment_mode_usage("mcp_tools_total")
                await metrics_store.record_mcp_tool_call(
                    tool_name=tool_name or name,
                    status=status,
                    latency_ms=latency_ms,
                    scenario_id=scenario_id,
                )
            except Exception:
                return

        def _backend_error(*, degradable: bool) -> CallToolResult:
            payload = {"code": "backend_not_configured", "message": BACKEND_NOT_CONFIGURED_MESSAGE}
            logger.warning(
                "backend not configured repo=%s base_url=%s model=%s",
                repo_root,
                str(getattr(config.backend, "base_url", "")),
                str(getattr(config.backend, "model", "")),
            )
            return _json_result(payload, is_error=not degradable)

        if name == "vaner.status":
            await scenario_store.mark_stale()
            lint_report = await run_lint(scenario_store)
            freshness = await scenario_store.freshness_counts()
            memory_counts = await scenario_store.memory_state_counts()
            quality = await metrics_store.memory_quality_snapshot()
            calibration = await metrics_store.calibration_snapshot()
            scenarios = await scenario_store.list_top(limit=50)
            write_index(repo_root, scenarios)
            latest = DecisionRecord.read_latest(repo_root)
            payload = {
                "ready": True,
                "daemon_running": (repo_root / ".vaner" / "runtime" / "daemon.pid").exists(),
                "index_state": "ready",
                "cache_state": "warm" if freshness.get("total", 0) > 0 else "cold",
                "hot_areas": lint_report.hot_areas,
                "stale_areas": lint_report.stale_areas,
                "pending_jobs": 0,
                "capabilities": {
                    "suggest": True,
                    "resolve": True,
                    "expand": True,
                    "feedback": True,
                    "search": True,
                    "warm": True,
                    "inspect": True,
                    "explain": True,
                    "debug_trace": True,
                },
                "last_decision_id": latest.id if latest else None,
                "backend_configured": _ensure_backend(config),
                "lint": {
                    "contradictions": lint_report.contradictions,
                    "orphan_entities": lint_report.orphan_entities,
                    "coverage_gaps": lint_report.coverage_gaps,
                },
                "memory": {"counts": memory_counts, "quality": quality, "calibration": calibration},
            }
            append_log(repo_root, tool=name, label="status", decision_id=None, provenance_mode=None, memory_state=None)
            await _record("ok")
            return _json_result(payload)

        if name == "vaner.suggest":
            query = str(args.get("query", "")).strip()
            limit = max(1, int(args.get("limit", 5)))
            if not query:
                await _record("error")
                return _json_result({"code": "invalid_input", "message": "query is required"}, is_error=True)
            scenarios = await scenario_store.list_top(limit=max(8, limit * 2))
            query_tokens = _tokenize(query)
            ranked: list[dict[str, Any]] = []
            for scenario in scenarios:
                overlap = len(query_tokens & set(tok.lower() for tok in scenario.entities))
                confidence = min(
                    0.98,
                    max(0.0, 0.25 + (overlap * 0.15) + (scenario.score * 0.45) - _scenario_penalty(scenario, query_tokens)),
                )
                label = f"{scenario.kind} / {' '.join(scenario.entities[:4]) or scenario.id}"
                suggestion_id = f"sug_{hashlib.sha1((query + scenario.id).encode('utf-8')).hexdigest()[:8]}"
                item = {
                    "id": suggestion_id,
                    "label": label.strip(),
                    "confidence": round(confidence, 4),
                    "reason": "token/entity overlap with high-ranked scenario",
                    "scenario_id": scenario.id,
                }
                ranked.append(item)
            ranked.sort(key=lambda item: float(item["confidence"]), reverse=True)
            picked = ranked[:limit]
            for item in picked:
                _put_suggestion(str(item["id"]), item)
            top_confidence = float(picked[0]["confidence"]) if picked else 0.0
            payload = {"suggestions": picked, "needs_clarification": top_confidence < 0.4}
            append_log(repo_root, tool=name, label=query[:60], decision_id=None, provenance_mode=None, memory_state=None)
            await _record("ok")
            return _json_result(payload)

        if name == "vaner.resolve":
            # 0.8.1: converged path — delegate to VanerEngine.resolve_query.
            # When an engine is injected (tests + in-process embedders), call
            # the engine directly. Otherwise forward to the daemon's /resolve
            # endpoint via the shared VanerDaemonClient (mirrors the WS3
            # predictions.* forward pattern).
            if not _ensure_backend(config):
                await _record("error")
                return _backend_error(degradable=False)
            resolve_started_monotonic = time.monotonic()
            query = str(args.get("query", "")).strip()
            if not query:
                await _record("error")
                return _json_result({"code": "invalid_input", "message": "query is required"}, is_error=True)
            suggestion_id = str(args.get("suggestion_id", "")).strip()
            context_arg = args.get("context") or {}
            if suggestion_id and suggestion_id in suggestion_cache:
                # Carry the suggestion's label through to the engine so the
                # resolve picks up the canonical form even when the caller's
                # query text is terse.
                query = str(suggestion_cache[suggestion_id].get("label") or query)
            include_briefing = bool(args.get("include_briefing", False))
            include_predicted_response = bool(args.get("include_predicted_response", False))
            include_metrics = bool(args.get("include_metrics", False))

            try:
                if engine is not None:
                    resolution = await engine.resolve_query(
                        query,
                        context=context_arg,
                        include_briefing=include_briefing,
                        include_predicted_response=include_predicted_response,
                    )
                else:
                    resolution = await _daemon().resolve(
                        query,
                        context=context_arg if isinstance(context_arg, dict) else None,
                        include_briefing=include_briefing,
                        include_predicted_response=include_predicted_response,
                    )
            except VanerDaemonUnavailable as exc:
                await _record("error")
                return _json_result(
                    {
                        "code": "engine_unavailable",
                        "message": str(exc)
                        or "vaner.resolve needs either an in-process engine or a running `vaner daemon serve-http --with-engine`",
                    },
                    is_error=True,
                )
            except ValueError as exc:
                await _record("error")
                return _json_result(
                    {"code": "invalid_input", "message": str(exc)},
                    is_error=True,
                )

            # Low-confidence abstain — the engine doesn't produce Abstain
            # itself; it's an MCP-surface concern. We keep the same 0.35
            # threshold the pre-convergence handler used so callers see no
            # behaviour regression on this axis.
            if resolution.confidence < 0.35:
                abstain = Abstain(
                    reason="low_confidence",
                    message="Resolution confidence is below threshold.",
                    suggestions=[],
                )
                await metrics_store.increment_counter("abstain_total")
                await metrics_store.increment_counter("resolves_total")
                append_log(
                    repo_root,
                    tool=name,
                    label=query[:60],
                    decision_id=None,
                    provenance_mode=resolution.provenance.mode,
                    memory_state=None,
                )
                await _record("ok")
                return _json_result(abstain.model_dump(mode="json"))

            # Optional ResolutionMetrics layer — wraps the engine's
            # briefing_token_used with wall-clock + cost economics the
            # engine doesn't own. Opt-in to avoid paying the extra
            # serialisation cost for callers that don't consume it.
            if include_metrics:
                briefing_tokens = int(resolution.briefing_token_used or 0)
                evidence_text_chars = sum(
                    len(getattr(ev, "reason", "") or "") + len(str(getattr(ev, "locator", "") or "")) for ev in resolution.evidence
                )
                evidence_tokens = evidence_text_chars // 4
                total_context_tokens = briefing_tokens + evidence_tokens
                metrics_freshness = (
                    resolution.provenance.freshness if resolution.provenance.freshness in {"fresh", "recent", "stale"} else "fresh"
                )
                estimated_cost_per_1k = float(args.get("estimated_cost_per_1k_tokens", 0.0) or 0.0)
                estimated_cost_usd = (total_context_tokens / 1000.0) * estimated_cost_per_1k
                cache_tier = str(resolution.provenance.cache) if resolution.provenance.cache in {"cold", "warm", "hot"} else "cold"
                resolution = resolution.model_copy(
                    update={
                        "metrics": ResolutionMetrics(
                            briefing_tokens=briefing_tokens,
                            evidence_tokens=evidence_tokens,
                            total_context_tokens=total_context_tokens,
                            cache_tier=cache_tier,
                            freshness=metrics_freshness,
                            elapsed_ms=(time.monotonic() - resolve_started_monotonic) * 1000.0,
                            estimated_cost_per_1k_tokens=estimated_cost_per_1k,
                            estimated_cost_usd=estimated_cost_usd,
                        ),
                    }
                )

            await metrics_store.increment_counter("resolves_total")
            if resolution.provenance.mode == "predictive_hit":
                await metrics_store.increment_counter("predictive_hit_total")
            append_log(
                repo_root,
                tool=name,
                label=query[:60],
                decision_id=resolution.resolution_id,
                provenance_mode=resolution.provenance.mode,
                memory_state=None,
            )
            await _record("ok")
            return _json_result(resolution.model_dump(mode="json"))

        if name == "vaner.expand":
            if not _ensure_backend(config):
                await _record("error")
                return _backend_error(degradable=False)
            logger.info(
                "vaner.expand repo=%s backend_base_url=%s backend_model=%s",
                repo_root,
                str(getattr(config.backend, "base_url", "")),
                str(getattr(config.backend, "model", "")),
            )
            target_id = str(args.get("target_id", "")).strip()
            mode = str(args.get("mode", "details")).strip()
            if not target_id:
                await _record("error")
                return _json_result({"code": "invalid_input", "message": "target_id is required"}, is_error=True)
            if mode in {"timeline", "related"}:
                await _record("ok")
                return _json_result({"code": "mode_deferred", "message": f"expand mode '{mode}' lands in v1.1"})
            scenario = await scenario_store.get(target_id)
            if scenario is None:
                # Attempt evidence-id lookup in latest decision.
                latest = DecisionRecord.read_latest(repo_root)
                if latest is not None:
                    for selection in latest.selections:
                        if selection.artefact_key == target_id:
                            scenario = await scenario_store.get(selection.artefact_key)
                            break
            if scenario is None:
                await _record("error")
                return _json_result({"code": "not_found", "message": f"target '{target_id}' not found"}, is_error=True)
            await aprecompute(repo_root, config=config)
            await scenario_store.record_expansion(scenario.id)
            refreshed = await scenario_store.get(scenario.id)
            if refreshed is not None:
                await scenario_store.promote_scenario(
                    refreshed.id,
                    new_state=refreshed.memory_state,
                    confidence=refreshed.memory_confidence,
                    evidence_hashes=_scenario_fingerprints(refreshed),
                    at=time.time(),
                )
            payload = {
                "target_id": scenario.id,
                "expanded_summary": (refreshed.prepared_context if refreshed else scenario.prepared_context)[:400],
                "related_items": [
                    {"id": e.key, "source": e.source_path, "reason": "evidence relation"} for e in (refreshed or scenario).evidence[:8]
                ],
                "new_confidence": float((refreshed or scenario).confidence),
                "remaining_gaps": list((refreshed or scenario).coverage_gaps),
            }
            append_log(
                repo_root,
                tool=name,
                label=target_id,
                decision_id=None,
                provenance_mode=None,
                memory_state=(refreshed or scenario).memory_state,
            )
            write_index(repo_root, await scenario_store.list_top(limit=50))
            await _record("ok", scenario_id=scenario.id)
            return _json_result(payload)

        if name == "vaner.search":
            query = str(args.get("query", "")).strip()
            mode = str(args.get("mode", "hybrid")).strip()
            limit = max(1, int(args.get("limit", 10)))
            if not query:
                await _record("error")
                return _json_result({"code": "invalid_input", "message": "query is required"}, is_error=True)
            if mode in {"symbol", "path"}:
                await _record("ok")
                return _json_result({"code": "mode_deferred", "message": f"search mode '{mode}' lands in v1.1"})
            scenarios = await scenario_store.list_top(limit=max(limit * 2, 20))
            query_tokens = _tokenize(query)
            results = []
            for idx, scenario in enumerate(scenarios, start=1):
                score = len(query_tokens & set(tok.lower() for tok in scenario.entities))
                if score <= 0 and mode == "lexical":
                    continue
                final_score = round(
                    max(0.0, min(1.0, 0.2 + score * 0.2 + scenario.score * 0.5 - _scenario_penalty(scenario, query_tokens))),
                    4,
                )
                results.append(
                    {
                        "id": f"res_{idx}",
                        "source": scenario.id,
                        "kind": "file",
                        "snippet": scenario.prepared_context[:160],
                        "score": final_score,
                    }
                )
            results.sort(key=lambda item: float(item["score"]), reverse=True)
            payload = {
                "results": results[:limit],
                "search_quality": "good" if results and float(results[0]["score"]) > 0.65 else ("weak" if results else "poor"),
            }
            append_log(repo_root, tool=name, label=query[:60], decision_id=None, provenance_mode=None, memory_state=None)
            await _record("ok")
            return _json_result(payload)

        if name == "vaner.explain":
            resolution_id = str(args.get("resolution_id", "")).strip()
            decision = DecisionRecord.read_by_id(repo_root, resolution_id) if resolution_id else DecisionRecord.read_latest(repo_root)
            if decision is None:
                await _record("error")
                return _json_result({"code": "not_found", "message": "No decision record available"}, is_error=True)
            kept = [s for s in decision.selections if s.kept]
            dropped = [s for s in decision.selections if not s.kept]
            memory_note = {}
            conflict_note = {}
            for note in decision.notes:
                try:
                    payload = json.loads(note)
                except Exception:
                    continue
                if "memory_meta" in payload:
                    memory_note = payload["memory_meta"]
                if "conflict" in payload:
                    conflict_note = payload["conflict"]
            payload = {
                "selection_reason": [s.rationale for s in kept if s.rationale],
                "uncertainty_reason": [n for n in decision.notes if n.startswith("uncertainty:")],
                "fallback_used": decision.cache_tier == "miss",
                "rejected_paths": [{"source": s.source_path, "reason": s.drop_reason or "not selected"} for s in dropped],
                "memory": memory_note,
                "conflict": conflict_note,
            }
            append_log(
                repo_root,
                tool=name,
                label=resolution_id or "latest",
                decision_id=decision.id,
                provenance_mode=None,
                memory_state=memory_note.get("state"),
            )
            await _record("ok")
            return _json_result(payload)

        if name == "vaner.feedback":
            rating = str(args.get("rating", "")).strip()
            if rating not in {"useful", "partial", "wrong", "irrelevant"}:
                await _record("error")
                return _json_result({"code": "invalid_input", "message": "rating must be useful|partial|wrong|irrelevant"}, is_error=True)
            resolution_id = str(args.get("resolution_id", "")).strip()
            correction = str(args.get("correction", "")).strip()
            preferred_items = list(args.get("preferred_items") or [])
            rejected_items = list(args.get("rejected_items") or [])
            decision = DecisionRecord.read_by_id(repo_root, resolution_id) if resolution_id else DecisionRecord.read_latest(repo_root)
            scenario_id = decision.selections[0].artefact_key if decision and decision.selections else ""
            if not scenario_id:
                await _record("error")
                return _json_result({"code": "invalid_input", "message": "resolution_id with selected scenario is required"}, is_error=True)
            scenario = await scenario_store.get(scenario_id)
            if scenario is None:
                await _record("error")
                return _json_result({"code": "not_found", "message": f"scenario '{scenario_id}' not found"}, is_error=True)

            correction_strength = min(
                1.0,
                (min(2000, len(correction)) / 2000.0) + (len(preferred_items) * 0.1) + (len(rejected_items) * 0.05),
            )
            transition = {"from": scenario.memory_state, "to": scenario.memory_state, "reason": "unchanged"}
            evidence_hashes = _scenario_fingerprints(scenario)
            if rating in {"useful", "partial"}:
                promotion = decide_promotion(
                    PromotionContext(
                        rating=rating,
                        resolution_confidence=float(decision.partial_similarity if decision else scenario.confidence),
                        evidence_count=len(scenario.evidence),
                        contradiction_signal=float(scenario.contradiction_signal),
                        prior_successes=int(scenario.prior_successes),
                        has_explicit_pin=bool(preferred_items),
                        correction_confirmed=False,
                    ),
                    scenario.memory_state,
                )
                transition = {"from": promotion.from_state, "to": promotion.to_state, "reason": promotion.reason}
                if promotion.to_state != promotion.from_state:
                    await scenario_store.promote_scenario(
                        scenario_id,
                        new_state=promotion.to_state,
                        confidence=max(scenario.memory_confidence, scenario.confidence),
                        evidence_hashes=evidence_hashes,
                        at=time.time(),
                    )
                    await metrics_store.increment_counter("promotions_total")
                    if promotion.to_state == "trusted":
                        await metrics_store.increment_counter("trusted_scenarios_count")
                        await metrics_store.increment_counter("trusted_evidence_total", delta=len(evidence_hashes))
                if correction:
                    await scenario_store.merge_memory_section(
                        scenario_id,
                        section="feedback",
                        body=correction,
                        evidence_hashes=evidence_hashes,
                    )
                    await metrics_store.increment_counter("corrections_submitted")
            else:
                negative = decide_on_negative_feedback(
                    NegativeFeedbackContext(
                        rating=rating,
                        had_contradiction=float(scenario.contradiction_signal) >= 0.35,
                        prior_successes=int(scenario.prior_successes),
                    ),
                    scenario.memory_state,
                )
                transition = {"from": negative.from_state, "to": negative.to_state, "reason": negative.reason}
                await scenario_store.demote_scenario(
                    scenario_id,
                    new_state=negative.to_state,
                    contradiction_delta=0.25 if rating == "wrong" else 0.1,
                )
                await metrics_store.increment_counter("demotions_total")
                if scenario.memory_state == "trusted" and negative.to_state != "trusted":
                    await metrics_store.increment_counter("trusted_scenarios_count", delta=-1)

            reward = compute_reward(
                RewardInput(
                    cache_tier=decision.cache_tier if decision else "miss",
                    similarity=decision.partial_similarity if decision else 0.0,
                    rating=rating,
                    correction_strength=correction_strength,
                    contradiction_signal=float(scenario.contradiction_signal),
                )
            )
            legacy_outcome = "wrong" if rating == "wrong" else rating
            await scenario_store.record_outcome(scenario_id, legacy_outcome)
            try:
                await metrics_store.record_scenario_outcome(scenario_id=scenario_id, result=legacy_outcome, note=correction[:200])
            except Exception:
                pass
            await metrics_store.increment_counter("feedback_total")
            try:
                if rating == "useful":
                    await metrics_store.record_draft_event(status="useful", directional_correct=True, metadata={"source": "feedback"})
                elif rating in {"wrong", "irrelevant"}:
                    await metrics_store.record_draft_event(status="wrong", directional_correct=False, metadata={"source": "feedback"})
                else:
                    await metrics_store.record_draft_event(status="unused", directional_correct=False, metadata={"source": "feedback"})
            except Exception:
                pass
            if rating == "useful" and scenario.memory_state == "trusted":
                await metrics_store.increment_counter("promotions_still_trusted_total")
            if rating == "useful" and scenario.last_outcome == "wrong":
                await metrics_store.increment_counter("corrections_survived_total")
            if rating in {"useful", "partial"} and scenario.memory_state == "demoted":
                await metrics_store.increment_counter("demotion_recovery_total")
            promotion_ring.append(transition)
            if len(promotion_ring) > 5:
                del promotion_ring[0]
            updated = await scenario_store.get(scenario_id)
            append_log(
                repo_root,
                tool=name,
                label=rating,
                decision_id=resolution_id or None,
                provenance_mode=None,
                memory_state=updated.memory_state if updated else scenario.memory_state,
            )
            write_index(repo_root, await scenario_store.list_top(limit=50))
            await _record("ok", scenario_id=scenario_id)
            return _json_result({"accepted": True, "learning_applied": reward.reward_total, "memory_transition": transition})

        if name == "vaner.warm":
            if not _ensure_backend(config):
                await _record("error")
                return _backend_error(degradable=False)
            targets = list(args.get("targets") or [])
            await aprecompute(repo_root, config=config)
            append_log(
                repo_root,
                tool=name,
                label=",".join(str(item) for item in targets[:4]) or "-",
                decision_id=None,
                provenance_mode=None,
                memory_state=None,
            )
            await _record("ok")
            return _json_result({"accepted_targets": targets, "queued": len(targets)})

        if name == "vaner.inspect":
            item_id = str(args.get("item_id", "")).strip()
            if not item_id:
                await _record("error")
                return _json_result({"code": "invalid_input", "message": "item_id is required"}, is_error=True)
            scenario = await scenario_store.get(item_id)
            if scenario is None:
                for sid in DecisionRecord.list_recent_ids(repo_root, limit=10):
                    dec = DecisionRecord.read_by_id(repo_root, sid)
                    if dec is None:
                        continue
                    for selection in dec.selections:
                        if selection.artefact_key == item_id:
                            scenario = await scenario_store.get(selection.artefact_key)
                            break
                    if scenario is not None:
                        break
            if scenario is None:
                await _record("error")
                return _json_result({"code": "not_found", "message": f"item '{item_id}' not found"}, is_error=True)
            payload = {
                "id": scenario.id,
                "source": "scenario_store",
                "kind": "record",
                "locator": {"id": scenario.id, "kind": scenario.kind},
                "summary": scenario.prepared_context[:240],
                "memory": {
                    "state": scenario.memory_state,
                    "confidence": scenario.memory_confidence,
                    "evidence_count": len(json.loads(scenario.memory_evidence_hashes_json or "[]")),
                    "prior_successes": scenario.prior_successes,
                    "contradiction_signal": scenario.contradiction_signal,
                    "last_validated_at": scenario.memory_last_validated_at or 0.0,
                },
            }
            append_log(repo_root, tool=name, label=item_id, decision_id=None, provenance_mode=None, memory_state=scenario.memory_state)
            await _record("ok", scenario_id=scenario.id)
            return _json_result(payload)

        if name == "vaner.predictions.active":
            # In-process engine wins when present (used by tests + embedders).
            if engine is not None:
                active = engine.get_active_predictions()
                await _record("ok")
                return _json_result({"predictions": [_serialize_prediction_for_mcp(p) for p in active]})
            # Fall back to forwarding via the shared daemon HTTP client.
            # When the daemon is up with `--with-engine`, this returns live
            # predictions from its background precompute task.
            try:
                body = await _daemon().get_predictions_active()
            except VanerDaemonUnavailable:
                await _record("ok")
                return _json_result(
                    {
                        "predictions": [],
                        "engine_unavailable": True,
                        "hint": "start the daemon with `vaner daemon serve-http` to see live predictions",
                    }
                )
            await _record("ok")
            return _json_result(body)

        if name == "vaner.predictions.adopt":
            prediction_id_arg = str(args.get("prediction_id", "")).strip()
            if not prediction_id_arg:
                await _record("error")
                return _json_result(
                    {"code": "invalid_input", "message": "prediction_id is required"},
                    is_error=True,
                )
            # In-process engine path first.
            if engine is not None and engine.prediction_registry is not None:
                prompt = engine.prediction_registry.get(prediction_id_arg)
                if prompt is None:
                    await _record("error")
                    return _json_result(
                        {"code": "not_found", "message": f"no such prediction: {prediction_id_arg}"},
                        is_error=True,
                    )
                resolution = _build_adopt_resolution(prompt)
                # WS3.d: record adoption so the next rebalance has a signal.
                try:
                    async with engine.prediction_registry.lock:
                        engine.prediction_registry.record_adoption(prediction_id_arg)
                except Exception:
                    pass
                await _record("ok")
                return _json_result(resolution.model_dump(mode="json"))
            # Forward to daemon via the shared client.
            try:
                resolution = await _daemon().adopt_prediction(prediction_id_arg)
            except VanerDaemonNotFound:
                await _record("error")
                return _json_result(
                    {"code": "not_found", "message": f"no such prediction: {prediction_id_arg}"},
                    is_error=True,
                )
            except VanerDaemonUnavailable as exc:
                await _record("error")
                return _json_result(
                    {
                        "code": "engine_unavailable",
                        "message": str(exc)
                        or "vaner.predictions.adopt needs either an in-process engine or a running `vaner daemon serve-http --with-engine`",
                    },
                    is_error=True,
                )
            except ValueError as exc:
                await _record("error")
                return _json_result(
                    {"code": "invalid_input", "message": str(exc)},
                    is_error=True,
                )
            await _record("ok")
            return _json_result(resolution.model_dump(mode="json"))

        if name in {
            "vaner.goals.list",
            "vaner.goals.declare",
            "vaner.goals.update_status",
            "vaner.goals.delete",
        }:
            # WS7: goals live in the ArtefactStore so MCP and the daemon
            # share the same state without extra plumbing. Lazy-init so
            # legacy repos get the new table on first use.
            from vaner.intent.branch_parser import parse_branch_name
            from vaner.intent.goals import WorkspaceGoal
            from vaner.store.artefacts import ArtefactStore

            artefact_db_path = active_repo_root / ".vaner" / "artefacts.db"
            goals_store = ArtefactStore(artefact_db_path)
            await goals_store.initialize()

            if name == "vaner.goals.list":
                status = args.get("status")
                limit = int(args.get("limit", 50))
                rows: list[dict[str, object]] = await goals_store.list_workspace_goals(
                    status=status if isinstance(status, str) else None,
                    limit=max(1, min(200, limit)),
                )
                # Parse JSON-blob columns for clients.
                out: list[dict[str, Any]] = []
                for row in rows:
                    payload_row = dict(row)
                    try:
                        payload_row["evidence"] = json.loads(str(row.get("evidence_json") or "[]"))
                    except Exception:
                        payload_row["evidence"] = []
                    try:
                        payload_row["related_files"] = json.loads(str(row.get("related_files_json") or "[]"))
                    except Exception:
                        payload_row["related_files"] = []
                    payload_row.pop("evidence_json", None)
                    payload_row.pop("related_files_json", None)
                    out.append(payload_row)
                await _record("ok")
                return _json_result({"goals": out})

            if name == "vaner.goals.declare":
                title = str(args.get("title", "")).strip()
                if not title:
                    await _record("error")
                    return _json_result(
                        {"code": "invalid_input", "message": "title is required"},
                        is_error=True,
                    )
                description = str(args.get("description", "")).strip()
                related_files = args.get("related_files") or []
                if not isinstance(related_files, list):
                    related_files = []
                related_files = [str(p) for p in related_files if isinstance(p, str) and p.strip()]
                goal = WorkspaceGoal.from_hint(
                    title=title,
                    source="user_declared",
                    confidence=1.0,
                    description=description,
                    related_files=related_files,
                )
                await goals_store.upsert_workspace_goal(
                    id=goal.id,
                    title=goal.title,
                    description=goal.description,
                    source=goal.source,
                    confidence=goal.confidence,
                    status=goal.status,
                    evidence_json=json.dumps([{"kind": e.kind, "value": e.value, "weight": e.weight} for e in goal.evidence]),
                    related_files_json=json.dumps(goal.related_files),
                )
                await _record("ok")
                return _json_result({"goal_id": goal.id, "status": goal.status})

            if name == "vaner.goals.update_status":
                goal_id_arg = str(args.get("goal_id", "")).strip()
                new_status = str(args.get("status", "")).strip()
                if not goal_id_arg or not new_status:
                    await _record("error")
                    return _json_result(
                        {"code": "invalid_input", "message": "goal_id and status are required"},
                        is_error=True,
                    )
                if new_status not in {"active", "paused", "abandoned", "achieved"}:
                    await _record("error")
                    return _json_result(
                        {"code": "invalid_input", "message": f"unknown status: {new_status}"},
                        is_error=True,
                    )
                changed = await goals_store.update_workspace_goal_status(goal_id_arg, new_status)
                if not changed:
                    await _record("error")
                    return _json_result(
                        {"code": "not_found", "message": f"no such goal: {goal_id_arg}"},
                        is_error=True,
                    )
                await _record("ok")
                return _json_result({"goal_id": goal_id_arg, "status": new_status})

            if name == "vaner.goals.delete":
                goal_id_arg = str(args.get("goal_id", "")).strip()
                if not goal_id_arg:
                    await _record("error")
                    return _json_result(
                        {"code": "invalid_input", "message": "goal_id is required"},
                        is_error=True,
                    )
                deleted = await goals_store.delete_workspace_goal(goal_id_arg)
                if not deleted:
                    await _record("error")
                    return _json_result(
                        {"code": "not_found", "message": f"no such goal: {goal_id_arg}"},
                        is_error=True,
                    )
                await _record("ok")
                return _json_result({"goal_id": goal_id_arg, "deleted": True})
            # Defensive fallthrough — unreachable because of the set check above.
            _ = parse_branch_name  # reserved for branch-seeded declare flow

        # ---------------------------------------------------------------
        # 0.8.2 WS1 — intent-bearing artefacts
        # ---------------------------------------------------------------
        if name in {
            "vaner.artefacts.list",
            "vaner.artefacts.inspect",
            "vaner.artefacts.set_status",
            "vaner.artefacts.influence",
            "vaner.sources.status",
        }:
            from vaner.store.artefacts import ArtefactStore

            artefact_db_path = active_repo_root / ".vaner" / "artefacts.db"
            artefact_store = ArtefactStore(artefact_db_path)
            await artefact_store.initialize()

            if name == "vaner.artefacts.list":
                list_status = args.get("status")
                list_connector = args.get("connector")
                list_tier = args.get("source_tier")
                list_limit = int(args.get("limit", 50))
                rows = await artefact_store.list_intent_artefacts(
                    status=list_status if isinstance(list_status, str) else None,
                    connector=list_connector if isinstance(list_connector, str) else None,
                    source_tier=list_tier if isinstance(list_tier, str) else None,
                    limit=max(1, min(200, list_limit)),
                )
                artefacts_payload: list[dict[str, Any]] = []
                for row in rows:
                    entry = dict(row)
                    try:
                        entry["linked_goals"] = json.loads(str(row.get("linked_goals_json") or "[]"))
                    except Exception:
                        entry["linked_goals"] = []
                    try:
                        entry["linked_files"] = json.loads(str(row.get("linked_files_json") or "[]"))
                    except Exception:
                        entry["linked_files"] = []
                    entry.pop("linked_goals_json", None)
                    entry.pop("linked_files_json", None)
                    artefacts_payload.append(entry)
                await _record("ok")
                return _json_result({"artefacts": artefacts_payload})

            if name == "vaner.artefacts.inspect":
                artefact_id_arg = str(args.get("artefact_id", "")).strip()
                if not artefact_id_arg:
                    await _record("error")
                    return _json_result(
                        {"code": "invalid_input", "message": "artefact_id is required"},
                        is_error=True,
                    )
                artefact_row = await artefact_store.get_intent_artefact(artefact_id_arg)
                if artefact_row is None:
                    await _record("error")
                    return _json_result(
                        {"code": "not_found", "message": f"no such artefact: {artefact_id_arg}"},
                        is_error=True,
                    )
                latest_snapshot_id = str(artefact_row.get("latest_snapshot") or "")
                item_rows = (
                    await artefact_store.list_intent_artefact_items(
                        artefact_id=artefact_id_arg,
                        snapshot_id=latest_snapshot_id or None,
                    )
                    if latest_snapshot_id
                    else []
                )
                items_payload: list[dict[str, Any]] = []
                for item_row in item_rows:
                    item_entry = dict(item_row)
                    for json_field, friendly in (
                        ("related_files_json", "related_files"),
                        ("related_entities_json", "related_entities"),
                        ("evidence_refs_json", "evidence_refs"),
                    ):
                        try:
                            item_entry[friendly] = json.loads(str(item_row.get(json_field) or "[]"))
                        except Exception:
                            item_entry[friendly] = []
                        item_entry.pop(json_field, None)
                    items_payload.append(item_entry)
                artefact_payload = dict(artefact_row)
                try:
                    artefact_payload["linked_goals"] = json.loads(str(artefact_row.get("linked_goals_json") or "[]"))
                except Exception:
                    artefact_payload["linked_goals"] = []
                try:
                    artefact_payload["linked_files"] = json.loads(str(artefact_row.get("linked_files_json") or "[]"))
                except Exception:
                    artefact_payload["linked_files"] = []
                artefact_payload.pop("linked_goals_json", None)
                artefact_payload.pop("linked_files_json", None)
                await _record("ok")
                return _json_result(
                    {
                        "artefact": artefact_payload,
                        "items": items_payload,
                        "snapshot_id": latest_snapshot_id,
                    }
                )

            if name == "vaner.artefacts.set_status":
                artefact_id_arg = str(args.get("artefact_id", "")).strip()
                new_status = str(args.get("status", "")).strip()
                allowed_statuses = {"active", "dormant", "stale", "superseded", "archived"}
                if not artefact_id_arg or new_status not in allowed_statuses:
                    await _record("error")
                    return _json_result(
                        {
                            "code": "invalid_input",
                            "message": "artefact_id is required and status must be one of " + ", ".join(sorted(allowed_statuses)),
                        },
                        is_error=True,
                    )
                changed = await artefact_store.update_intent_artefact_status(artefact_id_arg, new_status)
                if not changed:
                    await _record("error")
                    return _json_result(
                        {"code": "not_found", "message": f"no such artefact: {artefact_id_arg}"},
                        is_error=True,
                    )
                await _record("ok")
                return _json_result({"artefact_id": artefact_id_arg, "status": new_status})

            if name == "vaner.artefacts.influence":
                # Spec §MCP inspectability: return which goals the artefact
                # backs, which active PredictionSpecs are anchored to its
                # items, and the most recent ReconciliationOutcome ids that
                # touched either. WS1 lands the shape; WS2 populates
                # backing_goals + anchored_predictions; WS3 populates
                # recent_reconciliation_outcomes.
                artefact_id_arg = str(args.get("artefact_id", "")).strip()
                if not artefact_id_arg:
                    await _record("error")
                    return _json_result(
                        {"code": "invalid_input", "message": "artefact_id is required"},
                        is_error=True,
                    )
                artefact_row = await artefact_store.get_intent_artefact(artefact_id_arg)
                if artefact_row is None:
                    await _record("error")
                    return _json_result(
                        {"code": "not_found", "message": f"no such artefact: {artefact_id_arg}"},
                        is_error=True,
                    )
                # Backing goals (0.8.2 WS2): scan workspace_goals for rows
                # whose artefact_refs_json names this artefact. The reverse
                # index — artefact → goal — is derived rather than stored
                # because the forward index on the goal row
                # (artefact_refs_json) is the single source of truth; this
                # avoids double-bookkeeping that could drift. Cost is
                # bounded by the active goal limit (20–50 rows).
                all_goals = await artefact_store.list_workspace_goals(status=None, limit=200)
                backing_goals_payload: list[dict[str, Any]] = []
                for goal_row in all_goals:
                    refs_json = goal_row.get("artefact_refs_json")
                    if not refs_json:
                        continue
                    try:
                        refs = json.loads(str(refs_json))
                    except Exception:
                        continue
                    if artefact_id_arg not in refs:
                        continue
                    backing_goals_payload.append(
                        {
                            "id": goal_row.get("id"),
                            "title": goal_row.get("title"),
                            "status": goal_row.get("status"),
                            "confidence": goal_row.get("confidence"),
                            "reconciliation_state": goal_row.get("pc_reconciliation_state"),
                            "unfinished_item_state": goal_row.get("pc_unfinished_item_state"),
                            "freshness": goal_row.get("pc_freshness"),
                        }
                    )
                # Anchored predictions (0.8.2 WS2): enumerate the artefact's
                # current-snapshot items in a state that would emit a
                # ``source="artefact_item"`` prediction spec
                # (pending/in_progress/stalled). The daemon's in-memory
                # prediction registry isn't reachable from the MCP handler,
                # so this returns the *anchor points* the engine would
                # emit specs for — which is the inspectability the spec
                # requires: the user can see which items are currently
                # driving prediction preparation.
                latest_snapshot_id = str(artefact_row.get("latest_snapshot") or "")
                anchored_predictions_payload: list[dict[str, Any]] = []
                if latest_snapshot_id:
                    eligible_items = await artefact_store.list_intent_artefact_items(
                        artefact_id=artefact_id_arg,
                        snapshot_id=latest_snapshot_id,
                    )
                    for item_row in eligible_items:
                        state = str(item_row.get("state") or "")
                        if state not in ("pending", "in_progress", "stalled"):
                            continue
                        anchored_predictions_payload.append(
                            {
                                "item_id": item_row.get("id"),
                                "text": item_row.get("text"),
                                "state": state,
                                "section_path": item_row.get("section_path"),
                                "source": "artefact_item",
                            }
                        )
                # Recent reconciliation outcomes for this artefact.
                outcome_rows = await artefact_store.list_reconciliation_outcomes(artefact_id=artefact_id_arg, limit=10)
                outcomes_payload = [
                    {
                        "id": row.get("id"),
                        "pass_at": row.get("pass_at"),
                        "triggering_signal_id": row.get("triggering_signal_id"),
                    }
                    for row in outcome_rows
                ]
                await _record("ok")
                return _json_result(
                    {
                        "artefact_id": artefact_id_arg,
                        "backing_goals": backing_goals_payload,
                        "anchored_predictions": anchored_predictions_payload,
                        "recent_reconciliation_outcomes": outcomes_payload,
                    }
                )

            if name == "vaner.sources.status":
                # Surface the 0.8.2 sources.intent_artefacts config + per-
                # connector ingest counts so the user can see what Vaner is
                # actually considering.
                sources_cfg = getattr(config, "sources", None)
                intent_cfg = getattr(sources_cfg, "intent_artefacts", None) if sources_cfg else None
                artefacts_all = await artefact_store.list_intent_artefacts(limit=200)
                counts_by_connector: dict[str, int] = {}
                counts_by_tier: dict[str, int] = {}
                for row in artefacts_all:
                    connector_name = str(row.get("connector") or "unknown")
                    tier_name = str(row.get("source_tier") or "unknown")
                    counts_by_connector[connector_name] = counts_by_connector.get(connector_name, 0) + 1
                    counts_by_tier[tier_name] = counts_by_tier.get(tier_name, 0) + 1

                sources_payload: dict[str, Any]
                if intent_cfg is None:
                    sources_payload = {"enabled": False, "note": "sources config not available"}
                else:
                    sources_payload = {
                        "enabled": intent_cfg.enabled,
                        "tiers": {
                            "T1": intent_cfg.tiers.T1,
                            "T2": intent_cfg.tiers.T2,
                            "T3": intent_cfg.tiers.T3,
                            "T4": intent_cfg.tiers.T4,
                        },
                        "local_plan": {
                            "allowlist": list(intent_cfg.local_plan.allowlist),
                            "excludelist": list(intent_cfg.local_plan.excludelist),
                        },
                        "markdown_outline": {
                            "enabled": intent_cfg.markdown_outline.enabled,
                            "max_candidates": intent_cfg.markdown_outline.max_candidates,
                        },
                        "github_issues": {
                            "enabled": intent_cfg.github_issues.enabled,
                            "repos": list(intent_cfg.github_issues.repos),
                            "include_closed": intent_cfg.github_issues.include_closed,
                            "max_issues": intent_cfg.github_issues.max_issues,
                        },
                    }
                await _record("ok")
                return _json_result(
                    {
                        "sources": sources_payload,
                        "ingest_counts": {
                            "by_connector": counts_by_connector,
                            "by_tier": counts_by_tier,
                            "total": len(artefacts_all),
                        },
                    }
                )
            await _record("error")
            return _json_result(
                {"code": "invalid_input", "message": f"unknown goals tool: {name}"},
                is_error=True,
            )

        if name == "vaner.debug.trace":
            if str(__import__("os").environ.get("VANER_MCP_DEBUG", "0")) != "1":
                await _record("ok")
                return _json_result({"code": "debug_disabled", "message": "Set VANER_MCP_DEBUG=1 to enable debug trace"})
            resolution_id = str(args.get("resolution_id", "")).strip()
            decision = DecisionRecord.read_by_id(repo_root, resolution_id) if resolution_id else DecisionRecord.read_latest(repo_root)
            payload = {
                "decision": decision.model_dump(mode="json") if decision else None,
                "memory_quality": await metrics_store.memory_quality_snapshot(),
                "recent_promotions": promotion_ring[-5:],
            }
            append_log(
                repo_root,
                tool=name,
                label=resolution_id or "latest",
                decision_id=decision.id if decision else None,
                provenance_mode=None,
                memory_state=None,
            )
            await _record("ok")
            return _json_result(payload)

        await _record("error")
        return _json_result({"code": "unknown_tool", "message": f"Unknown tool '{name}'"}, is_error=True)

    return server


async def run_smoke_probe(repo_root: Path) -> dict[str, Any]:
    """Run a lightweight MCP readiness probe for install/doctor checks."""
    if not (repo_root / ".vaner" / "config.toml").exists():
        init_repo(repo_root)
    config = load_config(repo_root)
    store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    await store.initialize()
    before = await store.list_top(limit=5)
    try:
        await aprecompute(repo_root, config=config)
    except Exception as exc:
        return {
            "ok": False,
            "detail": f"precompute_failed: {exc}",
            "fix": "Check backend/runtime config and run `vaner doctor --fix`.",
            "before_count": len(before),
            "after_count": len(before),
        }
    after = await store.list_top(limit=5)
    if not after:
        return {
            "ok": False,
            "detail": "no_scenarios_after_precompute",
            "fix": "Ensure repository has readable source files and rerun `vaner precompute`.",
            "before_count": len(before),
            "after_count": 0,
        }
    if _ensure_backend(config):
        await store.record_expansion(after[0].id)
    return {
        "ok": True,
        "detail": "list+precompute+expand checks passed",
        "before_count": len(before),
        "after_count": len(after),
        "scenario_id": after[0].id,
    }


async def run_stdio(repo_root: Path) -> None:
    """Run the MCP server on stdio (for Claude Desktop / Cursor local config)."""
    try:
        from mcp.server.lowlevel import NotificationOptions
        from mcp.server.stdio import stdio_server
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency path
        raise RuntimeError("MCP transport requires 'mcp[cli]>=1.0'. Install with: pip install 'mcp[cli]>=1.0'.") from exc

    server = build_server(repo_root)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="vaner",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(
                        prompts_changed=False,
                        resources_changed=False,
                        tools_changed=False,
                    ),
                    experimental_capabilities={},
                ),
            ),
        )


async def run_sse(repo_root: Path, host: str, port: int) -> None:
    import uvicorn

    try:
        from mcp.server.lowlevel import NotificationOptions
        from mcp.server.sse import SseServerTransport
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency path
        raise RuntimeError("MCP transport requires 'mcp[cli]>=1.0'. Install with: pip install 'mcp[cli]>=1.0'.") from exc

    from starlette.applications import Starlette
    from starlette.routing import Mount, Route

    server = build_server(repo_root)
    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse_transport.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(
                streams[0],
                streams[1],
                InitializationOptions(
                    server_name="vaner",
                    server_version="1.0.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(
                            prompts_changed=False,
                            resources_changed=False,
                            tools_changed=False,
                        ),
                        experimental_capabilities={},
                    ),
                ),
            )

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ]
    )
    config = uvicorn.Config(app, host=host, port=port)
    await uvicorn.Server(config).serve()
