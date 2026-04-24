# SPDX-License-Identifier: Apache-2.0
"""WS9 — BriefingAssembler tests.

Contract: one place, one rendering, deterministic token counts, structured
provenance.
"""

from __future__ import annotations

from dataclasses import dataclass

from vaner.intent.briefing import Briefing, BriefingAssembler, BriefingSection
from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)


def _prompt(
    *,
    label: str = "Work on parser",
    briefing: str | None = "## Context\nparser.py touches tokenize()",
    draft: str | None = "TENTATIVE: check tokenize boundaries",
) -> PredictedPrompt:
    spec = PredictionSpec(
        id=prediction_id("arc", "understanding", label),
        label=label,
        description="Suspected next move based on arc model",
        source="arc",
        anchor="understanding",
        confidence=0.72,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    run = PredictionRun(weight=0.6, token_budget=2048, scenarios_complete=3, updated_at=0.0)
    artifacts = PredictionArtifacts(
        scenario_ids=["scen-1", "scen-2"],
        evidence_score=2.5,
        draft_answer=draft,
        prepared_briefing=briefing,
        file_content_hashes={"src/parser.py": "hash-v1"},
    )
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


def test_from_prediction_includes_summary_evidence_draft_and_provenance():
    assembler = BriefingAssembler()
    briefing = assembler.from_prediction(_prompt())

    kinds = [s.kind for s in briefing.sections]
    assert "summary" in kinds
    assert "file_content" in kinds
    assert "draft_response" in kinds
    # Provenance is ALWAYS the last section when present.
    assert briefing.sections[-1].kind == "provenance"

    # The provenance text names the source + confidence + file-hash count.
    provenance_content = briefing.sections[-1].content
    assert "source: arc" in provenance_content
    assert "0.72" in provenance_content
    assert "file_content_hashes" in provenance_content


def test_from_prediction_omits_draft_when_absent():
    assembler = BriefingAssembler()
    briefing = assembler.from_prediction(_prompt(draft=None))
    kinds = [s.kind for s in briefing.sections]
    assert "draft_response" not in kinds


def test_token_count_uses_tokenizer_when_supplied():
    # Custom tokenizer returns a constant so we can assert it was used.
    assembler = BriefingAssembler(tokenizer=lambda text: 42)
    briefing = assembler.from_prediction(_prompt())
    assert briefing.token_count == 42


def test_token_count_falls_back_to_approximation_when_tokenizer_fails():
    def _broken(_text: str) -> int:
        raise RuntimeError("tokenizer offline")

    assembler = BriefingAssembler(tokenizer=_broken)
    briefing = assembler.from_prediction(_prompt())
    # Falls back to the 4-char heuristic, which must produce a positive
    # count for non-empty text.
    assert briefing.token_count > 0


def test_from_paths_matches_legacy_synthesise_briefing_shape():
    assembler = BriefingAssembler()
    briefing = assembler.from_paths(
        label="Explore parser",
        description="Likely drill into tokenize",
        paths=["src/parser.py", "src/tokens.py", "src/parser.py"],  # dup to test dedup
        source="arc",
        anchor="understanding",
        confidence=0.6,
        scenarios_complete=2,
        evidence_score=1.5,
    )
    # Relevant files is a dedicated section.
    related_sections = [s for s in briefing.sections if s.kind == "related"]
    assert len(related_sections) == 1
    assert "src/parser.py" in related_sections[0].content
    # Dedup: parser.py shows up once, not twice.
    assert related_sections[0].content.count("src/parser.py") == 1


def test_from_paths_handles_empty_path_list():
    assembler = BriefingAssembler()
    briefing = assembler.from_paths(
        label="Generic",
        description="",
        paths=[],
        source="history",
        anchor="debugging",
        confidence=0.3,
    )
    related = [s for s in briefing.sections if s.kind == "related"][0]
    assert "(no paths)" in related.content


def test_from_artefacts_builds_summary_plus_file_section():
    @dataclass
    class _Artefact:
        content: str
        source_path: str
        key: str

    artefacts = [
        _Artefact(content="def parse(x):\n  return x" * 30, source_path="src/parser.py", key="file:src/parser.py"),
        _Artefact(content="class Tokenizer: pass", source_path="src/tokens.py", key="file:src/tokens.py"),
    ]
    assembler = BriefingAssembler()
    briefing = assembler.from_artefacts(
        intent="Understand parser",
        artefacts=artefacts,
        paths=["src/parser.py"],
    )
    sections = {s.kind: s for s in briefing.sections}
    assert "summary" in sections
    assert "file_content" in sections
    assert "related" in sections
    assert "src/parser.py" in sections["file_content"].content
    assert briefing.provenance == ["src/parser.py", "src/tokens.py"]


def test_render_produces_markdown_with_titles():
    sections = [
        BriefingSection(kind="summary", title="Hello", content="World"),
        BriefingSection(kind="provenance", title="Provenance", content="- source: x"),
    ]
    # _render is internal but we validate via from_prediction too.
    from vaner.intent.briefing import _render

    text = _render(sections)
    assert "## Hello" in text
    assert "## Provenance" in text
    assert "World" in text


def test_briefing_is_frozen_dataclass():
    """Regression: Briefing is frozen so downstream consumers can safely
    hash/cache it."""
    b = Briefing(text="", sections=[], token_count=0, provenance=[], evidence_score=0.0)
    try:
        b.text = "x"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Briefing should be frozen")
