from __future__ import annotations

from pathlib import Path

from vaner.intent.frontier import ExplorationFrontier
from vaner.intent.skills_discovery import SkillRef


def test_seed_from_skills_admits_scenarios(temp_repo: Path) -> None:
    frontier = ExplorationFrontier(min_priority=0.01)
    skills = [
        SkillRef(
            name="review",
            path=temp_repo / ".cursor" / "skills" / "review" / "SKILL.md",
            tags=["review"],
            triggers=["tests/*.py"],
            vaner_kind="research",
        )
    ]
    available = ["tests/test_api.py", "src/app.py"]
    admitted = frontier.seed_from_skills(skills, available)
    assert admitted == 1
    popped = frontier.pop()
    assert popped is not None
    assert popped.source == "skill"
    assert "tests/test_api.py" in popped.file_paths
