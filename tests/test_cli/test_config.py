# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from vaner.cli.commands.config import load_config, set_compute_value


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

[gateway.passthrough]
enabled = true

[gateway.routes]
gpt- = "https://api.openai.com/v1"

[gateway.annotate]
response_trailer = false
system_note = "off"

[gateway.shadow]
rate = 0.15

[mcp]
transport = "sse"
http_host = "0.0.0.0"
http_port = 8999

[intent]
enabled = true
include_global_skills = true
skill_roots = [".cursor/skills"]

[intent.skills_loop]
enabled = false
max_feedback_events_per_cycle = 77

[exploration]
endpoint = "http://127.0.0.1:11434"
model = "qwen2.5-coder:14b"
backend = "ollama"

[privacy]
allowed_paths = ["src/**"]
excluded_patterns = ["*.env"]
redact_patterns = ["secret"]
telemetry = "local"

[limits]
max_age_seconds = 120
max_context_tokens = 2048

[compute]
device = "cuda:1"
cpu_fraction = 0.4
gpu_memory_fraction = 0.7
idle_only = true
idle_cpu_threshold = 0.55
idle_gpu_threshold = 0.8
embedding_device = "cuda"
exploration_concurrency = 6
max_parallel_precompute = 2

[sources.intent_artefacts]
enabled = false

[sources.intent_artefacts.markdown_outline]
enabled = true
max_candidates = 42

[refinement]
enabled = false
max_candidates_per_cycle = 2

[integrations]
guidance_variant = "strong"
advertise_guidance_resource = false

[integrations.context_injection]
mode = "digest_only"
digest_token_budget = 250

[setup]
mode = "advanced"
work_styles = ["coding", "research"]
priority = "quality"
compute_posture = "available_power"
cloud_posture = "hybrid_when_worth_it"
background_posture = "deep_run_aggressive"
completed_at = "2026-04-26T12:00:00+00:00"
version = 1

[policy]
selected_bundle_id = "deep_research"
auto_select = false
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
    assert config.gateway.passthrough_enabled is True
    assert config.gateway.routes["gpt-"] == "https://api.openai.com/v1"
    assert config.gateway.shadow_rate == 0.15
    assert config.mcp.transport == "sse"
    assert config.mcp.http_port == 8999
    if not hasattr(config, "intent"):
        pytest.skip("intent config unavailable on this CLI surface")
    assert config.intent.include_global_skills is True
    assert config.intent.skill_roots == [".cursor/skills"]
    assert config.intent.skills_loop_enabled is False
    assert config.intent.max_feedback_events_per_cycle == 77
    assert config.exploration.endpoint == "http://127.0.0.1:11434"
    assert config.exploration.model == "qwen2.5-coder:14b"
    assert config.privacy.allowed_paths == ["src/**"]
    assert config.max_age_seconds == 120
    assert config.max_context_tokens == 2048
    assert config.compute.device == "cuda:1"
    assert config.compute.cpu_fraction == 0.4
    assert config.compute.gpu_memory_fraction == 0.7
    assert config.compute.idle_only is True
    assert config.compute.idle_cpu_threshold == 0.55
    assert config.compute.idle_gpu_threshold == 0.8
    assert config.compute.embedding_device == "cuda"
    assert config.compute.exploration_concurrency == 6
    assert config.compute.max_parallel_precompute == 2
    assert config.sources.intent_artefacts.enabled is False
    assert config.sources.intent_artefacts.markdown_outline.enabled is True
    assert config.sources.intent_artefacts.markdown_outline.max_candidates == 42
    assert config.refinement.enabled is False
    assert config.refinement.max_candidates_per_cycle == 2
    assert config.integrations.guidance_variant == "strong"
    assert config.integrations.advertise_guidance_resource is False
    assert config.integrations.context_injection.mode == "digest_only"
    assert config.integrations.context_injection.digest_token_budget == 250
    assert config.setup.mode == "advanced"
    assert config.setup.work_styles == ["coding", "research"]
    assert config.setup.priority == "quality"
    assert config.setup.compute_posture == "available_power"
    assert config.setup.cloud_posture == "hybrid_when_worth_it"
    assert config.setup.background_posture == "deep_run_aggressive"
    assert config.setup.completed_at is not None
    assert config.policy.selected_bundle_id == "deep_research"
    assert config.policy.auto_select is False


def test_load_config_merges_global_and_local_setup_policy(
    temp_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_path = tmp_path / "global-config.toml"
    global_path.write_text(
        """
[setup]
priority = "speed"
work_styles = ["writing"]

[policy]
selected_bundle_id = "cost_saver"
auto_select = false
""".strip(),
        encoding="utf-8",
    )
    local_vaner_dir = temp_repo / ".vaner"
    local_vaner_dir.mkdir(parents=True, exist_ok=True)
    (local_vaner_dir / "config.toml").write_text(
        """
[setup]
priority = "privacy"

[policy]
bundle_overrides = { max_context_tokens = 1024 }
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("VANER_GLOBAL_CONFIG", str(global_path))

    config = load_config(temp_repo)

    assert config.setup.priority == "privacy"
    assert config.setup.work_styles == ["writing"]
    assert config.policy.selected_bundle_id == "cost_saver"
    assert config.policy.auto_select is False
    assert config.policy.bundle_overrides == {"max_context_tokens": 1024}


def test_load_config_warns_for_invalid_local_toml(
    temp_repo: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    vaner_dir = temp_repo / ".vaner"
    vaner_dir.mkdir(parents=True, exist_ok=True)
    (vaner_dir / "config.toml").write_text("[setup\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="vaner.cli.commands.config"):
        config = load_config(temp_repo)

    assert config.setup.priority == "balanced"
    assert "Ignoring invalid Vaner config TOML" in caplog.text
    assert str(vaner_dir / "config.toml") in caplog.text


def test_load_config_warns_and_defaults_invalid_semantic_sections(
    temp_repo: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    vaner_dir = temp_repo / ".vaner"
    vaner_dir.mkdir(parents=True, exist_ok=True)
    (vaner_dir / "config.toml").write_text(
        """
[setup]
priority = "not-a-priority"

[policy]
bundle_overrides = "not-a-table"

[integrations]
guidance_variant = "loud"

[limits]
max_age_seconds = "forever"
max_context_tokens = { bad = "shape" }
""".strip(),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="vaner.cli.commands.config"):
        config = load_config(temp_repo)

    assert config.setup.priority == "balanced"
    assert config.policy.bundle_overrides == {}
    assert config.integrations.guidance_variant == "canonical"
    assert config.max_age_seconds == 3600
    assert config.max_context_tokens == 8192
    assert "Ignoring invalid Vaner config section [setup]" in caplog.text
    assert "Ignoring invalid Vaner config section [policy]" in caplog.text
    assert "Ignoring invalid Vaner config section [integrations]" in caplog.text
    assert "Ignoring invalid Vaner config value [limits].max_age_seconds" in caplog.text
    assert "Ignoring invalid Vaner config value [limits].max_context_tokens" in caplog.text


def test_set_compute_value_updates_existing_section(temp_repo):
    vaner_dir = temp_repo / ".vaner"
    vaner_dir.mkdir(parents=True, exist_ok=True)
    config_path = vaner_dir / "config.toml"
    config_path.write_text("[compute]\ncpu_fraction = 0.2\n", encoding="utf-8")

    set_compute_value(temp_repo, "cpu_fraction", 0.5)
    set_compute_value(temp_repo, "device", "cuda:0")

    config = load_config(temp_repo)
    assert config.compute.cpu_fraction == 0.5
    assert config.compute.device == "cuda:0"
