# SPDX-License-Identifier: Apache-2.0
"""Tests for `vaner setup` CLI (0.8.6 WS6)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.setup import setup_app
from vaner.setup.hardware import HardwareProfile

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_hardware(monkeypatch: pytest.MonkeyPatch) -> HardwareProfile:
    """Pin hardware detection to a deterministic capable-tier profile.

    Mocking ``detect`` keeps the wizard tests independent of the host
    machine — test outcomes shouldn't change because the runner has a
    GPU, more RAM, etc.
    """

    profile = HardwareProfile(
        os="linux",
        cpu_class="mid",
        ram_gb=16,
        gpu="integrated",
        gpu_vram_gb=None,
        is_battery=False,
        thermal_constrained=False,
        detected_runtimes=(),
        detected_models=(),
        tier="capable",
    )
    monkeypatch.setattr("vaner.cli.commands.setup.detect", lambda: profile)
    return profile


# ---------------------------------------------------------------------------
# wizard
# ---------------------------------------------------------------------------


def test_accept_defaults_writes_config(tmp_path: Path, fake_hardware: HardwareProfile) -> None:
    """`vaner setup wizard --accept-defaults` non-interactively writes config."""

    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        setup_app,
        ["wizard", "--accept-defaults", "--path", str(repo), "--yes"],
    )
    assert result.exit_code == 0, result.output
    config_path = repo / ".vaner" / "config.toml"
    assert config_path.exists()
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["setup"]["mode"] == "simple"
    assert parsed["setup"]["work_styles"] == ["mixed"]
    assert parsed["setup"]["priority"] == "balanced"
    assert parsed["setup"]["completed_at"]
    # The default answers + capable hardware should pick hybrid_balanced.
    assert parsed["policy"]["selected_bundle_id"] == "hybrid_balanced"


def test_wizard_with_scripted_answers(tmp_path: Path, fake_hardware: HardwareProfile) -> None:
    """Drive the interactive wizard with scripted stdin answers."""

    repo = tmp_path / "repo"
    repo.mkdir()
    # Q1 work_styles: "coding" (token 6), Q2 priority: speed (2),
    # Q3 compute: balanced (2), Q4 cloud: local_only (1),
    # Q5 background: normal (2). Then 'y' to write.
    scripted = "\n".join(["6", "2", "2", "1", "2", "y", ""]) + "\n"
    result = runner.invoke(
        setup_app,
        ["wizard", "--path", str(repo)],
        input=scripted,
    )
    assert result.exit_code == 0, result.output
    config_path = repo / ".vaner" / "config.toml"
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["setup"]["work_styles"] == ["coding"]
    assert parsed["setup"]["priority"] == "speed"
    assert parsed["setup"]["cloud_posture"] == "local_only"
    # local_only + speed + capable should pick a local bundle (not a
    # cloud-preferred one). Concrete winner depends on the scoring;
    # we only assert the cloud-posture filter held.
    chosen = parsed["policy"]["selected_bundle_id"]
    assert chosen in {"local_balanced", "local_lightweight", "cost_saver"}


def test_wizard_aborts_on_cloud_widening_decline(
    tmp_path: Path,
    fake_hardware: HardwareProfile,
) -> None:
    """Decline the cloud-widening warning and config stays unchanged."""

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".vaner").mkdir()
    config_path = repo / ".vaner" / "config.toml"
    # Pre-seed with local_lightweight so the wizard's selection
    # widens posture (local_only -> hybrid).
    config_path.write_text(
        '[setup]\nmode = "simple"\nwork_styles = ["mixed"]\n[policy]\nselected_bundle_id = "local_lightweight"\n',
        encoding="utf-8",
    )
    snapshot = config_path.read_text(encoding="utf-8")

    # Q1 mixed (8), Q2 balanced (1), Q3 balanced (2), Q4 hybrid (3),
    # Q5 normal (2), then 'n' to decline cloud-widening warning.
    scripted = "\n".join(["8", "1", "2", "3", "2", "n", ""]) + "\n"
    result = runner.invoke(
        setup_app,
        ["wizard", "--path", str(repo)],
        input=scripted,
    )
    assert result.exit_code == 1, result.output
    # Config must be byte-identical — wizard aborted before write.
    assert config_path.read_text(encoding="utf-8") == snapshot


# ---------------------------------------------------------------------------
# recommend
# ---------------------------------------------------------------------------


def test_recommend_emits_valid_json(fake_hardware: HardwareProfile) -> None:
    """`vaner setup recommend` reads stdin JSON, writes valid SelectionResult JSON."""

    payload = {
        "work_styles": ["research"],
        "priority": "quality",
        "compute_posture": "available_power",
        "cloud_posture": "hybrid_when_worth_it",
        "background_posture": "deep_run_aggressive",
    }
    result = runner.invoke(setup_app, ["recommend"], input=json.dumps(payload))
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert "bundle" in parsed
    assert "id" in parsed["bundle"]
    assert "label" in parsed["bundle"]
    assert "score" in parsed
    assert "reasons" in parsed
    assert "runner_ups" in parsed
    assert "forced_fallback" in parsed
    assert isinstance(parsed["reasons"], list)
    assert isinstance(parsed["runner_ups"], list)


def test_recommend_with_answers_path(tmp_path: Path, fake_hardware: HardwareProfile) -> None:
    """`--answers <path>` reads from disk instead of stdin."""

    answers_file = tmp_path / "answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "work_styles": ["coding"],
                "priority": "speed",
                "compute_posture": "balanced",
                "cloud_posture": "local_only",
                "background_posture": "normal",
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(setup_app, ["recommend", "--answers", str(answers_file)])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    # local_only filter must hold.
    assert parsed["bundle"]["local_cloud_posture"] in {"local_only", "local_preferred"}


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_apply_with_explicit_bundle_id(
    tmp_path: Path,
    fake_hardware: HardwareProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`apply --bundle-id <id>` skips selection and pins the bundle."""

    # Avoid a live daemon ping in tests.
    monkeypatch.setattr(
        "vaner.cli.commands.setup._ping_daemon_for_refresh",
        lambda: {"reachable": False, "url": "test"},
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        setup_app,
        ["apply", "--bundle-id", "deep_research", "--path", str(repo)],
    )
    assert result.exit_code == 0, result.output
    parsed = tomllib.loads((repo / ".vaner" / "config.toml").read_text(encoding="utf-8"))
    assert parsed["policy"]["selected_bundle_id"] == "deep_research"


def test_apply_unknown_bundle_id_fails_cleanly(
    tmp_path: Path,
    fake_hardware: HardwareProfile,
) -> None:
    """`apply --bundle-id nonexistent` exits non-zero with a clear error."""

    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(
        setup_app,
        ["apply", "--bundle-id", "nonexistent", "--path", str(repo)],
    )
    assert result.exit_code != 0
    assert "unknown bundle id" in (result.output + (result.stderr if result.stderr else "")).lower()


def test_apply_with_answers_path(
    tmp_path: Path,
    fake_hardware: HardwareProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`apply --answers <path>` runs selection + persists without prompts."""

    monkeypatch.setattr(
        "vaner.cli.commands.setup._ping_daemon_for_refresh",
        lambda: {"reachable": False, "url": "test"},
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(
        json.dumps(
            {
                "work_styles": ["coding"],
                "priority": "balanced",
                "compute_posture": "balanced",
                "cloud_posture": "ask_first",
                "background_posture": "normal",
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        setup_app,
        ["apply", "--answers", str(answers_file), "--path", str(repo), "--json"],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["selected_bundle_id"]
    assert "config_path" in parsed


def test_apply_blocks_on_cloud_widening_without_confirm(
    tmp_path: Path,
    fake_hardware: HardwareProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`apply --bundle-id <wider>` aborts when cloud posture would widen.

    The 0.8.6 follow-up adds a non-interactive cloud-widening guard to the
    batch apply path so the desktop / CI surfaces can rely on the engine,
    not their own pre-flight, to enforce the invariant.
    """

    monkeypatch.setattr(
        "vaner.cli.commands.setup._ping_daemon_for_refresh",
        lambda: {"reachable": False, "url": "test"},
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    # Pin the prior bundle to a strict-local one so any move toward a
    # hybrid bundle widens the cloud posture.
    result_first = runner.invoke(
        setup_app,
        ["apply", "--bundle-id", "local_lightweight", "--path", str(repo)],
    )
    assert result_first.exit_code == 0, result_first.output

    # Now try to widen to hybrid_quality without --confirm-cloud-widening.
    result_blocked = runner.invoke(
        setup_app,
        ["apply", "--bundle-id", "hybrid_quality", "--path", str(repo), "--json"],
    )
    assert result_blocked.exit_code != 0
    parsed = json.loads(result_blocked.output)
    assert parsed["blocked"] is True
    assert parsed["block_reason"] == "cloud_widening_requires_confirm"
    assert parsed["widens_cloud_posture"] is True
    assert parsed["warnings"], "expected at least one widening warning"


def test_apply_proceeds_with_confirm_cloud_widening(
    tmp_path: Path,
    fake_hardware: HardwareProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`apply --bundle-id <wider> --confirm-cloud-widening` proceeds."""

    monkeypatch.setattr(
        "vaner.cli.commands.setup._ping_daemon_for_refresh",
        lambda: {"reachable": False, "url": "test"},
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    runner.invoke(setup_app, ["apply", "--bundle-id", "local_lightweight", "--path", str(repo)])
    result = runner.invoke(
        setup_app,
        [
            "apply",
            "--bundle-id",
            "hybrid_quality",
            "--path",
            str(repo),
            "--confirm-cloud-widening",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["selected_bundle_id"] == "hybrid_quality"
    assert parsed["widens_cloud_posture"] is True


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_with_no_config(tmp_path: Path, fake_hardware: HardwareProfile) -> None:
    """`vaner setup show` on a fresh dir reports setup not yet completed."""

    repo = tmp_path / "fresh"
    repo.mkdir()
    result = runner.invoke(setup_app, ["show", "--path", str(repo)])
    assert result.exit_code == 0, result.output
    assert "Setup not yet completed" in result.output


def test_show_json_with_config(
    tmp_path: Path,
    fake_hardware: HardwareProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After `wizard --accept-defaults`, `show --json` includes setup + policy."""

    monkeypatch.setattr(
        "vaner.cli.commands.setup._ping_daemon_for_refresh",
        lambda: {"reachable": False, "url": "test"},
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    runner.invoke(
        setup_app,
        ["wizard", "--accept-defaults", "--path", str(repo), "--yes"],
    )
    result = runner.invoke(setup_app, ["show", "--path", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["setup"]["mode"] == "simple"
    assert parsed["policy"]["selected_bundle_id"] == "hybrid_balanced"
    assert parsed["hardware"]["tier"] == "capable"
    assert parsed["applied_policy"]["bundle_id"] == "hybrid_balanced"


# ---------------------------------------------------------------------------
# hardware
# ---------------------------------------------------------------------------


def test_hardware_subcommand_does_not_raise(fake_hardware: HardwareProfile) -> None:
    """`vaner setup hardware` runs `detect()` once and prints OS/CPU/RAM lines."""

    result = runner.invoke(setup_app, ["hardware"])
    assert result.exit_code == 0, result.output
    assert "Linux" in result.output or "linux" in result.output
    assert "CPU class" in result.output
    assert "RAM" in result.output


def test_hardware_json(fake_hardware: HardwareProfile) -> None:
    """`vaner setup hardware --json` emits valid JSON with the expected fields."""

    result = runner.invoke(setup_app, ["hardware", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["os"] == "linux"
    assert parsed["cpu_class"] == "mid"
    assert parsed["ram_gb"] == 16
    assert parsed["tier"] == "capable"


# ---------------------------------------------------------------------------
# init chaining
# ---------------------------------------------------------------------------


def test_init_chains_into_wizard_when_interactive(
    tmp_path: Path,
    fake_hardware: HardwareProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive `vaner init` interactively; confirm wizard runs and persists."""

    repo = tmp_path / "repo"
    repo.mkdir()
    # Avoid touching ~/.claude during MCP wiring; init's primer / MCP
    # config code paths create files there. Pin HOME to a tmp dir.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    # Track whether the wizard was invoked. We monkeypatch the wizard
    # callable that init imports lazily.
    wizard_calls: list[str] = []

    def _fake_wizard(path: str | None = None, **_kwargs: object) -> None:
        wizard_calls.append(path or "")

    monkeypatch.setattr("vaner.cli.commands.setup.wizard_cmd", _fake_wizard)

    # Scripted answers for init's existing prompts:
    # - Step 1 backend: "7" → "skip" (but backend_preset stays None; downstream
    #   compute prompt still fires because the check is `backend_preset != "skip"`)
    # - Step 2 compute: "1" → background
    # - Shell completion confirm: "n" (skip subprocess install)
    # - Wizard chain confirm: "y" (we want to chain in)
    scripted = "\n".join(["7", "1", "n", "y", ""]) + "\n"
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(repo),
            "--interactive",
            "--no-mcp",
            "--no-primer",
        ],
        input=scripted,
    )
    assert result.exit_code == 0, result.output
    assert wizard_calls, "vaner init did not chain into the setup wizard"


def test_init_does_not_chain_when_non_interactive(
    tmp_path: Path,
    fake_hardware: HardwareProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`vaner init --no-interactive` does not invoke the wizard."""

    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    wizard_calls: list[str] = []

    def _fake_wizard(path: str | None = None, **_kwargs: object) -> None:
        wizard_calls.append(path or "")

    monkeypatch.setattr("vaner.cli.commands.setup.wizard_cmd", _fake_wizard)

    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(repo),
            "--no-interactive",
            "--backend-preset",
            "skip",
            "--no-mcp",
            "--no-primer",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not wizard_calls
