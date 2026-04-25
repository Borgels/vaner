# SPDX-License-Identifier: Apache-2.0
"""WS4 — `vaner deep-run` CLI tests (0.8.3)."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.deep_run import _parse_until
from vaner.intent.deep_run_gates import (
    reset_cost_gate,
    set_active_session_for_routing,
)


@pytest.fixture(autouse=True)
def _isolate_singletons():
    set_active_session_for_routing(None)
    reset_cost_gate(None)
    yield
    set_active_session_for_routing(None)
    reset_cost_gate(None)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def hi():\n    return 'hi'\n")
    return repo


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# `--until` parser
# ---------------------------------------------------------------------------


class TestParseUntil:
    def test_duration_hours(self) -> None:
        ts = _parse_until("8h", now=1000.0)
        assert ts == 1000.0 + 8 * 3600

    def test_duration_minutes(self) -> None:
        assert _parse_until("45m", now=0.0) == 45 * 60

    def test_duration_days(self) -> None:
        assert _parse_until("2d", now=0.0) == 2 * 86400

    def test_duration_seconds(self) -> None:
        assert _parse_until("90s", now=0.0) == 90.0

    def test_zero_duration_rejected(self) -> None:
        import typer

        with pytest.raises(typer.BadParameter):
            _parse_until("0h", now=0.0)

    def test_time_of_day_today(self) -> None:
        # Pick a base time well before noon so 14:00 today is in the future.
        morning = datetime(2026, 4, 24, 8, 0).astimezone().timestamp()
        ts = _parse_until("14:00", now=morning)
        assert ts > morning
        assert ts - morning < 24 * 3600

    def test_time_of_day_already_past_rolls_to_tomorrow(self) -> None:
        evening = datetime(2026, 4, 24, 22, 0).astimezone().timestamp()
        ts = _parse_until("07:00", now=evening)
        # Roll-forward by ~9 hours; not exactly 24h because the original
        # date may be 22:00 → next day 07:00 (9h jump).
        assert ts > evening
        assert ts - evening > 6 * 3600

    def test_iso_8601_absolute(self) -> None:
        ts = _parse_until("2026-12-31T07:00:00")
        # Sanity: parsed as 31-Dec-2026 in local TZ; must be >> base time
        assert ts > time.time()

    def test_invalid_form_rejects_with_typer_bad_param(self) -> None:
        import typer

        with pytest.raises(typer.BadParameter):
            _parse_until("nonsense")

    def test_empty_string_rejects(self) -> None:
        import typer

        with pytest.raises(typer.BadParameter):
            _parse_until("   ")


# ---------------------------------------------------------------------------
# CLI lifecycle: start → status → list → show → stop
# ---------------------------------------------------------------------------


class TestDeepRunCli:
    def test_status_when_no_session_human(self, runner: CliRunner, repo_root: Path) -> None:
        result = runner.invoke(app, ["deep-run", "status", "--path", str(repo_root)])
        assert result.exit_code == 0, result.output
        assert "No active Deep-Run session" in result.output

    def test_status_when_no_session_json(self, runner: CliRunner, repo_root: Path) -> None:
        result = runner.invoke(app, ["deep-run", "status", "--json", "--path", str(repo_root)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) is None

    def test_start_status_stop_lifecycle_json(self, runner: CliRunner, repo_root: Path) -> None:
        # Start
        start_res = runner.invoke(
            app,
            [
                "deep-run",
                "start",
                "--until",
                "1h",
                "--preset",
                "balanced",
                "--locality",
                "local_only",
                "--json",
                "--path",
                str(repo_root),
            ],
        )
        assert start_res.exit_code == 0, start_res.output
        started = json.loads(start_res.output)
        assert started["status"] == "active"
        assert started["preset"] == "balanced"
        assert started["locality"] == "local_only"
        assert started["cost_cap_usd"] == 0.0

        # Status reflects the active session
        status_res = runner.invoke(app, ["deep-run", "status", "--json", "--path", str(repo_root)])
        assert status_res.exit_code == 0
        status_payload = json.loads(status_res.output)
        assert status_payload is not None
        assert status_payload["id"] == started["id"]

        # List shows the session
        list_res = runner.invoke(app, ["deep-run", "list", "--json", "--path", str(repo_root)])
        assert list_res.exit_code == 0
        listed = json.loads(list_res.output)
        assert any(s["id"] == started["id"] for s in listed)

        # Show by id
        show_res = runner.invoke(
            app,
            [
                "deep-run",
                "show",
                started["id"],
                "--json",
                "--path",
                str(repo_root),
            ],
        )
        assert show_res.exit_code == 0
        shown = json.loads(show_res.output)
        assert shown["id"] == started["id"]

        # Stop returns the summary
        stop_res = runner.invoke(app, ["deep-run", "stop", "--json", "--path", str(repo_root)])
        assert stop_res.exit_code == 0
        summary = json.loads(stop_res.output)
        assert summary["session_id"] == started["id"]
        assert summary["final_status"] == "ended"
        # Honest 4-counter discipline: all four fields present
        assert "matured_kept" in summary
        assert "matured_discarded" in summary
        assert "matured_rolled_back" in summary
        assert "matured_failed" in summary

        # Status now empty
        post_status = runner.invoke(app, ["deep-run", "status", "--json", "--path", str(repo_root)])
        assert json.loads(post_status.output) is None

    def test_stop_with_kill_records_killed_status(self, runner: CliRunner, repo_root: Path) -> None:
        runner.invoke(
            app,
            ["deep-run", "start", "--until", "1h", "--json", "--path", str(repo_root)],
        )
        result = runner.invoke(
            app,
            [
                "deep-run",
                "stop",
                "--kill",
                "--reason",
                "user_request",
                "--json",
                "--path",
                str(repo_root),
            ],
        )
        assert result.exit_code == 0
        summary = json.loads(result.output)
        assert summary["final_status"] == "killed"

    def test_double_start_fails(self, runner: CliRunner, repo_root: Path) -> None:
        runner.invoke(
            app,
            ["deep-run", "start", "--until", "1h", "--json", "--path", str(repo_root)],
        )
        result = runner.invoke(
            app,
            ["deep-run", "start", "--until", "1h", "--json", "--path", str(repo_root)],
        )
        assert result.exit_code != 0

    def test_stop_with_no_session_json_returns_null(self, runner: CliRunner, repo_root: Path) -> None:
        result = runner.invoke(app, ["deep-run", "stop", "--json", "--path", str(repo_root)])
        assert result.exit_code == 0
        assert json.loads(result.output) is None

    def test_show_unknown_session_exit_nonzero(self, runner: CliRunner, repo_root: Path) -> None:
        result = runner.invoke(
            app,
            ["deep-run", "show", "ffffffffffffffff", "--path", str(repo_root)],
        )
        assert result.exit_code != 0

    def test_start_human_output_renders_panel(self, runner: CliRunner, repo_root: Path) -> None:
        result = runner.invoke(app, ["deep-run", "start", "--until", "1h", "--path", str(repo_root)])
        assert result.exit_code == 0, result.output
        assert "Deep-Run started" in result.output
        assert "preset" in result.output
        assert "balanced" in result.output

    def test_status_human_output_when_active(self, runner: CliRunner, repo_root: Path) -> None:
        runner.invoke(app, ["deep-run", "start", "--until", "1h", "--path", str(repo_root)])
        result = runner.invoke(app, ["deep-run", "status", "--path", str(repo_root)])
        assert result.exit_code == 0
        assert "session" in result.output
        assert "balanced" in result.output

    def test_list_human_when_empty(self, runner: CliRunner, repo_root: Path) -> None:
        result = runner.invoke(app, ["deep-run", "list", "--path", str(repo_root)])
        assert result.exit_code == 0
        assert "No Deep-Run sessions" in result.output

    # ------------------------------------------------------------------
    # WS9: anti-autonomy reminder + horizon_bias label rename.
    # ------------------------------------------------------------------

    def test_start_human_output_includes_anti_autonomy_panel(self, runner: CliRunner, repo_root: Path) -> None:
        """WS9: the start panel must end with the anti-autonomy notice."""

        result = runner.invoke(app, ["deep-run", "start", "--until", "1h", "--path", str(repo_root)])
        assert result.exit_code == 0, result.output
        assert "Deep-Run prepares; it does not act" in result.output
        assert "explicit confirmation" in result.output

    def test_start_json_output_includes_prepare_only_notice(self, runner: CliRunner, repo_root: Path) -> None:
        """WS9: --json mode carries the same notice as a structured field."""

        result = runner.invoke(app, ["deep-run", "start", "--until", "1h", "--json", "--path", str(repo_root)])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "prepare_only_notice" in payload
        assert "Deep-Run prepares" in payload["prepare_only_notice"]

    def test_status_renders_horizon_bias_label_not_storage_literal(self, runner: CliRunner, repo_root: Path) -> None:
        """WS9: rendered output uses the friendly label; storage value stays."""

        # Start with explicit horizon=long_horizon so we can assert the label rename.
        runner.invoke(
            app,
            [
                "deep-run",
                "start",
                "--until",
                "1h",
                "--horizon",
                "long_horizon",
                "--path",
                str(repo_root),
            ],
        )
        # Human render: shows the user-facing label.
        result = runner.invoke(app, ["deep-run", "status", "--path", str(repo_root)])
        assert result.exit_code == 0
        assert "Long-horizon work" in result.output
        # JSON view: the underlying storage literal is preserved.
        result_json = runner.invoke(app, ["deep-run", "status", "--json", "--path", str(repo_root)])
        assert result_json.exit_code == 0
        payload = json.loads(result_json.output)
        assert payload["horizon_bias"] == "long_horizon"


class TestHorizonBiasLabel:
    """WS9: storage literal → user-facing rendering helper."""

    def test_known_values_map_to_friendly_strings(self) -> None:
        from vaner.cli.commands.deep_run import horizon_bias_label

        assert horizon_bias_label("likely_next") == "Likely next moves"
        assert horizon_bias_label("long_horizon") == "Long-horizon work"
        assert horizon_bias_label("finish_partials") == "Finish what's in progress"
        assert horizon_bias_label("balanced") == "Balanced"

    def test_unknown_value_falls_back_to_storage_literal(self) -> None:
        from vaner.cli.commands.deep_run import horizon_bias_label

        # Forward-compat: a future literal still renders, just not relabeled.
        assert horizon_bias_label("future_unknown") == "future_unknown"


class TestDurationHelperIsExtracted:
    """WS9: parse_until is exposed at vaner.cli.duration for desktop reuse."""

    def test_helper_importable_at_shared_location(self) -> None:
        from vaner.cli.duration import parse_until

        assert parse_until("8h", now=0.0) == 8 * 3600

    def test_legacy_alias_still_works(self) -> None:
        # The original `_parse_until` import path stays for downstream
        # callers (and the existing test suite imports this name).
        from vaner.cli.commands.deep_run import _parse_until

        assert _parse_until("45m", now=0.0) == 45 * 60
