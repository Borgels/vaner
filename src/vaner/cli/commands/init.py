# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

DEFAULT_CONFIG = """# Vaner configuration
# Run `vaner init` to regenerate this file.
#
# REQUIRED: Set backend.base_url and backend.model to your LLM endpoint.
# Vaner works with any OpenAI-compatible API: OpenAI, Anthropic (via proxy),
# local Ollama, vLLM, LM Studio, etc.
#
# Examples:
#   OpenAI:       base_url = "https://api.openai.com/v1"   model = "gpt-4o"
#   Anthropic:    base_url = "https://api.anthropic.com/v1" model = "claude-opus-4-5"
#   Ollama:       base_url = "http://127.0.0.1:11434/v1"   model = "qwen2.5-coder:32b"
#   vLLM/local:   base_url = "http://127.0.0.1:8000/v1"    model = "Qwen/Qwen2.5-Coder-32B"

[backend]
name = "custom"
base_url = ""        # REQUIRED -- your LLM endpoint URL
model = ""           # REQUIRED -- model name to use
api_key_env = "OPENAI_API_KEY"   # env var that holds your API key
prefer_local = true
fallback_enabled = false
fallback_base_url = ""
fallback_model = ""
fallback_api_key_env = "OPENAI_API_KEY"
remote_budget_per_hour = 60

[generation]
# If true, Vaner uses the backend LLM to generate file summaries during
# background precompute. Significantly improves context quality.
use_llm = false
generation_model = ""   # leave empty to inherit from [backend]
max_file_chars = 8000
summary_max_tokens = 400
max_concurrent_generations = 4
max_generations_per_cycle = 200

[exploration]
# Optional: separate LLM Vaner uses internally for background exploration.
# Leave endpoint empty to auto-detect a local Ollama or vLLM instance.
enabled = true
endpoint = ""      # e.g. "http://127.0.0.1:11434" or "http://127.0.0.1:8000/v1"
model = ""         # e.g. "qwen2.5-coder:32b" -- leave empty to auto-select
backend = "auto"   # "auto" | "ollama" | "openai"

[proxy]
proxy_token = ""
max_requests_per_minute = 120
ssl_certfile = ""
ssl_keyfile = ""

[privacy]
allowed_paths = ["."]
excluded_patterns = ["*.env", "*.key", "*.pem", "credentials*", "secrets*"]
redact_patterns = []
telemetry = "local"

[limits]
max_age_seconds = 3600
max_context_tokens = 4096
"""


def init_repo(repo_root: Path) -> Path:
    vaner_dir = repo_root / ".vaner"
    runtime_dir = vaner_dir / "runtime"
    vaner_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path = vaner_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return config_path
