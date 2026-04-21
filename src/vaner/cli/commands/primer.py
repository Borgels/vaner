# SPDX-License-Identifier: Apache-2.0
"""Primer injection: install Vaner usage guidance into each MCP client.

MCP wiring alone is not enough. The model also needs explicit guidance about
when and how to use Vaner, otherwise usage stays weak and inconsistent. This
module owns the per-client primer surfaces and a non-destructive merge
primitive so that `vaner init` can install guidance alongside the MCP config.

A single canonical primer lives at
``src/vaner/defaults/prompts/agent-primer.md``. Per-client surfaces wrap it
with whatever the client expects (plain markdown, `.mdc` with frontmatter,
and so on).

Existing files are respected: the primer is written into a clearly delimited
block (``<!-- vaner-primer:start v=VERSION -->…<!-- vaner-primer:end -->``)
that can be replaced in place on re-init without touching content outside
the markers. Files with no existing block get the block appended; users who
want custom wording can simply edit outside the markers.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

PrimerAction = Literal["added", "updated", "skipped", "unsupported", "failed"]

PRIMER_BLOCK_START_PREFIX = "<!-- vaner-primer:start"
PRIMER_BLOCK_END = "<!-- vaner-primer:end -->"
# Cursor rules use HTML-hostile frontmatter; use a different marker there.
CURSOR_BLOCK_START_PREFIX = "{/* vaner-primer:start"
CURSOR_BLOCK_END = "{/* vaner-primer:end */}"


class PrimerScope(StrEnum):
    """Where a primer file lives relative to the user."""

    REPO = "repo"
    USER = "user"


@dataclass(slots=True)
class PrimerResult:
    """Outcome of a single per-client primer write."""

    client_id: str
    scope: PrimerScope
    path: Path | None
    action: PrimerAction
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PrimerSurface:
    """Declarative description of where and how to write a client's primer."""

    # Resolves the target file for a scope, or None if the scope is not supported.
    path: Callable[[Path, PrimerScope], Path | None]
    # Renders the canonical primer body into the file's native format.
    render: Callable[[str, str], str]  # (body, version) -> full-file body
    # "block" merges into a possibly-existing file; "replace" owns the file.
    strategy: Literal["block", "replace"]
    # Which delimiter pair the block strategy uses.
    marker_style: Literal["html", "cursor"] = "html"


def _home() -> Path:
    return Path.home()


# ---------------------------------------------------------------------------
# Canonical primer source
# ---------------------------------------------------------------------------


def load_canonical_primer() -> str:
    """Read the canonical primer text shipped with the package."""
    package_root = Path(__file__).resolve().parents[2]
    primer_path = package_root / "defaults" / "prompts" / "agent-primer.md"
    return primer_path.read_text(encoding="utf-8").rstrip() + "\n"


def primer_version() -> str:
    """Version string baked into primer block markers.

    Tracks ``vaner.__version__`` so re-runs after a Vaner upgrade refresh the
    block in place without surprising users.
    """
    try:
        from vaner import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive
        return "0"


# ---------------------------------------------------------------------------
# Delimited block merge primitive
# ---------------------------------------------------------------------------


def apply_primer_block(
    content: str,
    body: str,
    *,
    version: str,
    marker_style: Literal["html", "cursor"] = "html",
) -> tuple[str, PrimerAction]:
    """Insert or replace the Vaner primer block inside ``content``.

    The block is delimited by ``<!-- vaner-primer:start v=VERSION -->`` and
    ``<!-- vaner-primer:end -->`` (or the cursor-compatible equivalents).
    Returns the new content and an action describing what changed.
    """
    body = body.rstrip()
    if marker_style == "cursor":
        start_prefix = CURSOR_BLOCK_START_PREFIX
        end_marker = CURSOR_BLOCK_END
        start_line = f"{start_prefix} v={version} */}}"
    else:
        start_prefix = PRIMER_BLOCK_START_PREFIX
        end_marker = PRIMER_BLOCK_END
        start_line = f"{start_prefix} v={version} -->"

    new_block = f"{start_line}\n{body}\n{end_marker}"

    pattern = re.compile(
        re.escape(start_prefix) + r".*?" + re.escape(end_marker),
        flags=re.DOTALL,
    )
    match = pattern.search(content)
    if match is not None:
        existing = match.group(0)
        if existing == new_block:
            return content, "skipped"
        replaced = content[: match.start()] + new_block + content[match.end() :]
        return replaced, "updated"

    if content.strip():
        merged = content.rstrip("\n") + "\n\n" + new_block + "\n"
        return merged, "added"
    return new_block + "\n", "added"


# ---------------------------------------------------------------------------
# Per-client render functions
# ---------------------------------------------------------------------------


def _render_plain_block(body: str, version: str) -> str:
    """Render a markdown file wrapping the primer in an HTML-comment block."""
    return apply_primer_block("", body, version=version, marker_style="html")[0]


def _render_cursor_mdc(body: str, version: str) -> str:
    """Render a Cursor ``.mdc`` rules file with the required frontmatter.

    Cursor rules files use YAML frontmatter followed by Markdown. We own the
    file entirely (``strategy='replace'``) so frontmatter is static.
    """
    frontmatter = f"---\ndescription: Vaner usage primer (v={version})\nalwaysApply: true\n---\n"
    return frontmatter + "\n" + body.rstrip() + "\n"


# ---------------------------------------------------------------------------
# Per-client path resolvers
# ---------------------------------------------------------------------------


def _path_claude_code(repo_root: Path, scope: PrimerScope) -> Path | None:
    if scope == PrimerScope.REPO:
        return repo_root / ".claude" / "CLAUDE.md"
    return _home() / ".claude" / "CLAUDE.md"


def _path_cursor(repo_root: Path, scope: PrimerScope) -> Path | None:
    if scope != PrimerScope.REPO:
        return None
    return repo_root / ".cursor" / "rules" / "vaner.mdc"


def _path_vscode_copilot(repo_root: Path, scope: PrimerScope) -> Path | None:
    if scope != PrimerScope.REPO:
        return None
    return repo_root / ".github" / "copilot-instructions.md"


def _path_codex(repo_root: Path, scope: PrimerScope) -> Path | None:
    if scope != PrimerScope.REPO:
        return None
    return repo_root / "AGENTS.md"


def _path_cline(repo_root: Path, scope: PrimerScope) -> Path | None:
    if scope != PrimerScope.REPO:
        return None
    return repo_root / ".clinerules"


def _path_continue(repo_root: Path, scope: PrimerScope) -> Path | None:
    if scope != PrimerScope.REPO:
        return None
    return repo_root / ".continue" / "rules" / "vaner.md"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


PRIMER_SURFACES: dict[str, PrimerSurface] = {
    "claude-code": PrimerSurface(
        path=_path_claude_code,
        render=_render_plain_block,
        strategy="block",
        marker_style="html",
    ),
    "cursor": PrimerSurface(
        path=_path_cursor,
        render=_render_cursor_mdc,
        strategy="replace",
        marker_style="html",  # unused for replace-strategy surfaces
    ),
    "vscode-copilot": PrimerSurface(
        path=_path_vscode_copilot,
        render=_render_plain_block,
        strategy="block",
        marker_style="html",
    ),
    "codex-cli": PrimerSurface(
        path=_path_codex,
        render=_render_plain_block,
        strategy="block",
        marker_style="html",
    ),
    "cline": PrimerSurface(
        path=_path_cline,
        render=_render_plain_block,
        strategy="block",
        marker_style="html",
    ),
    "continue": PrimerSurface(
        path=_path_continue,
        render=_render_plain_block,
        strategy="replace",
        marker_style="html",
    ),
}


# ---------------------------------------------------------------------------
# Per-client writer
# ---------------------------------------------------------------------------


def write_primer_for_client(
    client_id: str,
    repo_root: Path,
    *,
    scope: PrimerScope = PrimerScope.REPO,
    body: str | None = None,
    version: str | None = None,
    dry_run: bool = False,
) -> PrimerResult:
    """Write the Vaner primer into ``client_id``'s primer surface.

    Non-destructive: existing files outside the primer block are preserved,
    and identical-content writes are reported as ``skipped`` so repeated
    ``vaner init`` invocations are idempotent.
    """
    surface = PRIMER_SURFACES.get(client_id)
    if surface is None:
        return PrimerResult(
            client_id=client_id,
            scope=scope,
            path=None,
            action="unsupported",
        )

    target = surface.path(repo_root, scope)
    if target is None:
        return PrimerResult(
            client_id=client_id,
            scope=scope,
            path=None,
            action="unsupported",
        )

    resolved_body = body if body is not None else load_canonical_primer()
    resolved_version = version or primer_version()
    rendered = surface.render(resolved_body, resolved_version)

    try:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
    except OSError as exc:
        return PrimerResult(
            client_id=client_id,
            scope=scope,
            path=target,
            action="failed",
            error=str(exc),
        )

    if surface.strategy == "replace":
        if existing == rendered:
            return PrimerResult(client_id=client_id, scope=scope, path=target, action="skipped")
        action: PrimerAction = "updated" if existing else "added"
        new_content = rendered
    else:
        new_content, action = apply_primer_block(existing, resolved_body, version=resolved_version, marker_style=surface.marker_style)
        if new_content == existing:
            action = "skipped"

    if action == "skipped" or dry_run:
        return PrimerResult(client_id=client_id, scope=scope, path=target, action=action)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_content, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive I/O guard
        return PrimerResult(
            client_id=client_id,
            scope=scope,
            path=target,
            action="failed",
            error=str(exc),
        )
    return PrimerResult(client_id=client_id, scope=scope, path=target, action=action)


def write_primers(
    client_ids: list[str],
    repo_root: Path,
    *,
    include_user_scope: bool = False,
    dry_run: bool = False,
) -> list[PrimerResult]:
    """Write the primer for each client in ``client_ids``.

    Repo-scoped surfaces are written unconditionally. User-scoped surfaces
    (currently only ``~/.claude/CLAUDE.md``) are written when
    ``include_user_scope=True``.
    """
    body = load_canonical_primer()
    version = primer_version()
    results: list[PrimerResult] = []
    for client_id in client_ids:
        results.append(
            write_primer_for_client(
                client_id,
                repo_root,
                scope=PrimerScope.REPO,
                body=body,
                version=version,
                dry_run=dry_run,
            )
        )
        if include_user_scope and client_id == "claude-code":
            results.append(
                write_primer_for_client(
                    client_id,
                    repo_root,
                    scope=PrimerScope.USER,
                    body=body,
                    version=version,
                    dry_run=dry_run,
                )
            )
    return results
