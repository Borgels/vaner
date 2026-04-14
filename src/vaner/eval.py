# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from vaner import api


class EvalCase(BaseModel):
    name: str
    prompt: str
    expected_paths: list[str] = Field(default_factory=list)


class EvalCaseResult(BaseModel):
    name: str
    selected_paths: list[str]
    expected_paths: list[str]
    matched_paths: list[str]
    token_used: int
    token_budget: int
    score: float


class EvalReport(BaseModel):
    repo_root: str
    overall_score: float
    cases: list[EvalCaseResult]


DEFAULT_EVAL_CASES = [
    EvalCase(
        name="daemon_flow",
        prompt="Explain how the daemon prepares artefacts.",
        expected_paths=["src/vaner/daemon/runner.py"],
    ),
    EvalCase(
        name="broker_selection",
        prompt="How does Vaner select context artefacts?",
        expected_paths=["src/vaner/broker/selector.py", "src/vaner/broker/assembler.py"],
    ),
    EvalCase(
        name="proxy_enrichment",
        prompt="How does the OpenAI-compatible proxy enrich requests?",
        expected_paths=["src/vaner/router/proxy.py"],
    ),
]


def _resolve_repo_root(path: Path | str) -> Path:
    return path.resolve() if isinstance(path, Path) else Path(path).resolve()


def evaluate_repo(repo_root: Path | str) -> EvalReport:
    resolved_root = _resolve_repo_root(repo_root)
    api.prepare(resolved_root)
    results: list[EvalCaseResult] = []

    for case in DEFAULT_EVAL_CASES:
        package = api.query(case.prompt, resolved_root)
        selected_paths = [selection.source_path for selection in package.selections]
        matched_paths = [path for path in case.expected_paths if path in selected_paths]
        score = len(matched_paths) / max(1, len(case.expected_paths))
        results.append(
            EvalCaseResult(
                name=case.name,
                selected_paths=selected_paths,
                expected_paths=case.expected_paths,
                matched_paths=matched_paths,
                token_used=package.token_used,
                token_budget=package.token_budget,
                score=score,
            )
        )

    overall_score = sum(result.score for result in results) / max(1, len(results))
    return EvalReport(repo_root=str(resolved_root), overall_score=overall_score, cases=results)
