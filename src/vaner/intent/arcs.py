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


@dataclass(frozen=True, slots=True)
class ArcPredictionDescription:
    """UX-shaped counterpart to ArcPrediction.

    `label` is a short imperative/phrase for display; `hypothesis_type` and
    `specificity` inform how the UI renders the row.
    """

    category: str
    confidence: float
    label: str
    description: str
    hypothesis_type: str  # likely_next | possible_branch | long_tail
    specificity: str  # concrete | category | anchor
    anchor: str


_CATEGORY_VERB: dict[str, str] = {
    "review": "Review",
    "planning": "Plan",
    "testing": "Write tests for",
    "debugging": "Debug",
    "implementation": "Implement",
    "cleanup": "Clean up",
    "understanding": "Understand",
}

# Verbs that commonly lead an imperative user prompt — stripped from the raw
# query when extracting a noun phrase for label synthesis, so "add tests for
# parser" produces "tests for parser" (not "add tests for parser").
_LEADING_VERBS: frozenset[str] = frozenset(
    {
        "add",
        "audit",
        "build",
        "check",
        "create",
        "debug",
        "explain",
        "fix",
        "help",
        "implement",
        "investigate",
        "make",
        "plan",
        "refactor",
        "remove",
        "rename",
        "review",
        "run",
        "show",
        "test",
        "update",
        "write",
    }
)

# Phase names returned by summarize_workflow_phase — used to disambiguate
# "concrete" anchors (a file or symbol) from "anchor" ones (a phase label).
_PHASE_ANCHORS: frozenset[str] = frozenset({"exploring", "discovery", "planning", "building", "validation", "stabilizing"})


def _hypothesis_type_for(confidence: float) -> str:
    if confidence >= 0.6:
        return "likely_next"
    if confidence >= 0.3:
        return "possible_branch"
    return "long_tail"


def _anchor_names_file_or_symbol(anchor: str) -> bool:
    """True when the anchor looks like a path/extension/symbol rather than a
    free-form phase label."""
    if not anchor:
        return False
    if "/" in anchor or "::" in anchor:
        return True
    for ext in (".py", ".ts", ".tsx", ".go", ".rs", ".js", ".md", ".java", ".cpp", ".c", ".rb", ".swift"):
        if ext in anchor:
            return True
    return False


def _specificity_for(anchor: str | None, macro: str | None) -> str:
    """Classify how specific a predicted prompt is.

    - ``concrete``: the anchor names a file/symbol, or the macro picks out a
      recognisable noun phrase. The UI should render the raw prompt context.
    - ``anchor``: the anchor is a phase label (e.g. "validation") — useful
      but not pointing at code.
    - ``category``: only the category is available.
    """
    if anchor and _anchor_names_file_or_symbol(anchor):
        return "concrete"
    if macro and macro != "general":
        return "concrete"
    if anchor and anchor not in _PHASE_ANCHORS:
        return "anchor"
    return "category"


def _noun_phrase_from_query(query: str, *, max_len: int = 40) -> str:
    """Extract a short noun phrase from a user query, preserving word order.

    Drops politeness prefixes, leading verbs, and articles. Returns an empty
    string for non-useful inputs. This is deliberately different from
    :func:`derive_prompt_macro` — the macro is a bag-of-tokens identity key,
    while this is a natural-reading snippet for UI labels.
    """
    if not query:
        return ""
    q = query.strip().rstrip(".?!:,; ")
    lowered = q.lower()
    for prefix in ("please ", "can you ", "could you ", "i want to ", "i need to "):
        if lowered.startswith(prefix):
            q = q[len(prefix) :]
            lowered = q.lower()
            break
    tokens = q.split()
    while tokens and tokens[0].lower() in _LEADING_VERBS:
        tokens.pop(0)
    while tokens and tokens[0].lower() in {"a", "an", "the", "some", "that"}:
        tokens.pop(0)
    phrase = " ".join(tokens).strip()
    if len(phrase) > max_len:
        phrase = phrase[:max_len].rstrip() + "…"
    return phrase


def _label_for(
    category: str,
    macro: str | None,
    hypothesis_type: str,
    *,
    specificity: str = "category",
    recent_query: str | None = None,
) -> str:
    """Synthesise a short imperative label for a predicted next prompt.

    When ``specificity == "concrete"`` and a recent query is available, the
    label echoes the user's own phrasing (via :func:`_noun_phrase_from_query`)
    so the UI shows a natural-reading continuation rather than the
    bag-of-tokens macro. Falls back to a macro-/category-derived label for
    less specific tiers.
    """
    verb = _CATEGORY_VERB.get(category, category.capitalize())
    if specificity == "concrete" and recent_query:
        noun = _noun_phrase_from_query(recent_query)
        if noun:
            if hypothesis_type == "likely_next":
                return f"{verb}: {noun}"
            if hypothesis_type == "possible_branch":
                return f"Maybe {verb.lower()}: {noun}"
            return f"Might follow: {verb.lower()} {noun}"
    macro_phrase = macro if macro and macro != "general" else ""
    if hypothesis_type == "likely_next" and macro_phrase:
        return f"{verb} {macro_phrase}".strip()
    if hypothesis_type == "likely_next":
        return f"{verb} next"
    if hypothesis_type == "possible_branch":
        return f"Maybe: {verb.lower()} {macro_phrase}".strip() if macro_phrase else f"Maybe: {verb.lower()}"
    return f"Long-tail: {verb.lower()}"


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
    def __init__(
        self,
        *,
        transition_priors: dict[str, dict[str, float]] | None = None,
        phase_affinity_priors: dict[str, dict[str, float]] | None = None,
        community_priors: dict[str, dict[str, float]] | None = None,
        laplace_alpha: float = 0.1,
        prior_weight: float = 50.0,
        community_blend_weight: float = 0.2,
    ) -> None:
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
        self._phase_affinity: dict[str, dict[str, float]] = {}
        self._laplace_alpha = max(0.0, float(laplace_alpha))
        self._prior_weight = max(1.0, float(prior_weight))
        self._community_blend_weight = max(0.0, min(1.0, float(community_blend_weight)))
        self._community_transitions: dict[str, dict[str, float]] = {}
        if transition_priors:
            self._seed_transition_priors(transition_priors)
        if phase_affinity_priors:
            self._seed_phase_affinity(phase_affinity_priors)
        if community_priors:
            self._community_transitions = {str(k): dict(v) for k, v in community_priors.items()}

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

    def describe_next(
        self,
        category: str,
        top_k: int = 3,
        *,
        recent_queries: list[str] | None = None,
    ) -> list[ArcPredictionDescription]:
        """UX-shaped view of rank_next: each category prediction gains a label,
        hypothesis_type, specificity, and description. Consumed by Phase A's
        prediction registry and Phase D's desktop pane.

        Label synthesis echoes the user's raw prompt (via
        :func:`_noun_phrase_from_query`) when ``specificity=="concrete"``, so
        the UI shows a natural-reading row instead of a bag-of-tokens macro.
        """
        ranked = self.rank_next(category, top_k=top_k, recent_queries=recent_queries)
        if not ranked:
            return []
        macro: str | None
        recent_query: str | None = None
        if recent_queries:
            recent_query = recent_queries[-1]
            macro = derive_prompt_macro(recent_query)
        else:
            macro = self._last_macro
        phase_summary = self.summarize_workflow_phase(recent_queries or [])
        out: list[ArcPredictionDescription] = []
        for prediction in ranked:
            hypothesis = _hypothesis_type_for(prediction.confidence)
            anchor = macro if macro and macro != "general" else phase_summary.phase
            specificity = _specificity_for(anchor, macro)
            label = _label_for(
                prediction.category,
                macro,
                hypothesis,
                specificity=specificity,
                recent_query=recent_query,
            )
            description = (
                f"Predicted next step: {prediction.category} after {phase_summary.phase}; "
                f"confidence {prediction.confidence:.2f}. Reason: {prediction.reason or 'rank_next'}."
            )
            out.append(
                ArcPredictionDescription(
                    category=prediction.category,
                    confidence=prediction.confidence,
                    label=label,
                    description=description,
                    hypothesis_type=hypothesis,
                    specificity=specificity,
                    anchor=anchor or category,
                )
            )
        return out

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

        community_scale = self._community_blend_weight if self._community_transitions else 0.0
        local_scale = 1.0 - community_scale

        add_normalized(self._transitions.get(category, Counter()), 0.55 * local_scale, f"category:{category}")

        macro = None
        if recent_queries:
            macro = derive_prompt_macro(recent_queries[-1]) if recent_queries else None
        elif self._last_macro is not None:
            macro = self._last_macro
        if macro is not None:
            add_normalized(self._macro_to_category.get(macro, Counter()), 0.30 * local_scale, f"macro:{macro}")

        phase_summary = self.summarize_workflow_phase(recent_queries or [])
        add_normalized(self._phase_transitions.get(phase_summary.phase, Counter()), 0.15 * local_scale, f"phase:{phase_summary.phase}")

        if community_scale > 0.0 and category in self._community_transitions:
            community_counts: Counter[str] = Counter({k: int(v * 100) for k, v in self._community_transitions[category].items()})
            add_normalized(community_counts, community_scale, "community_prior")

        if not scores:
            return []
        ranked = scores.most_common(top_k)
        return [ArcPrediction(category=label, confidence=score, reason=", ".join(reasons.get(label, []))) for label, score in ranked]

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
        if self._phase_affinity:
            recent = categories[-4:]
            category_counts = Counter(recent)
            phase_scores: dict[str, float] = {}
            for phase, affinity in self._phase_affinity.items():
                score = 0.0
                for category, count in category_counts.items():
                    score += float(affinity.get(category, 0.0)) * float(count)
                phase_scores[phase] = score
            if phase_scores:
                return max(phase_scores.items(), key=lambda item: item[1])[0]
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

    def _seed_transition_priors(self, transitions: dict[str, dict[str, float]]) -> None:
        categories = sorted(
            {str(from_cat) for from_cat in transitions.keys()}
            | {str(to_cat) for mapping in transitions.values() if isinstance(mapping, dict) for to_cat in mapping.keys()}
        )
        if not categories:
            return
        for from_cat in categories:
            row = transitions.get(from_cat, {})
            if not isinstance(row, dict):
                continue
            for to_cat in categories:
                raw_prob = float(row.get(to_cat, 0.0))
                smoothed = max(0.0, raw_prob) + self._laplace_alpha
                pseudo_count = smoothed * self._prior_weight
                self._transitions[from_cat][to_cat] += pseudo_count
                self._phase_transitions[from_cat][to_cat] += pseudo_count
                macro_key = f"prior:{from_cat}"
                self._macro_to_category[macro_key][to_cat] += pseudo_count
                self._macro_categories[macro_key][to_cat] += pseudo_count
                self._macro_counts[macro_key] += pseudo_count
                self._macro_examples.setdefault(macro_key, f"follow {from_cat} with {to_cat}")

    def _seed_phase_affinity(self, phase_affinity: dict[str, dict[str, float]]) -> None:
        cleaned: dict[str, dict[str, float]] = {}
        for phase, weights in phase_affinity.items():
            if not isinstance(weights, dict):
                continue
            normalized: dict[str, float] = {}
            total = 0.0
            for category, value in weights.items():
                prob = max(0.0, float(value))
                normalized[str(category)] = prob
                total += prob
            if total > 0.0:
                for category in list(normalized.keys()):
                    normalized[category] = normalized[category] / total
            cleaned[str(phase)] = normalized
        self._phase_affinity = cleaned
