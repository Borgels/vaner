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


# ---------------------------------------------------------------------------
# 0.8.6 WS7 — Setup-surface helpers.
#
# These keep the MCP setup tools light: no typer / Rich. The shared JSON
# shapes come from :mod:`vaner.setup.serializers` so the CLI surface
# (WS6) and the MCP surface (WS7) emit byte-identical contracts.
# ---------------------------------------------------------------------------


def _setup_question_schema() -> list[dict[str, Any]]:
    """Static ordered schema for the five Simple-Mode questions.

    Mirrors the choice tables in :mod:`vaner.cli.commands.setup`. Static
    data — no engine state — so cockpit / desktop UIs can render the
    same prompts without hardcoding strings.
    """

    return [
        {
            "id": "work_styles",
            "prompt": "What kind of work do you want help with?",
            "kind": "multi",
            "default": ["mixed"],
            "options": [
                {"value": "writing", "label": "Writing — drafting, editing, narrative"},
                {"value": "research", "label": "Research — surveys, deep reading, citations"},
                {"value": "planning", "label": "Planning — design docs, roadmaps, project layout"},
                {"value": "support", "label": "Support — answering questions, troubleshooting"},
                {"value": "learning", "label": "Learning — studying, exploring a new domain"},
                {"value": "coding", "label": "Coding — software development"},
                {"value": "general", "label": "General — knowledge work, mixed light tasks"},
                {"value": "mixed", "label": "Mixed — a bit of everything (safe default)"},
                {"value": "unsure", "label": "Unsure — I'd rather Vaner picks for me"},
            ],
        },
        {
            "id": "priority",
            "prompt": "What matters most?",
            "kind": "single",
            "default": "balanced",
            "options": [
                {"value": "balanced", "label": "Balanced — a sensible middle"},
                {"value": "speed", "label": "Speed — snappy responses"},
                {"value": "quality", "label": "Quality — best answer, even if slow"},
                {"value": "privacy", "label": "Privacy — keep data on this machine"},
                {"value": "cost", "label": "Cost — minimise spend"},
                {"value": "low_resource", "label": "Low-resource — go easy on this machine"},
            ],
        },
        {
            "id": "compute_posture",
            "prompt": "How hard should this machine work for you?",
            "kind": "single",
            "default": "balanced",
            "options": [
                {"value": "light", "label": "Light — barely use the CPU/GPU"},
                {"value": "balanced", "label": "Balanced — work with what's idle"},
                {"value": "available_power", "label": "Available-power — use what this box has"},
            ],
        },
        {
            "id": "cloud_posture",
            "prompt": "How do you feel about cloud LLMs?",
            "kind": "single",
            "default": "ask_first",
            "options": [
                {"value": "local_only", "label": "Local only — never reach for cloud LLMs"},
                {"value": "ask_first", "label": "Ask first — confirm before any cloud call"},
                {"value": "hybrid_when_worth_it", "label": "Hybrid — cloud when it's clearly worth it"},
                {"value": "best_available", "label": "Best available — use the best model for the job"},
            ],
        },
        {
            "id": "background_posture",
            "prompt": "How aggressive should background pondering be?",
            "kind": "single",
            "default": "normal",
            "options": [
                {"value": "minimal", "label": "Minimal — barely ponder when idle"},
                {"value": "normal", "label": "Normal — moderate background pondering"},
                {"value": "idle_more", "label": "Idle-more — ponder broadly when the box is idle"},
                {"value": "deep_run_aggressive", "label": "Deep-Run-aggressive — happy to run overnight"},
            ],
        },
    ]


def _setup_apply_handler(repo_root: Path, args: dict[str, Any]) -> CallToolResult:
    """Dispatch for ``vaner.setup.apply``.

    Resolves ``answers`` or ``bundle_id`` into the chosen bundle, runs
    :func:`apply_policy_bundle` to get the override list (incl. the
    cloud-widening sentinel), and persists the result unless
    ``dry_run`` is set or ``confirm_cloud_widening`` is missing while
    the change widens cloud posture.
    """

    from datetime import UTC, datetime

    from vaner.cli.commands.config import load_config as _load_config
    from vaner.cli.commands.setup import (
        _persist_setup_and_policy,
        _read_policy_section,
        _read_setup_section,
    )
    from vaner.setup.apply import (
        WIDENS_CLOUD_POSTURE_SENTINEL,
        apply_policy_bundle,
    )
    from vaner.setup.catalog import bundle_by_id
    from vaner.setup.hardware import detect as _hw_detect
    from vaner.setup.select import select_policy_bundle as _select_bundle
    from vaner.setup.serializers import (
        AnswersValidationError,
        answers_from_payload,
    )

    bundle_id_arg = args.get("bundle_id")
    answers_arg = args.get("answers")
    confirm_cloud_widening = bool(args.get("confirm_cloud_widening", False))
    dry_run = bool(args.get("dry_run", False))

    # ------------------------------------------------------------------
    # 1. Resolve answers + bundle.
    # ------------------------------------------------------------------
    chosen_bundle_id: str
    answers: Any
    if isinstance(bundle_id_arg, str) and bundle_id_arg:
        try:
            bundle = bundle_by_id(bundle_id_arg)
        except KeyError:
            return _json_result(
                {"code": "unknown_bundle_id", "message": f"unknown bundle id: {bundle_id_arg!r}"},
                is_error=True,
            )
        chosen_bundle_id = bundle.id
        existing_setup = _read_setup_section(repo_root)
        if existing_setup:
            try:
                answers = answers_from_payload(existing_setup)
            except AnswersValidationError:
                from vaner.setup.answers import SetupAnswers

                answers = SetupAnswers(
                    work_styles=("mixed",),
                    priority="balanced",
                    compute_posture="balanced",
                    cloud_posture="ask_first",
                    background_posture="normal",
                )
        else:
            from vaner.setup.answers import SetupAnswers

            answers = SetupAnswers(
                work_styles=("mixed",),
                priority="balanced",
                compute_posture="balanced",
                cloud_posture="ask_first",
                background_posture="normal",
            )
    else:
        if answers_arg is None:
            return _json_result(
                {
                    "code": "invalid_input",
                    "message": "either `answers` or `bundle_id` must be provided",
                },
                is_error=True,
            )
        try:
            answers = answers_from_payload(answers_arg)
        except AnswersValidationError as exc:
            return _json_result(
                {"code": "invalid_input", "message": str(exc)},
                is_error=True,
            )
        try:
            hardware = _hw_detect()
            selection = _select_bundle(answers, hardware)
        except Exception as exc:
            return _json_result(
                {"code": "recommend_failed", "message": str(exc)},
                is_error=True,
            )
        chosen_bundle_id = selection.bundle.id
        bundle = selection.bundle

    # ------------------------------------------------------------------
    # 2. Compute overrides + cloud-widening flag.
    # ------------------------------------------------------------------
    config = _load_config(repo_root)
    prior_policy_section = _read_policy_section(repo_root)
    prior_bundle_id = prior_policy_section.get("selected_bundle_id")
    if isinstance(prior_bundle_id, str) and prior_bundle_id:
        config = config.model_copy(update={"policy": config.policy.model_copy(update={"selected_bundle_id": prior_bundle_id})})
    applied = apply_policy_bundle(config, bundle)
    overrides_applied = list(applied.overrides_applied)
    widens_cloud_posture = any(line.startswith(WIDENS_CLOUD_POSTURE_SENTINEL) for line in overrides_applied)

    # ------------------------------------------------------------------
    # 3. Decide whether to write.
    # ------------------------------------------------------------------
    written = False
    block_reason: str | None = None
    if dry_run:
        block_reason = "dry_run=True; config not written"
    elif widens_cloud_posture and not confirm_cloud_widening:
        block_reason = "WIDENS_CLOUD_POSTURE: refusing to widen cloud posture without confirm_cloud_widening=True"
    else:
        try:
            _persist_setup_and_policy(
                repo_root,
                answers,
                chosen_bundle_id,
                completed_at=datetime.now(UTC),
            )
            written = True
        except Exception as exc:
            return _json_result(
                {"code": "persist_failed", "message": str(exc)},
                is_error=True,
            )

    return _json_result(
        {
            "bundle_id": chosen_bundle_id,
            "overrides_applied": overrides_applied,
            "widens_cloud_posture": widens_cloud_posture,
            "written": written,
            "block_reason": block_reason,
        }
    )


def _setup_status_handler(repo_root: Path) -> CallToolResult:
    """Dispatch for ``vaner.setup.status``.

    Read-only snapshot of the current ``[setup]`` / ``[policy]`` state
    plus a fresh hardware probe and the materialised :class:`AppliedPolicy`.
    """

    from vaner.cli.commands.config import load_config as _load_config
    from vaner.cli.commands.setup import (
        _read_policy_section,
        _read_setup_section,
    )
    from vaner.setup.apply import apply_policy_bundle
    from vaner.setup.catalog import bundle_by_id
    from vaner.setup.hardware import detect as _hw_detect
    from vaner.setup.serializers import hardware_to_dict

    setup_section = _read_setup_section(repo_root)
    policy_section = _read_policy_section(repo_root)
    hardware = _hw_detect()

    mode = setup_section.get("mode") if isinstance(setup_section, dict) else None
    if mode not in ("simple", "advanced"):
        mode = "simple"
    selected_bundle_id = policy_section.get("selected_bundle_id") or "hybrid_balanced"
    completed_at = setup_section.get("completed_at")

    applied_payload: dict[str, Any] | None = None
    try:
        bundle = bundle_by_id(str(selected_bundle_id))
        config = _load_config(repo_root)
        # Pin prior bundle to current so the cloud-widening guard does
        # not double-fire when status is re-rendered.
        config = config.model_copy(update={"policy": config.policy.model_copy(update={"selected_bundle_id": str(selected_bundle_id)})})
        applied = apply_policy_bundle(config, bundle)
        applied_payload = {
            "bundle_id": applied.bundle_id,
            "overrides_applied": list(applied.overrides_applied),
        }
    except KeyError:
        applied_payload = {
            "bundle_id": str(selected_bundle_id),
            "overrides_applied": [],
            "error": f"unknown bundle id {selected_bundle_id!r}",
        }

    return _json_result(
        {
            "mode": mode,
            "selected_bundle_id": str(selected_bundle_id),
            "completed_at": str(completed_at) if completed_at is not None else None,
            "applied_policy": applied_payload,
            "hardware": hardware_to_dict(hardware),
        }
    )


def _policy_show_handler(repo_root: Path) -> CallToolResult:
    """Dispatch for ``vaner.policy.show``.

    Returns the full :class:`VanerPolicyBundle` JSON for the currently
    selected bundle plus the verbatim ``overrides_applied`` list. Drives
    the desktop transparency disclosure panel.
    """

    from vaner.cli.commands.config import load_config as _load_config
    from vaner.cli.commands.setup import _read_policy_section
    from vaner.setup.apply import apply_policy_bundle
    from vaner.setup.catalog import bundle_by_id
    from vaner.setup.serializers import bundle_to_dict

    policy_section = _read_policy_section(repo_root)
    selected_bundle_id = policy_section.get("selected_bundle_id") or "hybrid_balanced"
    try:
        bundle = bundle_by_id(str(selected_bundle_id))
    except KeyError:
        return _json_result(
            {
                "code": "unknown_bundle_id",
                "message": f"unknown bundle id {selected_bundle_id!r}",
                "selected_bundle_id": str(selected_bundle_id),
            },
            is_error=True,
        )
    config = _load_config(repo_root)
    config = config.model_copy(update={"policy": config.policy.model_copy(update={"selected_bundle_id": str(selected_bundle_id)})})
    applied = apply_policy_bundle(config, bundle)
    return _json_result(
        {
            "bundle": bundle_to_dict(bundle),
            "overrides_applied": list(applied.overrides_applied),
        }
    )


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


def _serialize_prediction_for_mcp(prompt: Any, *, rank: int | None = None) -> dict[str, Any]:
    """Render a PredictedPrompt into the shape returned by vaner.predictions.active.

    0.8.5 WS5: payload now includes the UI-facing derivations (readiness_label,
    eta_bucket, adoptable, suppression_reason, source_label, ui_summary)
    computed by :func:`vaner.intent.prediction_card.derive_card_fields`. The
    fields are always present (optional in the Rust contract mirror) so
    MCP Apps clients and text-fallback renderers share one shape.
    """
    from vaner.intent.prediction_card import derive_card_fields

    spec = prompt.spec
    run = prompt.run
    artifacts = prompt.artifacts
    card = derive_card_fields(prompt)
    payload: dict[str, Any] = {
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
        "scenarios_spawned": run.scenarios_spawned,
        "evidence_score": artifacts.evidence_score,
        "has_draft": artifacts.draft_answer is not None,
        "has_briefing": artifacts.prepared_briefing is not None,
        # 0.8.5 WS5 — UI card derivations.
        "readiness_label": card.readiness_label,
        "eta_bucket": card.eta_bucket,
        "eta_bucket_label": card.eta_bucket_label,
        "adoptable": card.adoptable,
        "suppression_reason": card.suppression_reason,
        "source_label": card.source_label,
        "ui_summary": card.ui_summary,
    }
    if rank is not None:
        payload["rank"] = rank
    return payload


_RESOURCE_METRIC_TASKS: set[Any] = set()
"""Keep strong refs to background metric tasks until they settle.

Without this, `asyncio.run()` can tear the loop down before the task
completes, producing `Event loop is closed` warnings. Tracked via
`add_done_callback(discard)` so entries clear themselves when the task
finishes.
"""


def _increment_resource_metric(name: str, repo_root: Path) -> None:
    """Fire-and-forget metric increment for resource-read events.

    0.8.5 WS8: `read_resource` is a sync entry point in the SDK API, so we
    can't `await` on `MetricsStore`. Spawn an asyncio task when a loop is
    running; otherwise silently drop — metrics are best-effort.
    """
    import asyncio as _asyncio

    async def _do() -> None:
        try:
            store = MetricsStore(repo_root / ".vaner" / "metrics.db")
            await store.initialize()
            await store.increment_counter(name)
        except Exception:  # pragma: no cover - defensive metrics
            pass

    try:
        loop = _asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_do())
    _RESOURCE_METRIC_TASKS.add(task)
    task.add_done_callback(_RESOURCE_METRIC_TASKS.discard)


def _dashboard_fallback_text(cards: list[dict[str, Any]]) -> str:
    """Render a plain-text Vaner dashboard for non-UI MCP clients.

    0.8.5 WS5: called from the `vaner.predictions.dashboard` handler when
    the connected client does not advertise MCP Apps support. The text
    format matches the spec exactly so downstream scripts can regex it if
    they want to.
    """
    if not cards:
        return "Vaner is preparing likely next steps.\nNo adoptable predictions are ready yet."
    lines: list[str] = [f"Vaner has {len(cards)} active prediction(s):", ""]
    for i, card in enumerate(cards, start=1):
        readiness = card.get("readiness_label") or card.get("readiness") or "Unknown"
        eta = card.get("eta_bucket_label")
        label = card.get("label", "").strip() or "(untitled)"
        marker = "Ready" if card.get("adoptable") else readiness
        eta_text = f" ({eta})" if eta and eta != readiness else ""
        lines.append(f'{i}. {marker}{eta_text} — "{label}"')
        if card.get("suppression_reason"):
            lines.append(f"   Not adoptable yet: {card['suppression_reason']}")
    lines.append("")
    lines.append("Use vaner.predictions.adopt with a prediction id to adopt one.")
    return "\n".join(lines)


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

    def _detect_and_record_tier() -> None:
        """Lazy capability detection on first tool/resource call per session.

        The low-level MCP Server does not expose an ``on_initialize`` hook,
        so we piggyback on the first handler call. Safe to call repeatedly —
        :func:`record_tier` replaces any prior cache entry for the session.
        """
        try:
            from vaner.integrations.capability import detect_tier, record_tier

            ctx = getattr(server, "request_context", None)
            session = getattr(ctx, "session", None) if ctx is not None else None
            client_params = getattr(session, "client_params", None) if session is not None else None
            if session is None or client_params is None:
                return
            detection = detect_tier(client_params)
            record_tier(session, detection)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("capability detection skipped: %s", exc)

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        _detect_and_record_tier()
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
                    description=(
                        "Build a context package for an explicit query when no prepared prediction matches. "
                        "Returns briefing + draft answer + ranked evidence with provenance. "
                        "Call this when (a) you've checked vaner.predictions.active and no 'ready' prediction "
                        "matches the user's actual intent, or (b) the user asked for something Vaner couldn't "
                        "have anticipated. Do NOT call vaner.resolve in parallel with vaner.predictions.adopt "
                        "for the same intent — adopt already returns a Resolution. If the conversation context "
                        "already contains a fresh <VANER_ADOPTED_PACKAGE> block, answer from that block rather "
                        "than calling this tool."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "suggestion_id": {"type": "string"},
                            "context": {"type": "object"},
                            "budget": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
                            "max_evidence_items": {"type": "integer", "default": 8},
                            "include_briefing": {"type": "boolean", "default": False},
                            "include_predicted_response": {"type": "boolean", "default": False},
                            "include_metrics": {"type": "boolean", "default": False},
                            "estimated_cost_per_1k_tokens": {"type": "number", "default": 0.0},
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
                        "Return the predictions Vaner has already prepared for this workspace, ranked by readiness. "
                        "Each entry includes a label, readiness state (queued/grounding/evidence_gathering/drafting/ready/stale), "
                        "confidence, compute_contract, and when populated — readiness_label, eta_bucket, adoptable, rank. "
                        "Call this when (a) the user starts a new turn and intent is unclear, (b) the request is vague or "
                        "implicit, or (c) you're about to call vaner.resolve and want to check for a fresher prepared package. "
                        "Do NOT call mechanically — Vaner refreshes on its own cycle, so calling more than once per ~30s wastes "
                        "budget. If a prediction is 'ready' and matches intent, prefer vaner.predictions.adopt over "
                        "vaner.resolve to reuse cached compute."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="vaner.predictions.adopt",
                    description=(
                        "Adopt a specific prediction by id, marking it as the user's actual intent and returning the prepared "
                        "Resolution (briefing + draft + evidence + adopted_from_prediction_id). Call this instead of "
                        "vaner.resolve when vaner.predictions.active or the MCP Apps dashboard surfaced a 'ready'/'drafting' "
                        "prediction whose label matches the user's request — adoption is faster (cached) and improves Vaner's "
                        "future predictions via the adoption-outcome feedback loop. Adopt at most one prediction per user "
                        "turn. If no prediction matches, fall through to vaner.resolve."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "prediction_id": {"type": "string"},
                            "source": {
                                "type": "string",
                                "description": "Where the adopt request came from (for metrics): mcp_app, mcp_tool, cli, desktop, unknown.",
                                "enum": ["mcp_app", "mcp_tool", "cli", "desktop", "unknown"],
                                "default": "mcp_tool",
                            },
                        },
                        "required": ["prediction_id"],
                    },
                ),
                Tool(
                    name="vaner.predictions.dashboard",
                    description=(
                        "Open the interactive Vaner predictions dashboard. On MCP-Apps-capable clients "
                        "(Claude Desktop, ChatGPT, others that advertise the io.modelcontextprotocol/ui "
                        "extension) this attaches an inline ui:// resource — the user gets prediction "
                        "cards with Adopt buttons. On other clients it returns a structured text "
                        "summary of the top ready/drafting predictions. Call this when the user asks "
                        "'what are you preparing?', 'show me the dashboard', or when you want to let "
                        "the user pick a prediction rather than adopting one yourself. The returned "
                        "payload always includes the same compact card model regardless of UI support."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                            "min_readiness": {
                                "type": "string",
                                "enum": [
                                    "queued",
                                    "grounding",
                                    "evidence_gathering",
                                    "drafting",
                                    "ready",
                                ],
                                "default": "queued",
                            },
                            "include_details": {"type": "boolean", "default": False},
                        },
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
                # 0.8.3 WS4 — Deep-Run lifecycle MCP surface.
                Tool(
                    name="vaner.deep_run.start",
                    description=(
                        "Start a Deep-Run session declaring a long uninterrupted "
                        "preparation window. Cost cap defaults to 0 (no remote "
                        "spend). Returns the persisted session record."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "ends_at": {
                                "type": "number",
                                "description": "Absolute epoch end timestamp.",
                            },
                            "preset": {
                                "type": "string",
                                "enum": ["conservative", "balanced", "aggressive"],
                                "default": "balanced",
                            },
                            "focus": {
                                "type": "string",
                                "enum": ["active_goals", "current_workspace", "all_recent"],
                                "default": "active_goals",
                            },
                            "horizon_bias": {
                                "type": "string",
                                "enum": [
                                    "likely_next",
                                    "long_horizon",
                                    "finish_partials",
                                    "balanced",
                                ],
                                "default": "balanced",
                            },
                            "locality": {
                                "type": "string",
                                "enum": ["local_only", "local_preferred", "allow_cloud"],
                                "default": "local_preferred",
                            },
                            "cost_cap_usd": {"type": "number", "default": 0.0},
                            "metadata": {"type": "object"},
                        },
                        "required": ["ends_at"],
                    },
                ),
                Tool(
                    name="vaner.deep_run.stop",
                    description=(
                        "Stop the active Deep-Run session. Returns the summary "
                        "(four-counter honesty: kept / discarded / rolled_back "
                        "/ failed) or null if no session was active."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "kill": {"type": "boolean", "default": False},
                            "reason": {"type": "string"},
                        },
                    },
                ),
                Tool(
                    name="vaner.deep_run.status",
                    description="Return the active Deep-Run session, or null.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="vaner.deep_run.list",
                    description="List recent Deep-Run sessions, newest first.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "default": 20,
                                "minimum": 1,
                                "maximum": 200,
                            },
                        },
                    },
                ),
                Tool(
                    name="vaner.deep_run.show",
                    description="Show one Deep-Run session by id (active or historical).",
                    inputSchema={
                        "type": "object",
                        "properties": {"session_id": {"type": "string"}},
                        "required": ["session_id"],
                    },
                ),
                # 0.8.6 WS9 — Bundle-derived Deep-Run start-dialog seeds.
                Tool(
                    name="vaner.deep_run.defaults",
                    description=(
                        "Return the bundle-derived seed values for the "
                        "Deep-Run start dialog: preset, horizon_bias, "
                        "locality, cost_cap_usd, focus, plus a list of "
                        "human-readable reasons. Read-only. Surfaces / "
                        "desktops use this to pre-fill the form so users "
                        "see sensible bundle-aware defaults instead of a "
                        "blank dialog."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
                # 0.8.6 WS7 — Setup-surface MCP tools.
                Tool(
                    name="vaner.setup.questions",
                    description=(
                        "Return the ordered Simple-Mode question schema "
                        "(work styles, priority, compute posture, cloud "
                        "posture, background posture). Static — no engine "
                        "state. Desktops/cockpit consume this so they "
                        "don't hardcode question strings."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="vaner.setup.recommend",
                    description=(
                        "Run WS3 bundle selection on a SetupAnswers payload "
                        "and return the SelectionResult JSON (chosen bundle, "
                        "score, reasons, runner-ups, forced_fallback). Pure "
                        "read; no config write."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "work_styles": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ],
                                "description": "WorkStyle id(s); single string or list.",
                            },
                            "priority": {"type": "string"},
                            "compute_posture": {"type": "string"},
                            "cloud_posture": {"type": "string"},
                            "background_posture": {"type": "string"},
                        },
                    },
                ),
                Tool(
                    name="vaner.setup.apply",
                    description=(
                        "Persist a chosen policy bundle to .vaner/config.toml. "
                        "Pass `answers` for selection or `bundle_id` to pin a "
                        "specific bundle. Surfaces a WIDENS_CLOUD_POSTURE "
                        "warning via `widens_cloud_posture=true`; defaults to "
                        "NOT writing on widening unless `confirm_cloud_widening` "
                        "is true. Set `dry_run` to true to preview without "
                        "writing."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "answers": {
                                "type": "object",
                                "description": "SetupAnswers payload (optional if bundle_id is set).",
                            },
                            "bundle_id": {
                                "type": "string",
                                "description": "Skip selection; pin this bundle id directly.",
                            },
                            "confirm_cloud_widening": {"type": "boolean", "default": False},
                            "dry_run": {"type": "boolean", "default": False},
                        },
                    },
                ),
                Tool(
                    name="vaner.setup.status",
                    description=(
                        "Read current setup mode + selected bundle id + applied policy overrides + hardware profile. Read-only; no input."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="vaner.policy.show",
                    description=(
                        "Return the full VanerPolicyBundle JSON for the "
                        "currently selected bundle plus the overrides_applied "
                        "list. Drives the desktop transparency disclosure "
                        "panel. Read-only."
                    ),
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]
        )

    @server.list_resources()
    async def list_resources() -> list[Any]:
        """Advertise Vaner's MCP resources.

        - `vaner://guidance/current` — canonical operational guidance doc.
        - `ui://vaner/active-predictions` — MCP Apps UI bundle (when
          enabled via `mcp.apps_ui_enabled` config, default True).

        List reflects the current config snapshot; UI-capable clients pick
        up the `ui://` resource and pre-fetch it when rendering the
        dashboard tool result.
        """
        try:
            from mcp.types import Resource as _Resource
        except ModuleNotFoundError:  # pragma: no cover - optional dependency path
            return []
        from vaner.integrations.guidance import current_version

        resources: list[Any] = [
            _Resource(
                uri="vaner://guidance/current",  # type: ignore[arg-type]
                name="Vaner Guidance",
                title="Vaner operational guidance (canonical)",
                description=(
                    "Canonical operational guidance for agents using Vaner. "
                    "Version " + str(current_version()) + ". Embed this text in the system/developer prompt so the agent "
                    "calls Vaner tools correctly."
                ),
                mimeType="text/markdown",
            )
        ]
        try:
            cfg = load_config(repo_root)
            if getattr(cfg.mcp, "apps_ui_enabled", True):
                from vaner.mcp.apps import (
                    ACTIVE_PREDICTIONS_DESCRIPTION,
                    ACTIVE_PREDICTIONS_MIME,
                    ACTIVE_PREDICTIONS_NAME,
                    ACTIVE_PREDICTIONS_TITLE,
                    ACTIVE_PREDICTIONS_URI,
                    resource_meta,
                )

                resources.append(
                    _Resource(
                        uri=ACTIVE_PREDICTIONS_URI,  # type: ignore[arg-type]
                        name=ACTIVE_PREDICTIONS_NAME,
                        title=ACTIVE_PREDICTIONS_TITLE,
                        description=ACTIVE_PREDICTIONS_DESCRIPTION,
                        mimeType=ACTIVE_PREDICTIONS_MIME,
                        meta=resource_meta(),
                    )
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("MCP Apps resource registration skipped: %s", exc)
        return resources

    @server.read_resource()
    async def read_resource(uri: Any) -> list[Any]:
        """Serve guidance (`vaner://guidance/*`) or MCP Apps UI (`ui://vaner/*`)."""
        from mcp.server.lowlevel.helper_types import ReadResourceContents

        from vaner.integrations.guidance import available_variants, load_guidance
        from vaner.mcp.apps import (
            ACTIVE_PREDICTIONS_HTML,
            ACTIVE_PREDICTIONS_MIME,
            ACTIVE_PREDICTIONS_URI,
            resource_meta,
        )

        uri_str = str(uri)

        # MCP Apps UI bundle.
        if uri_str == ACTIVE_PREDICTIONS_URI:
            _increment_resource_metric("mcp_apps_bundle_read", repo_root)
            return [
                ReadResourceContents(
                    content=ACTIVE_PREDICTIONS_HTML,
                    mime_type=ACTIVE_PREDICTIONS_MIME,
                    meta=resource_meta(),
                )
            ]

        # Guidance variants.
        variant: str | None = None
        if uri_str == "vaner://guidance/current":
            variant = "canonical"
        elif uri_str.startswith("vaner://guidance/"):
            tail = uri_str.split("vaner://guidance/", 1)[1]
            name, _, query = tail.partition("?")
            v = "canonical"
            if query:
                for pair in query.split("&"):
                    k, _, val = pair.partition("=")
                    if k == "variant":
                        v = val
            if v not in available_variants():
                raise ValueError(f"unknown guidance variant: {v!r}")
            if name in ("current", ""):
                variant = v
        if variant is None:
            raise ValueError(f"unknown vaner resource: {uri_str!r}")
        body = load_guidance(variant).as_text()  # type: ignore[arg-type]
        _increment_resource_metric(f"guidance_resource_read_{variant}", repo_root)
        return [ReadResourceContents(content=body, mime_type="text/markdown")]

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
            # 0.8.3 WS4: surface active Deep-Run session state under
            # ``deep_run`` so cockpit / desktop / agents can render the
            # current policy mode without a second round-trip.
            try:
                from vaner.cli.commands.deep_run import _session_to_dict
                from vaner.server import astatus_deep_run

                deep_run_session = await astatus_deep_run(active_repo_root)
                payload["deep_run"] = {
                    "active": deep_run_session is not None,
                    "session": _session_to_dict(deep_run_session) if deep_run_session else None,
                }
            except Exception:
                payload["deep_run"] = {"active": False, "session": None}
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

            # 0.8.5 WS4: a fresh adopted-package handoff on disk short-circuits
            # resolution — the user already picked a prepared prediction on
            # the desktop (or equivalent UI) and we should return that
            # resolution verbatim rather than spending model budget on a
            # fresh resolve. Consume (read+delete) so subsequent turns don't
            # keep reusing the same package.
            try:
                from vaner.integrations.injection.handoff import consume_handoff

                ttl = int(config.integrations.context_injection.ttl_seconds)
                handoff = consume_handoff(ttl_seconds=ttl)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("handoff probe skipped: %s", exc)
                handoff = None
            if handoff is not None and handoff.intent is not None:
                logger.info(
                    "vaner.resolve suppressed by adopt handoff: pred=%s age=%.1fs",
                    handoff.adopted_from_prediction_id,
                    handoff.age_seconds,
                )
                await metrics_store.increment_counter("resolves_total")
                await metrics_store.increment_counter("tool_call_redundancy_suppressed")
                append_log(
                    repo_root,
                    tool=name,
                    label=(handoff.intent or "")[:60],
                    decision_id=None,
                    provenance_mode="handoff_hit",
                    memory_state=None,
                )
                await _record("ok", tool_name="vaner.resolve.handoff")
                payload = dict(handoff.raw)
                payload.setdefault("provenance", {})
                if isinstance(payload["provenance"], dict):
                    payload["provenance"].setdefault("mode", "handoff_hit")
                    payload["provenance"].setdefault("cache", "warm")
                    payload["provenance"].setdefault("freshness", "fresh")
                payload["suppressed_reason"] = "fresh_adopted_package_handoff"
                return _json_result(payload)
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

        if name == "vaner.predictions.dashboard":
            # 0.8.5 WS5: compact card-model + text fallback.
            from vaner.integrations.capability import ClientCapabilityTier, current_tier
            from vaner.intent.prediction_card import rank_cards

            limit = int(args.get("limit") or 5)
            limit = max(1, min(20, limit))
            include_details = bool(args.get("include_details") or False)
            min_readiness = str(args.get("min_readiness") or "queued")
            allowed_readiness = {"queued", "grounding", "evidence_gathering", "drafting", "ready"}
            readiness_order = [
                "queued",
                "grounding",
                "evidence_gathering",
                "drafting",
                "ready",
            ]
            if min_readiness not in allowed_readiness:
                min_readiness = "queued"
            min_rank = readiness_order.index(min_readiness)

            # Get predictions via engine (preferred) or daemon.
            active_prompts: list[Any] = []
            try:
                if engine is not None:
                    active_prompts = list(engine.get_active_predictions())
                else:
                    body = await _daemon().get_predictions_active()
                    # body["predictions"] is already serialized; we can't re-rank
                    # without the live prompt objects, so fall through to a
                    # direct payload return.
                    dashboard_payload = {
                        "predictions": body.get("predictions", [])[:limit],
                        "fallback_text": _dashboard_fallback_text(body.get("predictions", [])[:limit]),
                        "ui_available": False,
                        "source": "daemon",
                    }
                    await _record("ok")
                    return _json_result(dashboard_payload)
            except VanerDaemonUnavailable:
                await _record("ok")
                return _json_result(
                    {
                        "predictions": [],
                        "fallback_text": "Vaner is preparing likely next steps. No predictions are ready yet.",
                        "ui_available": False,
                        "engine_unavailable": True,
                    }
                )

            # Filter + rank.
            filtered = [
                p for p in active_prompts if p.run.readiness in allowed_readiness and readiness_order.index(p.run.readiness) >= min_rank
            ]
            ranked = rank_cards(filtered)[:limit]
            cards = [_serialize_prediction_for_mcp(p, rank=i + 1) for i, p in enumerate(ranked)]
            if not include_details:
                for card in cards:
                    # Trim the heaviest fields from the payload — the iframe
                    # JS re-queries vaner.predictions.active for details when
                    # the user expands a card.
                    card.pop("description", None)

            # Tier-gated UI attachment is the MCP-Apps job (WS7). WS5 ships
            # the text fallback + the flag so clients can tell whether a UI
            # is available on this session.
            try:
                session = getattr(getattr(server, "request_context", None), "session", None)
                tier = current_tier(session) if session is not None else ClientCapabilityTier.UNKNOWN
            except Exception:  # pragma: no cover - defensive
                tier = ClientCapabilityTier.UNKNOWN
            ui_available = tier is ClientCapabilityTier.TIER_4

            apps_ui_enabled = getattr(config.mcp, "apps_ui_enabled", True)
            attach_ui = ui_available and apps_ui_enabled
            dashboard_payload = {
                "predictions": cards,
                "fallback_text": _dashboard_fallback_text(cards),
                "ui_available": attach_ui,
                "client_tier": int(tier),
                "source": "engine",
            }
            try:
                await metrics_store.increment_counter("mcp_dashboard_called")
                if attach_ui:
                    await metrics_store.increment_counter("mcp_apps_ui_attached")
            except Exception:  # pragma: no cover - defensive metrics
                pass
            await _record("ok")
            # When a Tier-4 client is connected and the UI is enabled, attach
            # a ResourceLink alongside the JSON payload so the host iframe
            # can pick up the `ui://vaner/active-predictions` bundle and
            # populate the initial card list from the TextContent.
            if attach_ui:
                try:
                    from mcp.types import ResourceLink  # type: ignore[import-not-found]

                    from vaner.mcp.apps import (
                        ACTIVE_PREDICTIONS_DESCRIPTION,
                        ACTIVE_PREDICTIONS_MIME,
                        ACTIVE_PREDICTIONS_NAME,
                        ACTIVE_PREDICTIONS_TITLE,
                        ACTIVE_PREDICTIONS_URI,
                        tool_meta,
                    )

                    link = ResourceLink(
                        type="resource_link",
                        uri=ACTIVE_PREDICTIONS_URI,  # type: ignore[arg-type]
                        name=ACTIVE_PREDICTIONS_NAME,
                        title=ACTIVE_PREDICTIONS_TITLE,
                        description=ACTIVE_PREDICTIONS_DESCRIPTION,
                        mimeType=ACTIVE_PREDICTIONS_MIME,
                        meta=tool_meta(),
                    )
                    return CallToolResult(
                        content=[link, *_make_text(json.dumps(dashboard_payload))],
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("ResourceLink attach failed, falling back: %s", exc)
            return _json_result(dashboard_payload)

        if name == "vaner.predictions.adopt":
            prediction_id_arg = str(args.get("prediction_id", "")).strip()
            adopt_source = str(args.get("source", "")).strip() or "unknown"
            if not prediction_id_arg:
                await _record("error")
                return _json_result(
                    {"code": "invalid_input", "message": "prediction_id is required"},
                    is_error=True,
                )
            # 0.8.5 WS8: track adopts coming from the MCP Apps UI separately
            # from tool-initiated adopts so we can measure UI value.
            try:
                if adopt_source == "mcp_app":
                    await metrics_store.increment_counter("mcp_apps_adopt_clicked")
                await metrics_store.increment_counter(f"mcp_adopt_source_{adopt_source}")
            except Exception:  # pragma: no cover - defensive metrics
                pass
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
                all_goals: list[dict[str, object]] = await artefact_store.list_workspace_goals(status=None, limit=200)
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
                    eligible_items: list[dict[str, object]] = await artefact_store.list_intent_artefact_items(
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
                outcome_rows: list[dict[str, object]] = await artefact_store.list_reconciliation_outcomes(
                    artefact_id=artefact_id_arg, limit=10
                )
                # 0.8.2 WS3 — surface item_state and goal_status delta
                # counts alongside each outcome's pointer. Full deltas
                # stay in the store; this summary lets users see at a
                # glance which reconciliation passes produced change.
                outcomes_payload: list[dict[str, Any]] = []
                for outcome_row in outcome_rows:
                    try:
                        item_deltas = json.loads(str(outcome_row.get("item_state_deltas_json") or "[]"))
                    except Exception:
                        item_deltas = []
                    try:
                        goal_deltas = json.loads(str(outcome_row.get("goal_status_deltas_json") or "[]"))
                    except Exception:
                        goal_deltas = []
                    outcomes_payload.append(
                        {
                            "id": outcome_row.get("id"),
                            "pass_at": outcome_row.get("pass_at"),
                            "triggering_signal_id": outcome_row.get("triggering_signal_id"),
                            "item_state_delta_count": (len(item_deltas) if isinstance(item_deltas, list) else 0),
                            "goal_status_delta_count": (len(goal_deltas) if isinstance(goal_deltas, list) else 0),
                        }
                    )
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
                artefacts_all: list[dict[str, object]] = await artefact_store.list_intent_artefacts(limit=200)
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

        # ------------------------------------------------------------------
        # 0.8.3 WS4 — Deep-Run lifecycle MCP tools.
        # ------------------------------------------------------------------
        if name.startswith("vaner.deep_run."):
            from vaner.cli.commands.deep_run import (
                _session_to_dict,
                _summary_to_dict,
            )
            from vaner.server import (
                alist_deep_run_sessions,
                aresolve_deep_run_session,
                astart_deep_run,
                astatus_deep_run,
                astop_deep_run,
            )

            if name == "vaner.deep_run.start":
                ends_at = args.get("ends_at")
                if not isinstance(ends_at, (int, float)):
                    await _record("error")
                    return _json_result(
                        {
                            "code": "invalid_input",
                            "message": "ends_at (epoch number) is required",
                        },
                        is_error=True,
                    )
                try:
                    session = await astart_deep_run(
                        active_repo_root,
                        ends_at=float(ends_at),
                        preset=str(args.get("preset", "balanced")),
                        focus=str(args.get("focus", "active_goals")),
                        horizon_bias=str(args.get("horizon_bias", "balanced")),
                        locality=str(args.get("locality", "local_preferred")),
                        cost_cap_usd=float(args.get("cost_cap_usd", 0.0)),
                        metadata=(dict(args.get("metadata") or {}) | {"caller": "mcp"}),
                    )
                except Exception as exc:
                    await _record("error")
                    return _json_result(
                        {
                            "code": "deep_run_start_failed",
                            "message": str(exc),
                        },
                        is_error=True,
                    )
                await _record("ok")
                return _json_result(_session_to_dict(session))

            if name == "vaner.deep_run.stop":
                summary = await astop_deep_run(
                    active_repo_root,
                    kill=bool(args.get("kill", False)),
                    reason=args.get("reason") if isinstance(args.get("reason"), str) else None,
                )
                await _record("ok")
                if summary is None:
                    return _json_result({"summary": None})
                return _json_result({"summary": _summary_to_dict(summary)})

            if name == "vaner.deep_run.status":
                session = await astatus_deep_run(active_repo_root)
                await _record("ok")
                return _json_result({"session": _session_to_dict(session) if session else None})

            if name == "vaner.deep_run.list":
                limit = max(1, min(200, int(args.get("limit", 20))))
                sessions = await alist_deep_run_sessions(active_repo_root, limit=limit)
                await _record("ok")
                return _json_result({"sessions": [_session_to_dict(s) for s in sessions]})

            if name == "vaner.deep_run.show":
                session_id = str(args.get("session_id", "")).strip()
                if not session_id:
                    await _record("error")
                    return _json_result(
                        {"code": "invalid_input", "message": "session_id required"},
                        is_error=True,
                    )
                session = await aresolve_deep_run_session(active_repo_root, session_id)
                if session is None:
                    await _record("error")
                    return _json_result(
                        {"code": "not_found", "message": f"session {session_id!r} not found"},
                        is_error=True,
                    )
                await _record("ok")
                return _json_result(_session_to_dict(session))

            if name == "vaner.deep_run.defaults":
                # WS9: bundle-derived seeds for the Deep-Run start dialog.
                from vaner.cli.commands.setup import (
                    _answers_from_payload,
                    _default_answers,
                    _read_policy_section,
                    _read_setup_section,
                )
                from vaner.intent.deep_run_defaults import (
                    deep_run_defaults_for,
                    defaults_to_dict,
                )
                from vaner.setup.catalog import bundle_by_id

                policy_section = _read_policy_section(active_repo_root)
                selected_bundle_id = policy_section.get("selected_bundle_id") or "hybrid_balanced"
                try:
                    bundle = bundle_by_id(str(selected_bundle_id))
                except KeyError:
                    await _record("error")
                    return _json_result(
                        {
                            "code": "unknown_bundle_id",
                            "message": f"unknown bundle id {selected_bundle_id!r}; run `vaner setup wizard`",
                        },
                        is_error=True,
                    )
                setup_section = _read_setup_section(active_repo_root)
                if setup_section:
                    try:
                        answers = _answers_from_payload(setup_section)
                    except Exception:
                        answers = _default_answers()
                else:
                    answers = _default_answers()
                defaults = deep_run_defaults_for(bundle, answers)
                await _record("ok")
                return _json_result(defaults_to_dict(defaults))

        # ------------------------------------------------------------------
        # 0.8.6 WS7 — Setup-surface MCP tools.
        # ------------------------------------------------------------------
        if name == "vaner.setup.questions":
            await _record("ok")
            return _json_result({"questions": _setup_question_schema()})

        if name == "vaner.setup.recommend":
            from vaner.setup.hardware import detect as _hw_detect
            from vaner.setup.select import select_policy_bundle as _select_bundle
            from vaner.setup.serializers import (
                AnswersValidationError,
                answers_from_payload,
                selection_to_dict,
            )

            try:
                answers = answers_from_payload(args)
            except AnswersValidationError as exc:
                await _record("error")
                return _json_result(
                    {"code": "invalid_input", "message": str(exc)},
                    is_error=True,
                )
            try:
                hardware = _hw_detect()
                selection = _select_bundle(answers, hardware)
            except Exception as exc:
                await _record("error")
                return _json_result(
                    {"code": "recommend_failed", "message": str(exc)},
                    is_error=True,
                )
            await _record("ok")
            return _json_result(selection_to_dict(selection))

        if name == "vaner.setup.apply":
            await _record("ok")
            return _setup_apply_handler(active_repo_root, args)

        if name == "vaner.setup.status":
            await _record("ok")
            return _setup_status_handler(active_repo_root)

        if name == "vaner.policy.show":
            await _record("ok")
            return _policy_show_handler(active_repo_root)

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
