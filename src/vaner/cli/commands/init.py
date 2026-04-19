# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULT_CONFIG = """# Vaner configuration
# Run `vaner init` to regenerate this file.
#
# Optional capability: [backend] is used by `vaner proxy`.
# MCP-first mode does not require proxy/backend by default.
# If you enable proxy, set backend.base_url and backend.model to your endpoint.
#
# Examples:
#   OpenAI:       base_url = "https://api.openai.com/v1"   model = "gpt-4o"
#   Anthropic:    base_url = "https://api.anthropic.com/v1" model = "claude-opus-4-5"
#   Ollama:       base_url = "http://127.0.0.1:11434/v1"   model = "qwen2.5-coder:32b"
#   vLLM/local:   base_url = "http://127.0.0.1:8000/v1"    model = "Qwen/Qwen2.5-Coder-32B"

[backend]
name = "custom"
base_url = ""        # Required only when using `vaner proxy`
model = ""           # Required only when using `vaner proxy`
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

[gateway.passthrough]
enabled = false

[gateway.routes]
# Route model prefixes to providers while keeping IDE model picker intact.
# gpt- = "https://api.openai.com/v1"
# claude- = "https://api.anthropic.com/v1"
# gemini- = "https://generativelanguage.googleapis.com/v1beta/openai"

[gateway.annotate]
response_trailer = false
system_note = "off"

[gateway.shadow]
rate = 0.0

[mcp]
transport = "stdio"
http_host = "127.0.0.1"
http_port = 8472

[compute]
device = "auto"
cpu_fraction = 0.2
gpu_memory_fraction = 0.5
idle_only = true
idle_cpu_threshold = 0.6
idle_gpu_threshold = 0.7
embedding_device = "cpu"
exploration_concurrency = 4
max_parallel_precompute = 1

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


def write_mcp_configs(repo_root: Path) -> list[Path]:
    written: list[Path] = []
    cursor_dir = repo_root / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    cursor_path = cursor_dir / "mcp.json"
    cursor_payload: dict[str, object] = {"mcpServers": {}}
    if cursor_path.exists():
        try:
            cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))
        except Exception:
            cursor_payload = {"mcpServers": {}}
    servers = cursor_payload.setdefault("mcpServers", {})
    if isinstance(servers, dict):
        servers["vaner"] = {"command": "vaner", "args": ["mcp", "--path", "."]}
    cursor_path.write_text(json.dumps(cursor_payload, indent=2) + "\n", encoding="utf-8")
    written.append(cursor_path)

    claude_path = Path.home() / ".claude" / "claude_desktop_config.json"
    claude_path.parent.mkdir(parents=True, exist_ok=True)
    claude_payload: dict[str, object] = {"mcpServers": {}}
    if claude_path.exists():
        try:
            claude_payload = json.loads(claude_path.read_text(encoding="utf-8"))
        except Exception:
            claude_payload = {"mcpServers": {}}
        backup_path = claude_path.with_suffix(f".backup-{int(time.time())}.json")
        backup_path.write_text(json.dumps(claude_payload, indent=2) + "\n", encoding="utf-8")
    claude_servers = claude_payload.setdefault("mcpServers", {})
    if isinstance(claude_servers, dict):
        claude_servers["vaner"] = {"command": "vaner", "args": ["mcp", "--path", str(repo_root)]}
    claude_path.write_text(json.dumps(claude_payload, indent=2) + "\n", encoding="utf-8")
    written.append(claude_path)
    return written
