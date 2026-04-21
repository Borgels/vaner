# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from vaner.models.scenario import Scenario


def memory_dir(repo_root: Path) -> Path:
    d = repo_root / ".vaner" / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_log(
    repo_root: Path,
    *,
    tool: str,
    label: str,
    decision_id: str | None,
    provenance_mode: str | None,
    memory_state: str | None = None,
) -> None:
    """Append an inspectability trace line.

    This log is an inspectability trace over MCP operations; it is intentionally
    NOT the semantic memory layer.
    """
    stamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    decision = decision_id or "-"
    mode = provenance_mode or "-"
    state = memory_state or "-"
    line = f"## [{stamp}] {tool} | {label or '-'} | {decision} | {mode} | {state}\n"
    (memory_dir(repo_root) / "log.md").open("a", encoding="utf-8").write(line)


def write_index(repo_root: Path, scenarios: list[Scenario]) -> Path:
    """Regenerate a human-friendly scenario index (inspectability only)."""
    path = memory_dir(repo_root) / "index.md"
    grouped: dict[str, list[Scenario]] = {"trusted": [], "candidate": [], "stale": [], "demoted": []}
    for scenario in scenarios:
        grouped.setdefault(scenario.memory_state, []).append(scenario)
    lines = ["# Vaner Scenario Index", ""]
    for state in ("trusted", "candidate", "stale", "demoted"):
        lines.append(f"## {state.title()}")
        for s in sorted(grouped.get(state, []), key=lambda item: item.score, reverse=True):
            lines.append(
                f"- `{s.id}` ({s.kind}, score={s.score:.2f}, conf={s.memory_confidence:.2f}, "
                f"evidence={len(s.evidence)}, outcome={s.last_outcome or '-'}) - entities: {', '.join(s.entities[:8])}"
            )
        lines.append("")
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def tail_log(repo_root: Path, n: int = 5) -> list[str]:
    path = memory_dir(repo_root) / "log.md"
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("## [")]
    return lines[-n:]
