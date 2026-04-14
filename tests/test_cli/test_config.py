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
prefer_local = true
fallback_enabled = true
fallback_base_url = "https://fallback.example.com/v1"
fallback_model = "fallback-model"
fallback_api_key_env = "FALLBACK_KEY"
remote_budget_per_hour = 12

[generation]
use_llm = true
generation_model = "gpt-test"
max_file_chars = 5000
summary_max_tokens = 256
max_concurrent_generations = 2
max_generations_per_cycle = 15

[proxy]
proxy_token = "token"
max_requests_per_minute = 10
ssl_certfile = "/tmp/test-cert.pem"
ssl_keyfile = "/tmp/test-key.pem"

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
    assert config.backend.fallback_enabled is True
    assert config.backend.remote_budget_per_hour == 12
    assert config.generation.use_llm is True
    assert config.generation.generation_model == "gpt-test"
    assert config.generation.max_concurrent_generations == 2
    assert config.generation.max_generations_per_cycle == 15
    assert config.proxy.proxy_token == "token"
    assert config.proxy.ssl_certfile == "/tmp/test-cert.pem"
    assert config.privacy.allowed_paths == ["src/**"]
    assert config.max_age_seconds == 120
    assert config.max_context_tokens == 2048
