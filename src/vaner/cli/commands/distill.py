from __future__ import annotations

import re
from pathlib import Path

from vaner.models.decision import DecisionRecord, SelectionDecision


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "skill"


def _kept(selections: list[SelectionDecision]) -> list[SelectionDecision]:
    return [selection for selection in selections if selection.kept]


def render_skill_markdown(decision: DecisionRecord, *, name: str) -> str:
    kept = _kept(decision.selections)
    tags = sorted({"vaner", "distilled", decision.cache_tier or "miss"})
    triggers = [entry.source_path for entry in kept[:8] if entry.source_path]
    lines = [
        "---",
        f"name: {name}",
        f"description: Distilled from Vaner decision {decision.id}.",
        f"tags: [{', '.join(tags)}]",
        "vaner:",
        "  kind: change",
        "x-vaner-managed: true",
    ]
    if triggers:
        lines.append(f"triggers: [{', '.join(triggers)}]")
    lines.extend(
        [
            "---",
            "",
            "Use this skill when a task resembles the original successful Vaner decision.",
            "",
            "Procedure:",
            f"1. Start from the same intent shape as: `{decision.prompt.strip()}`.",
            "2. Prioritize these artefacts first:",
        ]
    )
    if kept:
        for selection in kept[:8]:
            lines.append(f"   - `{selection.source_path or selection.artefact_key}` ({selection.rationale or 'relevant context'})")
    else:
        lines.append("   - No explicit artefacts were retained in the decision.")
    lines.extend(
        [
            "3. Query Vaner MCP tools (`list_scenarios` then `get_scenario`) before coding.",
            "4. Call `report_outcome` after finishing so Vaner can learn from the run.",
            "",
            f"Source decision id: `{decision.id}`",
        ]
    )
    return "\n".join(lines) + "\n"


def distill_skill_file(
    repo_root: Path,
    decision_id: str,
    *,
    out_dir: Path | None = None,
    skill_name: str | None = None,
    force: bool = False,
) -> Path:
    decision = DecisionRecord.read_by_id(repo_root, decision_id)
    if decision is None:
        raise FileNotFoundError(f"Decision not found: {decision_id}")
    name = skill_name or _slugify(decision.prompt[:80])
    target_dir = out_dir or (repo_root / ".cursor" / "skills" / "vaner-distilled" / name)
    target_path = target_dir / "SKILL.md"
    if target_path.exists() and not force:
        raise FileExistsError(f"{target_path} already exists; pass --force to overwrite")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path.write_text(render_skill_markdown(decision, name=name), encoding="utf-8")
    return target_path
