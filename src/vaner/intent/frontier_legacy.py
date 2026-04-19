# SPDX-License-Identifier: Apache-2.0
"""Exploration frontier — the admission/rejection/ordering brain of Vaner.

The ``ExplorationFrontier`` is a priority-driven queue with a full economics
layer: composite priority scoring, file-set fingerprint identity, Jaccard-based
duplicate suppression, and feedback-driven per-source multipliers.

Every scenario that wants to enter the exploration engine must pass through
this frontier.  The frontier decides whether it is worth exploring, when to
explore it, and (via feedback) adjusts its own prioritisation weights over time.
"""

from __future__ import annotations

import hashlib
import heapq
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from vaner.intent.scoring_policy import ScoringPolicy

if TYPE_CHECKING:
    from vaner.intent.arcs import ConversationArcModel
    from vaner.intent.graph import RelationshipGraph

# ---------------------------------------------------------------------------
# Category keywords — mirrored from engine.py to avoid a circular import
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "review": ["review", "audit", "comment", "feedback", "lint"],
    "planning": ["plan", "roadmap", "design", "architecture", "proposal"],
    "testing": ["test", "spec", "conftest", "_test", "test_", "ci"],
    "debugging": ["error", "exception", "log", "debug", "trace", "fix"],
    "implementation": ["impl", "core", "engine", "handler", "service"],
    "cleanup": ["util", "helper", "common", "base", "mixin", "refactor"],
    "understanding": ["readme", "doc", "api", "interface", "schema"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def file_set_fingerprint(file_paths: list[str]) -> str:
    """SHA1 of sorted, deduplicated file paths — the scenario identity key."""
    normalized = sorted(set(p.strip() for p in file_paths if p.strip()))
    return hashlib.sha1("\n".join(normalized).encode()).hexdigest()


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union > 0 else 0.0


def freshness_decay(last_access_ts: float) -> float:
    """Returns a freshness factor in [0, 1].

    Files accessed recently have near-1.0 freshness.  Files not accessed
    for more than 10 minutes decay toward 0.1 using a 5-minute half-life.
    """
    age_seconds = time.time() - last_access_ts
    if age_seconds <= 0:
        return 1.0
    return max(0.1, 2.0 ** (-age_seconds / 300.0))


def depth_discount(depth: int) -> float:
    """Priority multiplier based on LLM branching depth.

    Depth 0 = full priority (1.0).
    Depth 3 ≈ 0.53.
    Depth 10 ≈ 0.22.
    """
    return max(0.1, 1.0 / (1.0 + depth * 0.35))


def layer_bonus(layer: str) -> float:
    if layer == "strategic":
        return 1.12
    if layer == "tactical":
        return 1.05
    return 1.0


# ---------------------------------------------------------------------------
# Core data model
# ---------------------------------------------------------------------------

@dataclass
class ExplorationScenario:
    """A candidate context neighbourhood to explore."""

    id: str                       # SHA1 of sorted file_paths
    file_paths: list[str]         # the context neighbourhood
    anchor: str                   # what seeded this scenario
    source: str                   # "graph" | "arc" | "pattern" | "llm_branch"
    priority: float               # composite score (higher = explore sooner)
    depth: int = 0                # LLM hops from original seed
    parent_id: str | None = None  # which scenario spawned this one
    reason: str = ""              # why this scenario was proposed
    layer: str = "operational"   # strategic | tactical | operational

    def is_trivial(self) -> bool:
        """True for cheap, structural graph-walk scenarios that skip the LLM."""
        return self.source == "graph" and self.depth == 0


# ---------------------------------------------------------------------------
# Frontier
# ---------------------------------------------------------------------------

class ExplorationFrontier:
    """Priority-driven exploration frontier with admission control.

    The frontier unifies all scenario sources (graph, arc, pattern, LLM branch)
    into a single priority ordering.

    Admission gate enforces:
      - Already-explored fingerprint rejection
      - Exact-duplicate (same fingerprint) rejection/upgrade
      - Jaccard similarity deduplication against pending queue (>= threshold)
      - Depth cap enforcement
      - Minimum-priority threshold
      - Maximum frontier size cap

    Feedback adjusts per-source multipliers so the engine learns which signal
    source is most predictive for the current repo/user pair over time.
    """

    _SOURCE_MULTIPLIERS_INIT: dict[str, float] = {
        "graph": 1.0,
        "arc": 1.0,
        "pattern": 1.2,    # validated patterns get a slight head start
        "llm_branch": 0.9,
    }

    def __init__(
        self,
        *,
        max_depth: int = 3,
        max_size: int = 500,
        min_priority: float = 0.1,
        dedup_threshold: float = 0.7,
        saturation_coverage: float = 0.90,
        scoring_policy: ScoringPolicy | None = None,
    ) -> None:
        self.max_depth = max_depth
        self.max_size = max_size
        self.min_priority = min_priority
        self.dedup_threshold = dedup_threshold
        self.saturation_coverage = saturation_coverage
        self._scoring_policy = scoring_policy or ScoringPolicy()

        # (neg_priority, counter, scenario) heap — min-heap, so we negate priority
        self._heap: list[tuple[float, int, ExplorationScenario]] = []
        # scenario_id → latest push counter (for lazy-deletion / staleness check)
        self._entry_counter: dict[str, int] = {}
        # scenario_id → current pending scenario (source of truth)
        self._pending: dict[str, ExplorationScenario] = {}
        # File-sets of pending scenarios — used for Jaccard dedup on push
        self._pending_file_sets: list[frozenset[str]] = []
        # Explored fingerprints (never re-enter)
        self._explored: set[str] = set()
        # Per-source priority multipliers
        self._multipliers: dict[str, float] = dict(
            self._scoring_policy.source_multipliers or self._SOURCE_MULTIPLIERS_INIT
        )
        # Total available paths (updated by seeding; used for saturation check)
        self._total_available: int = 0
        # Files covered by explored scenarios
        self._covered_file_set: set[str] = set()
        # Monotonically increasing counter for heap entry tiebreaking / staleness
        self._push_counter: int = 0
        # Adaptive depth budget: starts at max_depth, can be temporarily lowered
        # for breadth-first phases; overridable via set_effective_max_depth()
        self._effective_max_depth: int = max_depth

    # -----------------------------------------------------------------------
    # Seeding
    # -----------------------------------------------------------------------

    def seed_from_graph(
        self,
        working_set: dict[str, float],
        graph: RelationshipGraph,
        available_paths: list[str],
        covered_paths: set[str],
    ) -> int:
        """Seed the frontier from structural dependency graph walks.

        For each path in the working set (sorted by recency), walk the
        relationship graph to collect the structural neighbourhood and push
        it as a depth-0 graph scenario.

        Returns the number of scenarios admitted.
        """
        available_set = set(available_paths)
        self._total_available = max(self._total_available, len(available_set))
        admitted = 0
        sorted_anchors = sorted(working_set.items(), key=lambda kv: kv[1], reverse=True)
        for i, (path, ts) in enumerate(sorted_anchors[:50]):
            file_key = f"file:{path}"
            neighbors = graph.propagate(file_key, depth=2)
            neighbor_paths = [
                key.split(":", 1)[1]
                for key in neighbors
                if key.startswith("file:") and key.split(":", 1)[1] in available_set
            ]
            file_paths = [path] + [p for p in neighbor_paths[:7] if p != path]
            file_set = frozenset(file_paths)
            recency = 1.0 / (1.0 + i * 0.1)
            uncovered_ratio = len(file_set - covered_paths) / max(1, len(file_set))
            depth_score = 1.0 if neighbor_paths else 0.5
            priority = self._score(
                source="graph",
                graph_proximity=depth_score * recency,
                arc_probability=0.0,
                coverage_gap=uncovered_ratio,
                pattern_strength=0.0,
                freshness_decay=freshness_decay(ts),
                depth=0,
            )
            scenario = ExplorationScenario(
                id=file_set_fingerprint(file_paths),
                file_paths=file_paths,
                anchor=path,
                source="graph",
                priority=priority,
                depth=0,
                reason=f"dependency neighbourhood of {path}",
                layer="operational",
            )
            if self.push(scenario):
                admitted += 1
        return admitted

    def seed_from_workflow_phase(
        self,
        arc_model: ConversationArcModel,
        recent_queries: list[str],
        available_paths: list[str],
    ) -> int:
        """Seed strategic scenarios from workflow-phase summaries."""
        self._total_available = max(self._total_available, len(available_paths))
        if not recent_queries:
            return 0
        phase_summary = arc_model.summarize_workflow_phase(recent_queries)
        if phase_summary.phase == "planning":
            strategic_categories = [("planning", 0.95), ("implementation", 0.65)]
        elif phase_summary.phase in {"validation", "stabilizing"}:
            strategic_categories = [("review", 0.9), ("testing", 0.8), ("debugging", 0.6)]
        elif phase_summary.phase in {"building", "discovery"}:
            strategic_categories = [(phase_summary.dominant_category, 0.9), ("implementation", 0.7)]
        else:
            strategic_categories = [(phase_summary.dominant_category, 0.75)]

        admitted = 0
        for category, confidence in strategic_categories[:3]:
            keywords = _CATEGORY_KEYWORDS.get(category, [])
            matched = [path for path in available_paths if any(keyword in path.lower() for keyword in keywords)][:10]
            if not matched:
                continue
            priority = self._score(
                source="arc",
                graph_proximity=0.0,
                arc_probability=confidence,
                coverage_gap=0.55,
                pattern_strength=0.15,
                freshness_decay=1.0,
                depth=0,
                layer="strategic",
            )
            scenario = ExplorationScenario(
                id=file_set_fingerprint(matched),
                file_paths=matched,
                anchor=phase_summary.phase,
                source="arc",
                priority=priority,
                depth=0,
                reason=f"workflow phase {phase_summary.phase} favors {category}",
                layer="strategic",
            )
            if self.push(scenario):
                admitted += 1
        return admitted

    def seed_from_arc(
        self,
        arc_model: ConversationArcModel,
        recent_queries: list[str],
        available_paths: list[str],
    ) -> int:
        """Seed from conversation arc model predictions.

        Predicts the next interaction category (testing, debugging, etc.) and
        selects paths that match that category's keywords.

        Returns the number of scenarios admitted.
        """
        self._total_available = max(self._total_available, len(available_paths))
        admitted = 0

        if recent_queries:
            from vaner.intent.arcs import classify_query_category

            phase_summary = arc_model.summarize_workflow_phase(recent_queries)
            phase = phase_summary.phase
            current_category = classify_query_category(recent_queries[-1])
            predictions = arc_model.rank_next(
                current_category,
                top_k=3,
                recent_queries=recent_queries,
            )
        else:
            phase = "exploring"
            predictions = []

        # Cold-start fallback: rank categories by matching path count
        if not predictions:
            ranked: list[tuple[str, float]] = []
            for cat, kws in _CATEGORY_KEYWORDS.items():
                count = sum(1 for p in available_paths if any(kw in p.lower() for kw in kws))
                if count:
                    ranked.append((cat, count / max(1, len(available_paths))))
            ranked.sort(key=lambda x: x[1], reverse=True)
            predictions = ranked[:3]

        seen_file_sets: list[frozenset[str]] = []
        for prediction in predictions[:4]:
            if isinstance(prediction, tuple):
                category, confidence = prediction
                prediction_reason = "cold_start"
            else:
                category = prediction.category
                confidence = prediction.confidence
                prediction_reason = getattr(prediction, "reason", "")
            keywords = _CATEGORY_KEYWORDS.get(category, [])
            matched = [p for p in available_paths if any(kw in p.lower() for kw in keywords)][:8]
            if not matched:
                continue
            file_set = frozenset(matched)
            if any(
                _jaccard(file_set, prev) > 0.7
                for prev in seen_file_sets
            ):
                continue
            seen_file_sets.append(file_set)
            priority = self._score(
                source="arc",
                graph_proximity=0.0,
                arc_probability=float(confidence),
                coverage_gap=0.5,
                pattern_strength=0.0,
                freshness_decay=1.0,
                depth=0,
                layer="tactical",
            )
            scenario = ExplorationScenario(
                id=file_set_fingerprint(matched),
                file_paths=matched,
                anchor=phase,
                source="arc",
                priority=priority,
                depth=0,
                reason=(
                    f"arc model: {phase} → {category} (p={confidence:.2f})"
                    + (f" [{prediction_reason}]" if prediction_reason else "")
                ),
                layer="tactical",
            )
            if self.push(scenario):
                admitted += 1
        return admitted

    def seed_from_prompt_macros(self, prompt_macros: list[dict[str, object]], available_paths: list[str]) -> int:
        """Seed from repeated prompt macros mined from query history."""
        self._total_available = max(self._total_available, len(available_paths))
        admitted = 0
        for macro in prompt_macros:
            macro_key = str(macro.get("macro_key", "")).strip()
            category = str(macro.get("category", "understanding"))
            use_count = int(macro.get("use_count", 0))
            if use_count < 2 or not macro_key:
                continue
            keywords = list(dict.fromkeys((macro_key.split() + _CATEGORY_KEYWORDS.get(category, []))[:10]))
            matched = [
                path for path in available_paths
                if any(keyword in path.lower() for keyword in keywords)
            ][:8]
            if not matched:
                fallback_keywords = _CATEGORY_KEYWORDS.get(category, [])
                matched = [path for path in available_paths if any(keyword in path.lower() for keyword in fallback_keywords)][:6]
            if not matched:
                continue
            confidence = min(1.0, float(macro.get("confidence", 0.0)) or (use_count / 10.0))
            priority = self._score(
                source="pattern",
                graph_proximity=0.0,
                arc_probability=confidence * 0.7,
                coverage_gap=0.4,
                pattern_strength=min(1.0, use_count / 8.0),
                freshness_decay=1.0,
                depth=0,
                layer="tactical",
            )
            scenario = ExplorationScenario(
                id=file_set_fingerprint(matched),
                file_paths=matched,
                anchor=macro_key,
                source="pattern",
                priority=priority,
                depth=0,
                reason=f"prompt macro '{macro_key}' ({use_count}x) in {category}",
                layer="tactical",
            )
            if self.push(scenario):
                admitted += 1
        return admitted

    def seed_from_patterns(self, validated_patterns: list[dict[str, object]]) -> int:
        """Seed from empirically validated patterns.

        Patterns are confirmed hit histories — high-confidence, no LLM needed.
        Returns the number admitted.
        """
        admitted = 0
        for pattern in validated_patterns:
            predicted_keys = pattern.get("predicted_keys", [])
            if not isinstance(predicted_keys, list):
                continue
            file_paths = [
                k.split(":", 1)[1]
                for k in predicted_keys
                if isinstance(k, str) and k.startswith("file_summary:")
            ]
            if not file_paths:
                file_paths = [k for k in predicted_keys if isinstance(k, str)]
            if not file_paths:
                continue
            confirmation_count = int(pattern.get("confirmation_count", 1))
            pattern_strength = min(1.0, confirmation_count / 10.0)
            priority = self._score(
                source="pattern",
                graph_proximity=0.0,
                arc_probability=0.0,
                coverage_gap=0.5,
                pattern_strength=pattern_strength,
                freshness_decay=1.0,
                depth=0,
                layer="tactical",
            )
            scenario = ExplorationScenario(
                id=file_set_fingerprint(file_paths),
                file_paths=file_paths,
                anchor=str(pattern.get("trigger_keywords", "")),
                source="pattern",
                priority=priority,
                depth=0,
                reason=f"validated pattern (confirmed {confirmation_count}x)",
                layer="tactical",
            )
            if self.push(scenario):
                admitted += 1
        return admitted

    # -----------------------------------------------------------------------
    # Admission gate
    # -----------------------------------------------------------------------

    def set_effective_max_depth(self, depth: int) -> None:
        """Temporarily override the effective max depth for adaptive budget allocation.

        During the breadth-first phase (early exploration), the engine calls
        this with a shallow depth (e.g. 1) to first build wide coverage. Once
        the shallow layer is saturated, it raises to the full configured depth
        for deep exploration of high-value branches.
        """
        self._effective_max_depth = depth

    def push(self, scenario: ExplorationScenario) -> bool:
        """Attempt to admit a scenario.

        Returns True if admitted (new entry), False if rejected.

        Rejection reasons (in order):
          1. Already explored
          2. Already pending with same or higher effective priority (no-op)
          3. Depth exceeds max_depth (or effective_max_depth if overridden)
          4. Effective priority below min_priority
          5. Frontier at max_size
          6. Jaccard >= dedup_threshold against an existing pending scenario
        """
        if scenario.id in self._explored:
            return False

        effective_priority = scenario.priority * self._multipliers.get(scenario.source, 1.0)

        # Upgrade existing pending entry if the new effective priority is higher
        if scenario.id in self._pending:
            existing = self._pending[scenario.id]
            if effective_priority > existing.priority:
                upgraded = ExplorationScenario(
                    id=scenario.id,
                    file_paths=scenario.file_paths,
                    anchor=scenario.anchor,
                    source=scenario.source,
                    priority=effective_priority,
                    depth=scenario.depth,
                    parent_id=scenario.parent_id,
                    reason=scenario.reason,
                    layer=scenario.layer,
                )
                self._pending[scenario.id] = upgraded
                self._push_counter += 1
                self._entry_counter[scenario.id] = self._push_counter
                heapq.heappush(
                    self._heap,
                    (-effective_priority, self._push_counter, upgraded),
                )
            return False  # was already pending

        effective_depth = getattr(self, "_effective_max_depth", self.max_depth)
        if scenario.depth > effective_depth:
            return False

        if effective_priority < self.min_priority:
            return False

        if len(self._pending) >= self.max_size:
            return False

        # Jaccard deduplication against all pending file sets
        file_set = frozenset(scenario.file_paths)
        for pending_set in self._pending_file_sets:
            if _jaccard(file_set, pending_set) >= self.dedup_threshold:
                return False

        # Admit — store with effective priority so pop() sees the real value
        admitted_scenario = ExplorationScenario(
            id=scenario.id,
            file_paths=scenario.file_paths,
            anchor=scenario.anchor,
            source=scenario.source,
            priority=effective_priority,
            depth=scenario.depth,
            parent_id=scenario.parent_id,
            reason=scenario.reason,
            layer=scenario.layer,
        )
        self._pending[scenario.id] = admitted_scenario
        self._pending_file_sets.append(file_set)
        self._push_counter += 1
        self._entry_counter[scenario.id] = self._push_counter
        heapq.heappush(
            self._heap,
            (-effective_priority, self._push_counter, admitted_scenario),
        )
        return True

    # -----------------------------------------------------------------------
    # Exploration
    # -----------------------------------------------------------------------

    def pop(self) -> ExplorationScenario | None:
        """Pop the highest-priority pending scenario, or None if empty."""
        while self._heap:
            _, counter, scenario = heapq.heappop(self._heap)
            if scenario.id in self._explored:
                continue
            if self._entry_counter.get(scenario.id) != counter:
                # Stale heap entry — a higher-priority replacement was pushed
                continue
            current = self._pending.get(scenario.id)
            if current is None:
                continue
            del self._pending[scenario.id]
            file_set = frozenset(scenario.file_paths)
            try:
                self._pending_file_sets.remove(file_set)
            except ValueError:
                pass
            return current
        return None

    def mark_explored(
        self,
        scenario_id: str,
        covered_files: list[str] | None = None,
    ) -> None:
        """Mark a scenario as explored and update coverage tracking.

        ``covered_files`` should be the actual file paths that were cached for
        this scenario (post-LLM ranking).  Pass it explicitly because ``pop()``
        already removed the scenario from ``_pending``, so the files can no
        longer be retrieved from there.
        """
        if covered_files:
            self._covered_file_set.update(covered_files)
        # Remove from pending if still there (e.g. called without a prior pop())
        scenario = self._pending.pop(scenario_id, None)
        if scenario is not None:
            self._covered_file_set.update(scenario.file_paths)
            file_set = frozenset(scenario.file_paths)
            try:
                self._pending_file_sets.remove(file_set)
            except ValueError:
                pass
        self._explored.add(scenario_id)

    def is_saturated(self) -> bool:
        """True when the frontier has nothing valuable left to explore.

        Conditions:
          - No pending scenarios, OR
          - Coverage of known file space exceeds saturation_coverage threshold
        """
        if not self._pending:
            return True
        if self._total_available > 0:
            coverage = len(self._covered_file_set) / self._total_available
            if coverage >= self.saturation_coverage:
                return True
        return False

    # -----------------------------------------------------------------------
    # Feedback
    # -----------------------------------------------------------------------

    def record_feedback(self, source: str, *, hit: bool) -> None:
        """Adjust per-source priority multiplier based on a hit or miss.

        A hit slightly boosts the source multiplier (cap 2.0).
        A miss slightly reduces it (floor 0.3).
        Adjustment is ±5% to avoid over-reacting to noise.
        """
        current = self._multipliers.get(source, 1.0)
        if hit:
            self._multipliers[source] = min(2.0, current * 1.05)
        else:
            self._multipliers[source] = max(0.3, current * 0.95)
        self._scoring_policy.source_multipliers[source] = self._multipliers[source]

    def seed_from_miss(
        self,
        miss_paths: list[str],
        available_paths: list[str],
    ) -> int:
        """Seed new scenarios from a cold-miss query context.

        When a user query results in a cold miss, the exploration engine
        clearly failed to pre-compute the needed context. Seed the missed
        paths back into the frontier at high priority so they are explored
        in the next cycle.

        Returns number of scenarios admitted.
        """
        if not miss_paths:
            return 0
        available_set = set(available_paths)
        valid_paths = [p for p in miss_paths if p in available_set]
        if not valid_paths:
            return 0
        priority = self._score(
            source="graph",
            graph_proximity=1.0,
            arc_probability=0.0,
            coverage_gap=1.0,
            pattern_strength=0.0,
            freshness_decay=1.0,
            depth=0,
        )
        scenario = ExplorationScenario(
            id=file_set_fingerprint(valid_paths),
            file_paths=valid_paths,
            anchor="miss_recovery",
            source="graph",
            priority=priority,
            depth=0,
            reason="recovery from cold-miss query",
            layer="tactical",
        )
        return 1 if self.push(scenario) else 0

    # -----------------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def explored_count(self) -> int:
        return len(self._explored)

    @property
    def source_multipliers(self) -> dict[str, float]:
        return dict(self._multipliers)

    @property
    def coverage_ratio(self) -> float:
        if self._total_available == 0:
            return 0.0
        return len(self._covered_file_set) / self._total_available

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _score(
        self,
        *,
        source: str,  # noqa: ARG002 — reserved for future source-specific tuning
        graph_proximity: float,
        arc_probability: float,
        coverage_gap: float,
        pattern_strength: float,
        freshness_decay: float,
        depth: int,
        layer: str = "operational",
    ) -> float:
        """Composite priority score in [0, 1].

        Weights favour structural signals (cheapest, most reliable) for code
        repos, with behavioural arc signals as a secondary signal.
        """
        return self._scoring_policy.compute_score(
            graph_proximity=graph_proximity,
            arc_probability=arc_probability,
            coverage_gap=coverage_gap,
            pattern_strength=pattern_strength,
            freshness_factor=freshness_decay,
            depth=depth,
            layer=layer,
        )
