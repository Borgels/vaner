# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

GuidanceVariant = Literal["canonical", "weak", "strong"]

_GUIDANCE_DIR = Path(__file__).parent
_FILENAMES: dict[str, str] = {
    "canonical": "vaner_guidance_v1.md",
    "weak": "vaner_guidance_v1_weak.md",
    "strong": "vaner_guidance_v1_strong.md",
}


@dataclass(frozen=True)
class GuidanceDoc:
    variant: GuidanceVariant
    frontmatter: dict[str, Any]
    body: str
    source_path: Path

    @property
    def version(self) -> int:
        return int(self.frontmatter.get("guidance_version", 1))

    @property
    def minimum_vaner_version(self) -> str:
        return str(self.frontmatter.get("minimum_vaner_version", ""))

    @property
    def recommended_tools(self) -> list[str]:
        value = self.frontmatter.get("recommended_tools", [])
        if isinstance(value, list):
            return [str(v) for v in value]
        return []

    @property
    def updated_at(self) -> str:
        return str(self.frontmatter.get("updated_at", ""))

    def as_text(self) -> str:
        """Return the body only (no frontmatter), for direct prompt injection."""
        return self.body.strip()

    def as_markdown(self) -> str:
        """Return frontmatter + body (original file contents)."""
        return self.source_path.read_text(encoding="utf-8")

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "version": self.version,
            "minimum_vaner_version": self.minimum_vaner_version,
            "recommended_tools": self.recommended_tools,
            "updated_at": self.updated_at,
            "body": self.as_text(),
        }


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Minimal YAML-frontmatter parser (subset: scalars + simple lists).

    Avoids adding a PyYAML dependency. Supports:
      - `key: value` scalars (str/int)
      - `key:` followed by `  - item` lists
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    raw = parts[1].strip("\n")
    body = parts[2].lstrip("\n")

    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            current_list_key = None
            continue
        if line.startswith("  - ") and current_list_key is not None:
            data.setdefault(current_list_key, []).append(line[4:].strip())
            continue
        if ":" not in line:
            current_list_key = None
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            # list follows
            current_list_key = key
            data[key] = []
            continue
        current_list_key = None
        # scalars: int, float, bool, str
        if value.isdigit():
            data[key] = int(value)
        elif value.lower() in ("true", "false"):
            data[key] = value.lower() == "true"
        else:
            data[key] = value.strip("'\"")
    return data, body


def load_guidance(variant: GuidanceVariant = "canonical") -> GuidanceDoc:
    if variant not in _FILENAMES:
        raise ValueError(f"unknown guidance variant: {variant!r}")
    path = _GUIDANCE_DIR / _FILENAMES[variant]
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)
    return GuidanceDoc(
        variant=variant,
        frontmatter=frontmatter,
        body=body,
        source_path=path,
    )


def current_version() -> int:
    return load_guidance("canonical").version


def available_variants() -> list[GuidanceVariant]:
    return ["canonical", "weak", "strong"]
