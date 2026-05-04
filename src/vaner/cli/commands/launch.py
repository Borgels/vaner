# SPDX-License-Identifier: Apache-2.0
"""Per-client launch orchestrator.

``vaner launch <client>`` (and the enhanced ``vaner clients install
<client>``) runs the full leverage stack for a single client in one
pass: MCP server entry, primer, skill / workflow / prompt, and
plugin / hooks where applicable.

Until this module landed, those four layers were split across
``vaner clients install`` (MCP only, one client) and ``vaner init``
(primers + skills + hooks for *every* detected client). Neither did
"set up Vaner properly for this one client, end-to-end" — which is
the natural mental model and the only one users should have to
remember.

Each layer reports its own status. The aggregate ``LaunchResult``
collects them so callers (CLI, desktop wizard) can show a single
per-layer chip set per client.

Reference: docs.vaner.ai/integrations/client-capabilities
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

LayerName = Literal["mcp", "primer", "skill", "hook"]


@dataclass(slots=True)
class LayerOutcome:
    """One layer's outcome for one client launch.

    ``applicable`` is False when Vaner has nothing to install at this
    layer for this client (e.g. Zed has no skill surface; Claude
    Desktop has no local primer). ``action`` only matters when
    applicable is True.
    """

    layer: LayerName
    applicable: bool
    action: str  # "added" | "updated" | "skipped" | "failed" | "unsupported" | "not-applicable"
    path: Path | None = None
    error: str | None = None


@dataclass(slots=True)
class LaunchResult:
    """Aggregate result of launching Vaner into one client."""

    client_id: str
    label: str
    detected: bool
    layers: list[LayerOutcome] = field(default_factory=list)

    @property
    def overall(self) -> Literal["ready", "partial", "failed", "missing"]:
        """One-word summary the CLI / wizard renders as a chip.

        ``ready`` — every applicable layer ended up wired
        ``partial`` — at least one applicable layer didn't write
        ``failed`` — every applicable write failed
        ``missing`` — no applicable layers (e.g. unknown client id)
        """

        applicable = [layer for layer in self.layers if layer.applicable]
        if not applicable:
            return "missing"
        wired = [layer for layer in applicable if layer.action in {"added", "updated", "skipped"}]
        if not wired:
            return "failed"
        if len(wired) == len(applicable):
            return "ready"
        return "partial"


def launch_client(
    client_id: str,
    repo_root: Path,
    *,
    server_key: str = "vaner",
    dry_run: bool = False,
    force: bool = False,
    skip_layers: tuple[LayerName, ...] = (),
) -> LaunchResult:
    """Install Vaner into one client at every applicable leverage layer.

    Layers run in order — MCP first (so the agent can call vaner.* at
    all), primer next (so the model knows when to call), skill third,
    plugin/hook last. A failure at one layer doesn't stop the rest;
    each layer's status is captured independently.

    Parameters
    ----------
    client_id:
        The client id from
        :data:`vaner.cli.commands.mcp_clients.CLIENTS` (e.g. ``cursor``,
        ``cline``, ``zed``).
    repo_root:
        Project root. Per-repo surfaces (most primers, all skills /
        workflows / prompts, hooks) write under this path. User-scope
        surfaces (Claude Code skills, Codex CLI skills) ignore it.
    server_key:
        Override the JSON mcpServers key. Claude Desktop uses
        ``vaner-<reponame>`` by default so multiple repos register
        independently.
    dry_run:
        When True, no layer writes anything to disk.
    force:
        Re-write the MCP entry even when it already matches. Other
        layers are already idempotent without a force flag.
    skip_layers:
        Skip these layers (typically used by ``--mcp-only`` callers).
    """

    from vaner.cli.commands import mcp_clients
    from vaner.cli.commands.hooks import HOOK_SURFACES, write_hook_for_client
    from vaner.cli.commands.plugins import PLUGIN_SURFACES, write_plugin_for_client
    from vaner.cli.commands.primer import PRIMER_SURFACES, write_primer_for_client
    from vaner.cli.commands.skills import SKILL_SURFACES, write_skill_for_client

    detected_list = mcp_clients.detect_all(repo_root)
    matching = [d for d in detected_list if d.spec.id == client_id]
    if not matching:
        # Unknown client id — surface as a single missing-layer result.
        return LaunchResult(
            client_id=client_id,
            label=client_id,
            detected=False,
            layers=[
                LayerOutcome(
                    layer="mcp",
                    applicable=False,
                    action="unsupported",
                    error=f"unknown client id {client_id!r}",
                )
            ],
        )
    detected = matching[0]
    is_detected = detected.status != mcp_clients.ClientStatus.MISSING

    layers: list[LayerOutcome] = []

    # ---- Layer 1: MCP server ------------------------------------------------
    if "mcp" in skip_layers:
        layers.append(LayerOutcome(layer="mcp", applicable=True, action="not-applicable"))
    else:
        launcher_cmd, launcher_args = mcp_clients.resolve_launcher(repo_root)
        key = server_key
        if key == "vaner" and detected.spec.id == "claude-desktop":
            key = f"vaner-{repo_root.name}"
        write = mcp_clients.write_client(
            detected,
            launcher_cmd=launcher_cmd,
            launcher_args=launcher_args,
            server_key=key,
            dry_run=dry_run,
            force=force,
        )
        layers.append(
            LayerOutcome(
                layer="mcp",
                applicable=True,
                action=write.action,
                path=write.path,
                error=write.error,
            )
        )

    # ---- Layer 2: Primer ----------------------------------------------------
    if "primer" in skip_layers or client_id not in PRIMER_SURFACES:
        layers.append(
            LayerOutcome(
                layer="primer",
                applicable=client_id in PRIMER_SURFACES,
                action="skipped" if "primer" in skip_layers else "not-applicable",
            )
        )
    else:
        primer = write_primer_for_client(client_id, repo_root, dry_run=dry_run)
        layers.append(
            LayerOutcome(
                layer="primer",
                applicable=True,
                action=primer.action,
                path=primer.path,
                error=primer.error,
            )
        )

    # ---- Layer 3: Skill / workflow / prompt ---------------------------------
    if "skill" in skip_layers or client_id not in SKILL_SURFACES:
        layers.append(
            LayerOutcome(
                layer="skill",
                applicable=client_id in SKILL_SURFACES,
                action="skipped" if "skill" in skip_layers else "not-applicable",
            )
        )
    else:
        skill = write_skill_for_client(client_id, repo_root, dry_run=dry_run)
        layers.append(
            LayerOutcome(
                layer="skill",
                applicable=True,
                action=skill.action,
                path=skill.path,
                error=skill.error,
            )
        )

    # ---- Layer 4: Hook / plugin --------------------------------------------
    # Vaner installs Claude Code's atomic plugin bundle directly, and
    # prompt-submit hooks for Cline + Windsurf via the hooks module.
    supports_layer = client_id in PLUGIN_SURFACES or client_id in HOOK_SURFACES
    if "hook" in skip_layers or not supports_layer:
        layers.append(
            LayerOutcome(
                layer="hook",
                applicable=supports_layer,
                action="skipped" if "hook" in skip_layers else "not-applicable",
            )
        )
    elif client_id in PLUGIN_SURFACES:
        plugin = write_plugin_for_client(client_id, dry_run=dry_run, force=force)
        layers.append(
            LayerOutcome(
                layer="hook",
                applicable=True,
                action=plugin.action,
                path=plugin.path,
                error=plugin.error,
            )
        )
    else:
        hook = write_hook_for_client(client_id, repo_root, dry_run=dry_run)
        layers.append(
            LayerOutcome(
                layer="hook",
                applicable=True,
                action=hook.action,
                path=hook.path,
                error=hook.error,
            )
        )

    return LaunchResult(
        client_id=client_id,
        label=detected.spec.label,
        detected=is_detected,
        layers=layers,
    )
