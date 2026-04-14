# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from vaner import api


class BenchmarkCase(BaseModel):
    case_id: str
    question: str
    expected_paths: list[str] = Field(default_factory=list)
    expected_points: list[str] = Field(default_factory=list)
    question_type: str = "understanding"


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


class EvalReport(BaseModel):
    run_id: str
    repo_root: str
    cases_path: str
    results_path: str
    overall_hit_at_1: float
    overall_hit_at_3: float
    overall_path_coverage: float
    overall_point_coverage: float
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
    resolved_root = _resolve_repo_root(repo_root)
    resolved_cases_path = (cases_path or _default_cases_path(resolved_root)).resolve()
    resolved_output_dir = (output_dir or _default_output_dir(resolved_root)).resolve()
    cases = load_cases(resolved_root, resolved_cases_path)

    api.prepare(resolved_root)
    results: list[EvalCaseResult] = []
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    for case in cases:
        package = api.query(case.question, resolved_root)
        selected_paths = [selection.source_path for selection in package.selections]
        matched_paths, hit_at_1, hit_at_3, path_coverage, point_coverage = _score_case(
            case, selected_paths, package.injected_context
        )
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
            )
        )

    overall_hit_at_1 = sum(1.0 for result in results if result.hit_at_1) / max(1, len(results))
    overall_hit_at_3 = sum(1.0 for result in results if result.hit_at_3) / max(1, len(results))
    overall_path_coverage = sum(result.path_coverage for result in results) / max(1, len(results))
    overall_point_coverage = sum(result.point_coverage for result in results) / max(1, len(results))

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
        cases=results,
    )
    results_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def evaluate_repo(repo_root: Path | str) -> EvalReport:
    """Backward compatible entrypoint used by existing tests/CLI."""
    return run_eval(repo_root)
