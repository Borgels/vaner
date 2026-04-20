# SPDX-License-Identifier: Apache-2.0
# mypy: ignore-errors

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

    _MCP_IMPORT_ERROR: ModuleNotFoundError | None = None
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency path
    _MCP_IMPORT_ERROR = exc

    class Server:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise ImportError("import error in vaner.mcp.server: No module named 'mcp'") from _MCP_IMPORT_ERROR

    class InitializationOptions(dict):  # type: ignore[no-redef]
        pass

    class CallToolResult(dict):  # type: ignore[no-redef]
        pass

    class ListToolsResult(dict):  # type: ignore[no-redef]
        pass

    class TextContent(dict):  # type: ignore[no-redef]
        pass

    class Tool(dict):  # type: ignore[no-redef]
        pass


from vaner.api import aprecompute
from vaner.cli.commands.config import load_config
from vaner.cli.commands.init import init_repo
from vaner.learning.reward import RewardInput, compute_reward
from vaner.mcp.contracts import CACHE_TIER_TO_PROVENANCE, Abstain, ConflictSignal, ContextEnvelope, MemoryMeta, Resolution
from vaner.mcp.lint import run_lint
from vaner.mcp.memory_log import append_log, write_index
from vaner.memory.policy import (
    ConflictInput,
    NegativeFeedbackContext,
    PromotionContext,
    ReuseInput,
    decide_on_negative_feedback,
    decide_promotion,
    decide_reuse,
    detect_conflict,
    evidence_fingerprint,
)
from vaner.models.decision import DecisionRecord, PredictionLink, ScoreFactor, SelectionDecision
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


def _env_similarity(left: str, right: dict[str, Any] | None) -> float:
    try:
        left_obj = json.loads(left or "{}")
    except Exception:
        left_obj = {}
    right_obj = right or {}
    left_tokens = _tokenize(json.dumps(left_obj, sort_keys=True))
    right_tokens = _tokenize(json.dumps(right_obj, sort_keys=True))
    if not left_tokens and not right_tokens:
        return 1.0
    denom = max(1, len(left_tokens | right_tokens))
    return len(left_tokens & right_tokens) / denom


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


def _build_decision_id(prompt: str) -> str:
    digest = hashlib.sha256(f"{prompt}-{time.time_ns()}".encode()).hexdigest()[:16]
    return f"dec_{digest}"


def build_server(repo_root: Path) -> Server:
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
                "memory": {"counts": memory_counts, "quality": quality},
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
                confidence = min(0.98, 0.25 + (overlap * 0.15) + (scenario.score * 0.45))
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
            if not _ensure_backend(config):
                await _record("error")
                return _backend_error(degradable=False)
            query = str(args.get("query", "")).strip()
            if not query:
                await _record("error")
                return _json_result({"code": "invalid_input", "message": "query is required"}, is_error=True)
            suggestion_id = str(args.get("suggestion_id", "")).strip()
            context = args.get("context") or {}
            query_for_selection = query
            if suggestion_id and suggestion_id in suggestion_cache:
                query_for_selection = str(suggestion_cache[suggestion_id].get("label") or query)
            query_tokens = _tokenize(query_for_selection)
            scenarios = await scenario_store.list_top(limit=25)
            candidate: Scenario | None = None
            best_overlap = -1
            for scenario in scenarios:
                overlap = len(query_tokens & set(tok.lower() for tok in scenario.entities))
                if overlap > best_overlap or (overlap == best_overlap and candidate and scenario.score > candidate.score):
                    candidate = scenario
                    best_overlap = overlap
            if candidate is None:
                await aprecompute(repo_root, config=config)
                scenarios = await scenario_store.list_top(limit=25)
                candidate = scenarios[0] if scenarios else None
                best_overlap = 0
            if candidate is None:
                abstain = Abstain(
                    reason="insufficient_evidence",
                    message="No scenarios are available yet.",
                    suggestions=[],
                )
                await metrics_store.increment_counter("abstain_total")
                await metrics_store.increment_counter("resolves_total")
                await _record("ok")
                return _json_result(abstain.model_dump(mode="json"))

            expected = set(json.loads(candidate.memory_evidence_hashes_json or "[]"))
            fresh_fingerprints = set(_scenario_fingerprints(candidate))
            evidence_fresh = expected.issubset(fresh_fingerprints) if expected else candidate.freshness != "stale"
            invalidated_this_request = False
            if candidate.memory_state in {"trusted", "candidate"} and expected and not evidence_fresh:
                await scenario_store.mark_stale_by_evidence(candidate.id, evidence_hashes_now=list(fresh_fingerprints))
                invalidated_this_request = True
                refreshed_after_invalidation = await scenario_store.get(candidate.id)
                if refreshed_after_invalidation is not None:
                    candidate = refreshed_after_invalidation
                    expected = set(json.loads(candidate.memory_evidence_hashes_json or "[]"))
                    fresh_fingerprints = set(_scenario_fingerprints(candidate))
                    evidence_fresh = expected.issubset(fresh_fingerprints) if expected else candidate.freshness != "stale"
            similarity = _env_similarity(candidate.context_envelope_json, context)
            reuse = decide_reuse(
                ReuseInput(
                    evidence_fresh=evidence_fresh,
                    envelope_similarity=similarity,
                    contradiction_since_last_validation=float(candidate.contradiction_signal) >= 0.5,
                    memory_state=candidate.memory_state,
                )
            )
            if reuse == "reuse_payload":
                cache_tier = "full_hit"
            elif reuse == "rerank_prior":
                cache_tier = "partial_hit"
            else:
                cache_tier = "warm_start"
                await aprecompute(repo_root, config=config)
                refreshed = await scenario_store.get(candidate.id)
                if refreshed is not None:
                    candidate = refreshed
                    fresh_fingerprints = set(_scenario_fingerprints(candidate))
                if not fresh_fingerprints:
                    cache_tier = "miss"
            compiled_sections = {"decision_digests": candidate.prepared_context[:500]}
            conflict = detect_conflict(
                ConflictInput(
                    compiled_sections=compiled_sections,
                    compiled_entities=set(candidate.entities),
                    compiled_fingerprints=list(expected),
                    fresh_entities=set(candidate.entities),
                    fresh_fingerprints=list(fresh_fingerprints),
                )
            )

            confidence = min(0.99, max(0.05, (candidate.confidence or 0.0) * 0.7 + candidate.score * 0.3))
            freshness = "fresh"
            gaps: list[str] = list(candidate.coverage_gaps)
            if candidate.freshness == "stale":
                freshness = "stale"
                await metrics_store.increment_counter("stale_hit_total")
            if invalidated_this_request:
                freshness = "stale"
                await metrics_store.increment_counter("stale_hit_total")
            if conflict.has_conflict and conflict.strength >= 0.5 and candidate.memory_state in {"trusted", "candidate"}:
                gaps.append("memory_conflict")
                freshness = "recent" if freshness == "fresh" else "stale"
                confidence = max(0.05, confidence * (1.0 - min(0.6, conflict.strength * 0.35)))
                await metrics_store.increment_counter("conflict_total")
                if conflict.strength >= 0.7:
                    abstain = Abstain(
                        reason="memory_conflict",
                        message="Compiled memory conflicts with current evidence.",
                        suggestions=[],
                        conflict=ConflictSignal.model_validate(conflict.model_dump(mode="json")),
                    )
                    await metrics_store.increment_counter("abstain_total")
                    await metrics_store.increment_counter("resolves_total")
                    append_log(
                        repo_root,
                        tool=name,
                        label=query[:60],
                        decision_id=None,
                        provenance_mode=CACHE_TIER_TO_PROVENANCE.get(cache_tier),
                        memory_state=candidate.memory_state,
                    )
                    await _record("ok", scenario_id=candidate.id)
                    return _json_result(abstain.model_dump(mode="json"))

            if confidence < 0.35:
                abstain = Abstain(
                    reason="low_confidence",
                    message="Resolution confidence is below threshold.",
                    suggestions=[],
                    conflict=ConflictSignal.model_validate(conflict.model_dump(mode="json")) if conflict.has_conflict else None,
                )
                await metrics_store.increment_counter("abstain_total")
                await metrics_store.increment_counter("resolves_total")
                append_log(
                    repo_root,
                    tool=name,
                    label=query[:60],
                    decision_id=None,
                    provenance_mode=CACHE_TIER_TO_PROVENANCE.get(cache_tier),
                    memory_state=candidate.memory_state,
                )
                await _record("ok", scenario_id=candidate.id)
                return _json_result(abstain.model_dump(mode="json"))

            decision = DecisionRecord(
                id=_build_decision_id(query),
                prompt=query,
                prompt_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
                assembled_at=time.time(),
                cache_tier=cache_tier,
                partial_similarity=float(similarity),
                token_budget=4096,
                token_used=min(2048, max(256, len(candidate.prepared_context) // 3)),
                selections=[
                    SelectionDecision(
                        artefact_key=candidate.id,
                        source_path="scenario_store",
                        final_score=float(candidate.score),
                        token_count=min(1024, max(128, len(candidate.prepared_context) // 4)),
                        stale=(candidate.freshness == "stale"),
                        rationale="highest overlap scenario",
                        factors=[
                            ScoreFactor(name="overlap", contribution=float(best_overlap), detail="query/entity overlap"),
                            ScoreFactor(name="score", contribution=float(candidate.score), detail="scenario score"),
                        ],
                    )
                ],
                prediction_links={
                    candidate.id: PredictionLink(
                        source="scenario_store",
                        scenario_question=query,
                        scenario_rationale="resolve",
                        confidence=float(confidence),
                    )
                },
                notes=[
                    json.dumps(
                        {
                            "memory_meta": {
                                "state": candidate.memory_state,
                                "confidence": candidate.memory_confidence,
                                "evidence_count": len(expected),
                                "prior_successes": candidate.prior_successes,
                                "contradiction_signal": candidate.contradiction_signal,
                                "last_validated_at": candidate.memory_last_validated_at,
                            }
                        }
                    ),
                    json.dumps({"conflict": conflict.model_dump(mode="json")}),
                ],
            )
            decision.write(repo_root)
            resolved_hashes = list(fresh_fingerprints or expected)
            await scenario_store.merge_memory_section(
                candidate.id,
                section="decision_digests",
                body=f"{query}\n\n{candidate.prepared_context[:600]}",
                evidence_hashes=resolved_hashes,
                mark_stale_older=False,
            )
            if candidate.entities:
                invariant_body = "Stable entities observed:\n" + "\n".join(f"- {entity}" for entity in candidate.entities[:8])
                await scenario_store.merge_memory_section(
                    candidate.id,
                    section="invariants",
                    body=invariant_body,
                    evidence_hashes=resolved_hashes,
                    mark_stale_older=False,
                )

            evidence_items = []
            max_evidence_items = max(1, int(args.get("max_evidence_items", 8)))
            for index, ev in enumerate(candidate.evidence[:max_evidence_items], start=1):
                evidence_items.append(
                    {
                        "id": f"ev_{index}",
                        "source": ev.source_path or "unknown",
                        "kind": "file",
                        "locator": {"symbol": ev.key},
                        "reason": "high-weight scenario evidence",
                        "fingerprint": evidence_fingerprint(ev.source_path, {"key": ev.key}, ev.excerpt[:128], ev.weight),
                    }
                )

            resolution = Resolution(
                intent=f"{candidate.kind}::{candidate.id}",
                confidence=float(confidence),
                summary=candidate.prepared_context[:400] or "No summary available.",
                evidence=evidence_items,
                alternatives_considered=[],
                gaps=sorted(set(gaps)),
                next_actions=["vaner.expand", "vaner.explain"],
                context_envelope=ContextEnvelope.model_validate(
                    {
                        "domain": str(context.get("domain", "code")),
                        "current_artifact": context.get("current_artifact"),
                        "selection": context.get("selection"),
                        "recent_queries": list(context.get("recent_queries") or []),
                        "agent_goal": context.get("agent_goal"),
                    }
                ),
                provenance={
                    "mode": CACHE_TIER_TO_PROVENANCE.get(cache_tier, "retrieval_fallback"),
                    "cache": "warm" if cache_tier in {"full_hit", "partial_hit"} else "cold",
                    "freshness": freshness,
                    "memory": MemoryMeta(
                        state=candidate.memory_state,
                        confidence=float(candidate.memory_confidence),
                        last_validated_at=float(candidate.memory_last_validated_at or 0.0),
                        evidence_count=len(expected),
                        prior_successes=int(candidate.prior_successes),
                        contradiction_signal=float(candidate.contradiction_signal),
                    ),
                },
                resolution_id=decision.id,
            )
            await metrics_store.increment_counter("resolves_total")
            if cache_tier == "full_hit":
                await metrics_store.increment_counter("predictive_hit_total")
            append_log(
                repo_root,
                tool=name,
                label=query[:60],
                decision_id=resolution.resolution_id,
                provenance_mode=resolution.provenance.mode,
                memory_state=candidate.memory_state,
            )
            write_index(repo_root, await scenario_store.list_top(limit=50))
            await _record("ok", scenario_id=candidate.id)
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
                results.append(
                    {
                        "id": f"res_{idx}",
                        "source": scenario.id,
                        "kind": "file",
                        "snippet": scenario.prepared_context[:160],
                        "score": round(min(1.0, 0.2 + score * 0.2 + scenario.score * 0.5), 4),
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
    from mcp.server.stdio import stdio_server

    server = build_server(repo_root)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="vaner",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )


async def run_sse(repo_root: Path, host: str, port: int) -> None:
    import uvicorn
    from mcp.server.sse import SseServerTransport
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
                        notification_options=None,
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
