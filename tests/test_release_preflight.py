from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PREFLIGHT_PATH = REPO_ROOT / "scripts" / "release" / "preflight.py"
SPEC = importlib.util.spec_from_file_location("vaner_release_preflight", PREFLIGHT_PATH)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def test_release_assets_are_deduped_and_primary_excludes_sigstore(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "vaner-0.8.9-py3-none-any.whl"
    sdist = dist / "vaner-0.8.9.tar.gz"
    wheel.write_text("wheel", encoding="utf-8")
    sdist.write_text("sdist", encoding="utf-8")
    Path(f"{wheel}.sigstore.json").write_text("bundle", encoding="utf-8")
    sbom = tmp_path / "sbom.json"
    sbom.write_text("{}", encoding="utf-8")

    assets = preflight.release_assets(dist, sbom)
    assert len(assets) == len(set(assets))
    assert Path(f"{wheel}.sigstore.json") in assets
    assert preflight.primary_artifacts(assets) == sorted([wheel, sdist, sbom], key=lambda path: path.as_posix())


def test_slsa_subjects_hash_only_primary_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "dist" / "vaner.whl"
    artifact.parent.mkdir()
    artifact.write_text("artifact", encoding="utf-8")
    bundle = Path(f"{artifact}.sigstore.json")
    bundle.write_text("signature", encoding="utf-8")

    encoded = preflight.slsa_subjects_b64([artifact, bundle])
    decoded = base64.b64decode(encoded).decode("utf-8")

    assert artifact.as_posix() in decoded
    assert bundle.as_posix() not in decoded


def test_slsa_subjects_use_repo_relative_names_for_repo_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(preflight, "REPO_ROOT", tmp_path)
    artifact = tmp_path / "dist" / "vaner.whl"
    artifact.parent.mkdir()
    artifact.write_text("artifact", encoding="utf-8")

    encoded = preflight.slsa_subjects_b64([artifact])
    decoded = base64.b64decode(encoded).decode("utf-8")

    assert "  dist/vaner.whl\n" in decoded
    assert tmp_path.as_posix() not in decoded


def test_required_check_validation_catches_stale_names(tmp_path: Path) -> None:
    expected = tmp_path / "required-checks.json"
    expected.write_text(json.dumps({"required_checks": ["Internal Boundary Guard / " + "no-" + "m" + "oat-paths"]}), encoding="utf-8")

    problems = preflight.check_required_checks(expected_path=expected)

    assert any("stale name" in problem for problem in problems)


def test_public_release_scan_catches_absolute_paths(tmp_path: Path) -> None:
    public_doc = tmp_path / "release-notes.md"
    public_doc.write_text("Generated from /home/example/repos/project", encoding="utf-8")

    offenders = preflight.scan_public_release_text([public_doc])

    assert offenders


def test_project_version_check_reports_mismatch(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")

    problems = preflight.check_project_version("1.2.4", pyproject=pyproject)

    assert problems == ["pyproject.toml version is '1.2.3', expected '1.2.4'"]
