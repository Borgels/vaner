# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

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
    assert config.exploration.exploration_endpoint == "http://127.0.0.1:11434"
    assert config.exploration.exploration_model == "qwen2.5-coder:14b"
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
