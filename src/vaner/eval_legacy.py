# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from vaner.engine import build_default_engine
from vaner.intent.arcs import classify_query_category


class BenchmarkCase(BaseModel):
    case_id: str
    question: str
    expected_paths: list[str] = Field(default_factory=list)
    expected_points: list[str] = Field(default_factory=list)
    question_type: str = "understanding"
    expected_category: str | None = None
    expected_predict_keys: list[str] = Field(default_factory=list)
    expected_cache_tier: str | None = None


class EvalCaseResult(BaseModel):
    case_id: str
    question_type: str
    selected_paths: list[str]
    expected_paths: list[str]
    matched_paths: list[str]
    hit_at_1: bool
    hit_at_3: bool
    path_coverage: float
    point_coverage: float
    token_used: int
    token_budget: int
    elapsed_seconds: float
    predict_overlap: float | None = None
    category_correct: bool | None = None
    observed_cache_tier: str | None = None
    expected_cache_tier: str | None = None
    second_query_speedup: float | None = None


class EvalReport(BaseModel):
    run_id: str
    repo_root: str
    cases_path: str
    results_path: str
    overall_hit_at_1: float
    overall_hit_at_3: float
    overall_path_coverage: float
    overall_point_coverage: float
    overall_predict_overlap: float = 0.0
    overall_category_accuracy: float = 0.0
    cache_tier_distribution: dict[str, float] = Field(default_factory=dict)
    overall_second_query_speedup: float = 0.0
    total_elapsed_seconds: float
    prepare_elapsed_seconds: float
    cases: list[EvalCaseResult]


DEFAULT_CASES_DATA = [
    {
        "case_id": "daemon_flow",
        "question": "Explain how the daemon prepares artefacts from repository changes.",
        "expected_paths": ["src/vaner/daemon/runner.py", "src/vaner/daemon/engine/planner.py"],
        "expected_points": ["run_once", "plan_targets", "score_paths", "upsert_working_set"],
        "question_type": "mechanism",
    },
    {
        "case_id": "broker_selection",
        "question": "How does Vaner select and compress context artefacts at query time?",
        "expected_paths": [
            "src/vaner/broker/selector.py",
            "src/vaner/broker/compressor.py",
            "src/vaner/broker/assembler.py",
        ],
        "expected_points": ["select_artefacts", "compress_context", "token_budget", "keyword_overlap"],
        "question_type": "understanding",
    },
    {
        "case_id": "proxy_enrichment",
        "question": "How does the OpenAI-compatible proxy enrich chat completions with repository context?",
        "expected_paths": ["src/vaner/router/proxy.py", "src/vaner/api.py", "src/vaner/router/backends.py"],
        "expected_points": ["chat/completions", "injected_context", "forward_chat_completion", "stream_chat_completion"],
        "question_type": "wiring",
    },
]


def _resolve_repo_root(path: Path | str) -> Path:
    return path.resolve() if isinstance(path, Path) else Path(path).resolve()


def _default_cases_path(repo_root: Path) -> Path:
    return repo_root / "eval" / "cases" / "default.json"


def _default_output_dir(repo_root: Path) -> Path:
    return repo_root / "eval" / "runs"


def load_cases(repo_root: Path, cases_path: Path | None = None) -> list[BenchmarkCase]:
    resolved_cases_path = (cases_path or _default_cases_path(repo_root)).resolve()
    if not resolved_cases_path.exists():
        return [BenchmarkCase(**item) for item in DEFAULT_CASES_DATA]
    raw = json.loads(resolved_cases_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Cases file must contain a JSON array: {resolved_cases_path}")
    return [BenchmarkCase(**item) for item in raw]


def _score_case(case: BenchmarkCase, selected_paths: list[str], injected_context: str) -> tuple[list[str], bool, bool, float, float]:
    matched_paths = [path for path in case.expected_paths if path in selected_paths]
    top_1 = selected_paths[:1]
    top_3 = selected_paths[:3]
    hit_at_1 = any(path in top_1 for path in case.expected_paths)
    hit_at_3 = any(path in top_3 for path in case.expected_paths)
    path_coverage = len(matched_paths) / max(1, len(case.expected_paths))

    lowered_context = injected_context.lower()
    matched_points = sum(1 for point in case.expected_points if point.lower() in lowered_context)
    point_coverage = matched_points / max(1, len(case.expected_points))
    return matched_paths, hit_at_1, hit_at_3, path_coverage, point_coverage


def run_eval(
    repo_root: Path | str,
    *,
    cases_path: Path | None = None,
    output_dir: Path | None = None,
) -> EvalReport:
    return asyncio.run(_run_eval_async(repo_root, cases_path=cases_path, output_dir=output_dir))


async def _run_eval_async(
    repo_root: Path | str,
    *,
    cases_path: Path | None = None,
    output_dir: Path | None = None,
) -> EvalReport:
    resolved_root = _resolve_repo_root(repo_root)
    resolved_cases_path = (cases_path or _default_cases_path(resolved_root)).resolve()
    resolved_output_dir = (output_dir or _default_output_dir(resolved_root)).resolve()
    cases = load_cases(resolved_root, resolved_cases_path)
    engine = build_default_engine(resolved_root)

    prepare_started_at = time.monotonic()
    await engine.prepare()
    prepare_elapsed_seconds = time.monotonic() - prepare_started_at
    results: list[EvalCaseResult] = []
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    eval_started_at = time.monotonic()
    tier_counts: dict[str, int] = {}
    predict_overlaps: list[float] = []
    category_hits: list[float] = []
    speedups: list[float] = []

    for case in cases:
        case_started_at = time.monotonic()
        package = await engine.query(case.question)
        elapsed_seconds = time.monotonic() - case_started_at
        selected_paths = [selection.source_path for selection in package.selections]
        matched_paths, hit_at_1, hit_at_3, path_coverage, point_coverage = _score_case(
            case, selected_paths, package.injected_context
        )
        predict_overlap: float | None = None
        if case.expected_predict_keys:
            predictions = await engine.predict(top_k=5)
            predicted_keys = {prediction.key for prediction in predictions}
            expected_keys = set(case.expected_predict_keys)
            predict_overlap = len(predicted_keys & expected_keys) / max(1, len(expected_keys))
            predict_overlaps.append(predict_overlap)

        category_correct: bool | None = None
        if case.expected_category:
            category_correct = classify_query_category(case.question) == case.expected_category
            category_hits.append(1.0 if category_correct else 0.0)

        feedback_rows = await engine.store.list_feedback_events(limit=1)
        observed_cache_tier = str(feedback_rows[0]["cache_tier"]) if feedback_rows else None
        if observed_cache_tier:
            tier_counts[observed_cache_tier] = tier_counts.get(observed_cache_tier, 0) + 1

        second_query_speedup: float | None = None
        if case.expected_cache_tier:
            second_started = time.monotonic()
            await engine.query(case.question)
            second_elapsed = time.monotonic() - second_started
            if second_elapsed > 0:
                second_query_speedup = elapsed_seconds / second_elapsed
                speedups.append(second_query_speedup)
            second_feedback = await engine.store.list_feedback_events(limit=1)
            if second_feedback:
                observed_cache_tier = str(second_feedback[0]["cache_tier"])
                tier_counts[observed_cache_tier] = tier_counts.get(observed_cache_tier, 0) + 1

        results.append(
            EvalCaseResult(
                case_id=case.case_id,
                question_type=case.question_type,
                selected_paths=selected_paths,
                expected_paths=case.expected_paths,
                matched_paths=matched_paths,
                hit_at_1=hit_at_1,
                hit_at_3=hit_at_3,
                path_coverage=path_coverage,
                point_coverage=point_coverage,
                token_used=package.token_used,
                token_budget=package.token_budget,
                elapsed_seconds=elapsed_seconds,
                predict_overlap=predict_overlap,
                category_correct=category_correct,
                observed_cache_tier=observed_cache_tier,
                expected_cache_tier=case.expected_cache_tier,
                second_query_speedup=second_query_speedup,
            )
        )

    overall_hit_at_1 = sum(1.0 for result in results if result.hit_at_1) / max(1, len(results))
    overall_hit_at_3 = sum(1.0 for result in results if result.hit_at_3) / max(1, len(results))
    overall_path_coverage = sum(result.path_coverage for result in results) / max(1, len(results))
    overall_point_coverage = sum(result.point_coverage for result in results) / max(1, len(results))
    overall_predict_overlap = sum(predict_overlaps) / max(1, len(predict_overlaps))
    overall_category_accuracy = sum(category_hits) / max(1, len(category_hits))
    overall_second_query_speedup = sum(speedups) / max(1, len(speedups))
    total_tier_events = sum(tier_counts.values())
    cache_tier_distribution = {
        tier: count / max(1, total_tier_events)
        for tier, count in sorted(tier_counts.items())
    }
    total_elapsed_seconds = time.monotonic() - eval_started_at

    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    results_path = resolved_output_dir / f"{run_id}.json"
    report = EvalReport(
        run_id=run_id,
        repo_root=str(resolved_root),
        cases_path=str(resolved_cases_path),
        results_path=str(results_path),
        overall_hit_at_1=overall_hit_at_1,
        overall_hit_at_3=overall_hit_at_3,
        overall_path_coverage=overall_path_coverage,
        overall_point_coverage=overall_point_coverage,
        overall_predict_overlap=overall_predict_overlap,
        overall_category_accuracy=overall_category_accuracy,
        cache_tier_distribution=cache_tier_distribution,
        overall_second_query_speedup=overall_second_query_speedup,
        total_elapsed_seconds=total_elapsed_seconds,
        prepare_elapsed_seconds=prepare_elapsed_seconds,
        cases=results,
    )
    results_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def evaluate_repo(repo_root: Path | str) -> EvalReport:
    """Backward compatible entrypoint used by existing tests/CLI."""
    return run_eval(repo_root)
