# SPDX-License-Identifier: Apache-2.0
"""Skill / workflow / prompt installer per MCP client.

Layer 3 of the four-layer leverage stack documented at
docs.vaner.ai/integrations/client-capabilities. Mirrors the design of
:mod:`vaner.cli.commands.primer`: a per-client surface with a path
resolver and a render function, plus a non-destructive writer.

Why per-client adapters instead of one canonical file
-----------------------------------------------------
The clients converge on the *idea* of "agent-callable subroutine" but
diverge on:

* **File location** — ``~/.claude/skills/...``, ``.cursor/skills/...``,
  ``~/.codex/skills/...``, ``.continue/prompts/...``,
  ``.clinerules/workflows/...``, ``.windsurf/workflows/...``,
  ``.roo/commands/...``.
* **File format** — Agent Skills (Claude Code, Cursor, Codex CLI, VS
  Code 1.108+) all share the ``SKILL.md + frontmatter`` standard.
  Continue uses prompts (``.md`` + ``invokable: true`` frontmatter).
  Cline + Windsurf use workflows (filename-as-command markdown).
  Roo uses slash commands (markdown + ``description`` frontmatter,
  agent-invocable via the ``run_slash_command`` tool).
* **Invocation model** — Agent-Skills clients auto-load by metadata
  match. Continue/Cline/Windsurf are user-invoked only. Roo can be
  agent-invoked via ``run_slash_command``.

So we ship the same essential body (when to feedback, how to call
``vaner.feedback``) and adapt the wrapping per client.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

SkillAction = Literal["added", "updated", "skipped", "unsupported", "failed"]

SKILL_NAME = "vaner-feedback"
SKILL_DESCRIPTION = "Report scenario outcomes back to Vaner after completing a task."


class SkillScope(StrEnum):
    """Where the skill file lives relative to the user."""

    REPO = "repo"
    USER = "user"


@dataclass(slots=True)
class SkillResult:
    """Outcome of a single per-client skill write."""

    client_id: str
    scope: SkillScope
    path: Path | None
    action: SkillAction
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SkillSurface:
    """Declarative description of where and how to write a client's skill."""

    # Resolves the target file for a scope, or None if not supported.
    path: Callable[[Path, SkillScope], Path | None]
    # Renders the canonical skill body (without frontmatter) into the
    # file's native format.
    render: Callable[[str, str], str]  # (body, version) -> full file content


# ---------------------------------------------------------------------------
# Canonical skill source — read once, cached
# ---------------------------------------------------------------------------


_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL_SKILL_PATH = _PACKAGE_ROOT / "defaults" / "skills" / "vaner-feedback" / "SKILL.md"

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", flags=re.DOTALL)


def load_canonical_skill_body() -> str:
    """Strip the canonical SKILL.md's frontmatter, leaving just the body.

    The Agent-Skills frontmatter (``name``, ``description``, ``tags``,
    ``vaner:``, ``x-vaner-managed``) is the contract Claude Code /
    Cursor / Codex CLI honor; other clients want a different
    frontmatter shape, so each renderer assembles their own.
    """

    text = _CANONICAL_SKILL_PATH.read_text(encoding="utf-8")
    return _FRONTMATTER_RE.sub("", text, count=1).lstrip()


def load_canonical_skill_full() -> str:
    """Return the full canonical SKILL.md including its Agent-Skills
    frontmatter — used unmodified for Claude Code, Cursor, Codex CLI."""

    return _CANONICAL_SKILL_PATH.read_text(encoding="utf-8")


def skill_version() -> str:
    """Track ``vaner.__version__`` so re-installs after upgrade refresh."""

    try:
        from vaner import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive
        return "0"


def _home() -> Path:
    return Path.home()


# ---------------------------------------------------------------------------
# Per-client render functions
# ---------------------------------------------------------------------------


def _render_agent_skill_md(_body: str, _version: str) -> str:
    """Claude Code / Cursor / Codex CLI — drop the canonical SKILL.md
    in unchanged. The Agent Skills frontmatter is the same standard
    across all three.

    ``_body`` and ``_version`` are accepted for signature parity with
    other renders but ignored — the canonical file already includes the
    full frontmatter.
    """

    return load_canonical_skill_full()


def _render_continue_prompt(body: str, version: str) -> str:
    """Continue ``.continue/prompts/<name>.md`` format.

    Continue prompts are user-invoked via ``/<name>`` in chat. The
    ``invokable: true`` flag is what makes the file appear as a slash
    command (see docs.continue.dev/customize/deep-dives/prompts).
    """

    frontmatter = f"---\nname: {SKILL_NAME}\ndescription: {SKILL_DESCRIPTION}\ninvokable: true\nx-vaner-managed: true v={version}\n---\n"
    return frontmatter + "\n" + body.rstrip() + "\n"


def _render_cline_workflow(body: str, version: str) -> str:
    """Cline workflow at ``.clinerules/workflows/<filename>.md``.

    The filename becomes the slash command (``/vaner-feedback.md`` →
    ``/vaner-feedback``). No formal frontmatter is required; we add a
    delimited header so re-runs can detect ownership.
    """

    title = f"# Vaner feedback (v={version})"
    header = f"<!-- x-vaner-managed: true v={version} -->\n{title}\n\n{SKILL_DESCRIPTION}\n"
    return header + "\n" + body.rstrip() + "\n"


def _render_windsurf_workflow(body: str, version: str) -> str:
    """Windsurf workflow at ``.windsurf/workflows/<name>.md``.

    Workflows are user-invoked from the Cascade panel (per
    docs.windsurf.com/windsurf/cascade/workflows). Windsurf renders the
    first ``# heading`` as the workflow title; we put the description on
    line 2 followed by the canonical body.
    """

    title = "# Vaner feedback"
    header = f"<!-- x-vaner-managed: true v={version} -->\n{title}\n\n{SKILL_DESCRIPTION}\n"
    return header + "\n" + body.rstrip() + "\n"


def _render_roo_slash_command(body: str, version: str) -> str:
    """Roo Code slash command at ``.roo/commands/<name>.md``.

    Frontmatter ``description`` populates the command palette; Roo's
    ``run_slash_command`` tool means the agent itself can invoke this
    programmatically (see docs.roocode.com/advanced-usage/available-tools/
    run-slash-command). That makes Roo the only manual-format client
    where Vaner gets agent-driven invocation back.
    """

    frontmatter = f"---\ndescription: {SKILL_DESCRIPTION}\nx-vaner-managed: true v={version}\n---\n"
    return frontmatter + "\n" + body.rstrip() + "\n"


# ---------------------------------------------------------------------------
# Per-client path resolvers
# ---------------------------------------------------------------------------


def _path_claude_code(_repo_root: Path, scope: SkillScope) -> Path | None:
    if scope != SkillScope.USER:
        # Claude Code skills are user-scoped by convention; the project
        # could place them at ``.claude/skills/`` too but the user-level
        # path is the more reliable surface, so we ship there.
        return None
    return _home() / ".claude" / "skills" / "vaner" / SKILL_NAME / "SKILL.md"


def _path_cursor(repo_root: Path, scope: SkillScope) -> Path | None:
    if scope != SkillScope.REPO:
        return None
    return repo_root / ".cursor" / "skills" / "vaner" / SKILL_NAME / "SKILL.md"


def _path_codex_cli(_repo_root: Path, scope: SkillScope) -> Path | None:
    if scope != SkillScope.USER:
        # Codex CLI also accepts ``.codex/skills/`` per-repo; we ship to
        # ``~/.codex/skills/`` so the skill is available across every repo
        # (Codex is typically used from any cwd).
        return None
    return _home() / ".codex" / "skills" / SKILL_NAME / "SKILL.md"


def _path_continue(repo_root: Path, scope: SkillScope) -> Path | None:
    if scope != SkillScope.REPO:
        return None
    return repo_root / ".continue" / "prompts" / f"{SKILL_NAME}.md"


def _path_cline(repo_root: Path, scope: SkillScope) -> Path | None:
    if scope != SkillScope.REPO:
        return None
    return repo_root / ".clinerules" / "workflows" / f"{SKILL_NAME}.md"


def _path_windsurf(repo_root: Path, scope: SkillScope) -> Path | None:
    if scope != SkillScope.REPO:
        return None
    return repo_root / ".windsurf" / "workflows" / f"{SKILL_NAME}.md"


def _path_roo(repo_root: Path, scope: SkillScope) -> Path | None:
    if scope != SkillScope.REPO:
        return None
    return repo_root / ".roo" / "commands" / f"{SKILL_NAME}.md"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


SKILL_SURFACES: dict[str, SkillSurface] = {
    "claude-code": SkillSurface(path=_path_claude_code, render=_render_agent_skill_md),
    "cursor": SkillSurface(path=_path_cursor, render=_render_agent_skill_md),
    "codex-cli": SkillSurface(path=_path_codex_cli, render=_render_agent_skill_md),
    "continue": SkillSurface(path=_path_continue, render=_render_continue_prompt),
    "cline": SkillSurface(path=_path_cline, render=_render_cline_workflow),
    "windsurf": SkillSurface(path=_path_windsurf, render=_render_windsurf_workflow),
    "roo": SkillSurface(path=_path_roo, render=_render_roo_slash_command),
}


# ---------------------------------------------------------------------------
# Per-client writer
# ---------------------------------------------------------------------------


def _default_scope(client_id: str) -> SkillScope:
    """Each client's "preferred" scope.

    Claude Code and Codex CLI ship to the user dir so the skill is
    available in any repo. Everyone else writes per-repo.
    """

    return SkillScope.USER if client_id in {"claude-code", "codex-cli"} else SkillScope.REPO


def write_skill_for_client(
    client_id: str,
    repo_root: Path,
    *,
    scope: SkillScope | None = None,
    body: str | None = None,
    version: str | None = None,
    dry_run: bool = False,
) -> SkillResult:
    """Write the Vaner skill into ``client_id``'s native surface.

    Idempotent: identical-content writes report ``skipped``. The
    rendered file is owned end-to-end by Vaner — there is no merge
    primitive here (unlike primers), since each skill file is named for
    Vaner and is not expected to be edited by the user.
    """

    surface = SKILL_SURFACES.get(client_id)
    if surface is None:
        return SkillResult(
            client_id=client_id,
            scope=scope or SkillScope.REPO,
            path=None,
            action="unsupported",
        )

    resolved_scope = scope or _default_scope(client_id)
    target = surface.path(repo_root, resolved_scope)
    if target is None:
        return SkillResult(
            client_id=client_id,
            scope=resolved_scope,
            path=None,
            action="unsupported",
        )

    resolved_body = body if body is not None else load_canonical_skill_body()
    resolved_version = version or skill_version()
    rendered = surface.render(resolved_body, resolved_version)

    try:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
    except OSError as exc:
        return SkillResult(
            client_id=client_id,
            scope=resolved_scope,
            path=target,
            action="failed",
            error=str(exc),
        )

    if existing == rendered:
        return SkillResult(client_id=client_id, scope=resolved_scope, path=target, action="skipped")

    action: SkillAction = "updated" if existing else "added"
    if dry_run:
        return SkillResult(client_id=client_id, scope=resolved_scope, path=target, action=action)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive I/O guard
        return SkillResult(
            client_id=client_id,
            scope=resolved_scope,
            path=target,
            action="failed",
            error=str(exc),
        )
    return SkillResult(client_id=client_id, scope=resolved_scope, path=target, action=action)


def write_skills(
    client_ids: list[str],
    repo_root: Path,
    *,
    dry_run: bool = False,
) -> list[SkillResult]:
    """Write the skill for each client in ``client_ids``.

    Each surface's preferred scope is used (claude-code and codex-cli
    write to the user dir; everyone else writes per-repo).
    """

    body = load_canonical_skill_body()
    version = skill_version()
    results: list[SkillResult] = []
    for client_id in client_ids:
        results.append(
            write_skill_for_client(
                client_id,
                repo_root,
                body=body,
                version=version,
                dry_run=dry_run,
            )
        )
    return results
