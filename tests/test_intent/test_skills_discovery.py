from __future__ import annotations

from pathlib import Path

from vaner.intent.skills_discovery import discover_skills


def test_discover_skills_reads_frontmatter(temp_repo: Path) -> None:
    skill_file = temp_repo / ".cursor" / "skills" / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(
        """---
name: demo-skill
description: Demo skill
tags: [python, tests]
triggers: ["src/*.py"]
vaner:
  kind: debug
---
body
""",
        encoding="utf-8",
    )
    skills = discover_skills(temp_repo, include_global=False)
    assert len(skills) == 1
    assert skills[0].name == "demo-skill"
    assert skills[0].vaner_kind == "debug"
    assert "python" in skills[0].tags


def test_discover_skills_excludes_global_when_disabled(temp_repo: Path) -> None:
    external_root = temp_repo.parent / "global-skills"
    external_skill = external_root / "g" / "SKILL.md"
    external_skill.parent.mkdir(parents=True, exist_ok=True)
    external_skill.write_text("---\nname: g\n---\n", encoding="utf-8")
    skills = discover_skills(
        temp_repo,
        include_global=False,
        skill_roots=[str(external_root)],
    )
    assert skills == []
