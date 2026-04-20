# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass
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
endpoint = ""      # e.g. "http://127.0.0.1:11434" or "http://127.0.0.1:8000/v1"
model = ""         # e.g. "qwen2.5-coder:32b" -- leave empty to auto-select
backend = "auto"   # "auto" | "ollama" | "openai"
api_key = ""       # optional API key env passthrough for remote exploration endpoints

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

[intent]
enabled = true
include_global_skills = false
skill_roots = [".cursor/skills", ".claude/skills", "skills"]

[intent.skills_loop]
enabled = true
max_feedback_events_per_cycle = 200

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
# Hard wall-clock cap for a single precompute cycle (seconds).
# Safe default: 5 minutes. Set to 0 to disable.
max_cycle_seconds = 300
# Optional total-runtime cap for a continuous `vaner daemon` session (minutes).
# Leave commented / unset for unbounded. Set to e.g. 30 to never ponder
# longer than 30 minutes in one go.
# max_session_minutes = 30

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


@dataclass(slots=True)
class BackendPreset:
    """A canned [backend] configuration the installer or `vaner init` can apply."""

    name: str
    base_url: str
    default_model: str
    api_key_env: str = ""


BACKEND_PRESETS: dict[str, BackendPreset] = {
    "ollama": BackendPreset(
        name="ollama",
        base_url="http://127.0.0.1:11434/v1",
        default_model="qwen2.5-coder:7b",
    ),
    "lmstudio": BackendPreset(
        name="lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        default_model="",
    ),
    "vllm": BackendPreset(
        name="vllm",
        base_url="http://127.0.0.1:8000/v1",
        default_model="",
    ),
    "openai": BackendPreset(
        name="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        api_key_env="OPENAI_API_KEY",
    ),
    "anthropic": BackendPreset(
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-opus-4-5",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "openrouter": BackendPreset(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="anthropic/claude-3.5-sonnet",
        api_key_env="OPENROUTER_API_KEY",
    ),
}


COMPUTE_PRESETS: dict[str, dict[str, object]] = {
    "background": {
        "cpu_fraction": 0.2,
        "gpu_memory_fraction": 0.5,
        "idle_only": True,
    },
    "balanced": {
        "cpu_fraction": 0.5,
        "gpu_memory_fraction": 0.7,
        "idle_only": False,
    },
    "dedicated": {
        "cpu_fraction": 1.0,
        "gpu_memory_fraction": 1.0,
        "idle_only": False,
    },
}


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _prompt(prompt: str, default: str = "") -> str:
    if not _is_tty():
        return default
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return answer or default


def _update_toml_section(text: str, section: str, values: dict[str, object]) -> str:
    """Update keys within a TOML section, leaving unrelated keys untouched.

    This is a deliberately small TOML editor: we only support single-line
    ``key = value`` lines inside a flat ``[section]`` block, which matches
    the shape of ``DEFAULT_CONFIG``. We never touch keys the caller did not
    ask to set, so this function is idempotent.
    """

    def literal(value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if value is None:
            return '""'
        escaped = str(value).replace('"', '\\"')
        return f'"{escaped}"'

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
            lines.append(f"{key} = {literal(val)}")
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
            lines[idx] = f"{key} = {literal(remaining.pop(key))}"
    if remaining:
        insert_at = end
        for key, val in remaining.items():
            lines.insert(insert_at, f"{key} = {literal(val)}")
            insert_at += 1
    out = "\n".join(lines)
    return out + ("\n" if not out.endswith("\n") else "")


def apply_backend_config(
    config_path: Path,
    preset_id: str,
    *,
    base_url: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    force: bool = False,
) -> bool:
    """Apply a backend preset to an existing ``config.toml``.

    Returns ``True`` when the file was modified. Idempotent by default: if
    ``[backend].base_url`` and ``[backend].model`` are already non-empty and
    ``force=False``, the existing values win.
    """
    if preset_id not in BACKEND_PRESETS and (base_url is None or model is None):
        return False
    preset = BACKEND_PRESETS.get(preset_id)
    resolved_base_url = base_url or (preset.base_url if preset else "")
    resolved_model = model or (preset.default_model if preset else "")
    name = preset.name if preset else preset_id

    text = config_path.read_text(encoding="utf-8")
    if not force:
        import tomllib as _tomllib

        try:
            parsed = _tomllib.loads(text)
        except Exception:
            parsed = {}
        existing = parsed.get("backend", {}) if isinstance(parsed, dict) else {}
        if isinstance(existing, dict):
            cur_url = str(existing.get("base_url", "")).strip()
            cur_model = str(existing.get("model", "")).strip()
            if cur_url and cur_model:
                return False

    updated = _update_toml_section(
        text,
        "backend",
        {
            "name": name,
            "base_url": resolved_base_url,
            "model": resolved_model,
        },
    )
    if updated != text:
        config_path.write_text(updated, encoding="utf-8")
        return True
    return False


def apply_compute_preset(
    config_path: Path,
    preset_id: str,
    *,
    max_session_minutes: int | None = None,
) -> bool:
    if preset_id not in COMPUTE_PRESETS and max_session_minutes is None:
        return False
    values: dict[str, object] = dict(COMPUTE_PRESETS.get(preset_id, {}))
    if max_session_minutes is not None and max_session_minutes > 0:
        values["max_session_minutes"] = int(max_session_minutes)
    if not values:
        return False
    text = config_path.read_text(encoding="utf-8")
    updated = _update_toml_section(text, "compute", values)
    if updated != text:
        config_path.write_text(updated, encoding="utf-8")
        return True
    return False


def interactive_backend_choice() -> str | None:
    """Render the same backend menu the installer shows. TTY-only."""
    if not _is_tty():
        return None
    menu = [
        ("1", "ollama", "Ollama — local, auto-detect, privacy-first (recommended)"),
        ("2", "lmstudio", "LM Studio — local app you already run"),
        ("3", "vllm", "vLLM / OpenAI-compatible self-hosted"),
        ("4", "openai", "OpenAI — cloud, needs API key"),
        ("5", "anthropic", "Anthropic — cloud, needs API key"),
        ("6", "openrouter", "OpenRouter — cloud, 100+ models via one key"),
        ("7", "skip", "Skip (read-only MCP tools still work)"),
    ]
    sys.stdout.write("\nPick a model backend (Vaner needs an LLM for scenario expansion):\n")
    for num, _slug, label in menu:
        sys.stdout.write(f"  {num}) {label}\n")
    choice = _prompt("Choice", "1")
    mapping = {num: slug for num, slug, _ in menu}
    mapping.update({slug: slug for _num, slug, _ in menu})
    return mapping.get(choice, "skip")


def interactive_compute_choice() -> str | None:
    if not _is_tty():
        return None
    sys.stdout.write("\nCompute budget:\n")
    sys.stdout.write("  1) background — cpu_fraction=0.2, gpu_memory_fraction=0.5, idle_only=true (default)\n")
    sys.stdout.write("  2) balanced   — cpu_fraction=0.5, gpu_memory_fraction=0.7\n")
    sys.stdout.write("  3) dedicated  — cpu_fraction=1.0, gpu_memory_fraction=1.0\n")
    choice = _prompt("Choice", "1")
    return {"1": "background", "2": "balanced", "3": "dedicated"}.get(choice, choice if choice in COMPUTE_PRESETS else "background")


def _default_vaner_feedback_skill() -> str:
    default_path = Path(__file__).resolve().parents[4] / "src" / "vaner" / "defaults" / "skills" / "vaner-feedback" / "SKILL.md"
    if default_path.exists():
        return default_path.read_text(encoding="utf-8")
    return (
        "---\n"
        "name: vaner-feedback\n"
        "description: Report scenario outcomes back to Vaner after completing a task.\n"
        "tags: [vaner, feedback]\n"
        "vaner:\n"
        "  kind: research\n"
        "  feedback: auto\n"
        "x-vaner-managed: true\n"
        "---\n\n"
        "Use this skill when finishing a task that used Vaner MCP scenarios.\n\n"
        "1. Keep scenario ids returned by list/get/expand calls.\n"
        "2. Call report_outcome with id, result (useful|partial|irrelevant), optional note.\n"
        "3. Set skill to vaner-feedback for attribution.\n"
    )


def _write_managed_skill(path: Path, content: str) -> bool:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if "x-vaner-managed: true" not in existing:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def write_mcp_configs(repo_root: Path) -> tuple[list[Path], str]:
    written: list[Path] = []
    vaner_command = shutil.which("vaner")
    uvx_command = shutil.which("uvx")
    if vaner_command:
        command = vaner_command
        args = ["mcp", "--path", "."]
    elif uvx_command:
        command = uvx_command
        args = ["--from", "vaner[mcp]", "vaner", "mcp", "--path", "."]
    else:
        command = "vaner"
        args = ["mcp", "--path", "."]
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
        servers["vaner"] = {"command": command, "args": args}
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
        claude_args = list(args)
        if "--path" in claude_args:
            idx = claude_args.index("--path")
            claude_args[idx + 1] = str(repo_root)
        claude_servers["vaner"] = {"command": command, "args": claude_args}
    claude_path.write_text(json.dumps(claude_payload, indent=2) + "\n", encoding="utf-8")
    written.append(claude_path)
    skill_content = _default_vaner_feedback_skill()
    managed_targets = [
        repo_root / ".cursor" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md",
    ]
    for target in managed_targets:
        if _write_managed_skill(target, skill_content):
            written.append(target)
    return written, command
