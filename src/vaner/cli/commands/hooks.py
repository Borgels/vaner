# SPDX-License-Identifier: Apache-2.0
"""Per-client hook installer.

Layer 4 of the four-layer leverage stack documented at
docs.vaner.ai/integrations/client-capabilities. Mirrors the design of
:mod:`vaner.cli.commands.primer` and :mod:`vaner.cli.commands.skills`:
a per-client surface with a path resolver and a render function, plus
a non-destructive writer.

What's shipped today
--------------------

This module installs **prompt-submit hooks** for the two clients with
stable hook APIs that aren't already covered by an atomic plugin:

* **Cline** — drops an executable script into ``.clinerules/hooks/``.
  Cline scans the directory and dispatches by filename; we ship
  ``UserPromptSubmit`` so Vaner pre-fetches prepared work as the user
  types.
* **Windsurf** — writes ``.windsurf/hooks.json`` declaring a shell
  command that runs the canonical composer-submit script on the
  ``user_prompt`` event.

The composer-submit script (``defaults/hooks/composer_submit.py``)
is shared across both clients — same contract, same behaviour. It
POSTs a DraftIntentSnapshot to the local daemon's ``/signals/composer``
endpoint and never blocks the user (always exits 0 with ``{}`` output).

Out of scope here
-----------------

* **Claude Code** and **Cursor** install hooks via their plugin systems
  (``plugins/vaner/hooks/hooks.json`` and ``cursor-plugins/vaner/hooks.json``
  respectively). Those bundles are atomic install units; this module
  doesn't duplicate them.
* **Codex CLI** has hooks behind a ``codex_hooks = true`` flag —
  defer until GA.
* **VS Code Copilot** exposes only ``resolveMcpServerDefinition`` to
  third-party extensions; no general session-lifecycle hook surface.
* **Continue / Zed / Roo / Claude Desktop** have no hook system.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

HookAction = Literal["added", "updated", "skipped", "unsupported", "failed"]


class HookScope(StrEnum):
    REPO = "repo"
    USER = "user"


@dataclass(slots=True)
class HookResult:
    client_id: str
    scope: HookScope
    path: Path | None
    action: HookAction
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HookSurface:
    """Declarative description of where and how to write a client's hook."""

    # Resolves the target file for a scope, or None if not supported.
    path: Callable[[Path, HookScope], Path | None]
    # Renders the canonical hook config / script for the file's
    # native format.
    render: Callable[[Path, str], str]  # (script_source_path, version) -> file body
    # ``script`` writes an executable file (Cline). ``config`` writes
    # a JSON config that references the canonical script (Windsurf).
    kind: Literal["script", "config"]


# ---------------------------------------------------------------------------
# Canonical composer-submit script
# ---------------------------------------------------------------------------


_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL_SCRIPT = _PACKAGE_ROOT / "defaults" / "hooks" / "composer_submit.py"


def canonical_script_path() -> Path:
    """Return the path to the canonical composer-submit script bundled
    with the vaner package. The hook installers either copy this file
    verbatim (Cline) or reference it from a JSON config (Windsurf).
    """

    return _CANONICAL_SCRIPT


def hook_version() -> str:
    """Track ``vaner.__version__`` so re-installs after upgrade refresh."""

    try:
        from vaner import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive
        return "0"


# ---------------------------------------------------------------------------
# Per-client renders
# ---------------------------------------------------------------------------


def _render_cline_user_prompt_submit(script_source: Path, _version: str) -> str:
    """Cline scans ``.clinerules/hooks/`` for executable scripts. The
    filename maps to the event name (``UserPromptSubmit``). We copy
    the canonical composer-submit script verbatim.

    Per docs.cline.bot/features/hooks: hooks "receive JSON via stdin
    and emit JSON via stdout." The canonical script does exactly that,
    so it works as-is for Cline.
    """

    return script_source.read_text(encoding="utf-8")


def _render_windsurf_hooks_json(script_source: Path, version: str) -> str:
    """Windsurf reads a single ``.windsurf/hooks.json`` declaring shell
    commands per event. We invoke the canonical script via ``python3``
    so the file format stays portable across OSes — Windsurf doesn't
    require executable bits.

    Per docs.windsurf.com/windsurf/cascade/hooks: pre-hooks block by
    exiting with code 2; observational hooks ignore output. Our script
    always exits 0, so the hook is observational by construction.
    """

    payload = {
        "version": 1,
        "x-vaner-managed": f"v={version}",
        "hooks": {
            "user_prompt": [
                {
                    "command": ["python3", str(script_source)],
                    "timeout_ms": 3000,
                    "fail_closed": False,
                }
            ]
        },
    }
    return json.dumps(payload, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Per-client path resolvers
# ---------------------------------------------------------------------------


def _path_cline(repo_root: Path, scope: HookScope) -> Path | None:
    if scope != HookScope.REPO:
        return None
    return repo_root / ".clinerules" / "hooks" / "UserPromptSubmit"


def _path_windsurf(repo_root: Path, scope: HookScope) -> Path | None:
    if scope != HookScope.REPO:
        return None
    return repo_root / ".windsurf" / "hooks.json"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


HOOK_SURFACES: dict[str, HookSurface] = {
    "cline": HookSurface(path=_path_cline, render=_render_cline_user_prompt_submit, kind="script"),
    "windsurf": HookSurface(path=_path_windsurf, render=_render_windsurf_hooks_json, kind="config"),
}


# ---------------------------------------------------------------------------
# Per-client writer
# ---------------------------------------------------------------------------


def write_hook_for_client(
    client_id: str,
    repo_root: Path,
    *,
    scope: HookScope = HookScope.REPO,
    version: str | None = None,
    dry_run: bool = False,
) -> HookResult:
    """Install the prompt-submit hook for ``client_id``.

    Idempotent: identical-content writes return ``skipped``. The hook
    file is owned end-to-end by Vaner — there is no merge primitive,
    since each hook file is named for Vaner's purpose.

    Cline scripts get an executable bit (``chmod +x``) so the OS will
    actually run them. Windsurf JSON configs don't need it.
    """

    surface = HOOK_SURFACES.get(client_id)
    if surface is None:
        return HookResult(client_id=client_id, scope=scope, path=None, action="unsupported")

    target = surface.path(repo_root, scope)
    if target is None:
        return HookResult(client_id=client_id, scope=scope, path=None, action="unsupported")

    resolved_version = version or hook_version()
    rendered = surface.render(canonical_script_path(), resolved_version)

    try:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
    except OSError as exc:
        return HookResult(client_id=client_id, scope=scope, path=target, action="failed", error=str(exc))

    if existing == rendered:
        return HookResult(client_id=client_id, scope=scope, path=target, action="skipped")

    action: HookAction = "updated" if existing else "added"
    if dry_run:
        return HookResult(client_id=client_id, scope=scope, path=target, action=action)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        if surface.kind == "script":
            # Make the script executable so Cline can run it.
            current_mode = target.stat().st_mode
            target.chmod(current_mode | 0o111)
    except OSError as exc:  # pragma: no cover - defensive I/O guard
        return HookResult(client_id=client_id, scope=scope, path=target, action="failed", error=str(exc))
    return HookResult(client_id=client_id, scope=scope, path=target, action=action)


def write_hooks(
    client_ids: list[str],
    repo_root: Path,
    *,
    dry_run: bool = False,
) -> list[HookResult]:
    """Install hooks for each client in ``client_ids``."""

    version = hook_version()
    results: list[HookResult] = []
    for client_id in client_ids:
        results.append(write_hook_for_client(client_id, repo_root, version=version, dry_run=dry_run))
    return results


# ``shutil`` is imported above only so test fixtures can patch it; not
# used directly in this module.
_ = shutil
