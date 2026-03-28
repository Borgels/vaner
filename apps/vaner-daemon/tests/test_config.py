"""Tests for DaemonConfig."""
from __future__ import annotations

import json


from vaner_daemon.config import DaemonConfig


def test_default_config_no_file(tmp_path):
    """Loads default config with no file present — all defaults correct."""
    cfg = DaemonConfig.load(tmp_path)
    assert cfg.repo_path == tmp_path.resolve()
    assert ".py" in cfg.watch_extensions
    assert ".ts" in cfg.watch_extensions
    assert ".git" in cfg.watch_ignore_dirs
    assert cfg.max_active_files == 10
    assert cfg.diff_cache_ttl_seconds == 30.0
    assert cfg.min_seconds_between_prep == 5.0
    assert cfg.cache_freshness_seconds == 1800.0
    assert cfg.max_concurrent_jobs == 2
    assert cfg.max_queue_depth == 20


def test_round_trip(tmp_path):
    """Saves and reloads config — round-trip is lossless."""
    cfg = DaemonConfig.load(tmp_path)
    cfg.max_active_files = 7
    cfg.diff_cache_ttl_seconds = 60.0
    cfg.watch_extensions = [".py", ".rs"]
    cfg.watch_ignore_dirs = {".git", "dist"}
    cfg.save(tmp_path)

    cfg2 = DaemonConfig.load(tmp_path)
    assert cfg2.max_active_files == 7
    assert cfg2.diff_cache_ttl_seconds == 60.0
    assert set(cfg2.watch_extensions) == {".py", ".rs"}
    assert cfg2.watch_ignore_dirs == {".git", "dist"}


def test_bad_json_falls_back_to_defaults(tmp_path):
    """Bad JSON in config file → falls back to defaults, no crash."""
    config_dir = tmp_path / ".vaner"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("this is not json {{{")

    cfg = DaemonConfig.load(tmp_path)
    # Should not crash and should have defaults
    assert cfg.max_active_files == 10
    assert cfg.repo_path == tmp_path.resolve()


def test_partial_config_keeps_defaults_for_missing_keys(tmp_path):
    """Partial config file merges with defaults for missing keys."""
    config_dir = tmp_path / ".vaner"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({"max_active_files": 5}))

    cfg = DaemonConfig.load(tmp_path)
    assert cfg.max_active_files == 5
    assert cfg.diff_cache_ttl_seconds == 30.0  # default preserved


def test_watch_ignore_dirs_loaded_as_set(tmp_path):
    """watch_ignore_dirs stored as list in JSON, loaded as set."""
    config_dir = tmp_path / ".vaner"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"watch_ignore_dirs": [".git", "node_modules"]})
    )
    cfg = DaemonConfig.load(tmp_path)
    assert isinstance(cfg.watch_ignore_dirs, set)
    assert cfg.watch_ignore_dirs == {".git", "node_modules"}
