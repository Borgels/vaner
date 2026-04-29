# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vaner.setup.answers import SetupAnswers
from vaner.setup.config_io import (
    persist_setup_and_policy,
    read_policy_section,
    read_setup_section,
)


def test_read_sections_return_empty_for_missing_config(tmp_path: Path) -> None:
    assert read_setup_section(tmp_path) == {}
    assert read_policy_section(tmp_path) == {}


def test_read_sections_return_empty_for_corrupt_config(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_dir = tmp_path / ".vaner"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("[setup\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="vaner.setup.config_io"):
        assert read_setup_section(tmp_path) == {}
    assert read_policy_section(tmp_path) == {}
    assert "Ignoring invalid Vaner setup config TOML" in caplog.text


def test_read_sections_ignore_non_table_sections(tmp_path: Path) -> None:
    config_dir = tmp_path / ".vaner"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('setup = "not a table"\n', encoding="utf-8")

    assert read_setup_section(tmp_path) == {}


def test_persist_setup_and_policy_is_idempotent_and_preserves_sections(tmp_path: Path) -> None:
    config_dir = tmp_path / ".vaner"
    config_dir.mkdir()
    config_path = config_dir / "config.toml"
    config_path.write_text(
        '# custom config\n\n[backend]\nmodel = "local"\n\n[setup]\nmode = "simple"\n',
        encoding="utf-8",
    )
    answers = SetupAnswers(
        work_styles=("coding",),
        priority="speed",
        compute_posture="balanced",
        cloud_posture="local_only",
        background_posture="normal",
    )
    completed_at = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    written = persist_setup_and_policy(
        tmp_path,
        answers,
        "local_lightweight",
        completed_at=completed_at,
    )
    first_text = written.read_text(encoding="utf-8")
    persist_setup_and_policy(
        tmp_path,
        answers,
        "local_lightweight",
        completed_at=completed_at,
    )

    assert written == config_path
    assert config_path.read_text(encoding="utf-8") == first_text
    parsed = tomllib.loads(first_text)
    assert parsed["backend"]["model"] == "local"
    assert parsed["setup"]["work_styles"] == ["coding"]
    assert parsed["setup"]["completed_at"] == completed_at.isoformat()
    assert parsed["policy"]["selected_bundle_id"] == "local_lightweight"
    assert not (config_dir / ".config.toml.tmp").exists()
