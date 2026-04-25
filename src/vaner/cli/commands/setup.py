# SPDX-License-Identifier: Apache-2.0
"""WS6 — `vaner setup` CLI commands (0.8.6).

Six subcommands compose the Simple-Mode wizard surface for the CLI:

- ``vaner setup wizard`` — interactive five-question Rich flow that
  collects answers, runs hardware detection, picks a policy bundle,
  surfaces overrides + cloud-widening warnings, and persists the
  result to ``.vaner/config.toml``.
- ``vaner setup show`` — print the current ``[setup]`` / ``[policy]``
  state plus a fresh hardware probe and the materialised
  :class:`AppliedPolicy`. ``--json`` for programmatic consumers.
- ``vaner setup recommend`` — read :class:`SetupAnswers` from stdin or
  ``--answers`` and emit :class:`SelectionResult` as JSON. Always JSON
  — this is the programmatic-recommendation surface (desktop apps,
  scripts, MCP wiring).
- ``vaner setup apply`` — persist already-collected answers (or an
  explicit ``--bundle-id``) without re-running the wizard. Best-effort
  daemon ping; the daemon picks up the change on its next config
  refresh.
- ``vaner setup advanced`` — open ``.vaner/config.toml`` in ``$EDITOR``
  for direct knob editing. Prints a one-line "managed sections" hint.
- ``vaner setup hardware`` — print :class:`HardwareProfile` from
  :func:`vaner.setup.hardware.detect`. ``--json`` for raw output.

The CLI never mutates :mod:`vaner.setup` modules; it only consumes
their pure-function surface. Persistence here uses the shared
``_update_toml_section`` helper from :mod:`vaner.cli.commands.init`
to keep ``[setup]`` / ``[policy]`` writes consistent with the rest of
the config-editing surface.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vaner.cli.commands.config import load_config
from vaner.cli.commands.init import init_repo
from vaner.setup.answers import SetupAnswers
from vaner.setup.apply import (
    WIDENS_CLOUD_POSTURE_SENTINEL,
    AppliedPolicy,
    apply_policy_bundle,
)
from vaner.setup.catalog import bundle_by_id
from vaner.setup.hardware import HardwareProfile, detect
from vaner.setup.select import SelectionResult, select_policy_bundle
from vaner.setup.serializers import (
    AnswersValidationError,
    answers_from_payload,
    bundle_to_dict,
    hardware_to_dict,
    selection_to_dict,
)

setup_app = typer.Typer(
    help=("Simple-Mode setup wizard: pick a policy bundle, persist it to .vaner/config.toml, and inspect hardware/policy state."),
    no_args_is_help=True,
)

_console = Console()
_DAEMON_URL = "http://127.0.0.1:8473"

# ---------------------------------------------------------------------------
# Path / config helpers
# ---------------------------------------------------------------------------


def _repo_root(path: str | None) -> Path:
    """Resolve ``--path`` / ``$VANER_PATH`` / ``cwd`` to a repo root.

    Mirrors the convention from :mod:`vaner.cli.commands.app` so every
    setup subcommand picks up the same root the rest of the CLI does.
    """

    if path:
        return Path(path).resolve()
    env_path = os.environ.get("VANER_PATH", "").strip()
    return Path(env_path).resolve() if env_path else Path.cwd()


def _read_setup_section(repo_root: Path) -> dict[str, Any]:
    """Read the raw ``[setup]`` table from ``.vaner/config.toml``.

    ``load_config`` does not yet hydrate :class:`SetupConfig` /
    :class:`PolicyConfig` from disk, so the CLI reads them directly.
    Returns an empty dict when the file or section is absent.
    """

    config_path = repo_root / ".vaner" / "config.toml"
    if not config_path.exists():
        return {}
    try:
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    section = parsed.get("setup", {})
    return section if isinstance(section, dict) else {}


def _read_policy_section(repo_root: Path) -> dict[str, Any]:
    """Read the raw ``[policy]`` table from ``.vaner/config.toml``."""

    config_path = repo_root / ".vaner" / "config.toml"
    if not config_path.exists():
        return {}
    try:
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    section = parsed.get("policy", {})
    return section if isinstance(section, dict) else {}


def _toml_literal(value: object) -> str:
    """Render a Python value as a TOML literal.

    Extension over the small editor in :mod:`vaner.cli.commands.init`:
    we also support ``list[str]`` (rendered as a TOML inline array).
    The ``[setup]`` section needs proper ``work_styles = ["mixed"]``
    arrays — not the JSON-string the init helper would emit.
    """

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return '""'
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, str):
                escaped = item.replace("\\", "\\\\").replace('"', '\\"')
                items.append(f'"{escaped}"')
            else:
                items.append(_toml_literal(item))
        return "[" + ", ".join(items) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _update_section(text: str, section: str, values: dict[str, object]) -> str:
    """Update keys within a TOML section, leaving unrelated keys untouched.

    Mirror of ``_update_toml_section`` in :mod:`vaner.cli.commands.init`
    but with proper list rendering. Single-line ``key = value`` only;
    matches the shape the wizard writes.
    """

    if not values:
        return text
    lines = text.splitlines()
    header = f"[{section}]"
    start: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == header:
            start = idx
            break
    if start is None:
        lines.append("")
        lines.append(header)
        for key, val in values.items():
            lines.append(f"{key} = {_toml_literal(val)}")
        return "\n".join(lines) + ("\n" if not text.endswith("\n") else "")

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = idx
            break
    remaining = dict(values)
    for idx in range(start + 1, end):
        stripped = lines[idx].lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[idx] = f"{key} = {_toml_literal(remaining.pop(key))}"
    if remaining:
        insert_at = end
        for key, val in remaining.items():
            lines.insert(insert_at, f"{key} = {_toml_literal(val)}")
            insert_at += 1
    out = "\n".join(lines)
    return out + ("\n" if not out.endswith("\n") else "")


def _persist_setup_and_policy(
    repo_root: Path,
    answers: SetupAnswers,
    bundle_id: str,
    *,
    completed_at: datetime | None = None,
) -> Path:
    """Write the wizard answers + selected bundle id to ``config.toml``.

    Returns the path to the written file. Idempotent: re-writing the
    same values produces a byte-identical file. The caller is
    responsible for any user confirmation upstream.
    """

    config_path = init_repo(repo_root)
    text = config_path.read_text(encoding="utf-8")
    setup_values: dict[str, object] = {
        "mode": "simple",
        "work_styles": list(answers.work_styles),
        "priority": answers.priority,
        "compute_posture": answers.compute_posture,
        "cloud_posture": answers.cloud_posture,
        "background_posture": answers.background_posture,
        "version": 1,
    }
    if completed_at is not None:
        setup_values["completed_at"] = completed_at.isoformat()
    text = _update_section(text, "setup", setup_values)
    text = _update_section(
        text,
        "policy",
        {"selected_bundle_id": bundle_id, "auto_select": True},
    )
    config_path.write_text(text, encoding="utf-8")
    return config_path


# ---------------------------------------------------------------------------
# Serialisation helpers (JSON-emitting subcommands)
# ---------------------------------------------------------------------------


# 0.8.6 WS7: serialisation helpers moved to :mod:`vaner.setup.serializers`
# so the MCP server (which must not pull in typer/rich) can share the
# same JSON shape. The underscore-prefixed names remain as aliases for
# in-tree callers and tests that imported them before WS7.
_bundle_to_dict = bundle_to_dict
_selection_to_dict = selection_to_dict
_hardware_to_dict = hardware_to_dict


def _tier_to_human(tier: str) -> str:
    return {
        "light": "Light",
        "capable": "Capable",
        "high_performance": "High-performance",
        "unknown": "Unknown",
    }.get(tier, tier)


def _os_to_human(os_name: str) -> str:
    return {"linux": "Linux", "darwin": "macOS", "windows": "Windows"}.get(os_name, os_name)


# ---------------------------------------------------------------------------
# Wizard prompt helpers
# ---------------------------------------------------------------------------


_WORK_STYLE_CHOICES: tuple[tuple[str, str], ...] = (
    ("writing", "Writing — drafting, editing, narrative"),
    ("research", "Research — surveys, deep reading, citations"),
    ("planning", "Planning — design docs, roadmaps, project layout"),
    ("support", "Support — answering questions, troubleshooting"),
    ("learning", "Learning — studying, exploring a new domain"),
    ("coding", "Coding — software development"),
    ("general", "General — knowledge work, mixed light tasks"),
    ("mixed", "Mixed — a bit of everything (safe default)"),
    ("unsure", "Unsure — I'd rather Vaner picks for me"),
)


_PRIORITY_CHOICES: tuple[tuple[str, str], ...] = (
    ("balanced", "Balanced — a sensible middle"),
    ("speed", "Speed — snappy responses"),
    ("quality", "Quality — best answer, even if slow"),
    ("privacy", "Privacy — keep data on this machine"),
    ("cost", "Cost — minimise spend"),
    ("low_resource", "Low-resource — go easy on this machine"),
)


_COMPUTE_CHOICES: tuple[tuple[str, str], ...] = (
    ("light", "Light — barely use the CPU/GPU"),
    ("balanced", "Balanced — work with what's idle"),
    ("available_power", "Available-power — use what this box has"),
)


_CLOUD_CHOICES: tuple[tuple[str, str], ...] = (
    ("local_only", "Local only — never reach for cloud LLMs"),
    ("ask_first", "Ask first — confirm before any cloud call"),
    ("hybrid_when_worth_it", "Hybrid — cloud when it's clearly worth it"),
    ("best_available", "Best available — use the best model for the job"),
)


_BACKGROUND_CHOICES: tuple[tuple[str, str], ...] = (
    ("minimal", "Minimal — barely ponder when idle"),
    ("normal", "Normal — moderate background pondering"),
    ("idle_more", "Idle-more — ponder broadly when the box is idle"),
    ("deep_run_aggressive", "Deep-Run-aggressive — happy to run overnight"),
)


def _print_choice_menu(
    console: Console,
    title: str,
    choices: tuple[tuple[str, str], ...],
) -> None:
    """Render a numbered choice menu (1-indexed)."""

    console.print(f"\n[bold]{title}[/bold]")
    for idx, (_value, label) in enumerate(choices, start=1):
        console.print(f"  [cyan]{idx}[/cyan]) {label}")


def _resolve_choice_token(
    token: str,
    choices: tuple[tuple[str, str], ...],
) -> str | None:
    """Map a user-typed token (number or value-id) to a choice value."""

    cleaned = token.strip().lower()
    if not cleaned:
        return None
    if cleaned.isdigit():
        idx = int(cleaned) - 1
        if 0 <= idx < len(choices):
            return choices[idx][0]
        return None
    by_value = {value for value, _label in choices}
    if cleaned in by_value:
        return cleaned
    return None


def _prompt_single(
    title: str,
    choices: tuple[tuple[str, str], ...],
    default_value: str,
) -> str:
    """Prompt for a single-select answer; returns the chosen value."""

    _print_choice_menu(_console, title, choices)
    default_idx = next(
        (i + 1 for i, (value, _label) in enumerate(choices) if value == default_value),
        1,
    )
    answer = typer.prompt("Choice", default=str(default_idx))
    resolved = _resolve_choice_token(str(answer), choices)
    return resolved or default_value


def _prompt_multi(
    title: str,
    choices: tuple[tuple[str, str], ...],
    default_values: tuple[str, ...],
) -> tuple[str, ...]:
    """Prompt for a multi-select answer; returns a tuple of values.

    Empty input keeps ``default_values``. Tokens may be numbers (1..N)
    or value-ids; comma- or space-separated.
    """

    _print_choice_menu(_console, title, choices)
    default_str = ",".join(default_values) if default_values else "mixed"
    answer = typer.prompt(
        'Choice(s) (comma- or space-separated; e.g. "1 3" or "writing,research")',
        default=default_str,
    )
    raw_tokens = [token for token in str(answer).replace(",", " ").split() if token.strip()]
    if not raw_tokens:
        return default_values or ("mixed",)
    selected: list[str] = []
    for token in raw_tokens:
        resolved = _resolve_choice_token(token, choices)
        if resolved is not None and resolved not in selected:
            selected.append(resolved)
    if not selected:
        return default_values or ("mixed",)
    return tuple(selected)


def _collect_answers_interactive() -> SetupAnswers:
    """Run the five Simple-Mode questions and return :class:`SetupAnswers`."""

    work_styles = _prompt_multi(
        "1/5 — What kind of work do you want help with?",
        _WORK_STYLE_CHOICES,
        ("mixed",),
    )
    priority = _prompt_single(
        "2/5 — What matters most?",
        _PRIORITY_CHOICES,
        "balanced",
    )
    compute = _prompt_single(
        "3/5 — How hard should this machine work for you?",
        _COMPUTE_CHOICES,
        "balanced",
    )
    cloud = _prompt_single(
        "4/5 — How do you feel about cloud LLMs?",
        _CLOUD_CHOICES,
        "ask_first",
    )
    background = _prompt_single(
        "5/5 — How aggressive should background pondering be?",
        _BACKGROUND_CHOICES,
        "normal",
    )
    return SetupAnswers(
        work_styles=work_styles,  # type: ignore[arg-type]
        priority=priority,  # type: ignore[arg-type]
        compute_posture=compute,  # type: ignore[arg-type]
        cloud_posture=cloud,  # type: ignore[arg-type]
        background_posture=background,  # type: ignore[arg-type]
    )


def _default_answers() -> SetupAnswers:
    """Sensible defaults for non-interactive / ``--accept-defaults`` use."""

    return SetupAnswers(
        work_styles=("mixed",),
        priority="balanced",
        compute_posture="balanced",
        cloud_posture="ask_first",
        background_posture="normal",
    )


def _load_answers_from_path(path: Path) -> SetupAnswers:
    """Load a :class:`SetupAnswers` from a JSON file.

    Accepts the shape produced by :func:`dataclasses.asdict` plus the
    minor adjustment of ``work_styles`` being a list (the dataclass
    converts back to a tuple on construction).
    """

    raw = json.loads(path.read_text(encoding="utf-8"))
    return _answers_from_payload(raw)


def _answers_from_payload(raw: object) -> SetupAnswers:
    """CLI-side wrapper around :func:`answers_from_payload`.

    Translates :class:`AnswersValidationError` into ``typer.BadParameter``
    so the CLI commands keep their previous error surface unchanged.
    """

    try:
        return answers_from_payload(raw)
    except AnswersValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc


# ---------------------------------------------------------------------------
# Wizard rendering helpers
# ---------------------------------------------------------------------------


def _render_hardware_one_line(console: Console, hw: HardwareProfile) -> None:
    console.print(f"This [bold]{_os_to_human(hw.os)}[/bold] machine looks [bold]{_tier_to_human(hw.tier)}[/bold].")


def _render_selection_panel(console: Console, result: SelectionResult) -> None:
    title = f"Recommended bundle: {result.bundle.label} ({result.bundle.id})"
    body_lines = [
        f"[bold]{result.bundle.label}[/bold]",
        result.bundle.description,
        "",
        "Why this bundle:",
    ]
    if result.reasons:
        body_lines.extend(f"  - {reason}" for reason in result.reasons)
    else:
        body_lines.append("  - (no positive reasons; safe default)")
    if result.runner_ups:
        body_lines.append("")
        body_lines.append("Runner-ups:")
        for ru in result.runner_ups[:2]:
            body_lines.append(f"  - {ru.label} ({ru.id})")
    if result.forced_fallback:
        body_lines.append("")
        body_lines.append("[yellow]Note:[/yellow] no bundle matched filters; using safe default.")
    console.print(Panel("\n".join(body_lines), title=title, border_style="cyan"))


def _split_overrides(applied: AppliedPolicy) -> tuple[list[str], list[str]]:
    """Split overrides into (cloud_widening_warnings, regular_overrides)."""

    warnings: list[str] = []
    regular: list[str] = []
    for entry in applied.overrides_applied:
        if entry.startswith(WIDENS_CLOUD_POSTURE_SENTINEL):
            warnings.append(entry)
        else:
            regular.append(entry)
    return warnings, regular


def _render_overrides_table(console: Console, overrides: list[str]) -> None:
    if not overrides:
        console.print("[dim]No overrides applied (config already matches the bundle).[/dim]")
        return
    table = Table(title="Overrides applied", show_header=True, header_style="bold")
    table.add_column("#")
    table.add_column("Description")
    for idx, line in enumerate(overrides, start=1):
        table.add_row(str(idx), line)
    console.print(table)


def _render_cloud_widening_warning(console: Console, warnings: list[str]) -> None:
    body_lines = [
        "Switching to this bundle widens your cloud posture:",
        "",
    ]
    body_lines.extend(f"  - {entry}" for entry in warnings)
    body_lines.append("")
    body_lines.append("Cloud calls may incur cost and route data off this machine.")
    console.print(
        Panel(
            "\n".join(body_lines),
            title="Cloud posture is widening",
            border_style="yellow",
        )
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@setup_app.command(
    "wizard",
    help="Interactive Simple-Mode wizard. Asks five questions, picks a bundle, persists it.",
)
def wizard_cmd(
    path: Annotated[
        str | None,
        typer.Option("--path", help="Repository root override."),
    ] = None,
    accept_defaults: Annotated[
        bool,
        typer.Option(
            "--accept-defaults",
            help="Skip prompts; accept sensible defaults (CI / Docker / non-TTY).",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Auto-confirm cloud-widening + write prompts (use with --accept-defaults).",
        ),
    ] = False,
) -> None:
    """Run the Simple-Mode wizard against the resolved repo root."""

    repo_root = _repo_root(path)
    config_path = init_repo(repo_root)

    if accept_defaults:
        answers = _default_answers()
        _console.print("[dim]--accept-defaults: using sensible default answers.[/dim]")
    else:
        try:
            answers = _collect_answers_interactive()
        except (EOFError, KeyboardInterrupt):
            _console.print("[red]Wizard aborted.[/red]")
            raise typer.Exit(code=1) from None

    hardware = detect()
    _render_hardware_one_line(_console, hardware)

    selection = select_policy_bundle(answers, hardware)
    _render_selection_panel(_console, selection)

    config = load_config(repo_root)
    # Reflect the on-disk previous bundle id into the runtime config so
    # the cloud-widening guard fires correctly. ``load_config`` does
    # not yet re-read ``[policy]`` so we patch it from the raw section.
    prior_policy_section = _read_policy_section(repo_root)
    prior_bundle_id = prior_policy_section.get("selected_bundle_id")
    if isinstance(prior_bundle_id, str) and prior_bundle_id:
        config = config.model_copy(update={"policy": config.policy.model_copy(update={"selected_bundle_id": prior_bundle_id})})

    applied = apply_policy_bundle(config, selection.bundle)
    warnings, regular_overrides = _split_overrides(applied)

    if warnings:
        _render_cloud_widening_warning(_console, warnings)
        if not yes:
            try:
                proceed = typer.confirm(
                    "Continue and widen the cloud posture?",
                    default=False,
                )
            except (EOFError, KeyboardInterrupt):
                proceed = False
            if not proceed:
                _console.print("[red]Aborted: cloud posture not widened. Config left unchanged.[/red]")
                raise typer.Exit(code=1)

    _render_overrides_table(_console, regular_overrides)

    if yes or accept_defaults:
        write_proceed = True
    else:
        try:
            write_proceed = typer.confirm(
                f"Write to {config_path}?",
                default=True,
            )
        except (EOFError, KeyboardInterrupt):
            write_proceed = False

    if not write_proceed:
        _console.print("[yellow]Skipped writing config.[/yellow]")
        return

    completed_at = datetime.now(UTC)
    _persist_setup_and_policy(repo_root, answers, selection.bundle.id, completed_at=completed_at)
    _console.print(f"[green]Wrote setup + policy to[/green] {config_path}")
    _console.print(f"[dim]selected_bundle_id={selection.bundle.id}[/dim]")


@setup_app.command(
    "show",
    help="Print current [setup]/[policy] state, hardware profile, and applied-policy overrides.",
)
def show_cmd(
    path: Annotated[
        str | None,
        typer.Option("--path", help="Repository root override."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of Rich tables."),
    ] = False,
) -> None:
    """Show the current setup/policy state for this repo root."""

    repo_root = _repo_root(path)
    setup_section = _read_setup_section(repo_root)
    policy_section = _read_policy_section(repo_root)
    hardware = detect()

    selected_bundle_id = policy_section.get("selected_bundle_id") or "hybrid_balanced"
    applied_dict: dict[str, Any] | None = None
    bundle_dict: dict[str, Any] | None = None
    try:
        bundle = bundle_by_id(str(selected_bundle_id))
        bundle_dict = _bundle_to_dict(bundle)
        config = load_config(repo_root)
        # Make the cloud-widening guard a no-op against itself by
        # pinning prior bundle id to current.
        config = config.model_copy(update={"policy": config.policy.model_copy(update={"selected_bundle_id": str(selected_bundle_id)})})
        applied = apply_policy_bundle(config, bundle)
        applied_dict = {
            "bundle_id": applied.bundle_id,
            "overrides_applied": list(applied.overrides_applied),
        }
    except KeyError:
        applied_dict = {"error": f"unknown bundle id {selected_bundle_id!r}"}

    payload: dict[str, Any] = {
        "repo_root": str(repo_root),
        "setup": setup_section,
        "policy": policy_section,
        "hardware": _hardware_to_dict(hardware),
        "applied_policy": applied_dict,
        "bundle": bundle_dict,
    }

    if as_json:
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    completed_at = setup_section.get("completed_at")
    if not setup_section or completed_at is None:
        _console.print("[yellow]Setup not yet completed.[/yellow] Run [bold]vaner setup wizard[/bold] to choose a profile.")
    else:
        _console.print(f"[bold]Setup completed at:[/bold] {completed_at}")

    setup_table = Table(title="Setup", show_header=True, header_style="bold")
    setup_table.add_column("Field")
    setup_table.add_column("Value")
    for key in ("mode", "work_styles", "priority", "compute_posture", "cloud_posture", "background_posture"):
        setup_table.add_row(key, str(setup_section.get(key, "(default)")))
    _console.print(setup_table)

    policy_table = Table(title="Policy", show_header=True, header_style="bold")
    policy_table.add_column("Field")
    policy_table.add_column("Value")
    policy_table.add_row("selected_bundle_id", str(selected_bundle_id))
    if bundle_dict is not None:
        policy_table.add_row("bundle.label", str(bundle_dict["label"]))
        policy_table.add_row("bundle.local_cloud_posture", str(bundle_dict["local_cloud_posture"]))
        policy_table.add_row("bundle.runtime_profile", str(bundle_dict["runtime_profile"]))
        policy_table.add_row("bundle.deep_run_profile", str(bundle_dict["deep_run_profile"]))
    policy_table.add_row("auto_select", str(policy_section.get("auto_select", True)))
    _console.print(policy_table)

    hw_table = Table(title="Hardware", show_header=True, header_style="bold")
    hw_table.add_column("Field")
    hw_table.add_column("Value")
    for key in ("os", "cpu_class", "ram_gb", "gpu", "gpu_vram_gb", "is_battery", "thermal_constrained", "tier"):
        hw_table.add_row(key, str(payload["hardware"][key]))
    _console.print(hw_table)

    if applied_dict and "overrides_applied" in applied_dict:
        overrides = list(applied_dict["overrides_applied"])
        if overrides:
            _console.print("[bold]Applied policy overrides:[/bold]")
            for line in overrides:
                style = "yellow" if line.startswith(WIDENS_CLOUD_POSTURE_SENTINEL) else "dim"
                _console.print(f"  [{style}]- {line}[/{style}]")


@setup_app.command(
    "recommend",
    help="Read SetupAnswers JSON (stdin or --answers) and emit a SelectionResult JSON.",
)
def recommend_cmd(
    answers_path: Annotated[
        Path | None,
        typer.Option("--answers", help="Path to a JSON file holding SetupAnswers; '-' or omit for stdin."),
    ] = None,
) -> None:
    """Programmatic-recommendation surface. Always emits JSON.

    The output shape is the contract MCP wiring (WS7) and desktop
    apps consume:

    .. code-block:: json

        {
          "bundle": {"id": "...", "label": "...", ...},
          "score": 5.2,
          "reasons": ["..."],
          "runner_ups": [{"id": "...", ...}],
          "forced_fallback": false
        }
    """

    if answers_path is not None and str(answers_path) != "-":
        try:
            answers = _load_answers_from_path(answers_path)
        except FileNotFoundError as exc:
            typer.secho(f"answers file not found: {answers_path}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            typer.secho(f"failed to parse answers: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
    else:
        try:
            raw_text = sys.stdin.read()
        except Exception as exc:  # pragma: no cover - defensive
            typer.secho(f"failed to read stdin: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        if not raw_text.strip():
            typer.secho(
                "no answers JSON on stdin (pass --answers <path> or pipe JSON)",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            payload = json.loads(raw_text)
            answers = _answers_from_payload(payload)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            typer.secho(f"failed to parse answers: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

    hardware = detect()
    selection = select_policy_bundle(answers, hardware)
    typer.echo(json.dumps(_selection_to_dict(selection), indent=2))


@setup_app.command(
    "apply",
    help=(
        "Persist answers (or an explicit --bundle-id) without prompts. Best-effort "
        "daemon ping; the daemon picks up changes on the next cycle (no live-refresh "
        "endpoint until WS8)."
    ),
)
def apply_cmd(
    path: Annotated[
        str | None,
        typer.Option("--path", help="Repository root override."),
    ] = None,
    answers_path: Annotated[
        Path | None,
        typer.Option("--answers", help="Path to a JSON file holding SetupAnswers."),
    ] = None,
    bundle_id: Annotated[
        str | None,
        typer.Option("--bundle-id", help="Skip selection; pin this bundle id directly."),
    ] = None,
    confirm_cloud_widening: Annotated[
        bool,
        typer.Option(
            "--confirm-cloud-widening",
            help=(
                "Required to proceed when the new bundle widens cloud posture "
                "relative to the prior bundle. Without this flag, apply aborts "
                "and prints the widening details so callers (desktop apps, CI) "
                "can re-invoke with explicit consent."
            ),
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit a JSON status object instead of human-readable lines."),
    ] = False,
) -> None:
    """Non-interactive apply for batch / scripting use."""

    repo_root = _repo_root(path)

    if bundle_id is not None:
        try:
            bundle = bundle_by_id(bundle_id)
        except KeyError as exc:
            typer.secho(f"unknown bundle id: {bundle_id!r}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        existing_setup = _read_setup_section(repo_root)
        if existing_setup:
            answers = _answers_from_payload(existing_setup)
        else:
            answers = _default_answers()
        chosen_bundle = bundle
        reasons: list[str] = ["explicit --bundle-id override"]
    else:
        if answers_path is None:
            existing_setup = _read_setup_section(repo_root)
            if not existing_setup:
                typer.secho(
                    "no answers provided and no [setup] section on disk; pass --answers or run `vaner setup wizard`",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(code=1)
            answers = _answers_from_payload(existing_setup)
        else:
            try:
                answers = _load_answers_from_path(answers_path)
            except FileNotFoundError as exc:
                typer.secho(f"answers file not found: {answers_path}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                typer.secho(f"failed to parse answers: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc
        hardware = detect()
        selection = select_policy_bundle(answers, hardware)
        chosen_bundle = selection.bundle
        reasons = list(selection.reasons)

    # Cloud-widening guard. apply_policy_bundle records sentinel-prefixed
    # entries in overrides_applied when the new bundle widens cloud posture
    # relative to the prior bundle on disk. Batch callers must opt in
    # explicitly via --confirm-cloud-widening; otherwise we abort with the
    # widening details so the caller (desktop UI, CI) can re-invoke with
    # consent.
    config = load_config(repo_root)
    prior_policy_section = _read_policy_section(repo_root)
    prior_bundle_id = prior_policy_section.get("selected_bundle_id")
    if isinstance(prior_bundle_id, str) and prior_bundle_id:
        config = config.model_copy(update={"policy": config.policy.model_copy(update={"selected_bundle_id": prior_bundle_id})})
    applied = apply_policy_bundle(config, chosen_bundle)
    warnings, _regular_overrides = _split_overrides(applied)

    if warnings and not confirm_cloud_widening:
        if as_json:
            typer.echo(
                json.dumps(
                    {
                        "blocked": True,
                        "block_reason": "cloud_widening_requires_confirm",
                        "selected_bundle_id": chosen_bundle.id,
                        "widens_cloud_posture": True,
                        "warnings": warnings,
                        "hint": "re-invoke with --confirm-cloud-widening to proceed",
                    },
                    indent=2,
                )
            )
        else:
            typer.secho(
                "Aborted: this bundle widens cloud posture; re-invoke with --confirm-cloud-widening.",
                fg=typer.colors.RED,
                err=True,
            )
            for entry in warnings:
                typer.secho(f"  - {entry}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)

    completed_at = datetime.now(UTC)
    config_path = _persist_setup_and_policy(
        repo_root,
        answers,
        chosen_bundle.id,
        completed_at=completed_at,
    )

    daemon_status = _ping_daemon_for_refresh()

    payload: dict[str, Any] = {
        "config_path": str(config_path),
        "selected_bundle_id": chosen_bundle.id,
        "reasons": reasons,
        "widens_cloud_posture": bool(warnings),
        "daemon": daemon_status,
    }
    if as_json:
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    _console.print(f"[green]Wrote setup + policy to[/green] {config_path}")
    _console.print(f"[dim]selected_bundle_id={chosen_bundle.id}[/dim]")
    if warnings:
        _console.print("[yellow]Cloud posture widened (confirmed via --confirm-cloud-widening).[/yellow]")
    if daemon_status.get("reachable"):
        _console.print("[dim]Daemon reachable. Daemon will pick up changes on next config reload.[/dim]")
    else:
        _console.print("[dim]Daemon not running; changes will apply at next start.[/dim]")


@setup_app.command(
    "advanced",
    help="Open .vaner/config.toml in $EDITOR (or fallback) for direct knob editing.",
)
def advanced_cmd(
    path: Annotated[
        str | None,
        typer.Option("--path", help="Repository root override."),
    ] = None,
) -> None:
    """Hand the config off to ``$EDITOR`` for free-form editing."""

    repo_root = _repo_root(path)
    config_path = init_repo(repo_root)
    _console.print("[dim]Sections managed by the wizard: [setup], [policy]. Other sections are free-form.[/dim]")
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or shutil.which("nano") or shutil.which("vi") or shutil.which("vim")
    if editor is None:
        typer.secho(
            "no editor found; set $EDITOR or install nano/vi",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    cmd = [editor, str(config_path)]
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        raise typer.Exit(code=completed.returncode)


@setup_app.command(
    "hardware",
    help="Print HardwareProfile.detect() output. --json for raw JSON.",
)
def hardware_cmd(
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of a Rich panel."),
    ] = False,
) -> None:
    """Print the detected hardware profile."""

    hw = detect()
    if as_json:
        typer.echo(json.dumps(_hardware_to_dict(hw), indent=2, default=str))
        return
    body_lines = [
        f"OS: [bold]{_os_to_human(hw.os)}[/bold] ({hw.os})",
        f"Tier: [bold]{_tier_to_human(hw.tier)}[/bold]",
        f"CPU class: {hw.cpu_class}",
        f"RAM: {hw.ram_gb} GB",
        f"GPU: {hw.gpu}" + (f" ({hw.gpu_vram_gb} GB VRAM)" if hw.gpu_vram_gb else ""),
        f"Battery: {'yes' if hw.is_battery else 'no'}",
        f"Thermal-constrained: {'yes' if hw.thermal_constrained else 'no'}",
    ]
    if hw.detected_runtimes:
        body_lines.append(f"Runtimes: {', '.join(hw.detected_runtimes)}")
    if hw.detected_models:
        body_lines.append(f"Detected models: {len(hw.detected_models)}")
    _console.print(Panel("\n".join(body_lines), title="Hardware profile", border_style="cyan"))


# ---------------------------------------------------------------------------
# Daemon ping
# ---------------------------------------------------------------------------


def _ping_daemon_for_refresh() -> dict[str, Any]:
    """Best-effort liveness probe of the local daemon HTTP surface.

    Returns a dict suitable for JSON output. The daemon currently has
    no live-refresh endpoint; WS8 will add one. Today the engine
    re-reads ``[setup]`` / ``[policy]`` at the top of every
    ``precompute_cycle`` (see ``_refresh_policy_bundle_state``), so
    config writes propagate without a daemon restart.
    """

    try:
        with httpx.Client(timeout=1.0) as client:
            resp = client.get(f"{_DAEMON_URL}/status")
            if resp.status_code == 200:
                return {
                    "reachable": True,
                    "url": _DAEMON_URL,
                    "note": "daemon will pick up changes on next config reload",
                }
            return {
                "reachable": False,
                "url": _DAEMON_URL,
                "status_code": resp.status_code,
            }
    except (httpx.HTTPError, OSError) as exc:
        return {
            "reachable": False,
            "url": _DAEMON_URL,
            "error": f"{type(exc).__name__}: {exc}",
        }


# Defensive: keep ``dataclasses`` imported even if unused above so that
# downstream maintainers can rely on ``dataclasses.asdict`` here for
# the recommend serialiser. The helper is intentionally explicit (see
# :func:`_selection_to_dict`) so we don't lock the JSON shape to the
# dataclass field order.
_ = dataclasses


__all__ = ["setup_app"]
