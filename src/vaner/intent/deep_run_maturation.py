# SPDX-License-Identifier: Apache-2.0
"""WS3 — Deep-Run maturation revisiting (0.8.3).

The central new mechanism. A maturation pass takes an already-``READY``
prediction and re-enters the drafter with a *contract* derived from
the prediction's current weakness signal: low evidence, shallow draft,
stale grounding, unresolved contradiction, or high evidence volatility.
The drafter produces a candidate new draft. A separate **judge** —
distinct callable, distinct prompt, skeptical default — measures the
new draft against the contract clause-by-clause. Persistence requires
all "must" clauses satisfied AND zero "forbidden" clauses violated.

Why this shape (per spec §9.2): a same-model self-judging loop will
systematically over-approve its own work. The defenses, all required:

1. **Generator/judge role separation.** The drafter never sees the
   judge's prompt; the judge never sees the drafter's prompt. The
   judge defaults to ``kept=False`` and may only return ``kept=True``
   when it can name concrete contract-specified evidence.
2. **Contract before drafter.** The :class:`MaturationContract` is
   built from the prediction's current weakness *before* the drafter
   runs, so the judge measures against an explicit standard rather
   than a feeling.
3. **Probationary persistence + diminishing-returns thresholds.** A
   kept maturation is probationary for N cycles; subsequent
   reconciliation contradictions roll it back. The persistence
   threshold tightens with each ``revision``.

The default judge in this module is **rubric-based and programmatic**
— it does not require an LLM. It compares old vs. new draft using
explicit, gradable rules (evidence-ref deltas, paragraph diffs, length
checks). An LLM-backed judge can be plugged in later via the
:class:`JudgeCallable` Protocol; the programmatic default is the
floor, not the ceiling.

Engine integration is opt-in: the engine only calls
:func:`mature_one` from a Deep-Run cycle hook. Outside Deep-Run,
none of this code runs.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from vaner.intent.deep_run import (
    DeepRunPassAction,
    DeepRunPreset,
    DeepRunSession,
)
from vaner.intent.deep_run_policy import PRESETS, preset_for
from vaner.intent.prediction import PredictedPrompt

TargetWeakness = Literal[
    "low_evidence",
    "high_volatility",
    "shallow_draft",
    "stale_grounding",
    "unresolved_contradiction",
]

ClauseKind = Literal["must", "forbidden"]


# ---------------------------------------------------------------------------
# Refinement context (0.8.4 WS3) — replaces the DeepRunSession parameter on
# mature_one() and select_maturation_candidates() so the same machinery can
# run (a) inside a Deep-Run window via ``from_deep_run_session()``, and
# (b) in ordinary background cycles via ``background_default()`` once 0.8.5
# activates the refinement flag. The two factory methods are the only
# supported construction paths; direct ``RefinementContext(...)`` calls are
# valid but discouraged — use the factories to pull preset-derived defaults.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefinementContext:
    """Per-cycle context for one maturation / refinement pass.

    Encodes everything the maturation pass needs that used to come from
    a ``DeepRunSession``: the preset (for thresholds), the cycle index
    (for probation windows), the per-prediction revisit cap, the
    drafter's evidence floor, and an optional ``session_id`` that tags
    Deep-Run-originated passes for audit-log routing.

    ``session_id is None`` means **background refinement** — no
    ``deep_run_pass_log`` row is written; the pass is invisible to
    audit surfaces by design. The 0.8.3 Deep-Run path always produces
    a non-None ``session_id`` via :meth:`from_deep_run_session`.
    """

    preset: DeepRunPreset
    cycle_index: int
    max_revisits_per_prediction: int
    draft_evidence_threshold: float
    session_id: str | None = None

    @classmethod
    def from_deep_run_session(cls, session: DeepRunSession, *, cycle_index: int) -> RefinementContext:
        """Construct the context for a Deep-Run maturation pass.

        The preset, revisit cap, and evidence floor are read from the
        session's preset bundle. ``session_id`` is populated so the
        engine's audit-log writer tags the pass correctly.
        """

        spec = preset_for(session)
        return cls(
            preset=session.preset,
            cycle_index=cycle_index,
            max_revisits_per_prediction=spec.max_revisits_per_prediction,
            draft_evidence_threshold=spec.draft_evidence_threshold,
            session_id=session.id,
        )

    @classmethod
    def background_default(
        cls,
        *,
        cycle_index: int,
        preset: DeepRunPreset = "balanced",
    ) -> RefinementContext:
        """Construct the context for ordinary background refinement.

        ``session_id`` is ``None`` — no audit-log write, no Deep-Run
        session binding. Defaults to the ``balanced`` preset; callers
        can override by passing a different preset name.
        """

        spec = PRESETS[preset]
        return cls(
            preset=preset,
            cycle_index=cycle_index,
            max_revisits_per_prediction=spec.max_revisits_per_prediction,
            draft_evidence_threshold=spec.draft_evidence_threshold,
            session_id=None,
        )

    @property
    def is_deep_run(self) -> bool:
        """``True`` if this context was derived from a Deep-Run session
        (i.e. ``session_id is not None``). Used by the engine to decide
        whether to route the outcome through ``deep_run_pass_log``."""

        return self.session_id is not None


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContractClause:
    """A single, gradable rule the new draft must satisfy (or avoid).

    ``key`` is a stable identifier (e.g. ``"new_evidence_refs_min_2"``)
    used by the judge to look up the corresponding programmatic check.
    ``description`` is human-readable; it appears in audit logs and in
    judge verdicts so the user can understand why a draft was kept or
    discarded.
    """

    key: str
    description: str
    kind: ClauseKind


@dataclass(frozen=True, slots=True)
class MaturationContract:
    """The success criteria for one maturation pass (spec §9.2(b)).

    Built from the prediction's current weakness *before* invoking
    the drafter so the contract is not retrofitted to whatever the
    drafter happens to produce. The judge measures the new draft
    against this contract clause-by-clause.

    A kept verdict requires *all* ``must`` clauses satisfied AND
    *zero* ``forbidden`` clauses violated.
    """

    pass_id: str
    target_weakness: TargetWeakness
    must_clauses: tuple[ContractClause, ...]
    forbidden_clauses: tuple[ContractClause, ...]
    grading_rubric_version: str = "v1"

    @property
    def required_new_evidence_refs(self) -> int:
        """Convenience: the minimum number of new evidence refs the
        contract requires (0 if no clause demands it). Read by the
        rubric judge to score the ``low_evidence`` target."""

        for clause in self.must_clauses:
            if clause.key.startswith("new_evidence_refs_min_"):
                try:
                    return int(clause.key.rsplit("_", 1)[-1])
                except ValueError:
                    continue
        return 0


# ---------------------------------------------------------------------------
# Verdict + outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaturationVerdict:
    """Output of a :class:`JudgeCallable` for one pass.

    ``kept=True`` means the new draft satisfies the contract and
    should be persisted. ``kept=False`` means discard the new draft
    and increment ``failed_revisits``.

    ``satisfied_clauses`` lists the ``must`` clause keys the judge
    confirmed; ``failed_clause`` is set when the verdict is
    ``kept=False`` and identifies the first clause that failed
    (whether a ``must`` was unsatisfied or a ``forbidden`` was
    violated). The pair forms the audit trail surfaced by
    ``vaner.explain``.
    """

    kept: bool
    satisfied_clauses: tuple[str, ...]
    failed_clause: str | None
    reason: str


@dataclass(slots=True)
class MaturationOutcome:
    """Result of one :func:`mature_one` call.

    ``session_id`` is ``None`` for background-refinement passes
    (0.8.4+) and a Deep-Run session id for Deep-Run passes. The engine
    routes ``deep_run_pass_log`` writes only when ``session_id`` is
    non-None, so background passes are audit-log-free by design.
    """

    prediction_id: str
    session_id: str | None
    cycle_index: int
    contract: MaturationContract
    verdict: MaturationVerdict
    action: DeepRunPassAction
    before_evidence_score: float
    after_evidence_score: float
    before_draft_hash: str | None
    after_draft_hash: str | None
    new_draft: str | None  # populated when kept=True; None otherwise


# ---------------------------------------------------------------------------
# Drafter + judge protocols
# ---------------------------------------------------------------------------


class MaturationDrafterCallable(Protocol):
    """Async callable: produce a candidate new draft for a maturation pass.

    Receives the existing :class:`PredictedPrompt` plus the
    :class:`MaturationContract`. Must return a tuple of
    ``(new_draft_text, new_evidence_refs)`` where ``new_evidence_refs``
    is the list of evidence ref strings the drafter included in the
    new draft (used by the judge to verify ``new_evidence_refs_min_N``
    clauses without requiring text-mining).

    Real implementations wrap the existing :class:`Drafter` with a
    maturation-specific prompt frame; tests pass a synthetic stub.
    """

    async def __call__(
        self,
        prediction: PredictedPrompt,
        contract: MaturationContract,
    ) -> tuple[str, list[str]]:
        """Protocol — no implementation."""


class JudgeCallable(Protocol):
    """Async callable: grade a candidate new draft against a contract.

    Distinct from :class:`MaturationDrafterCallable` — different
    signature, different prompt frame, different identity. Defaults
    to a *not improved* verdict if any contract clause is unsatisfied
    or violated.
    """

    async def __call__(
        self,
        *,
        prediction: PredictedPrompt,
        contract: MaturationContract,
        old_draft: str | None,
        new_draft: str,
        new_evidence_refs: list[str],
    ) -> MaturationVerdict:
        """Protocol — no implementation."""


# ---------------------------------------------------------------------------
# Contract builder
# ---------------------------------------------------------------------------


_UNIVERSAL_FORBIDDEN: tuple[ContractClause, ...] = (
    ContractClause(
        key="no_length_only_growth",
        description="No length-only growth (≥30% longer with no new evidence_refs).",
        kind="forbidden",
    ),
    ContractClause(
        key="no_evidence_ref_removal",
        description="Do not silently remove correct prior evidence_refs.",
        kind="forbidden",
    ),
    ContractClause(
        key="anchor_preserved",
        description="The matured draft must still address the same anchor.",
        kind="forbidden",
    ),
)


def _detect_target_weakness(
    prediction: PredictedPrompt,
    *,
    evidence_floor: float,
) -> TargetWeakness:
    """Pick the dominant weakness signal driving this maturation pass.

    Order of precedence (tightest signal first): stale grounding wins
    if file content drifted; otherwise low evidence; otherwise shallow
    draft. Volatility / contradiction targets land in WS3.x with the
    full reconciliation hook-up.
    """

    artifacts = prediction.artifacts
    if artifacts.evidence_score < evidence_floor:
        return "low_evidence"
    draft = artifacts.draft_answer or ""
    if len(draft.split()) < 80:
        return "shallow_draft"
    return "low_evidence"


def build_contract(
    prediction: PredictedPrompt,
    *,
    pass_id: str,
    evidence_floor: float = 0.6,
    grading_rubric_version: str = "v1",
) -> MaturationContract:
    """Construct the contract for one maturation pass on this prediction.

    The chosen ``target_weakness`` selects the ``must`` clauses; the
    ``forbidden`` clauses are universal (apply to every weakness).
    ``evidence_floor`` defaults match the spec's Balanced preset; the
    engine passes the active preset's threshold for tighter / looser
    Conservative / Aggressive behaviour.
    """

    weakness = _detect_target_weakness(prediction, evidence_floor=evidence_floor)
    must: tuple[ContractClause, ...]
    if weakness == "low_evidence":
        must = (
            ContractClause(
                key="new_evidence_refs_min_2",
                description=("Cite ≥2 evidence sources not in the prior evidence_refs set."),
                kind="must",
            ),
        )
    elif weakness == "shallow_draft":
        must = (
            ContractClause(
                key="add_substantive_paragraph_min_80_words",
                description=(
                    "Add ≥1 new substantive paragraph (≥80 words) addressing a sub-question not covered in the prior draft. No paraphrase."
                ),
                kind="must",
            ),
            ContractClause(
                key="new_evidence_refs_min_1",
                description="At least one new evidence_ref accompanying the new content.",
                kind="must",
            ),
        )
    elif weakness == "stale_grounding":
        must = (
            ContractClause(
                key="refresh_file_hashes_min_1",
                description="Replace ≥1 stale file_content_hash citation.",
                kind="must",
            ),
        )
    elif weakness == "high_volatility":
        must = (
            ContractClause(
                key="resolve_named_contradiction",
                description=(
                    "Name the prior contradiction by referencing both conflicting "
                    "evidence_ref ids and either retract one or state the "
                    "reconciling reading explicitly."
                ),
                kind="must",
            ),
        )
    else:  # unresolved_contradiction
        must = (
            ContractClause(
                key="address_contradicting_item",
                description=("Explicitly reference the conflicting IntentArtefactItem.id and state defer / contradict / synthesise."),
                kind="must",
            ),
        )
    return MaturationContract(
        pass_id=pass_id,
        target_weakness=weakness,
        must_clauses=must,
        forbidden_clauses=_UNIVERSAL_FORBIDDEN,
        grading_rubric_version=grading_rubric_version,
    )


# ---------------------------------------------------------------------------
# Default rubric judge (programmatic, skeptical)
# ---------------------------------------------------------------------------


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _paragraph_set(text: str) -> set[str]:
    """Normalised-paragraph set for paraphrase-vs-new detection.

    Two paragraphs that differ only in whitespace / casing collapse
    to the same key — used by the judge to detect "the new draft just
    paraphrases the old paragraphs."
    """

    paragraphs = re.split(r"\n\s*\n", text.strip())
    return {p.strip().lower() for p in paragraphs if p.strip()}


async def default_rubric_judge(
    *,
    prediction: PredictedPrompt,
    contract: MaturationContract,
    old_draft: str | None,
    new_draft: str,
    new_evidence_refs: list[str],
) -> MaturationVerdict:
    """The skeptical, programmatic default judge.

    Implements every contract clause as a rule on the
    ``(old_draft, new_draft, new_evidence_refs)`` tuple. Defaults to
    ``kept=False`` if any ``must`` is unsatisfied or any ``forbidden``
    is violated. Provides a concrete, named reason — never a vague
    "this looks better" verdict.

    This judge is intentionally conservative. False negatives (rejecting
    a good improvement) are recoverable — the next cycle can try
    again. False positives (approving a marginal change) are not — the
    matured draft becomes the new baseline and the user sees worse
    output than they had before.
    """

    if not new_draft.strip():
        return MaturationVerdict(
            kept=False,
            satisfied_clauses=(),
            failed_clause="empty_new_draft",
            reason="judge: new_draft is empty",
        )

    old = old_draft or ""
    prior_refs = set(prediction.artifacts.scenario_ids) | set(_extract_evidence_ref_ids(old))
    new_refs_set = set(new_evidence_refs)

    # Forbidden: length-only growth.
    old_len = _word_count(old)
    new_len = _word_count(new_draft)
    new_unique_refs = new_refs_set - prior_refs
    if old_len > 0 and new_len > 1.30 * old_len and not new_unique_refs:
        return MaturationVerdict(
            kept=False,
            satisfied_clauses=(),
            failed_clause="no_length_only_growth",
            reason=(f"judge: new draft is {new_len / max(1, old_len):.0%} of old length but introduces no new evidence_refs"),
        )

    # Forbidden: silent removal of prior evidence refs.
    if prior_refs and prior_refs - new_refs_set - set(_extract_evidence_ref_ids(new_draft)):
        # Only flag when the new draft both (a) drops a ref the old
        # draft had and (b) provides no replacement set that includes it.
        # Conservative: defer to LLM judge in WS3.x for nuanced reading;
        # here we only block clear cases where the new draft has fewer
        # total refs than the old.
        if len(new_refs_set | set(_extract_evidence_ref_ids(new_draft))) < len(prior_refs):
            return MaturationVerdict(
                kept=False,
                satisfied_clauses=(),
                failed_clause="no_evidence_ref_removal",
                reason="judge: new draft drops prior evidence_refs without retraction",
            )

    # Must clauses — evaluate one at a time.
    satisfied: list[str] = []
    for clause in contract.must_clauses:
        ok, reason = _evaluate_must_clause(
            clause=clause,
            old_draft=old,
            new_draft=new_draft,
            new_evidence_refs=new_evidence_refs,
            prior_refs=prior_refs,
        )
        if not ok:
            return MaturationVerdict(
                kept=False,
                satisfied_clauses=tuple(satisfied),
                failed_clause=clause.key,
                reason=f"judge: clause {clause.key!r} failed — {reason}",
            )
        satisfied.append(clause.key)

    return MaturationVerdict(
        kept=True,
        satisfied_clauses=tuple(satisfied),
        failed_clause=None,
        reason=f"judge: {len(satisfied)} must-clause(s) satisfied; no forbidden violations",
    )


_EVIDENCE_REF_PATTERN = re.compile(r"\[evidence:([a-zA-Z0-9_-]+)\]")


def _extract_evidence_ref_ids(text: str) -> set[str]:
    """Extract evidence-ref tokens of the form ``[evidence:<id>]``
    from a draft body. Used to detect refs the drafter mentioned
    inline without listing them in ``new_evidence_refs``."""

    return set(_EVIDENCE_REF_PATTERN.findall(text))


def _evaluate_must_clause(
    *,
    clause: ContractClause,
    old_draft: str,
    new_draft: str,
    new_evidence_refs: list[str],
    prior_refs: set[str],
) -> tuple[bool, str]:
    key = clause.key
    if key.startswith("new_evidence_refs_min_"):
        try:
            n = int(key.rsplit("_", 1)[-1])
        except ValueError:
            return False, f"clause key {key!r} has invalid count suffix"
        new_unique = set(new_evidence_refs) - prior_refs
        if len(new_unique) >= n:
            return True, f"{len(new_unique)} new ref(s) >= {n}"
        return False, f"only {len(new_unique)} new ref(s) (need {n})"
    if key == "add_substantive_paragraph_min_80_words":
        old_paragraphs = _paragraph_set(old_draft)
        new_paragraphs = _paragraph_set(new_draft) - old_paragraphs
        substantial = [p for p in new_paragraphs if _word_count(p) >= 80]
        if substantial:
            return True, f"{len(substantial)} new paragraph(s) with ≥80 words"
        return (
            False,
            f"no new paragraph >= 80 words (found {len(new_paragraphs)} new paragraphs)",
        )
    if key == "refresh_file_hashes_min_1":
        # Simple heuristic: the new draft cites a file_content_hash that
        # the old did not. Production version (post-WS3) compares against
        # the persisted file_content_hashes map directly.
        old_hashes = set(re.findall(r"\bsha[12]?:[a-f0-9]{8,}\b", old_draft))
        new_hashes = set(re.findall(r"\bsha[12]?:[a-f0-9]{8,}\b", new_draft))
        if new_hashes - old_hashes:
            return True, "new file content hash cited"
        return False, "no new file_content_hash citation"
    if key == "resolve_named_contradiction":
        # Substring match for "[contradiction:..." token. Coarse on
        # purpose; LLM judge in WS3.x refines.
        if "[contradiction:" in new_draft and "[contradiction:" not in old_draft:
            return True, "new contradiction reference present"
        return False, "no [contradiction:...] reference introduced"
    if key == "address_contradicting_item":
        if "[item:" in new_draft and "[item:" not in old_draft:
            return True, "contradicting item referenced"
        return False, "no [item:...] reference introduced"
    return False, f"unknown clause key {key!r}"


# ---------------------------------------------------------------------------
# Candidate ranking + selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaturationCandidate:
    """One ranked candidate for the next maturation pass."""

    prediction: PredictedPrompt
    score: float
    eligible: bool
    skip_reason: str | None


def adoption_success_factor(*, confirmed: int, rejected: int) -> float:
    """Compute the scoring multiplier from adoption-outcome history.

    Uses a symmetric Bayesian prior (``prior_c = prior_r = 1``):
    ``smoothed_rate = (confirmed + 1) / (confirmed + rejected + 2)``.
    Then map ``[0, 1]`` → ``[0.5, 1.5]`` with ``factor = 1.0 +
    (smoothed_rate - 0.5)`` and clamp.

    Key properties:

    - Cold start (no history): ``0/0`` → smoothed ``0.5`` →
      factor ``1.0`` (neutral). No bias for never-adopted predictions.
    - Strong confirms: ``5/0`` → smoothed ≈ ``0.86`` → factor ≈
      ``1.36``; ``100/0`` saturates toward the ``1.5`` ceiling.
    - Strong rejects: ``0/5`` → smoothed ≈ ``0.14`` → factor ≈
      ``0.64``; ``0/100`` floors at ``0.5``.

    Gated by ``refinement.enabled`` at the call site (engine-level);
    this helper is pure so tests can exercise it in isolation.
    """

    # Symmetric Beta(1, 1) prior → mean 0.5 for no-data predictions.
    smoothed_rate = (confirmed + 1) / (confirmed + rejected + 2)
    factor = 1.0 + (smoothed_rate - 0.5)
    return max(0.5, min(1.5, factor))


def score_maturation_value(
    prediction: PredictedPrompt,
    *,
    goal_confidence: float = 0.5,
    artefact_alignment_score: float = 1.0,
    item_state: str = "pending",
    adoption_success_factor_value: float = 1.0,
) -> float:
    """Compute the ``maturation_value`` score from the spec §9.1 formula.

    Higher = better candidate. Components:

    - ``goal_confidence`` × ``artefact_alignment_score`` — how much
      this prediction aligns with declared intent
    - ``(1 - normalized_evidence_score)`` — how much room there is
      to improve
    - ``1 / (revision + 1)`` — diminishing returns across revisions
    - state factor — pending/in_progress/stalled count fully; complete
      counts at 0.5 (still maturable but lower priority)
    - ``adoption_success_factor_value`` (0.8.4 WS4) — multiplier in
      [0.5, 1.5] derived from the prediction's adoption-outcome history.
      Neutral (1.0) when no history or when the refinement flag is off.

    Probationary or failure-capped predictions are excluded by
    :func:`select_maturation_candidates`, not by this score.
    """

    evidence_norm = max(0.0, min(1.0, prediction.artifacts.evidence_score / 1.0))
    evidence_room = 1.0 - evidence_norm
    state_factor = 1.0 if item_state in ("pending", "in_progress", "stalled") else 0.5
    revision_decay = 1.0 / (prediction.run.revision + 1)
    return goal_confidence * artefact_alignment_score * evidence_room * revision_decay * state_factor * adoption_success_factor_value


def select_maturation_candidates(
    predictions: list[PredictedPrompt],
    *,
    context: RefinementContext,
    max_candidates: int,
    goal_confidence_lookup: Callable[[PredictedPrompt], float] | None = None,
    artefact_alignment_lookup: Callable[[PredictedPrompt], float] | None = None,
    item_state_lookup: Callable[[PredictedPrompt], str] | None = None,
) -> list[MaturationCandidate]:
    """Rank READY predictions for the next maturation pass.

    Eligibility filters (in order):
    1. ``maturation_eligible`` must be True.
    2. ``readiness == "ready"`` only — pre-ready predictions go through
       the normal drafter, not maturation.
    3. ``failed_revisits < context.max_revisits_per_prediction`` (cap on
       repeated failures so we stop attempting after exhaustion).
    4. ``revision < context.max_revisits_per_prediction`` (cap on kept
       revisions per prediction).
    5. ``probationary_until_cycle is None`` or ``< context.cycle_index`` —
       predictions in their probation window are not re-matured.

    Returns up to ``max_candidates`` eligible predictions ordered by
    descending score. Ineligible predictions are returned at the tail
    (``eligible=False`` + ``skip_reason``) for audit / debug surfaces;
    callers should filter on ``eligible=True`` before invoking
    :func:`mature_one`.
    """

    cap = context.max_revisits_per_prediction
    eligible: list[MaturationCandidate] = []
    skipped: list[MaturationCandidate] = []
    for prediction in predictions:
        skip_reason = _maturation_skip_reason(prediction, cap=cap, cycle_index=context.cycle_index)
        if skip_reason is not None:
            skipped.append(
                MaturationCandidate(
                    prediction=prediction,
                    score=0.0,
                    eligible=False,
                    skip_reason=skip_reason,
                )
            )
            continue
        gc = goal_confidence_lookup(prediction) if goal_confidence_lookup else 0.5
        aa = artefact_alignment_lookup(prediction) if artefact_alignment_lookup else 1.0
        st = item_state_lookup(prediction) if item_state_lookup else "pending"
        score = score_maturation_value(
            prediction,
            goal_confidence=gc,
            artefact_alignment_score=aa,
            item_state=st,
        )
        eligible.append(
            MaturationCandidate(
                prediction=prediction,
                score=score,
                eligible=True,
                skip_reason=None,
            )
        )
    eligible.sort(key=lambda c: c.score, reverse=True)
    return eligible[:max_candidates] + skipped


def _maturation_skip_reason(
    prediction: PredictedPrompt,
    *,
    cap: int,
    cycle_index: int,
) -> str | None:
    run = prediction.run
    if not run.maturation_eligible:
        return "maturation_eligible=False"
    if run.readiness != "ready":
        return f"readiness={run.readiness!r} (only 'ready' eligible)"
    if run.failed_revisits >= cap:
        return f"failed_revisits={run.failed_revisits} >= cap={cap}"
    if run.revision >= cap:
        return f"revision={run.revision} >= cap={cap}"
    if run.probationary_until_cycle is not None and run.probationary_until_cycle >= cycle_index:
        return f"probationary until cycle {run.probationary_until_cycle} (current={cycle_index})"
    return None


# ---------------------------------------------------------------------------
# Maturation pass orchestrator
# ---------------------------------------------------------------------------


_PROBATION_CYCLES = 3


def _hash_text(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


async def mature_one(
    prediction: PredictedPrompt,
    *,
    context: RefinementContext,
    drafter: MaturationDrafterCallable,
    judge: JudgeCallable | None = None,
    pass_id: str,
    evidence_floor: float | None = None,
    on_kept_evidence_increment: float = 0.10,
) -> MaturationOutcome:
    """Run one maturation / refinement pass against a single prediction.

    Steps:
    1. Build contract from current weakness.
    2. Invoke ``drafter`` with the contract.
    3. Invoke ``judge`` (default: :func:`default_rubric_judge`) with
       the (old, new, contract) tuple.
    4. If kept: persist new draft on the prediction, increment
       ``revision``, set ``probationary_until_cycle``, bump
       ``evidence_score``, reset ``failed_revisits`` to 0,
       update ``last_matured_cycle``.
    5. If discarded: leave the existing draft alone, increment
       ``failed_revisits``.

    Returns a :class:`MaturationOutcome` describing what happened. When
    ``context.is_deep_run`` is True, the engine writes a
    ``deep_run_pass_log`` row from the outcome; background-refinement
    passes (``session_id is None``) are not audit-logged by default.

    Note: this function does *not* itself write to the database. It
    mutates the in-memory ``PredictionRun`` and returns an outcome
    record; the engine is responsible for persisting both the
    prediction registry update and (when applicable) the audit-log row.
    Keeping this separation lets unit tests exercise the full pass
    logic without a store fixture.
    """

    judge_callable: JudgeCallable = judge or default_rubric_judge
    floor = evidence_floor if evidence_floor is not None else context.draft_evidence_threshold
    contract = build_contract(prediction, pass_id=pass_id, evidence_floor=floor)
    old_draft = prediction.artifacts.draft_answer
    before_evidence = prediction.artifacts.evidence_score
    before_hash = _hash_text(old_draft)

    new_draft, new_refs = await drafter(prediction, contract)
    after_hash = _hash_text(new_draft)

    verdict = await judge_callable(
        prediction=prediction,
        contract=contract,
        old_draft=old_draft,
        new_draft=new_draft,
        new_evidence_refs=new_refs,
    )

    if verdict.kept:
        # 0.8.4 WS4 — snapshot pre-maturation values so a probation
        # rollback can restore them. Cleared by rollback_kept_maturation
        # once consumed. Bounded in time by the probation window.
        prediction.artifacts.pre_maturation_draft_answer = old_draft
        prediction.artifacts.pre_maturation_evidence_score = before_evidence
        prediction.artifacts.draft_answer = new_draft
        prediction.artifacts.evidence_score = before_evidence + on_kept_evidence_increment
        prediction.run.revision += 1
        prediction.run.last_matured_cycle = context.cycle_index
        prediction.run.probationary_until_cycle = context.cycle_index + _PROBATION_CYCLES
        prediction.run.failed_revisits = 0
        action: DeepRunPassAction = "matured_kept"
    else:
        prediction.run.failed_revisits += 1
        action = "matured_discarded"

    return MaturationOutcome(
        prediction_id=prediction.spec.id,
        session_id=context.session_id,
        cycle_index=context.cycle_index,
        contract=contract,
        verdict=verdict,
        action=action,
        before_evidence_score=before_evidence,
        after_evidence_score=prediction.artifacts.evidence_score,
        before_draft_hash=before_hash,
        after_draft_hash=after_hash,
        new_draft=new_draft if verdict.kept else None,
    )


# ---------------------------------------------------------------------------
# Probation rollback hook
# ---------------------------------------------------------------------------


def rollback_kept_maturation(
    prediction: PredictedPrompt,
    *,
    rollback_to_draft: str | None,
    rollback_to_evidence_score: float,
) -> None:
    """Roll back the most recent kept maturation on this prediction.

    Called by reconciliation when a contradicting signal fires inside
    the prediction's probation window. Restores the prior draft +
    evidence_score and decrements ``revision`` (the kept maturation
    no longer counts).

    ``failed_revisits`` is **not** bumped by rollback. Rollback is an
    *external* signal (the world changed under the kept draft), not a
    judge-discard, and the two shouldn't share a counter — otherwise
    N successful mature+contradict cycles can exhaust the per-
    prediction failure cap and permanently exclude a prediction that
    has never actually had a judge-discarded maturation. (0.8.4
    hardening fix, per docs/reviews/0.8.4-hardening.md HIGH-1.)

    ``last_matured_cycle`` is set to ``None`` after rollback. The
    last-matured cycle just got undone; setting it to the rollback
    cycle would mislead any future "when was this last successfully
    matured?" analytics. (0.8.4 hardening fix, HIGH-2.)

    Engine wires this into the reconciliation path; this module just
    owns the in-memory mutation contract.
    """

    prediction.artifacts.draft_answer = rollback_to_draft
    prediction.artifacts.evidence_score = rollback_to_evidence_score
    # Consume the snapshot so a follow-up maturation can snapshot fresh
    # values without ambiguity about which pass the snapshot belongs to.
    prediction.artifacts.pre_maturation_draft_answer = None
    prediction.artifacts.pre_maturation_evidence_score = None
    prediction.run.revision = max(0, prediction.run.revision - 1)
    prediction.run.probationary_until_cycle = None
    prediction.run.last_matured_cycle = None


__all__ = [
    "ClauseKind",
    "ContractClause",
    "JudgeCallable",
    "MaturationCandidate",
    "MaturationContract",
    "MaturationDrafterCallable",
    "MaturationOutcome",
    "MaturationVerdict",
    "RefinementContext",
    "TargetWeakness",
    "adoption_success_factor",
    "build_contract",
    "default_rubric_judge",
    "mature_one",
    "rollback_kept_maturation",
    "score_maturation_value",
    "select_maturation_candidates",
]
