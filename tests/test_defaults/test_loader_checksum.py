# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vaner.defaults.loader import (
    DefaultsIntegrityError,
    DefaultsVersionError,
    _enforce_top_manifest_version,
    _resolve_from_manifest,
    _version_tuple,
    drain_checksum_mismatches,
)


def _write_group(tmp_group: Path, key: str, body: str, sha: str) -> None:
    tmp_group.mkdir(parents=True, exist_ok=True)
    (tmp_group / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "files": {
                    key: {"path": f"./{key}.json", "sha256": sha, "size_bytes": len(body)},
                },
            }
        )
    )
    (tmp_group / f"{key}.json").write_text(body)


@pytest.fixture
def patched_defaults_dir(tmp_path, monkeypatch):
    import vaner.defaults.loader as loader

    monkeypatch.setattr(loader, "_DEFAULTS_DIR", tmp_path)
    return tmp_path


def test_version_tuple_parses_standard():
    assert _version_tuple("0.7.0") == (0, 7, 0)
    assert _version_tuple("1.2.3") == (1, 2, 3)


def test_version_tuple_handles_suffixes():
    assert _version_tuple("0.7.1rc1") == (0, 7, 1)
    assert _version_tuple("0.8.0") == (0, 8, 0)


def test_version_tuple_empty_returns_zero():
    assert _version_tuple("") == (0,)


def test_checksum_mismatch_raises(patched_defaults_dir):
    body = '{"foo": "bar"}'
    # Wrong SHA on purpose
    wrong_sha = "0" * 64
    _write_group(patched_defaults_dir / "group_a", "file_x", body, wrong_sha)
    with pytest.raises(DefaultsIntegrityError) as excinfo:
        _resolve_from_manifest("group_a", "file_x", fallback="./file_x.json")
    assert excinfo.value.key == "file_x"
    assert excinfo.value.expected == wrong_sha


def test_checksum_match_returns_path(patched_defaults_dir):
    body = '{"hello": "world"}'
    real_sha = hashlib.sha256(body.encode()).hexdigest()
    _write_group(patched_defaults_dir / "group_b", "file_y", body, real_sha)
    path = _resolve_from_manifest("group_b", "file_y", fallback="./file_y.json")
    assert path.exists()
    assert path.read_text() == body


def test_permissive_mode_logs_and_continues(patched_defaults_dir, monkeypatch):
    monkeypatch.setenv("VANER_DEFAULTS_ALLOW_MISMATCH", "1")
    body = '{"foo": 1}'
    wrong_sha = "1" * 64
    _write_group(patched_defaults_dir / "group_c", "file_z", body, wrong_sha)
    # Clear any pending log entries from prior tests
    drain_checksum_mismatches()
    path = _resolve_from_manifest("group_c", "file_z", fallback="./file_z.json")
    assert path.exists()  # returned anyway under permissive mode
    pending = drain_checksum_mismatches()
    assert len(pending) == 1
    assert pending[0][0] == "file_z"
    assert pending[0][1] == wrong_sha


def test_drain_clears_log(patched_defaults_dir, monkeypatch):
    monkeypatch.setenv("VANER_DEFAULTS_ALLOW_MISMATCH", "1")
    _write_group(patched_defaults_dir / "g", "f", "x", "2" * 64)
    _resolve_from_manifest("g", "f", fallback="./f.json")
    assert len(drain_checksum_mismatches()) == 1
    assert drain_checksum_mismatches() == []


def test_no_sha_entry_returns_path_unchecked(patched_defaults_dir):
    (patched_defaults_dir / "grp").mkdir()
    (patched_defaults_dir / "grp" / "manifest.json").write_text(json.dumps({"schema_version": "1", "files": {"f": "./f.json"}}))
    (patched_defaults_dir / "grp" / "f.json").write_text("{}")
    path = _resolve_from_manifest("grp", "f", fallback="./f.json")
    assert path.exists()


def test_min_reader_version_enforced(patched_defaults_dir):
    (patched_defaults_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "min_reader_version": "99.0.0",
                "groups": {},
            }
        )
    )
    with pytest.raises(DefaultsVersionError) as excinfo:
        _enforce_top_manifest_version()
    assert "99.0.0" in str(excinfo.value)


def test_min_reader_version_absent_ok(patched_defaults_dir):
    (patched_defaults_dir / "manifest.json").write_text(json.dumps({"schema_version": "1"}))
    _enforce_top_manifest_version()  # no raise


def test_min_reader_version_satisfied(patched_defaults_dir):
    (patched_defaults_dir / "manifest.json").write_text(json.dumps({"schema_version": "1", "min_reader_version": "0.1.0"}))
    _enforce_top_manifest_version()  # no raise
