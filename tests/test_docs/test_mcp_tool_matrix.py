from __future__ import annotations

import re
from pathlib import Path

NEW_TOOL_NAMES = {
    "vaner.status",
    "vaner.suggest",
    "vaner.resolve",
    "vaner.expand",
    "vaner.search",
    "vaner.explain",
    "vaner.feedback",
    "vaner.warm",
    "vaner.inspect",
    "vaner.debug.trace",
}

FORBIDDEN_TOOL_PATTERNS = [
    re.compile(r"`legacy_get_context`"),
    re.compile(r"`legacy_precompute`"),
    re.compile(r"`legacy_get_metrics`"),
    re.compile(r'"name"\s*:\s*"legacy_get_context"'),
    re.compile(r'"name"\s*:\s*"legacy_precompute"'),
    re.compile(r'"name"\s*:\s*"legacy_get_metrics"'),
    re.compile(r"\blist_scenarios\b"),
    re.compile(r"\bget_scenario\b"),
    re.compile(r"\bexpand_scenario\b"),
    re.compile(r"\bcompare_scenarios\b"),
    re.compile(r"\breport_outcome\b"),
    re.compile(r"\bpin_scenario\b"),
    re.compile(r"\bmerge_prepared_context\b"),
]


def test_docs_do_not_reference_removed_mcp_tools() -> None:
    docs_root = Path(__file__).resolve().parents[3] / "vaner-docs"
    if not docs_root.exists():
        return
    external_patterns = [pattern for pattern in FORBIDDEN_TOOL_PATTERNS if "legacy_" in pattern.pattern]
    violations: list[str] = []
    for path in docs_root.glob("content/docs/**/*.mdx"):
        text = path.read_text(encoding="utf-8")
        for pattern in external_patterns:
            if pattern.search(text):
                violations.append(f"{path}: {pattern.pattern}")
    assert violations == []


def test_mcp_v2_tools_documented() -> None:
    """Every NEW_TOOL_NAME must be documented somewhere authoritative.

    docs.vaner.ai is the canonical reference; the README is a front-door
    only and intentionally does not enumerate the full tool surface
    (see PR #212). We check the in-repo migration doc plus the
    vaner-docs sibling checkout when present. CI environments that
    don't clone vaner-docs alongside this repo can satisfy the matrix
    via ``docs/mcp-migration.md`` alone.
    """

    root = Path(__file__).resolve().parents[2]
    targets: list[Path] = [
        root / "docs" / "mcp-migration.md",
    ]
    docs_root = root / "vaner-docs"
    if docs_root.exists():
        targets.extend(docs_root.glob("content/docs/**/*.mdx"))
    content = "\n".join(path.read_text(encoding="utf-8") for path in targets if path.exists())
    missing = [name for name in sorted(NEW_TOOL_NAMES) if name not in content]
    assert missing == [], (
        "Missing tool names from docs/mcp-migration.md (and vaner-docs if checked out): "
        f"{missing}. Add them to docs/mcp-migration.md or to the appropriate page under "
        "docs.vaner.ai (vaner-docs/content/docs/)."
    )


def test_source_and_docs_do_not_reference_removed_tools() -> None:
    root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    candidates: list[Path] = []
    candidates.extend((root / "src" / "vaner").glob("**/*.py"))
    candidates.extend((root / "docs").glob("**/*"))
    cursor_mcp = root / ".cursor" / "mcp.json"
    if cursor_mcp.exists():
        candidates.append(cursor_mcp)
    for static_path in [root / "README.md"]:
        if static_path.exists():
            candidates.append(static_path)
    source_patterns = [pattern for pattern in FORBIDDEN_TOOL_PATTERNS if "legacy_" in pattern.pattern]
    for path in candidates:
        if not path.is_file():
            continue
        if path.name == "mcp-migration.md":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in source_patterns:
            if pattern.search(text):
                violations.append(f"{path}: {pattern.pattern}")
    assert violations == []


def test_readme_does_not_reference_retired_init_flags() -> None:
    """The README must not reference flags that were removed from the CLI.

    Previously this test also asserted that ``--no-mcp`` and
    ``--interactive/--no-interactive`` were *present* in the README. The
    README is now a front-door (see PR #212) and intentionally does not
    enumerate every flag — that surface lives at
    docs.vaner.ai/getting-started. We keep the negative assertions so a
    stale flag can't sneak back in unnoticed.
    """

    root = Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert "--clients" not in readme
    assert "--accept-cloud-costs" not in readme
