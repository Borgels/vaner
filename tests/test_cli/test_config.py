# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.cli.commands.config import load_config


def test_load_config_reads_toml_values(temp_repo):
    vaner_dir = temp_repo / ".vaner"
    vaner_dir.mkdir(parents=True, exist_ok=True)
    (vaner_dir / "config.toml").write_text(
        """
[backend]
name = "openai"
base_url = "https://example.com/v1"
model = "example-model"
api_key_env = "API_KEY"

[privacy]
allowed_paths = ["src/**"]
excluded_patterns = ["*.env"]
redact_patterns = ["secret"]
telemetry = "local"

[limits]
max_age_seconds = 120
max_context_tokens = 2048
""".strip(),
        encoding="utf-8",
    )

    config = load_config(temp_repo)

    assert config.backend.base_url == "https://example.com/v1"
    assert config.privacy.allowed_paths == ["src/**"]
    assert config.max_age_seconds == 120
    assert config.max_context_tokens == 2048
