# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.models.decision import DecisionRecord, PredictionLink, SelectionDecision


def render_json(record: DecisionRecord) -> str:
    return record.to_json()


def _render_prediction(link: PredictionLink | None) -> list[str]:
    if link is None:
        return []
    lines = [f"    - predicted by: {link.source}"]
    if link.scenario_question:
        confidence = f" (conf {link.confidence:.2f})" if link.confidence is not None else ""
        lines.append(f'      "{link.scenario_question}"{confidence}')
    elif link.confidence is not None:
        lines.append(f"      confidence: {link.confidence:.2f}")
    if link.scenario_rationale:
        lines.append(f"      rationale: {link.scenario_rationale}")
    return lines


def _selection_line(selection: SelectionDecision) -> str:
    status = "kept" if selection.kept else f"dropped ({selection.drop_reason or 'unknown'})"
    rationale = selection.rationale or "n/a"
    return (
        f"  {selection.source_path:<40} "
        f"score {selection.final_score:>5.2f}  "
        f"{status}, {selection.token_count} tokens  "
        f"{rationale}"
    )


def render_human(record: DecisionRecord, verbose: bool = False) -> str:
    similarity = f" (sim {record.partial_similarity:.2f})" if record.cache_tier == "partial_hit" else ""
    lines = [
        f"prompt: {record.prompt}",
        f"decision: {record.id}  cache={record.cache_tier}{similarity}  tokens {record.token_used}/{record.token_budget}",
        "",
        "selections:",
    ]
    for selection in record.selections:
        lines.append(_selection_line(selection))
        lines.extend(_render_prediction(record.prediction_links.get(selection.artefact_key)))
        if verbose and selection.factors:
            for factor in selection.factors:
                lines.append(
                    f"    - factor {factor.name}: {factor.contribution:.2f}"
                    + (f" ({factor.detail})" if factor.detail else "")
                )
    prewarmed = sum(1 for selection in record.selections if selection.kept and selection.artefact_key in record.prediction_links)
    kept = sum(1 for selection in record.selections if selection.kept)
    lines.extend(
        [
            "",
            f"{prewarmed} artefacts linked to predictions out of {kept} kept selections.",
        ]
    )
    if record.notes:
        lines.append("notes:")
        lines.extend([f"  - {note}" for note in record.notes])
    return "\n".join(lines)
