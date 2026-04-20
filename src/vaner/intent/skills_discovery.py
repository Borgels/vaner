from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

try:
    import yaml  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    yaml = None


@dataclass(slots=True)
class SkillRef:
    name: str
    path: Path
    description: str = ""
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    vaner_kind: str | None = None

    def as_signal_payload(self, repo_root: Path) -> dict[str, object]:
        try:
            rel_path = str(self.path.relative_to(repo_root))
            privacy_zone = "project_local"
        except ValueError:
            rel_path = str(self.path)
            privacy_zone = "external"
        return {
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "triggers": self.triggers,
            "vaner_kind": self.vaner_kind,
            "path": rel_path,
            "privacy_zone": privacy_zone,
            "corpus_id": "repo",
        }


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    stripped = text.lstrip()
    if not stripped.startswith("---\n"):
        return None, text
    rest = stripped[4:]
    marker = "\n---\n"
    idx = rest.find(marker)
    if idx < 0:
        return None, text
    return rest[:idx], rest[idx + len(marker) :]


def _str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _parse_frontmatter(frontmatter: str | None) -> dict[str, object]:
    if not frontmatter:
        return {}
    if yaml is not None:
        parsed = yaml.safe_load(frontmatter)
        if isinstance(parsed, dict):
            return parsed
    fallback: dict[str, object] = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key:
            fallback[key] = value.strip().strip("\"'")
    return fallback


def _resolve_roots(repo_root: Path, roots: list[str], *, include_global: bool) -> list[Path]:
    resolved: list[Path] = []
    for root in roots:
        candidate = Path(root).expanduser()
        if not candidate.is_absolute():
            candidate = (repo_root / candidate).resolve()
        if not include_global:
            try:
                candidate.relative_to(repo_root)
            except ValueError:
                continue
        resolved.append(candidate)
    return resolved


def discover_skills(
    repo_root: Path,
    *,
    include_global: bool = False,
    skill_roots: list[str] | None = None,
) -> list[SkillRef]:
    roots = skill_roots or [".cursor/skills", ".claude/skills", "skills"]
    found: dict[str, SkillRef] = {}
    for root in _resolve_roots(repo_root, roots, include_global=include_global):
        if not root.exists():
            continue
        for path in root.rglob("SKILL.md"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            frontmatter, _ = _split_frontmatter(text)
            parsed = _parse_frontmatter(frontmatter)
            name = str(parsed.get("name", "")).strip() or path.parent.name
            description = str(parsed.get("description", "")).strip()
            tags = _str_list(parsed.get("tags"))
            triggers = _str_list(parsed.get("triggers"))
            vaner_kind: str | None = None
            vaner = parsed.get("vaner")
            if isinstance(vaner, dict):
                kind = vaner.get("kind")
                if isinstance(kind, str) and kind.strip():
                    vaner_kind = kind.strip()
            ref = SkillRef(
                name=name,
                path=path.resolve(),
                description=description,
                tags=tags,
                triggers=triggers,
                vaner_kind=vaner_kind,
            )
            found[str(ref.path)] = ref
    return sorted(found.values(), key=lambda item: str(item.path))


def match_skill_paths(skill: SkillRef, available_paths: list[str], *, limit: int = 10) -> list[str]:
    matched: list[str] = []
    for trigger in skill.triggers:
        if any(ch in trigger for ch in "*?[]"):
            matched.extend(path for path in available_paths if fnmatch(path, trigger))
        else:
            token = trigger.strip().lower()
            if token:
                matched.extend(path for path in available_paths if token in path.lower())
        if len(matched) >= limit:
            break
    if not matched:
        for token in [skill.name, *skill.tags]:
            norm = token.strip().lower()
            if norm:
                matched.extend(path for path in available_paths if norm in path.lower())
            if len(matched) >= limit:
                break
    return sorted(dict.fromkeys(matched))[:limit]
