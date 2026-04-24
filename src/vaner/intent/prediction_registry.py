# SPDX-License-Identifier: Apache-2.0
"""PredictionRegistry — in-memory lifecycle manager for PredictedPrompts in a cycle.

The registry is the engine-side owner of the prediction population for the
current precompute cycle. It enrols predictions, attaches scenarios and
artifacts, moves predictions through the readiness state machine, rebalances
weight based on observed yield, and stales the population when the user turn
shifts.

This module deliberately holds no business logic beyond bookkeeping + the
rebalance arithmetic. Scheduling decisions stay in engine.py and frontier.py;
the registry just records what they did.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    ReadinessState,
    is_transition_allowed,
)


@dataclass(frozen=True, slots=True)
class PredictionEvent:
    """A discrete event emitted by the registry. Phase C wires this into SSE."""

    kind: str
    prediction_id: str
    payload: dict[str, object]
    ts: float


EventListener = Callable[[PredictionEvent], None]


class InvalidTransitionError(ValueError):
    """Raised when a caller attempts an illegal readiness transition."""


class PredictionRegistry:
    """In-memory store + lifecycle manager for PredictedPrompts in one cycle."""

    MIN_FLOOR_WEIGHT = 0.05
    """Minimum fraction of cycle attention any admitted prediction may hold.

    Below this floor, a prediction would effectively be starved; we'd rather
    stale it than keep it nominally alive.
    """

    MIN_TOKEN_BUDGET = 128
    """Absolute minimum token budget per admitted prediction.

    When ``cycle_token_pool`` is small, the weight-derived token budget can
    collapse to a handful of tokens that no real LLM call can use. Clamp at
    this floor so a budget either buys a real inference or the prediction is
    staled outright.
    """

    MAX_THINKING_TRACES = 10
    """Cap the per-prediction thinking-trace ring buffer.

    Reasoning models can emit many thousands of thinking tokens per call.
    Keeping every trace unbounded blows up cycle memory and inflates the
    adopt-Resolution payload. Newer traces evict older ones FIFO.
    """

    MAX_THINKING_TRACE_BYTES = 32_000
    """Per-trace byte cap — longer traces are truncated with an ellipsis."""

    def __init__(
        self,
        *,
        cycle_token_pool: int = 4096,
        listener: EventListener | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._predictions: dict[str, PredictedPrompt] = {}
        self._cycle_token_pool = max(0, int(cycle_token_pool))
        self._listener = listener
        self._clock = clock
        # 0.8.4 WS4 — adoption-outcome descriptor queue. record_adoption()
        # is sync, so it cannot write to the SQLite store directly.
        # Instead it appends a descriptor dict here; the engine drains
        # the queue at end-of-cycle and writes pending-outcome rows via
        # the async DAO. Drained descriptors are popped — callers should
        # use ``consume_pending_adoption_descriptors()``.
        #
        # The queue is guarded by a plain ``threading.Lock`` (not the
        # asyncio ``self.lock`` below) because ``record_adoption`` is a
        # sync method callable from any context, and the flush path is
        # async. The two-op snapshot-then-clear is not atomic under the
        # GIL on its own. 0.8.4 hardening fix — see HIGH-3 in
        # docs/reviews/0.8.4-hardening.md.
        self._pending_adoption_descriptors: list[dict[str, object]] = []
        self._pending_adoption_lock = threading.Lock()
        # Phase 4 / WS1.c: registry is accessed from concurrent
        # ``_process_scenario`` workers. Callers may ``async with registry.lock:``
        # around sequences that must be atomic (e.g. rebalance).
        self.lock = asyncio.Lock()

    # -----------------------------------------------------------------------
    # Enrolment
    # -----------------------------------------------------------------------

    def enroll(self, spec: PredictionSpec, *, initial_weight: float) -> PredictedPrompt:
        """Enrol a prediction with an initial weight. Returns the enrolled PredictedPrompt.

        Raises ValueError if a prediction with the same id is already enrolled.
        """
        if spec.id in self._predictions:
            raise ValueError(f"prediction already enrolled: {spec.id}")
        weight = max(self.MIN_FLOOR_WEIGHT, float(initial_weight))
        run = PredictionRun(
            weight=weight,
            token_budget=max(self.MIN_TOKEN_BUDGET, int(self._cycle_token_pool * weight)),
            updated_at=self._clock(),
        )
        prompt = PredictedPrompt(spec=spec, run=run, artifacts=PredictionArtifacts())
        self._predictions[spec.id] = prompt
        self._emit(
            "prediction.enrolled",
            spec.id,
            {
                "label": spec.label,
                "source": spec.source,
                "confidence": spec.confidence,
                "weight": weight,
                "token_budget": run.token_budget,
                "hypothesis_type": spec.hypothesis_type,
                "specificity": spec.specificity,
            },
        )
        return prompt

    def enroll_batch(
        self,
        specs: Iterable[PredictionSpec],
    ) -> list[PredictedPrompt]:
        """Enrol multiple specs in one shot, allocating initial weights by the
        plan's formula:

            w_i = max(floor, confidence_i) / sum(max(floor, confidence_j))

        The floor is self.MIN_FLOOR_WEIGHT. Returns the enrolled PredictedPrompts.
        """
        materialized = list(specs)
        if not materialized:
            return []
        floored = [max(self.MIN_FLOOR_WEIGHT, float(s.confidence)) for s in materialized]
        total = sum(floored)
        if total <= 0:
            return []
        weights = [f / total for f in floored]
        return [self.enroll(spec, initial_weight=w) for spec, w in zip(materialized, weights, strict=True)]

    def merge(
        self,
        specs: Iterable[PredictionSpec],
        *,
        cycle_n: int,
    ) -> list[PredictedPrompt]:
        """Merge a fresh batch of specs into the existing registry.

        For each spec:
          - If the id already exists (spec was observed in a prior cycle), **update
            in place**: refresh spec.confidence, bump ``run.last_seen_cycle``, clear
            any stale ``invalidation_reason`` from a prior cycle, keep
            ``scenarios_spawned``/``scenarios_complete``/``tokens_used``/``model_calls``
            /``evidence_score``/``thinking_traces``/``prepared_briefing`` /
            ``draft_answer`` / ``file_content_hashes`` untouched. A ``spent``
            prediction stays spent — it surfaces again only after invalidation
            clears its evidence.
          - If the id is new, enrol it via ``enroll_batch``'s weight formula.

        Predictions that existed before but are **not** in ``specs`` this cycle
        are NOT removed. Missing-from-cycle is not a reason to stale — use
        invalidation signals via ``apply_invalidation_signals`` for that.

        This is the WS6 replacement for per-cycle rebuild. Called from the
        engine's ``_merge_prediction_specs``.
        """
        materialized = list(specs)
        if not materialized:
            return []
        existing: list[PredictionSpec] = []
        new_specs: list[PredictionSpec] = []
        for spec in materialized:
            if spec.id in self._predictions:
                existing.append(spec)
            else:
                new_specs.append(spec)

        touched: list[PredictedPrompt] = []

        # In-place update for specs we've seen before.
        for spec in existing:
            prompt = self._predictions[spec.id]
            # Refresh immutable-in-spec fields that *can* shift between cycles
            # (confidence can go up or down; label/description are stable by id).
            prompt.spec = spec  # type: ignore[misc]  # slots dataclass allows reassign
            prompt.run.last_seen_cycle = cycle_n
            prompt.run.updated_at = self._clock()
            # Don't reset invalidation_reason here — it gets cleared by
            # apply_invalidation_signals when the cause is gone.
            touched.append(prompt)

        # Enrol brand-new specs using the existing batch weight formula.
        if new_specs:
            fresh = self.enroll_batch(new_specs)
            for prompt in fresh:
                prompt.run.last_seen_cycle = cycle_n
            touched.extend(fresh)

        return touched

    # -----------------------------------------------------------------------
    # Attach / record
    # -----------------------------------------------------------------------

    def attach_scenario(self, prediction_id: str, scenario_id: str) -> None:
        prompt = self._require(prediction_id)
        if scenario_id in prompt.artifacts.scenario_ids:
            return
        prompt.artifacts.scenario_ids.append(scenario_id)
        prompt.run.scenarios_spawned += 1
        prompt.run.updated_at = self._clock()
        # First scenario attached → we are no longer queued.
        if prompt.run.readiness == "queued":
            self._transition(prompt, "grounding", reason="first scenario attached")

    def complete_scenario(self, prediction_id: str, scenario_id: str) -> None:
        """Mark a scenario as completed under its parent prediction."""
        prompt = self._require(prediction_id)
        if scenario_id not in prompt.artifacts.scenario_ids:
            return
        prompt.run.scenarios_complete += 1
        prompt.run.updated_at = self._clock()

    def record_call(self, prediction_id: str, *, tokens_used: int) -> None:
        prompt = self._require(prediction_id)
        prompt.run.model_calls += 1
        prompt.run.tokens_used += max(0, int(tokens_used))
        prompt.run.updated_at = self._clock()
        self._emit(
            "prediction.progress",
            prediction_id,
            {
                "tokens_used": prompt.run.tokens_used,
                "token_budget": prompt.run.token_budget,
                "scenarios_complete": prompt.run.scenarios_complete,
                "evidence_score": prompt.artifacts.evidence_score,
                "model_calls": prompt.run.model_calls,
            },
        )

    def record_evidence(self, prediction_id: str, *, delta_score: float) -> None:
        prompt = self._require(prediction_id)
        prompt.artifacts.evidence_score += float(delta_score)
        prompt.run.updated_at = self._clock()
        self._emit(
            "prediction.progress",
            prediction_id,
            {
                "tokens_used": prompt.run.tokens_used,
                "token_budget": prompt.run.token_budget,
                "scenarios_complete": prompt.run.scenarios_complete,
                "evidence_score": prompt.artifacts.evidence_score,
                "model_calls": prompt.run.model_calls,
            },
        )

    def record_adoption(self, prediction_id: str) -> None:
        """Record that a prediction was adopted by a downstream agent.

        Adoption is the strongest positive signal a prediction can get —
        stronger than any evidence_score increment, because it reflects the
        agent's *actual* decision to inject the prepared package. We bump
        the prediction's evidence_score by a large-but-bounded amount so
        the next ``rebalance()`` reallocates budget toward it, and set
        ``spent=True`` so adopted predictions don't immediately resurface on
        the next cycle — they come back only after invalidation clears the
        evidence that justified them.

        No state machine transition — adoption can happen from any non-stale
        state (typically ``ready`` via the HTTP/MCP path, but also ``drafting``
        for partially-prepared predictions).

        0.8.4 WS4: appends an adoption descriptor to the pending-outcome
        queue. The engine drains the queue at end-of-cycle and writes a
        pending-outcome row to ``prediction_adoption_outcomes``. The
        descriptor carries everything the DAO needs that is known at
        adoption time; ``workspace_root`` is added by the engine.
        """
        prompt = self._predictions.get(prediction_id)
        if prompt is None:
            return
        prompt.artifacts.evidence_score += 1.0
        prompt.run.spent = True
        prompt.run.updated_at = self._clock()
        descriptor = {
            "prediction_id": prediction_id,
            "label": prompt.spec.label,
            "anchor": prompt.spec.anchor,
            "revision_at_adoption": int(prompt.run.revision),
            "source": str(prompt.spec.source),
        }
        with self._pending_adoption_lock:
            self._pending_adoption_descriptors.append(descriptor)
        self._emit(
            "prediction.artifact_added",
            prediction_id,
            {"kind": "adoption"},
        )

    def consume_pending_adoption_descriptors(self) -> list[dict[str, object]]:
        """Return the queued adoption descriptors and clear the queue.

        Called by the engine at end-of-cycle to drain pending adoption
        writes into the SQLite store. Empty list when nothing was
        adopted this cycle.

        Acquires ``_pending_adoption_lock`` so the snapshot + clear
        pair is atomic vs. concurrent ``record_adoption()`` appends.
        (0.8.4 hardening — see HIGH-3 in the hardening doc.)
        """

        with self._pending_adoption_lock:
            drained = list(self._pending_adoption_descriptors)
            self._pending_adoption_descriptors.clear()
        return drained

    def attach_artifact(
        self,
        prediction_id: str,
        *,
        draft: str | None = None,
        briefing: str | None = None,
        thinking: str | None = None,
        file_content_hashes: dict[str, str] | None = None,
    ) -> None:
        prompt = self._require(prediction_id)
        kinds: list[str] = []
        if draft is not None:
            prompt.artifacts.draft_answer = draft
            kinds.append("draft")
        if briefing is not None:
            prompt.artifacts.prepared_briefing = briefing
            kinds.append("briefing")
        if thinking is not None:
            truncated = thinking
            if len(truncated) > self.MAX_THINKING_TRACE_BYTES:
                truncated = truncated[: self.MAX_THINKING_TRACE_BYTES].rstrip() + "…"
            prompt.artifacts.thinking_traces.append(truncated)
            # FIFO ring buffer — keep only the most recent MAX_THINKING_TRACES.
            if len(prompt.artifacts.thinking_traces) > self.MAX_THINKING_TRACES:
                overflow = len(prompt.artifacts.thinking_traces) - self.MAX_THINKING_TRACES
                del prompt.artifacts.thinking_traces[:overflow]
            kinds.append("thinking")
        if file_content_hashes:
            # WS6: capture which file contents the briefing/draft were
            # synthesised against. The invalidation sweep compares these
            # to the current git_state at cycle-start; mismatches demote
            # the prediction and clear its briefing.
            prompt.artifacts.file_content_hashes.update(file_content_hashes)
        if not kinds:
            return
        prompt.run.updated_at = self._clock()
        for kind in kinds:
            self._emit(
                "prediction.artifact_added",
                prediction_id,
                {"kind": kind},
            )

    # -----------------------------------------------------------------------
    # Readiness transitions
    # -----------------------------------------------------------------------

    def transition(self, prediction_id: str, to_state: ReadinessState, *, reason: str = "") -> None:
        """Move a prediction to `to_state`. Raises InvalidTransitionError on illegal moves."""
        prompt = self._require(prediction_id)
        self._transition(prompt, to_state, reason=reason)

    def stale_all(self, reason: str) -> list[str]:
        """Mark every non-terminal prediction stale. Returns list of staled ids.

        Called on new user-turn observation or other context-shift signals.
        """
        staled: list[str] = []
        for prompt in list(self._predictions.values()):
            if prompt.run.readiness == "stale":
                continue
            try:
                self._transition(prompt, "stale", reason=reason)
                staled.append(prompt.id)
            except InvalidTransitionError:
                continue
        return staled

    # -----------------------------------------------------------------------
    # Rebalance
    # -----------------------------------------------------------------------

    def rebalance(self) -> dict[str, float]:
        """Redistribute remaining weight based on observed per-prediction yield.

        Yield = Δ(evidence_score) / tokens_used since enrolment, measured
        on currently-active (non-terminal) predictions. The rebalance respects
        the MIN_FLOOR_WEIGHT so no admitted prediction is starved.

        Returns the new weight map (prediction_id -> weight).
        """
        active = [p for p in self._predictions.values() if not p.is_terminal()]
        if not active:
            return {}

        yields: dict[str, float] = {}
        for prompt in active:
            tokens = max(1, prompt.run.tokens_used)
            yields[prompt.id] = max(0.0, prompt.artifacts.evidence_score) / tokens

        total_yield = sum(yields.values())
        if total_yield <= 0:
            # No evidence yet — preserve existing weights.
            return {p.id: p.run.weight for p in active}

        # Normalise yields into weight shares, then apply the starvation
        # floor post-normalisation so predictions with non-zero yield stay
        # proportional to their observed usefulness. Without the post-step
        # floor, every tiny yield collapses to MIN_FLOOR_WEIGHT and the
        # rebalance loses its signal (weaker predictions end up equal-weight
        # with adopted ones).
        new_weights: dict[str, float] = {}
        for prompt in active:
            share = yields[prompt.id] / total_yield
            weight = max(self.MIN_FLOOR_WEIGHT, share)
            prompt.run.weight = weight
            prompt.run.token_budget = max(self.MIN_TOKEN_BUDGET, int(self._cycle_token_pool * weight))
            prompt.run.updated_at = self._clock()
            new_weights[prompt.id] = weight
        return new_weights

    # -----------------------------------------------------------------------
    # WS6 — invalidation
    # -----------------------------------------------------------------------

    FILE_CHANGE_WEIGHT_DECAY = 0.5
    """On a file_change signal, demote prediction weight by this multiplicative
    factor. Post-decay weight below ``MIN_FLOOR_WEIGHT`` triggers a stale
    transition — the prediction has been starved enough that the registry
    shouldn't keep pretending to work on it."""

    CATEGORY_SHIFT_CONFIDENCE_DECAY = 0.1
    """On a category_shift signal, subtract this from each affected
    prediction's spec.confidence per shift. Predictions with confidence <
    0.1 after decay are staled."""

    def apply_invalidation_signals(self, signals: list) -> dict[str, str]:
        """Apply a batch of :class:`InvalidationSignal` records to the population.

        Returns a ``{prediction_id: outcome}`` map describing what was done to
        each touched prediction (``"demoted"``, ``"cleared_briefing"``,
        ``"staled"``, ``"spent"``). Predictions not mentioned in the return
        map were untouched this round.

        Semantics per signal kind:

        - ``file_change``: for each prediction whose ``artifacts
          .file_content_hashes`` contains any changed path, halve its weight
          (see ``FILE_CHANGE_WEIGHT_DECAY``), clear ``prepared_briefing`` +
          ``draft_answer`` (evidence needs re-derivation), record the
          ``invalidation_reason``, and stale when the post-decay weight
          falls below ``MIN_FLOOR_WEIGHT``.
        - ``commit``: stale predictions whose ``spec.specificity != "concrete"``
          — these are phase/category-anchored and most likely resolved by the
          commit itself. Concrete file-anchored predictions are covered by
          the file_change signal.
        - ``category_shift``: subtract
          ``CATEGORY_SHIFT_CONFIDENCE_DECAY`` from the spec.confidence of
          each prediction whose ``spec.anchor`` equals the
          ``payload["from"]`` category. Stale when the decayed confidence
          falls below 0.1.
        - ``adoption``: routes through ``record_adoption``.

        This method is a no-op when ``signals`` is empty.
        """
        outcomes: dict[str, str] = {}
        if not signals:
            return outcomes

        for sig in signals:
            kind = getattr(sig, "kind", None)
            payload = getattr(sig, "payload", {}) or {}

            if kind == "file_change":
                changed = set(payload.get("changed_paths", []) or [])
                new_hashes = payload.get("new_hashes", {}) or {}
                if not changed:
                    continue
                for prompt in list(self._predictions.values()):
                    if prompt.is_terminal():
                        continue
                    captured = prompt.artifacts.file_content_hashes
                    if not captured:
                        continue
                    if not any(path in captured for path in changed):
                        continue
                    prompt.run.weight = max(0.0, prompt.run.weight * self.FILE_CHANGE_WEIGHT_DECAY)
                    prompt.run.token_budget = max(
                        self.MIN_TOKEN_BUDGET,
                        int(self._cycle_token_pool * max(self.MIN_FLOOR_WEIGHT, prompt.run.weight)),
                    )
                    prompt.artifacts.prepared_briefing = None
                    prompt.artifacts.draft_answer = None
                    prompt.run.invalidation_reason = f"file_change: {len(changed & set(captured.keys()))} path(s) changed"
                    # Refresh captured hashes with the new values we have,
                    # so subsequent no-op cycles don't re-trigger on the
                    # same change. Paths no longer in new_hashes are
                    # removed (file deleted) so the prediction doesn't
                    # keep referencing ghost paths.
                    refreshed: dict[str, str] = {}
                    for path, sha in captured.items():
                        if path in new_hashes:
                            refreshed[path] = new_hashes[path]
                        elif path not in changed:
                            refreshed[path] = sha
                    prompt.artifacts.file_content_hashes = refreshed
                    if prompt.run.weight < self.MIN_FLOOR_WEIGHT:
                        try:
                            self._transition(prompt, "stale", reason=prompt.run.invalidation_reason)
                        except InvalidTransitionError:
                            pass
                        outcomes[prompt.id] = "staled"
                    else:
                        outcomes[prompt.id] = "cleared_briefing"
                    prompt.run.updated_at = self._clock()

            elif kind == "commit":
                to_sha = payload.get("to_sha", "")
                for prompt in list(self._predictions.values()):
                    if prompt.is_terminal():
                        continue
                    # Concrete file-anchored predictions are left to the
                    # file_change signal. Phase/category predictions (arc,
                    # history at category specificity) are the ones a commit
                    # most likely resolves.
                    if prompt.spec.specificity == "concrete":
                        continue
                    prompt.run.invalidation_reason = f"commit: HEAD → {str(to_sha)[:12]}"
                    try:
                        self._transition(prompt, "stale", reason=prompt.run.invalidation_reason)
                        outcomes[prompt.id] = "staled"
                    except InvalidTransitionError:
                        pass

            elif kind == "category_shift":
                from_cat = str(payload.get("from", ""))
                if not from_cat:
                    continue
                for prompt in list(self._predictions.values()):
                    if prompt.is_terminal():
                        continue
                    if prompt.spec.anchor != from_cat:
                        continue
                    # confidence is on the frozen spec; we mirror the decay
                    # by directly demoting weight instead. Same practical
                    # effect: the prediction gets proportionally less of
                    # this cycle's compute and eventually stales out.
                    decayed = max(0.0, prompt.run.weight - self.CATEGORY_SHIFT_CONFIDENCE_DECAY)
                    prompt.run.weight = decayed
                    prompt.run.invalidation_reason = f"category_shift: {from_cat} → {payload.get('to')}"
                    if decayed < self.MIN_FLOOR_WEIGHT:
                        try:
                            self._transition(prompt, "stale", reason=prompt.run.invalidation_reason)
                            outcomes[prompt.id] = "staled"
                        except InvalidTransitionError:
                            pass
                    else:
                        prompt.run.token_budget = max(
                            self.MIN_TOKEN_BUDGET,
                            int(self._cycle_token_pool * max(self.MIN_FLOOR_WEIGHT, decayed)),
                        )
                        outcomes[prompt.id] = "demoted"
                    prompt.run.updated_at = self._clock()

            elif kind == "adoption":
                pid = str(payload.get("prediction_id", ""))
                if not pid:
                    continue
                if pid in self._predictions:
                    self.record_adoption(pid)
                    outcomes[pid] = "spent"

            # 0.8.2 WS3 — intent-artefact signals. The actual state
            # transitions and confidence updates live on the persisted
            # :class:`ReconciliationOutcome`; the registry's job here is
            # to reflect item-level transitions into the artefact-item-
            # anchored predictions that were emitted in WS2.
            elif kind == "artefact_seen":
                # Cycle-top emit: a new/updated snapshot landed. Don't
                # touch predictions here — the next ``merge`` pass in
                # ``_merge_prediction_specs`` re-emits the specs with
                # fresh confidence. We record it as a no-op for
                # observability only.
                continue

            elif kind == "artefact_superseded":
                old_id = str(payload.get("old_artefact_id") or "")
                if not old_id:
                    continue
                for prompt in list(self._predictions.values()):
                    if prompt.is_terminal() or prompt.spec.source != "artefact_item":
                        continue
                    # ``anchor`` on artefact_item specs is the item id;
                    # we don't store a reverse artefact_id lookup here,
                    # so we demote predictions whose
                    # ``invalidation_reason`` already names the old
                    # artefact or stale them outright when the old
                    # artefact dominates the description.
                    if old_id in prompt.spec.description:
                        self._transition(
                            prompt,
                            "stale",
                            reason=f"artefact_superseded: {old_id}",
                        )
                        prompt.run.invalidation_reason = f"artefact_superseded: {old_id}"
                        outcomes[prompt.spec.id] = "staled"

            elif kind == "progress_reconciled":
                # Pointer-only payload — full detail lives on the
                # persisted ``ReconciliationOutcome``. The registry
                # alone can't load it (the store is async and not
                # injected here), so the engine's cycle-top loop is
                # responsible for calling ``apply_item_state_delta``
                # per delta after fetching the outcome record. This
                # branch exists so the signal kind is recognized and
                # doesn't fall through as an unknown signal.
                continue

        return outcomes

    # ---------------------------------------------------------------
    # 0.8.2 WS3 — item-state delta application
    # ---------------------------------------------------------------

    def apply_item_state_delta(
        self,
        *,
        item_id: str,
        from_state: str,
        to_state: str,
    ) -> str | None:
        """Translate one reconciliation item-state transition into the
        right effect on any artefact-item-anchored prediction.

        Returns the outcome label (``"spent"``, ``"demoted"``,
        ``"staled"``, ``"flipped_possible_branch"``) or ``None`` when
        no such prediction exists. The engine drains its per-cycle
        outcome list through this method after calling
        :func:`vaner.intent.reconcile.reconcile_artefact`.

        State-transition → registry effect:

        - ``* → complete``: spec is adopted / done; prediction goes
          ``spent`` so it stops resurfacing until the evidence
          invalidates.
        - ``* → contradicted``: demote weight and stale; confidence
          decays to the floor.
        - ``* → stalled``: flip the spec's hypothesis_type to
          ``possible_branch`` via demotion — pure in-registry effect.
        - ``* → in_progress``: no-op (the spec is still likely_next).
        """

        for prompt in self._predictions.values():
            if prompt.spec.source != "artefact_item":
                continue
            if prompt.spec.anchor != item_id:
                continue
            if to_state == "complete":
                self.record_adoption(prompt.spec.id)
                return "spent"
            if to_state == "contradicted":
                prompt.run.weight = max(0.0, prompt.run.weight * 0.25)
                prompt.run.invalidation_reason = f"item state {from_state!r} → {to_state!r}"
                self._transition(
                    prompt,
                    "stale",
                    reason=f"contradicted: {item_id}",
                )
                return "staled"
            if to_state == "stalled":
                prompt.run.weight = max(0.0, prompt.run.weight * 0.5)
                prompt.run.invalidation_reason = f"item state {from_state!r} → {to_state!r}"
                return "demoted"
            if to_state == "in_progress":
                # Still on the critical path — leave the spec alone;
                # the next cycle's merge will re-emit it with fresh
                # confidence from the §6.6 metadata block.
                return None
        return None

    # -----------------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------------

    def get(self, prediction_id: str) -> PredictedPrompt | None:
        return self._predictions.get(prediction_id)

    def active(self) -> list[PredictedPrompt]:
        """Snapshot of currently adoptable predictions.

        Excludes ``stale`` (terminal) and ``spent`` (already adopted — the
        prediction shouldn't resurface until its underlying evidence is
        invalidated, at which point the cycle will rebuild it fresh).
        """
        return [p for p in self._predictions.values() if not p.is_terminal() and not p.run.spent]

    def all(self) -> list[PredictedPrompt]:
        return list(self._predictions.values())

    def __len__(self) -> int:
        return len(self._predictions)

    def __contains__(self, prediction_id: object) -> bool:
        return prediction_id in self._predictions

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _require(self, prediction_id: str) -> PredictedPrompt:
        prompt = self._predictions.get(prediction_id)
        if prompt is None:
            raise KeyError(f"no such prediction: {prediction_id}")
        return prompt

    def _transition(self, prompt: PredictedPrompt, to_state: ReadinessState, *, reason: str) -> None:
        from_state = prompt.run.readiness
        if from_state == to_state:
            return
        if not is_transition_allowed(from_state, to_state):
            raise InvalidTransitionError(f"illegal transition {from_state} -> {to_state} for prediction {prompt.id}")
        prompt.run.readiness = to_state
        prompt.run.updated_at = self._clock()
        self._emit(
            "prediction.readiness_changed",
            prompt.id,
            {"from_state": from_state, "to_state": to_state, "reason": reason},
        )
        if to_state == "stale":
            self._emit("prediction.staled", prompt.id, {"reason": reason})

    def _emit(self, kind: str, prediction_id: str, payload: dict[str, object]) -> None:
        if self._listener is None:
            return
        self._listener(PredictionEvent(kind=kind, prediction_id=prediction_id, payload=payload, ts=self._clock()))
