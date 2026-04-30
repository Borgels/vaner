#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Release preflight helpers shared by local checks and GitHub workflows."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REQUIRED_CHECKS = REPO_ROOT / ".github" / "required-checks.json"
DEFAULT_RELEASE_LEAK_PATHS = (
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "README.md",
    "RELEASE.md",
    ".github/workflows",
    "docs/benchmarks",
)
FORBIDDEN_PUBLIC_PATTERNS = (
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"\b[A-Z]:\\Users\\", re.IGNORECASE),
    re.compile(r"\bvaner[-_]train\b", re.IGNORECASE),
    re.compile(r"\bmoat\b", re.IGNORECASE),
    re.compile(r"\bapi[_ -]?key\b\s*[:=]", re.IGNORECASE),
)


def _as_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def release_assets(dist_dir: str | Path = "dist", sbom: str | Path = "sbom.json") -> list[Path]:
    """Return the canonical, de-duplicated release asset list.

    This intentionally includes generated sigstore bundles as release assets.
    Verification code must filter those bundles back out before treating files
    as primary artifacts.
    """
    dist = _as_path(dist_dir)
    sbom_path = _as_path(sbom)
    candidates: list[Path] = []
    if dist.exists():
        candidates.extend(path for path in sorted(dist.iterdir()) if path.is_file())
    candidates.append(sbom_path)
    candidates.append(Path(f"{sbom_path}.sigstore.json"))
    existing_or_expected = {path for path in candidates if path.exists() or path == sbom_path or path.name.endswith(".sigstore.json")}
    return sorted(existing_or_expected, key=lambda path: path.as_posix())


def primary_artifacts(paths: Iterable[str | Path]) -> list[Path]:
    """Filter release assets to files that should be attested/verified directly."""
    primary: list[Path] = []
    for raw in paths:
        path = _as_path(raw)
        if path.name.endswith(".sigstore.json"):
            continue
        primary.append(path)
    return sorted(set(primary), key=lambda path: path.as_posix())


def slsa_subjects_b64(paths: Iterable[str | Path]) -> str:
    """Return base64-encoded `sha256sum` lines for the supplied unique paths."""
    lines: list[str] = []
    for path in primary_artifacts(paths):
        if not path.exists():
            raise FileNotFoundError(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        subject_name = path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else path.as_posix()
        lines.append(f"{digest}  {subject_name}\n")
    payload = "".join(lines).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=check)


def _workflow_job_ids(path: Path) -> set[str]:
    job_ids: set[str] = set()
    in_jobs = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if raw_line and not raw_line.startswith((" ", "#")):
            in_jobs = False
            continue
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", raw_line)
        if match:
            job_ids.add(match.group(1))
    return job_ids


def _load_expected_required_checks(path: Path = DEFAULT_REQUIRED_CHECKS) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    checks = payload.get("required_checks", [])
    if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks):
        raise ValueError(f"{path} must contain a string list at required_checks")
    return checks


def check_required_checks(*, github: bool = False, expected_path: Path = DEFAULT_REQUIRED_CHECKS) -> list[str]:
    """Validate checked-in and optionally live branch-protection required checks."""
    expected = _load_expected_required_checks(expected_path)
    problems: list[str] = []
    workflow_job_ids: set[str] = set()
    for workflow in (REPO_ROOT / ".github" / "workflows").glob("*.yml"):
        workflow_job_ids.update(_workflow_job_ids(workflow))
    for check in expected:
        job_id = check.split(" / ", 1)[-1]
        if job_id not in workflow_job_ids:
            problems.append(f"expected required check has no workflow job: {check}")
    stale_markers = ("no-" + "m" + "oat-paths",)
    for check in expected:
        if any(marker in check for marker in stale_markers):
            problems.append(f"expected required check uses stale name: {check}")
    if github:
        live = _read_live_required_checks()
        if live is None:
            problems.append("could not read live branch protection required checks with gh")
        elif sorted(live) != sorted(expected):
            problems.append(f"live branch protection checks differ from .github/required-checks.json: live={live!r} expected={expected!r}")
    return problems


def _read_live_required_checks() -> list[str] | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        remotes = _run(["git", "remote", "get-url", "origin"], check=False)
        if remotes.returncode == 0:
            match = re.search(r"github\.com[:/]([^/]+/[^/.]+)", remotes.stdout.strip())
            if match:
                repo = match.group(1)
    if not repo:
        return None
    result = _run(
        [
            "gh",
            "api",
            f"repos/{repo}/branches/main/protection/required_status_checks",
            "--jq",
            ".contexts // []",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return [str(item) for item in data]


def scan_public_release_text(paths: Iterable[str | Path] = DEFAULT_RELEASE_LEAK_PATHS) -> list[str]:
    offenders: list[str] = []
    for raw in paths:
        path = _as_path(raw)
        entries = [path]
        if path.is_dir():
            entries = [entry for entry in path.rglob("*") if entry.is_file()]
        for entry in entries:
            if not entry.exists() or not entry.is_file():
                continue
            try:
                text = entry.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rel = entry.relative_to(REPO_ROOT).as_posix() if entry.is_relative_to(REPO_ROOT) else entry.as_posix()
            for pattern in FORBIDDEN_PUBLIC_PATTERNS:
                if pattern.search(text):
                    offenders.append(f"{rel}: {pattern.pattern}")
    return offenders


def check_project_version(expected: str, *, pyproject: Path = REPO_ROOT / "pyproject.toml") -> list[str]:
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    actual = str(payload.get("project", {}).get("version", ""))
    if actual != expected:
        return [f"pyproject.toml version is {actual!r}, expected {expected!r}"]
    return []


def _print_paths(paths: Iterable[Path]) -> None:
    for path in paths:
        rendered = path.relative_to(REPO_ROOT).as_posix() if path.is_absolute() and path.is_relative_to(REPO_ROOT) else path.as_posix()
        sys.stdout.write(f"{rendered}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vaner release preflight helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    assets_parser = subparsers.add_parser("assets", help="Print canonical release asset paths")
    assets_parser.add_argument("--dist-dir", default="dist")
    assets_parser.add_argument("--sbom", default="sbom.json")

    primary_parser = subparsers.add_parser("primary-artifacts", help="Print primary artifacts to verify")
    primary_parser.add_argument("paths", nargs="*")
    primary_parser.add_argument("--dist-dir", default="dist")
    primary_parser.add_argument("--sbom", default="sbom.json")

    slsa_parser = subparsers.add_parser("slsa-subjects", help="Print base64-encoded sha256 subjects")
    slsa_parser.add_argument("paths", nargs="*")
    slsa_parser.add_argument("--dist-dir", default="dist")
    slsa_parser.add_argument("--sbom", default="sbom.json")

    subparsers.add_parser("scan-public-assets", help="Scan public release docs/workflows for leaks")

    checks_parser = subparsers.add_parser("required-checks", help="Validate required check names")
    checks_parser.add_argument("--github", action="store_true", help="Also compare with live branch protection via gh")
    checks_parser.add_argument("--expected", default=str(DEFAULT_REQUIRED_CHECKS))

    version_parser = subparsers.add_parser("version", help="Validate pyproject.toml release version")
    version_parser.add_argument("expected")

    args = parser.parse_args(argv)
    if args.command == "assets":
        _print_paths(release_assets(args.dist_dir, args.sbom))
        return 0
    if args.command == "primary-artifacts":
        paths = args.paths or release_assets(args.dist_dir, args.sbom)
        _print_paths(primary_artifacts(paths))
        return 0
    if args.command == "slsa-subjects":
        paths = args.paths or release_assets(args.dist_dir, args.sbom)
        sys.stdout.write(f"{slsa_subjects_b64(paths)}\n")
        return 0
    if args.command == "scan-public-assets":
        offenders = scan_public_release_text()
        if offenders:
            sys.stderr.write("Public release leak scan failed:\n")
            for offender in offenders:
                sys.stderr.write(f"- {offender}\n")
            return 1
        return 0
    if args.command == "required-checks":
        problems = check_required_checks(github=args.github, expected_path=_as_path(args.expected))
        if problems:
            sys.stderr.write("Required check validation failed:\n")
            for problem in problems:
                sys.stderr.write(f"- {problem}\n")
            return 1
        return 0
    if args.command == "version":
        problems = check_project_version(args.expected)
        if problems:
            sys.stderr.write("Release version validation failed:\n")
            for problem in problems:
                sys.stderr.write(f"- {problem}\n")
            return 1
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
