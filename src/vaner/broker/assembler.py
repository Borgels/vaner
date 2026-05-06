# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from vaner.broker.answerable import build_answerable_briefing
from vaner.broker.compressor import compress_context
from vaner.broker.selector import score_artefact
from vaner.models.answerable import EvidenceAssemblyMode
from vaner.models.artefact import Artefact
from vaner.models.context import ContextPackage, ContextSelection
from vaner.models.context_preparation import PreparedContextDiagnostics
from vaner.models.decision import DecisionRecord, ScoreFactor, SelectionDecision
from vaner.policy.staleness import is_stale_timestamp


def _is_stale(artefact: Artefact, repo_root: Path | None, max_age_seconds: int) -> bool:
    if is_stale_timestamp(artefact.generated_at, max_age_seconds):
        return True
    if repo_root is None:
        return False
    source_abs = repo_root / artefact.source_path
    if source_abs.exists() and source_abs.stat().st_mtime > artefact.source_mtime:
        return True
    return False


def assemble_context_package(
    prompt: str,
    artefacts: list[Artefact],
    max_tokens: int,
    repo_root: Path | None = None,
    max_age_seconds: int = 3600,
    score_map: dict[str, float] | None = None,
    factor_map: dict[str, list[ScoreFactor]] | None = None,
    drop_reasons: dict[str, str] | None = None,
    provenance_by_key: dict[str, str] | None = None,
    conflict_notes_by_key: dict[str, list[str]] | None = None,
    conflict_notes: list[str] | None = None,
    evidence_assembly_mode: EvidenceAssemblyMode = "shadow",
    evidence_assembly_quality_bias: str = "protect_recall",
    evidence_assembly_cost_sensitivity: str = "balanced",
    prepared_context_diagnostics: PreparedContextDiagnostics | None = None,
    return_decision: bool = False,
) -> ContextPackage | tuple[ContextPackage, DecisionRecord]:
    resolved_score_map = score_map or {artefact.key: score_artefact(prompt, artefact) for artefact in artefacts}
    injected_context, token_map, used, kept_keys = compress_context(
        artefacts,
        max_tokens=max_tokens,
        score_by_key=resolved_score_map,
        query=prompt,
    )
    selection_decisions: list[SelectionDecision] = []
    context_selections: list[ContextSelection] = []
    for artefact in artefacts:
        stale = _is_stale(artefact, repo_root, max_age_seconds)
        factors = list((factor_map or {}).get(artefact.key, []))
        kept = artefact.key in kept_keys
        drop_reason = None if kept else (drop_reasons or {}).get(artefact.key, "budget")
        rationale_tokens = [factor.name for factor in factors if factor.contribution > 0]
        if not rationale_tokens:
            rationale_tokens = ["intent_ranked" if score_map is not None else "keyword_overlap"]
        rationale = "+".join(rationale_tokens[:4])
        decision = SelectionDecision(
            artefact_key=artefact.key,
            source_path=artefact.source_path,
            final_score=resolved_score_map.get(artefact.key, 0.0),
            stale=stale,
            token_count=token_map.get(artefact.key, 0),
            kept=kept,
            drop_reason=drop_reason,
            rationale=rationale,
            factors=factors,
        )
        selection_decisions.append(decision)
        if kept:
            context_selections.append(
                ContextSelection(
                    artefact_key=artefact.key,
                    source_path=artefact.source_path,
                    score=decision.final_score,
                    stale=decision.stale,
                    token_count=decision.token_count,
                    rationale=decision.rationale,
                    corpus_id=str(artefact.metadata.get("corpus_id", "default")),
                    privacy_zone=str(artefact.metadata.get("privacy_zone", "local")),
                    provenance=(provenance_by_key or {}).get(
                        artefact.key,
                        str(artefact.metadata.get("provenance", "prediction")),
                    ),
                    conflict_notes=list((conflict_notes_by_key or {}).get(artefact.key, [])),
                )
            )
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    context_package = ContextPackage(
        id=f"ctx_{prompt_hash[:12]}",
        prompt_hash=prompt_hash,
        assembled_at=time.time(),
        token_budget=max_tokens,
        token_used=used,
        selections=context_selections,
        injected_context=injected_context,
        conflict_notes=list(conflict_notes or []),
    )
    answerable = build_answerable_briefing(
        prompt,
        artefacts,
        repo_root=repo_root,
        max_tokens=max_tokens,
        channels_by_key={
            artefact.key: (provenance_by_key or {}).get(
                artefact.key,
                str(artefact.metadata.get("provenance", "vaner_resolve")),
            )
            for artefact in artefacts
        },
        conflict_notes=conflict_notes,
        assembly_mode=evidence_assembly_mode,
        quality_bias=evidence_assembly_quality_bias,
        cost_sensitivity=evidence_assembly_cost_sensitivity,
    )
    context_package.answerable_briefing = answerable
    context_package.answerability_metadata = answerable.metadata
    context_package.prepared_context_briefing = answerable.text
    context_package.prepared_context_mode = _prepared_context_mode(prepared_context_diagnostics, answerable.metadata.answerability)
    if prepared_context_diagnostics is not None:
        prepared_context_diagnostics.token_used = used
        prepared_context_diagnostics.truncation_risk = answerable.metadata.truncation_risk
    context_package.prepared_context_diagnostics = prepared_context_diagnostics
    decision_record = DecisionRecord(
        id=context_package.id,
        prompt=prompt,
        prompt_hash=prompt_hash,
        assembled_at=context_package.assembled_at,
        token_budget=max_tokens,
        token_used=used,
        selections=selection_decisions,
        prepared_context_diagnostics=prepared_context_diagnostics,
    )
    if return_decision:
        return context_package, decision_record
    return context_package


def _prepared_context_mode(diagnostics: PreparedContextDiagnostics | None, answerability: str) -> str:
    if diagnostics is None:
        return "answerable"
    need = diagnostics.profile.need
    if need == "implementation_support":
        return "implementation"
    if need == "research_mapping":
        return "research"
    if need == "creative_grounding":
        return "writing"
    if need == "decision_support":
        return "decision"
    if need == "conflict_resolution":
        return "conflict_resolution"
    if need == "absence_check":
        return "absence_check"
    if answerability in {"full", "weak", "conflict"}:
        return "answerable"
    return "planning"
