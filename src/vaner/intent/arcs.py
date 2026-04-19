# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "should",
    "that",
    "the",
    "this",
    "to",
    "us",
    "we",
    "with",
    "you",
}


@dataclass(frozen=True, slots=True)
class ArcObservation:
    query_text: str
    category: str
    prompt_macro: str
    workflow_phase: str
    dominant_category: str
    recent_categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HabitTransition:
    previous_category: str
    category: str
    previous_macro: str
    prompt_macro: str
    workflow_phase: str


@dataclass(frozen=True, slots=True)
class WorkflowPhaseSummary:
    phase: str
    dominant_category: str
    recent_categories: tuple[str, ...]
    recent_macro: str


@dataclass(frozen=True, slots=True)
class ArcPrediction:
    category: str
    confidence: float
    reason: str


def _tokenize(text: str) -> list[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [token for token in normalized.split() if token]


def derive_prompt_macro(query: str, *, max_terms: int = 6) -> str:
    tokens = _tokenize(query)
    if not tokens:
        return "general"
    kept: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if len(token) < 3 or token in _STOPWORDS:
            continue
        if token in seen:
            continue
        kept.append(token)
        seen.add(token)
        if len(kept) >= max_terms:
            break
    return " ".join(kept) if kept else "general"


def classify_query_category(query: str) -> str:
    q = query.lower()
    if any(term in q for term in ("review", "audit", "critique", "comments", "feedback")):
        return "review"
    if any(term in q for term in ("plan", "roadmap", "strategy", "design", "architecture")):
        return "planning"
    if any(term in q for term in ("test", "assert", "coverage", "ci", "spec")):
        return "testing"
    if any(term in q for term in ("error", "bug", "traceback", "exception", "fail", "fix")):
        return "debugging"
    if any(term in q for term in ("implement", "add", "create", "build", "write")):
        return "implementation"
    if any(term in q for term in ("refactor", "cleanup", "rename", "simplify")):
        return "cleanup"
    return "understanding"


class ConversationArcModel:
    def __init__(self) -> None:
        self._transitions: dict[str, Counter[str]] = defaultdict(Counter)
        self._macro_to_category: dict[str, Counter[str]] = defaultdict(Counter)
        self._macro_transitions: dict[str, Counter[str]] = defaultdict(Counter)
        self._phase_transitions: dict[str, Counter[str]] = defaultdict(Counter)
        self._macro_counts: Counter[str] = Counter()
        self._macro_examples: dict[str, str] = {}
        self._macro_categories: dict[str, Counter[str]] = defaultdict(Counter)
        self._last_category: str | None = None
        self._last_macro: str | None = None
        self._last_transition: HabitTransition | None = None
        self._recent_categories: deque[str] = deque(maxlen=6)

    def observe(self, query_text: str) -> str:
        return self.observe_detail(query_text).category

    def observe_detail(self, query_text: str) -> ArcObservation:
        category = classify_query_category(query_text)
        prompt_macro = derive_prompt_macro(query_text)
        previous_phase = self._infer_phase(tuple(self._recent_categories))
        previous_category = self._last_category
        previous_macro = self._last_macro

        if previous_category is not None:
            self._transitions[previous_category][category] += 1
        if previous_macro is not None:
            self._macro_transitions[previous_macro][prompt_macro] += 1
            self._macro_to_category[previous_macro][category] += 1
        if self._recent_categories:
            self._phase_transitions[previous_phase][category] += 1

        self._macro_counts[prompt_macro] += 1
        self._macro_categories[prompt_macro][category] += 1
        self._macro_examples.setdefault(prompt_macro, query_text)

        self._recent_categories.append(category)
        self._last_category = category
        self._last_macro = prompt_macro
        phase_summary = self.summarize_workflow_phase_from_categories(tuple(self._recent_categories))

        self._last_transition = None
        if previous_category is not None and previous_macro is not None:
            self._last_transition = HabitTransition(
                previous_category=previous_category,
                category=category,
                previous_macro=previous_macro,
                prompt_macro=prompt_macro,
                workflow_phase=phase_summary.phase,
            )

        return ArcObservation(
            query_text=query_text,
            category=category,
            prompt_macro=prompt_macro,
            workflow_phase=phase_summary.phase,
            dominant_category=phase_summary.dominant_category,
            recent_categories=phase_summary.recent_categories,
        )

    def predict_next(
        self,
        category: str,
        top_k: int = 3,
        *,
        recent_queries: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        predictions = self.rank_next(category, top_k=top_k, recent_queries=recent_queries)
        return [(item.category, item.confidence) for item in predictions]

    def rank_next(
        self,
        category: str,
        top_k: int = 3,
        *,
        recent_queries: list[str] | None = None,
    ) -> list[ArcPrediction]:
        scores: Counter[str] = Counter()
        reasons: dict[str, list[str]] = defaultdict(list)

        def add_normalized(counter: Counter[str], weight: float, reason: str) -> None:
            total = sum(counter.values())
            if total <= 0:
                return
            for label, count in counter.items():
                scores[label] += weight * (count / total)
                reasons[label].append(reason)

        add_normalized(self._transitions.get(category, Counter()), 0.55, f"category:{category}")

        macro = None
        if recent_queries:
            macro = derive_prompt_macro(recent_queries[-1]) if recent_queries else None
        elif self._last_macro is not None:
            macro = self._last_macro
        if macro is not None:
            add_normalized(self._macro_to_category.get(macro, Counter()), 0.30, f"macro:{macro}")

        phase_summary = self.summarize_workflow_phase(recent_queries or [])
        add_normalized(self._phase_transitions.get(phase_summary.phase, Counter()), 0.15, f"phase:{phase_summary.phase}")

        if not scores:
            return []
        ranked = scores.most_common(top_k)
        return [
            ArcPrediction(category=label, confidence=score, reason=", ".join(reasons.get(label, [])))
            for label, score in ranked
        ]

    def rebuild_from_history(self, queries: list[str]) -> None:
        self._transitions.clear()
        self._macro_to_category.clear()
        self._macro_transitions.clear()
        self._phase_transitions.clear()
        self._macro_counts.clear()
        self._macro_examples.clear()
        self._macro_categories.clear()
        self._last_category = None
        self._last_macro = None
        self._last_transition = None
        self._recent_categories.clear()
        for query in queries:
            self.observe_detail(query)

    def detect_session_phase(self, recent_queries: list[str]) -> str:
        return self.summarize_workflow_phase(recent_queries).dominant_category

    def summarize_workflow_phase(self, recent_queries: list[str]) -> WorkflowPhaseSummary:
        if recent_queries:
            categories = tuple(classify_query_category(query) for query in recent_queries[-5:])
            recent_macro = derive_prompt_macro(recent_queries[-1])
        else:
            categories = tuple(self._recent_categories)
            recent_macro = self._last_macro or "general"
        return self.summarize_workflow_phase_from_categories(categories, recent_macro=recent_macro)

    def summarize_workflow_phase_from_categories(
        self,
        categories: tuple[str, ...],
        *,
        recent_macro: str | None = None,
    ) -> WorkflowPhaseSummary:
        if not categories:
            return WorkflowPhaseSummary(
                phase="exploring",
                dominant_category="understanding",
                recent_categories=(),
                recent_macro=recent_macro or self._last_macro or "general",
            )
        dominant = self._dominant_category(categories)
        phase = self._infer_phase(categories)
        return WorkflowPhaseSummary(
            phase=phase,
            dominant_category=dominant,
            recent_categories=categories[-5:],
            recent_macro=recent_macro or self._last_macro or "general",
        )

    def mine_prompt_macros(self, *, min_support: int = 2, top_k: int = 20) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for macro, count in self._macro_counts.most_common():
            if count < min_support or macro == "general":
                continue
            categories = self._macro_categories.get(macro, Counter())
            top_category, top_count = categories.most_common(1)[0] if categories else ("understanding", count)
            rows.append(
                {
                    "macro_key": macro,
                    "example_query": self._macro_examples.get(macro, macro),
                    "category": top_category,
                    "use_count": count,
                    "confidence": top_count / max(1, count),
                }
            )
            if len(rows) >= top_k:
                break
        return rows

    def export_habit_transitions(self, *, min_count: int = 1, top_k: int = 200) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for previous_category, next_categories in self._transitions.items():
            for category, count in next_categories.items():
                if count < min_count:
                    continue
                rows.append(
                    {
                        "previous_category": previous_category,
                        "category": category,
                        "previous_macro": "",
                        "prompt_macro": "",
                        "transition_count": count,
                    }
                )
        rows.sort(key=lambda item: int(item["transition_count"]), reverse=True)
        return rows[:top_k]

    @property
    def last_transition(self) -> HabitTransition | None:
        return self._last_transition

    @property
    def last_macro(self) -> str | None:
        return self._last_macro

    def _dominant_category(self, categories: tuple[str, ...]) -> str:
        counts = Counter(categories)
        if not counts:
            return "understanding"
        return counts.most_common(1)[0][0]

    def _infer_phase(self, categories: tuple[str, ...]) -> str:
        if not categories:
            return "exploring"
        recent = categories[-4:]
        counts = Counter(recent)
        if counts["testing"] + counts["review"] >= 2:
            return "validation"
        if counts["debugging"] >= 1 and (counts["testing"] >= 1 or counts["implementation"] >= 1):
            return "stabilizing"
        if counts["implementation"] + counts["cleanup"] >= 2:
            return "building"
        if counts["planning"] >= 1:
            return "planning"
        if counts["understanding"] >= 2:
            return "discovery"
        return recent[-1]

    def _dominant_category_for_macro(self, macro: str) -> str:
        categories = self._macro_categories.get(macro, Counter())
        if not categories:
            return "understanding"
        return categories.most_common(1)[0][0]

    def _previous_macro(self, current_macro: str) -> str | None:
        if not self._macro_transitions:
            return None
        for previous_macro, counter in self._macro_transitions.items():
            if current_macro in counter:
                return previous_macro
        return None
