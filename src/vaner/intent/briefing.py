# SPDX-License-Identifier: Apache-2.0
"""WS9 — single canonical briefing assembler.

Prior to WS9, briefing text was assembled in three unrelated places:

- ``scenario_builder.build_scenarios`` (``prepared_context`` string-concat)
- ``engine._precompute_predicted_responses`` (draft-path briefing)
- ``engine._synthesise_briefing_from_scenarios`` (arc/history evidence-
  threshold path)
- ``mcp.server._build_adopt_resolution`` (token counts on adopt)

Each used a different template, none owned a real token count, and the
MCP resolve path never even consulted the briefings the engine had
already synthesised. :class:`BriefingAssembler` consolidates the logic
so exactly one module knows how to go from raw inputs → typed
:class:`Briefing`, and the transports (MCP, HTTP, engine.resolve_query)
just read off the structured output.

Token counting policy
---------------------

The assembler accepts an optional ``tokenizer: Callable[[str], int] |
None``. When provided (e.g. a real BPE tokenizer from the LLM client
being used) token_count is reported from that source. When absent the
assembler falls back to the four-chars-per-token heuristic matching
``vaner.clients.llm_response.approx_tokens``; a one-time warning is
logged so the operator knows approximations are in play.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from vaner.clients.llm_response import approx_tokens
from vaner.intent.prediction import PredictedPrompt

SectionKind = Literal[
    "summary",
    "file_content",
    "diff",
    "draft_response",
    "related",
    "provenance",
]


@dataclass(frozen=True, slots=True)
class BriefingSection:
    """One labelled chunk of a briefing.

    ``evidence_refs`` carries the provenance handle for whatever
    underlies this section — scenario_ids, artefact keys, or file
    paths — so downstream consumers can cite it without re-parsing the
    text.
    """

    kind: SectionKind
    title: str
    content: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Briefing:
    """A typed, provenance-tracked briefing.

    ``text`` is the flat rendering used by transports that still need a
    single string. ``sections`` is the structured view for consumers
    that want to reason about which part came from where (e.g. the
    cockpit's per-section reveal). ``token_count`` is the real count
    when a tokenizer was supplied and the four-char heuristic otherwise.
    """

    text: str
    sections: list[BriefingSection]
    token_count: int
    provenance: list[str]
    evidence_score: float


_log = logging.getLogger(__name__)


class BriefingAssembler:
    """The only place in Vaner that assembles briefing text.

    Construct once per engine lifetime; call the ``from_*`` builders on
    each briefing event. The assembler holds the optional tokenizer and
    the approximation-warning latch.
    """

    _approximation_warned = False

    def __init__(self, tokenizer: Callable[[str], int] | None = None) -> None:
        self._tokenizer = tokenizer
        # 0.8.6 WS4 — preferred artefact-template ids derived from the
        # active work-style mix. Updated each cycle by the engine via
        # :meth:`set_preferred_templates`. The assembler ranks registered
        # templates by this preference when an artefact-template registry
        # is consulted (forward hook — no-op until the registry lands in
        # a later WS). Empty tuple is the neutral default.
        self._preferred_artefact_templates: tuple[str, ...] = ()

    def set_preferred_templates(self, template_ids: tuple[str, ...]) -> None:
        """Set the preferred artefact-template ids for tie-breaking.

        0.8.6 WS4. Called once per engine cycle with the work-style
        mix's preferred templates (e.g. ``("decision_memo", "risk_list")``
        for planning). When the briefing assembler consults an artefact-
        template registry it should prefer ids in this tuple. Templates
        that aren't registered are silently ignored — the assembler falls
        back to its existing template selection.
        """

        self._preferred_artefact_templates = tuple(template_ids)

    @property
    def preferred_artefact_templates(self) -> tuple[str, ...]:
        """The preferred artefact-template ids (0.8.6 WS4 forward hook)."""

        return self._preferred_artefact_templates

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._tokenizer is not None:
            try:
                return int(self._tokenizer(text))
            except Exception:
                # Tokenizer can fail mid-cycle (e.g. transient provider
                # unavailability). Fall through to the heuristic.
                pass
        if not type(self)._approximation_warned:
            type(self)._approximation_warned = True
            _log.warning(
                "BriefingAssembler: no tokenizer available — reporting approximate "
                "token counts via the four-char heuristic. Token budgets will be "
                "imprecise until a structured LLM client is wired in."
            )
        return approx_tokens(text)

    # -----------------------------------------------------------------------
    # Builders
    # -----------------------------------------------------------------------

    def from_prediction(self, prompt: PredictedPrompt) -> Briefing:
        """Build a :class:`Briefing` for a :class:`PredictedPrompt` whose
        artifacts already carry ``prepared_briefing`` + (optionally) a
        ``draft_answer`` + scenarios.

        This is the adopt path — the MCP ``vaner.predictions.adopt``
        handler used to hand-roll the evidence and token counts; now it
        calls this method and reads the structured output.
        """
        spec = prompt.spec
        artifacts = prompt.artifacts
        sections: list[BriefingSection] = []

        # Summary header — the prediction's label + confidence + evidence score.
        summary_content = f"Predicted next step: {spec.label}\n{spec.description or ''}".strip()
        sections.append(
            BriefingSection(
                kind="summary",
                title=spec.label,
                content=summary_content,
                evidence_refs=[spec.id],
            )
        )

        # Prepared briefing content as the evidence section when present.
        if artifacts.prepared_briefing:
            sections.append(
                BriefingSection(
                    kind="file_content",
                    title="Prepared evidence",
                    content=artifacts.prepared_briefing,
                    evidence_refs=list(artifacts.scenario_ids),
                )
            )

        # Draft if one was speculated.
        if artifacts.draft_answer:
            sections.append(
                BriefingSection(
                    kind="draft_response",
                    title="Speculative draft",
                    content=artifacts.draft_answer,
                    evidence_refs=[spec.id],
                )
            )

        # Provenance footer — always the last section, always cites at
        # minimum the prediction id + source. Downstream consumers use
        # this to render "cited from" rows.
        provenance_lines = [
            f"- source: {spec.source}",
            f"- anchor: {spec.anchor}",
            f"- confidence: {spec.confidence:.2f}",
            f"- scenarios_complete: {prompt.run.scenarios_complete}",
            f"- evidence_score: {artifacts.evidence_score:.2f}",
        ]
        if artifacts.file_content_hashes:
            provenance_lines.append(f"- file_content_hashes: {len(artifacts.file_content_hashes)} path(s)")
        sections.append(
            BriefingSection(
                kind="provenance",
                title="Provenance",
                content="\n".join(provenance_lines),
                evidence_refs=[spec.id],
            )
        )

        text = _render(sections)
        provenance = [spec.id, *artifacts.scenario_ids]
        return Briefing(
            text=text,
            sections=sections,
            token_count=self._count_tokens(text),
            provenance=provenance,
            evidence_score=float(artifacts.evidence_score),
        )

    def from_paths(
        self,
        *,
        label: str,
        description: str,
        paths: list[str],
        source: str,
        anchor: str,
        confidence: float,
        scenarios_complete: int = 0,
        evidence_score: float = 0.0,
    ) -> Briefing:
        """Build a lightweight briefing directly from a path list.

        Replaces ``engine._synthesise_briefing_from_scenarios`` — the
        evidence-threshold drafting path used to string-concat its own
        template. Same inputs, same output shape, routed through the
        assembler so the rendering is consistent with everything else.
        """
        deduped_paths: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if path and path not in seen:
                seen.add(path)
                deduped_paths.append(path)

        path_lines = "\n".join(f"- {p}" for p in deduped_paths[:10]) or "(no paths)"
        sections: list[BriefingSection] = [
            BriefingSection(
                kind="summary",
                title=label,
                content=f"Predicted next step: {label}\n{description}".strip(),
                evidence_refs=[anchor],
            ),
            BriefingSection(
                kind="related",
                title="Relevant files",
                content=path_lines,
                evidence_refs=deduped_paths[:10],
            ),
            BriefingSection(
                kind="provenance",
                title="Provenance",
                content=(
                    f"- source: {source}\n"
                    f"- anchor: {anchor}\n"
                    f"- confidence: {confidence:.2f}\n"
                    f"- scenarios_complete: {scenarios_complete}\n"
                    f"- evidence_score: {evidence_score:.2f}"
                ),
                evidence_refs=[anchor],
            ),
        ]
        text = _render(sections)
        return Briefing(
            text=text,
            sections=sections,
            token_count=self._count_tokens(text),
            provenance=[anchor, *deduped_paths[:10]],
            evidence_score=float(evidence_score),
        )

    def from_artefacts(
        self,
        *,
        intent: str,
        artefacts: list[Any],
        paths: list[str] | None = None,
    ) -> Briefing:
        """Build a briefing from a list of :class:`Artefact` plus the intent string.

        Used by the draft path in ``_precompute_predicted_responses`` to
        synthesise a briefing from recent file summaries when no
        prediction-owned briefing exists yet.
        """
        summary_lines: list[str] = []
        evidence_refs: list[str] = []
        for artefact in artefacts[:6]:
            content = getattr(artefact, "content", "") or ""
            source_path = getattr(artefact, "source_path", "") or ""
            key = getattr(artefact, "key", source_path or "")
            snippet = content[:400].replace("\n", " ").strip()
            if source_path:
                summary_lines.append(f"- {source_path}: {snippet}")
                evidence_refs.append(source_path)
            else:
                summary_lines.append(f"- {key}: {snippet}")
                evidence_refs.append(str(key))

        body = "\n".join(summary_lines) or "(no artefact summaries available)"
        sections: list[BriefingSection] = [
            BriefingSection(
                kind="summary",
                title=intent,
                content=f"Intent: {intent}",
                evidence_refs=[intent],
            ),
            BriefingSection(
                kind="file_content",
                title="File summaries",
                content=body,
                evidence_refs=evidence_refs,
            ),
        ]
        if paths:
            sections.append(
                BriefingSection(
                    kind="related",
                    title="Relevant paths",
                    content="\n".join(f"- {p}" for p in paths[:10]),
                    evidence_refs=list(paths[:10]),
                )
            )
        text = _render(sections)
        return Briefing(
            text=text,
            sections=sections,
            token_count=self._count_tokens(text),
            provenance=evidence_refs,
            evidence_score=0.0,
        )


def _render(sections: list[BriefingSection]) -> str:
    """Render a list of sections into a single Markdown-style briefing string."""
    parts: list[str] = []
    for section in sections:
        parts.append(f"## {section.title}")
        parts.append(section.content.strip())
        parts.append("")
    return "\n".join(parts).strip()
