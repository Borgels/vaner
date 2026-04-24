# SPDX-License-Identifier: Apache-2.0
"""WS1 — intent-bearing artefact classifier.

Two-stage classifier per 0.8.2 release spec §7 step 2:

1. **Structural heuristics** (this module) — cheap, deterministic, runs on
   every candidate. Reads checkbox density, heading shape, list counts,
   status-verb hits, planning-keyword hits, and title-hint keywords. Emits
   a :class:`ClassificationResult` with ``(is_intent_bearing, kind,
   confidence, reasoning)`` and a human-readable trace.

2. **LLM fallback** — only invoked when structural confidence falls in the
   configurable ambiguous band (default ``0.35–0.65``) *and* a callable is
   injected. The LLM adjudicates in a structured prompt; parse failures
   leave the structural result in place. Connectors do **not** call LLMs
   themselves; the pipeline does.

The classifier is intentionally **conservative** — the 0.8.2 ship gate is
≥0.85 precision and ≥0.70 recall per domain. False positives (noisy prose
classified as plans) degrade user trust more than false negatives, so
ambiguous cases err on the ``False`` side when no LLM is available.

Keyword lists are generalized: no dev-centric assumptions. Writers,
researchers, operators, and support workers all see their artefact shapes
here (chapter outlines, reading plans, meeting agendas, runbooks,
playbooks). Per-domain recall parity is enforced downstream by the labeled
fixture corpus (WS1-J) and the spec §14.1 cross-domain gate.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import get_args

from vaner.intent.adapter import RawArtefact
from vaner.intent.artefacts import IntentArtefactKind

# Callable the pipeline injects for LLM-adjudicated classification. Matches
# the engine-wide ``LLMCallable`` shape (prompt: str → str) so any LLM client
# the caller already owns can be passed here without adapter boilerplate.
ClassifierLLMCallable = Callable[[str], Awaitable[str]]

# --------------------------------------------------------------------------
# Tunable constants
# --------------------------------------------------------------------------

# Minimum words for a document to be considered plan-sized. Below this a
# ``- [ ] buy milk`` scratchpad slips through as noise — we'd rather miss it
# than pollute the artefact store. The penalty is skipped when the document
# has a strong structural plan-shape (see ``has_plan_shape`` below), so a
# tight 25-word release checklist is not treated as too-short.
MIN_WORDS = 20

# Upper word bound. Above this we're almost certainly looking at a long doc
# (a book chapter, a full design spec, a research paper) rather than an
# intent-bearing artefact. The signal is noisy — it's used as a penalty, not
# a hard cutoff.
PLAN_WORD_CEILING = 10_000
LONG_DOC_WORD_CEILING = 30_000

# Structural-confidence bands. Anything inside the ambiguous band is a
# candidate for LLM adjudication; anything outside takes the structural
# verdict directly.
DEFAULT_AMBIGUOUS_BAND: tuple[float, float] = (0.35, 0.65)

# Minimum score to be classified as intent-bearing when an LLM is not
# available. Above-band structural confidence lands here directly; the
# ambiguous band falls back to ``False`` when the LLM is absent.
# Tuned against the WS1-J labeled fixture corpus (see tests/fixtures/
# intent_artefacts/) to hit the spec §14.1 per-domain precision ≥0.80 /
# cross-domain recall ≥0.70 gates.
STRUCTURAL_ACCEPT_THRESHOLD = 0.55

_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s*\[[ xX\-/!\?~]\]\s*", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S", re.MULTILINE)
_ORDERED_LIST_RE = re.compile(r"^\s*\d+[.)]\s+\S", re.MULTILINE)
_UNORDERED_LIST_RE = re.compile(r"^\s*[-*+]\s+\S", re.MULTILINE)
_KANBAN_COLUMN_RE = re.compile(r"^\s*(todo|doing|in\s*progress|blocked|done|backlog|up\s*next)\s*[:\-]", re.IGNORECASE | re.MULTILINE)

# Status verbs that signal work-tracking content. Case-insensitive match.
_STATUS_VERBS: tuple[str, ...] = (
    "TODO",
    "DONE",
    "WIP",
    "FIXME",
    "XXX",
    "in progress",
    "blocked",
    "stalled",
    "complete",
    "completed",
    "pending",
    "next up",
    "next steps",
    "upcoming",
    "deferred",
)

# Planning / outlining vocabulary that crosses domains. Writers say "scene"
# and "chapter"; researchers say "reading plan"; ops say "runbook"; devs
# say "sprint" and "milestone". One shared keyword set, weighted lightly so
# no single hit dominates.
_PLANNING_KEYWORDS: tuple[str, ...] = (
    # Universal
    "plan",
    "roadmap",
    "agenda",
    "outline",
    "milestone",
    "milestones",
    "phase",
    "phases",
    "next steps",
    "next up",
    "dependencies",
    "blockers",
    # Writers
    "chapter",
    "chapters",
    "scene",
    "scenes",
    "revision",
    "draft",
    "outline",
    # Researchers
    "reading plan",
    "literature review",
    "hypothesis",
    "research plan",
    "question list",
    "synthesis",
    # Operators / support
    "runbook",
    "playbook",
    "checklist",
    "escalation",
    "escalate",
    "rotation",
    "on-call",
    "oncall",
    "incident",
    "postmortem",
    "post-mortem",
    "outage",
    "triage",
    "failover",
    "recovery",
    "rollback",
    "mitigation",
    "response plan",
    # Developers
    "sprint",
    "epic",
    "backlog",
    "release plan",
    "implementation plan",
)

# Filename / title hints. If the connector supplies ``title_hint`` (usually
# from the filename or document title), these short tokens boost the
# structural score because the user has already labeled the doc.
_TITLE_KEYWORDS: tuple[str, ...] = (
    "plan",
    "roadmap",
    "agenda",
    "outline",
    "todo",
    "to-do",
    "to_do",
    "tasks",
    "checklist",
    "brief",
    "playbook",
    "runbook",
    "revision",
    "chapters",
    "reading",
    "milestones",
    "next-steps",
    "release",
    "sprint",
    "scratch",
)

# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StructuralSignals:
    """Observable structural features of a raw artefact.

    Pure derived data — given the same ``RawArtefact`` you get the same
    ``StructuralSignals`` back. Persisted alongside the classification
    result so ``vaner.explain`` can surface *why* the classifier voted the
    way it did.
    """

    total_lines: int
    total_words: int
    checkbox_count: int
    checkbox_density: float
    heading_count: int
    max_heading_depth: int
    ordered_list_count: int
    unordered_list_count: int
    kanban_column_count: int
    status_verb_hits: int
    planning_keyword_hits: int
    title_keyword_hits: int
    matched_title_keywords: tuple[str, ...] = ()
    matched_planning_keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Output of the classifier.

    - ``is_intent_bearing`` — the ingestion pipeline's accept/reject bit.
    - ``kind`` — best-guess :class:`IntentArtefactKind`. ``None`` when the
      artefact was rejected.
    - ``confidence`` — 0.0–1.0 posterior. Persisted on
      :class:`IntentArtefact`.
    - ``reasoning`` — human-readable trace of the structural signals that
      led to the verdict. Shown via ``vaner.artefacts.inspect``.
    - ``used_llm_fallback`` — whether the LLM adjudicated this record.
    - ``signals`` — the raw :class:`StructuralSignals` the verdict was
      computed from. Kept for debugging and for the WS1 test suite.
    """

    is_intent_bearing: bool
    kind: IntentArtefactKind | None
    confidence: float
    reasoning: str
    used_llm_fallback: bool = False
    signals: StructuralSignals | None = None


# --------------------------------------------------------------------------
# Structural signal extraction
# --------------------------------------------------------------------------


def _count_matches(pattern: re.Pattern[str], text: str) -> int:
    return sum(1 for _ in pattern.finditer(text))


def _contains_any_ci(text_lower: str, keywords: tuple[str, ...]) -> tuple[int, tuple[str, ...]]:
    hits = 0
    matched: list[str] = []
    for kw in keywords:
        if kw.lower() in text_lower:
            hits += 1
            matched.append(kw)
    return hits, tuple(matched)


def extract_structural_signals(raw: RawArtefact) -> StructuralSignals:
    """Pure extraction of structural features from raw text.

    Cheap — regex-only, no tokenization beyond whitespace splits. Safe to
    call on every candidate in a discovery pass.
    """

    text = raw.text or ""
    lines = text.splitlines()
    total_lines = len([line for line in lines if line.strip()])
    total_words = sum(1 for _ in re.finditer(r"\b\w+\b", text))

    checkbox_count = _count_matches(_CHECKBOX_RE, text)
    heading_matches = list(_HEADING_RE.finditer(text))
    heading_count = len(heading_matches)
    max_heading_depth = max((len(m.group(1)) for m in heading_matches), default=0)
    ordered_list_count = _count_matches(_ORDERED_LIST_RE, text)
    unordered_list_count = _count_matches(_UNORDERED_LIST_RE, text)
    kanban_column_count = _count_matches(_KANBAN_COLUMN_RE, text)

    text_lower = text.lower()
    status_verb_hits, _ = _contains_any_ci(text_lower, _STATUS_VERBS)
    planning_keyword_hits, matched_planning = _contains_any_ci(text_lower, _PLANNING_KEYWORDS)

    title_lower = (raw.title_hint or "").lower()
    title_hit_count, matched_title = _contains_any_ci(title_lower, _TITLE_KEYWORDS)

    checkbox_density = checkbox_count / total_lines if total_lines else 0.0

    return StructuralSignals(
        total_lines=total_lines,
        total_words=total_words,
        checkbox_count=checkbox_count,
        checkbox_density=checkbox_density,
        heading_count=heading_count,
        max_heading_depth=max_heading_depth,
        ordered_list_count=ordered_list_count,
        unordered_list_count=unordered_list_count,
        kanban_column_count=kanban_column_count,
        status_verb_hits=status_verb_hits,
        planning_keyword_hits=planning_keyword_hits,
        title_keyword_hits=title_hit_count,
        matched_title_keywords=matched_title,
        matched_planning_keywords=matched_planning[:5],
    )


# --------------------------------------------------------------------------
# Structural verdict
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _ScoreTrace:
    """Accumulator for the score + reasoning trace."""

    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def add(self, delta: float, note: str) -> None:
        if delta == 0.0:
            return
        self.score += delta
        sign = "+" if delta > 0 else "−"
        self.reasons.append(f"{sign}{abs(delta):.2f} {note}")

    def clamp(self) -> float:
        return max(0.0, min(1.0, self.score))


def _infer_kind(signals: StructuralSignals, *, title_hint: str) -> IntentArtefactKind:
    """Choose the most likely :class:`IntentArtefactKind`.

    Precedence: explicit title tokens > structural dominance > fallback
    (``plan``). Kept deliberately simple — the LLM fallback can refine when
    called.
    """

    title_lower = title_hint.lower()

    # Explicit title-token overrides.
    if any(tok in title_lower for tok in ("runbook", "playbook")):
        return "runbook"
    if "checklist" in title_lower:
        return "checklist"
    if "brief" in title_lower:
        return "brief"
    if "agenda" in title_lower or "queue" in title_lower:
        return "queue"
    if "outline" in title_lower or "chapters" in title_lower:
        return "outline"
    if any(tok in title_lower for tok in ("todo", "to-do", "tasks")):
        return "task_list"
    if "roadmap" in title_lower or "milestones" in title_lower:
        return "plan"

    # Structural dominance.
    if signals.kanban_column_count >= 2:
        return "queue"
    if signals.ordered_list_count >= 5 and signals.checkbox_count < 3:
        return "runbook"
    if signals.checkbox_count >= 3 and signals.heading_count >= 2:
        # Structured plan: hierarchical headings with enough checked items
        # under them. Distinct from a flat checklist.
        return "plan"
    if signals.checkbox_density >= 0.15 and signals.max_heading_depth <= 1:
        return "checklist" if signals.checkbox_count < 10 else "task_list"
    if signals.max_heading_depth >= 2 and signals.checkbox_count < 3:
        return "outline"
    if signals.total_words < 200 and signals.planning_keyword_hits >= 1 and signals.checkbox_count < 3:
        return "brief"

    return "plan"


def classify_structural(raw: RawArtefact) -> ClassificationResult:
    """Run the structural heuristic classifier.

    Returns a :class:`ClassificationResult` whose ``confidence`` field is the
    raw structural score. The pipeline then decides whether to accept
    directly, reject directly, or escalate to the LLM fallback via
    :func:`classify`.
    """

    signals = extract_structural_signals(raw)
    trace = _ScoreTrace()

    # Short documents with clear plan shape (a tight release checklist, a
    # quick outline, a small task list) are legitimate — only penalise
    # shortness when there is also no structural evidence. A 20-word doc
    # with 6 checkboxes is a checklist; a 20-word doc with no structure is
    # scratch.
    has_plan_shape = (
        signals.checkbox_count >= 2
        or signals.ordered_list_count >= 4
        or signals.kanban_column_count >= 2
        or (signals.max_heading_depth >= 2 and signals.heading_count >= 3)
    )
    if signals.total_words < MIN_WORDS and not has_plan_shape:
        trace.add(-0.60, f"too short ({signals.total_words} words)")
    if signals.total_words > LONG_DOC_WORD_CEILING:
        trace.add(-0.30, f"very long doc ({signals.total_words} words)")
    elif signals.total_words > PLAN_WORD_CEILING and signals.checkbox_density < 0.02:
        trace.add(-0.20, "long doc with no task structure")

    # Positive structural signals.
    if signals.checkbox_density >= 0.40 and signals.checkbox_count >= 3:
        # Checkbox-dominant shape — most non-empty lines are checkboxes.
        # That alone IS the task-list signal, regardless of vocabulary,
        # so it clears the accept threshold without needing title hints.
        trace.add(0.60, f"checkbox-dominant ({signals.checkbox_density:.2f})")
    elif signals.checkbox_density >= 0.20:
        trace.add(0.45, f"checkbox-dense ({signals.checkbox_density:.2f})")
    elif signals.checkbox_density >= 0.10:
        trace.add(0.30, f"many checkboxes ({signals.checkbox_density:.2f})")
    elif signals.checkbox_count >= 3:
        trace.add(0.15, f"some checkboxes ({signals.checkbox_count})")

    if signals.status_verb_hits >= 4:
        trace.add(0.20, f"status verbs ×{signals.status_verb_hits}")
    elif signals.status_verb_hits >= 2:
        trace.add(0.10, f"status verbs ×{signals.status_verb_hits}")

    if signals.title_keyword_hits >= 1:
        matched = ", ".join(signals.matched_title_keywords)
        trace.add(0.30, f"title hint ({matched})")

    if signals.planning_keyword_hits >= 7:
        trace.add(0.30, f"rich planning vocabulary ×{signals.planning_keyword_hits}")
    elif signals.planning_keyword_hits >= 4:
        trace.add(0.20, f"planning vocabulary ×{signals.planning_keyword_hits}")
    elif signals.planning_keyword_hits >= 2:
        trace.add(0.10, f"planning vocabulary ×{signals.planning_keyword_hits}")

    # Hierarchical outline shape. Deep outlines with many headings are
    # the strongest non-checkbox signal for intent (chapter outlines,
    # paper outlines, syllabi, scene lists, comparison tables).
    if signals.max_heading_depth >= 2 and signals.heading_count >= 7:
        trace.add(0.40, f"extensive outline ({signals.heading_count} headings)")
    elif signals.max_heading_depth >= 3 and signals.heading_count >= 4:
        trace.add(0.30, f"deep outline (depth {signals.max_heading_depth}, {signals.heading_count} headings)")
    elif signals.max_heading_depth >= 2 and signals.heading_count >= 5:
        trace.add(0.20, f"hierarchical outline ({signals.heading_count} headings)")
    elif signals.max_heading_depth >= 2 and signals.heading_count >= 3:
        trace.add(0.10, "hierarchical headings")

    if signals.ordered_list_count >= 5 and signals.checkbox_count < 3:
        # Procedural steps with no checkboxes are the runbook / recipe
        # shape — weight this higher when they dominate the document, so
        # a small runbook clears the accept threshold without needing
        # title-keyword support.
        if signals.ordered_list_count >= signals.heading_count and signals.total_words < 800:
            trace.add(0.45, f"procedural runbook shape ×{signals.ordered_list_count}")
        else:
            trace.add(0.20, f"procedural numbered steps ×{signals.ordered_list_count}")

    # Bullet-dominant structures under hierarchical headings (flat queues,
    # agendas, OKR lists). When many bullets live under ≥2 h2-level
    # headings, that is explicit structural intent.
    if signals.unordered_list_count >= 5 and signals.max_heading_depth >= 2 and signals.checkbox_count < 3:
        trace.add(0.25, f"bullet-dominant structure ×{signals.unordered_list_count}")

    if signals.kanban_column_count >= 2:
        trace.add(0.20, f"kanban columns ×{signals.kanban_column_count}")

    # Narrative brief shape — very short intent-bearing documents that
    # declare direction in prose rather than structure. Requires title or
    # strong planning vocabulary to distinguish from generic prose.
    if (
        signals.total_words < 150
        and signals.checkbox_count == 0
        and signals.ordered_list_count < 3
        and (signals.title_keyword_hits >= 1 or signals.planning_keyword_hits >= 3)
    ):
        trace.add(0.35, "narrative brief shape")

    # Small size-normalized negative pressure when *nothing* plan-shaped
    # appears. Prevents arbitrary prose from sneaking in on planning-keyword
    # hits alone.
    if (
        signals.checkbox_count == 0
        and signals.ordered_list_count < 3
        and signals.max_heading_depth < 2
        and signals.title_keyword_hits == 0
        and signals.planning_keyword_hits < 3
    ):
        trace.add(-0.30, "no plan-shaped structure")

    confidence = trace.clamp()
    is_intent_bearing = confidence >= STRUCTURAL_ACCEPT_THRESHOLD

    kind: IntentArtefactKind | None
    kind = _infer_kind(signals, title_hint=raw.title_hint or raw.source_uri) if is_intent_bearing else None

    reasoning = "; ".join(trace.reasons) if trace.reasons else "no signal"

    return ClassificationResult(
        is_intent_bearing=is_intent_bearing,
        kind=kind,
        confidence=confidence,
        reasoning=reasoning,
        used_llm_fallback=False,
        signals=signals,
    )


# --------------------------------------------------------------------------
# LLM fallback
# --------------------------------------------------------------------------

_LLM_PROMPT = """You are classifying a document to decide whether it is an \
*intent-bearing artefact* — a plan, outline, task list, brief, queue, \
checklist, or runbook that declares the user's intended direction or \
upcoming work. You are NOT classifying general prose, source code, \
documentation, or reference material.

Document title hint: {title}

Document content (truncated to 4000 characters):
---
{body}
---

Return STRICTLY one line of JSON with three fields:
{{"is_intent_bearing": <true|false>, \
"kind": <one of: plan, outline, task_list, brief, queue, checklist, runbook, \
or null if not intent-bearing>, \
"confidence": <float between 0.0 and 1.0>}}

Examples of intent-bearing artefacts across domains:
- software engineering: release plan, sprint checklist, architectural roadmap
- writing: chapter outline, revision checklist, scene list, draft plan
- research: reading plan, question list, synthesis outline, experiment plan
- operations: runbook, escalation playbook, on-call checklist, meeting agenda
- support: response playbook, ticket triage queue, incident runbook

NOT intent-bearing (return false):
- source code files, configuration files
- published documentation or tutorials
- reference material, API docs, design specs (finished docs, not plans)
- logs, transcripts, chat exports
- raw notes without stated intent

Reply with ONLY the JSON. No prose before or after."""


def _parse_llm_response(response: str) -> tuple[bool, IntentArtefactKind | None, float] | None:
    """Parse the LLM's single-line JSON. Returns ``None`` on malformed
    output so the caller can fall back to the structural result."""

    import json

    stripped = response.strip()
    # Tolerate models that wrap output in ```json fences
    if stripped.startswith("```"):
        fenced = stripped.strip("`")
        first_nl = fenced.find("\n")
        if first_nl != -1:
            fenced = fenced[first_nl + 1 :]
        if fenced.endswith("```"):
            fenced = fenced[:-3]
        stripped = fenced.strip()
    # Take just the first line with a ``{`` — some models include preamble.
    brace = stripped.find("{")
    if brace == -1:
        return None
    end = stripped.find("}", brace)
    if end == -1:
        return None
    try:
        parsed = json.loads(stripped[brace : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    is_intent_bearing = bool(parsed.get("is_intent_bearing", False))
    kind_raw = parsed.get("kind")
    kind: IntentArtefactKind | None = None
    if isinstance(kind_raw, str) and kind_raw in get_args(IntentArtefactKind):
        kind = kind_raw  # type: ignore[assignment]
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return is_intent_bearing, kind, confidence


async def classify(
    raw: RawArtefact,
    *,
    llm: ClassifierLLMCallable | None = None,
    ambiguous_band: tuple[float, float] = DEFAULT_AMBIGUOUS_BAND,
) -> ClassificationResult:
    """Two-stage classifier.

    Returns the structural result directly when its confidence is outside
    the ambiguous band, or when ``llm`` is ``None``. Otherwise asks the LLM
    to adjudicate; a malformed response leaves the structural result in
    place with ``used_llm_fallback=True`` noted in the reasoning.
    """

    structural = classify_structural(raw)

    low, high = ambiguous_band
    within_band = low <= structural.confidence <= high
    if not within_band or llm is None:
        return structural

    body = raw.text
    if len(body) > 4000:
        body = body[:4000] + "\n…[truncated]"
    prompt = _LLM_PROMPT.format(title=raw.title_hint or raw.source_uri, body=body)

    try:
        response = await llm(prompt)
    except Exception as exc:  # noqa: BLE001 — classifier must not throw
        reasoning = f"{structural.reasoning}; llm error: {type(exc).__name__}"
        return ClassificationResult(
            is_intent_bearing=structural.is_intent_bearing,
            kind=structural.kind,
            confidence=structural.confidence,
            reasoning=reasoning,
            used_llm_fallback=True,
            signals=structural.signals,
        )

    parsed = _parse_llm_response(response)
    if parsed is None:
        reasoning = f"{structural.reasoning}; llm response unparseable"
        return ClassificationResult(
            is_intent_bearing=structural.is_intent_bearing,
            kind=structural.kind,
            confidence=structural.confidence,
            reasoning=reasoning,
            used_llm_fallback=True,
            signals=structural.signals,
        )

    is_intent_bearing, kind, llm_confidence = parsed
    # Blend the two confidences conservatively: structural keeps half the
    # weight so a confidently-wrong LLM can't flip a clearly-structural case.
    blended = 0.5 * structural.confidence + 0.5 * llm_confidence
    final_confidence = max(0.0, min(1.0, blended))

    if is_intent_bearing and kind is None:
        # LLM said "yes" but didn't pick a kind — fall back to structural.
        kind = structural.kind or _infer_kind(
            structural.signals or extract_structural_signals(raw),
            title_hint=raw.title_hint or raw.source_uri,
        )

    reasoning = f"{structural.reasoning}; llm→ ({is_intent_bearing}, {kind}, {llm_confidence:.2f})"

    return ClassificationResult(
        is_intent_bearing=is_intent_bearing,
        kind=kind if is_intent_bearing else None,
        confidence=final_confidence,
        reasoning=reasoning,
        used_llm_fallback=True,
        signals=structural.signals,
    )
