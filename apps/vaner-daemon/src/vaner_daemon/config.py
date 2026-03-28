"""Vaner daemon configuration.

Loads from .vaner/config.json in the watched repo root.
All fields have sensible defaults — config file is optional.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DaemonConfig:
    # Path to the repo being watched
    repo_path: Path = field(default_factory=lambda: Path.cwd())

    # File watch settings
    watch_extensions: list[str] = field(default_factory=lambda: [
        ".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs", ".java",
        ".c", ".cpp", ".h", ".md", ".toml", ".yaml", ".yml", ".json",
    ])
    watch_ignore_dirs: set[str] = field(default_factory=lambda: {
        ".git", "__pycache__", ".venv", "node_modules", ".vaner",
        ".ruff_cache", ".mypy_cache", ".pytest_cache", "dist", "build",
    })

    # State engine settings
    max_active_files: int = 10        # LRU window for recently touched files
    diff_cache_ttl_seconds: float = 30.0

    # Preparation trigger thresholds
    min_seconds_between_prep: float = 5.0    # debounce rapid saves
    cache_freshness_seconds: float = 1800.0  # 30 min before full refresh

    # Resource limits
    max_concurrent_jobs: int = 2
    max_queue_depth: int = 20

    @classmethod
    def load(cls, repo_path: Path) -> "DaemonConfig":
        """Load config from .vaner/config.json, falling back to defaults."""
        config_file = repo_path / ".vaner" / "config.json"
        cfg = cls(repo_path=repo_path)
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text())
                for key, val in data.items():
                    if hasattr(cfg, key):
                        # Convert lists to sets where field is a set
                        field_val = getattr(cfg, key)
                        if isinstance(field_val, set) and isinstance(val, list):
                            setattr(cfg, key, set(val))
                        else:
                            setattr(cfg, key, val)
            except Exception:
                pass  # bad config → use defaults
        # Normalise repo_path to absolute Path
        cfg.repo_path = Path(repo_path).expanduser().resolve()
        return cfg

    def save(self, repo_path: Path) -> None:
        """Write current config to .vaner/config.json."""
        config_file = repo_path / ".vaner" / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            k: list(v) if isinstance(v, set) else str(v) if isinstance(v, Path) else v
            for k, v in self.__dict__.items()
            if k != "repo_path"
        }
        config_file.write_text(json.dumps(data, indent=2))
