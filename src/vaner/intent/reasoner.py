# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import dataclass, field

from vaner.intent.adapter import ReasonerContext


@dataclass(slots=True)
class Hypothesis:
    question: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    relevant_keys: list[str] = field(default_factory=list)
    category: str = "understanding"
    response_format: str = "explanation"
    follow_ups: list[str] = field(default_factory=list)


@dataclass
class PredictionScenario:
    question: str
    unit_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    rationale: str = ""
    depth: int = 0

    # Backward-compat alias so callers using .file_paths still work
    @property
    def file_paths(self) -> list[str]:
        return self.unit_ids


class CorpusReasoner:
    def __init__(self) -> None:
        pass

    async def generate(
        self,
        *,
        context: ReasonerContext,
        llm_output: str | None = None,
        fallback_items: list[str] | None = None,
        existing_questions: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Hypothesis]:
        existing = {question.strip().lower() for question in (existing_questions or []) if question.strip()}
        if llm_output:
            parsed = self._try_parse_json_list(llm_output)
            if parsed:
                hypotheses: list[Hypothesis] = []
                for item in parsed:
                    question = str(item.get("question", "What should I work on next?"))
                    if question.strip().lower() in existing:
                        continue
                    hypotheses.append(
                        Hypothesis(
                            question=question,
                            confidence=_safe_float(item.get("confidence", 0.5), default=0.5),
                            evidence=_as_str_list(item.get("evidence", [])),
                            relevant_keys=_as_str_list(item.get("relevant_keys", [])),
                            category=str(item.get("category", "understanding")),
                            response_format=str(item.get("response_format", "explanation")),
                            follow_ups=_as_str_list(item.get("follow_ups", [])),
                        )
                    )
                if hypotheses:
                    if limit is not None:
                        return hypotheses[:limit]
                    return hypotheses

        fallback_items = fallback_items or []
        questions = [
            "What is the intent behind the most recent changes?",
            "Which files should be modified next to finish the current task?",
            "Are there missing tests for the changed logic?",
        ]
        hypotheses = []
        for idx, question in enumerate(questions):
            if question.strip().lower() in existing:
                continue
            hypotheses.append(
                Hypothesis(
                    question=question,
                    confidence=max(0.2, 0.7 - (idx * 0.15)),
                    evidence=[context.summary[:300]],
                    relevant_keys=fallback_items[:4],
                    category="implementation" if idx == 1 else "understanding",
                    response_format="explanation",
                    follow_ups=["What edge cases are still uncovered?"] if idx == 1 else [],
                )
            )
            if limit is not None and len(hypotheses) >= limit:
                break
        return hypotheses

    async def generate_scenarios(
        self,
        *,
        llm_output: str | None,
        available_paths: list[str],
        covered_paths: set[str],
        limit: int = 5,
    ) -> list[PredictionScenario]:
        parsed = self._try_parse_json_list(llm_output or "")
        normalized_available = {path.strip() for path in available_paths if path.strip()}
        scenarios: list[PredictionScenario] = []

        if parsed:
            for item in parsed:
                question = str(item.get("question", "")).strip()
                if not question:
                    continue
                file_paths = [path for path in _as_str_list(item.get("file_paths", [])) if path in normalized_available]
                if not file_paths:
                    continue
                scenarios.append(
                    PredictionScenario(
                        question=question,
                        unit_ids=file_paths[:8],
                        confidence=_safe_float(item.get("confidence", 0.5), default=0.5),
                        rationale=str(item.get("rationale", "")),
                    )
                )
                if len(scenarios) >= limit:
                    break

        if scenarios:
            return scenarios[:limit]

        uncovered = [path for path in available_paths if path not in covered_paths]
        if not uncovered:
            uncovered = available_paths
        fallback: list[PredictionScenario] = []
        for path in uncovered[:limit]:
            fallback.append(
                PredictionScenario(
                    question=f"Explain implementation details in {path}",
                    unit_ids=[path],
                    confidence=0.35,
                    rationale="fallback_uncovered_path",
                )
            )
        return fallback

    @staticmethod
    def _try_parse_json_list(llm_output: str) -> list[dict[str, object]] | None:
        text = llm_output.strip()
        if not text:
            return None
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end >= start:
            text = text[start : end + 1]
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(obj, list):
            return [item for item in obj if isinstance(item, dict)]
        return None


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _safe_float(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
