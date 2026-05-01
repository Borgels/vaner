#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Smoke-test Vaner Prepared Work through Codex CLI's MCP client.

The default mode is non-model: create a public fixture repo, seed one Prepared
Work item, register Vaner as a Codex MCP server in a temporary CODEX_HOME, and
verify that Codex CLI can list the server. Use --run-agent only in environments
with a configured Codex model credential.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from vaner.daemon.signals.git_reader import read_content_hashes
from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductEvidenceRef,
    WorkProductFreshness,
    WorkProductSelfEval,
    WorkProductSourceSnapshot,
    WorkProductStatus,
    WorkProductType,
)
from vaner.store.artefacts import ArtefactStore


def _run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 120) -> str:
    proc = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc.stdout


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _make_fixture(repo: Path) -> None:
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "src" / "calculator.py").write_text(
        "def divide(left, right):\n    return left / right\n",
        encoding="utf-8",
    )
    (repo / "docs" / "calculator.md").write_text(
        "# Calculator\n\nThe divide helper does not describe zero handling yet.\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Public Codex Prepared Work Fixture\n", encoding="utf-8")
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Codex Prepared Work Smoke"], cwd=repo)
    _run(["git", "config", "commit.gpgsign", "false"], cwd=repo)
    _run(["git", "add", "README.md", "src/calculator.py", "docs/calculator.md"], cwd=repo)
    _run(["git", "commit", "-m", "seed public fixture"], cwd=repo)


async def _seed_prepared_work(repo: Path) -> None:
    now = time.time()
    hashes = read_content_hashes(repo, ["src/calculator.py", "docs/calculator.md"])
    product = WorkProduct(
        id="wp-codex-zero-guard",
        type=WorkProductType.VIRTUAL_DIFF,
        title="Guard divide by zero",
        summary="A one-file virtual diff adds an explicit zero-division guard.",
        body="```diff\n"
        "--- a/src/calculator.py\n"
        "+++ b/src/calculator.py\n"
        "@@\n"
        " def divide(left, right):\n"
        "+    if right == 0:\n"
        '+        raise ValueError("right must not be zero")\n'
        "     return left / right\n"
        "```",
        evidence_refs=[
            WorkProductEvidenceRef(kind="file", path="src/calculator.py", reason="target function source"),
            WorkProductEvidenceRef(kind="file", path="docs/calculator.md", reason="missing zero handling note"),
        ],
        source_snapshot=WorkProductSourceSnapshot(
            project_id="codex-prepared-work-fixture",
            relative_paths=["src/calculator.py", "docs/calculator.md"],
            file_hashes=hashes,
            base_commit=_run(["git", "rev-parse", "HEAD"], cwd=repo).strip(),
            generated_at=now,
            generator_version="codex-smoke.v1",
        ),
        confidence=0.89,
        freshness=WorkProductFreshness.FRESH,
        status=WorkProductStatus.SURFACED,
        adoptability=WorkProductAdoptability.EXPORTABLE,
        self_eval=WorkProductSelfEval(
            evidence_coverage=0.9,
            groundedness=0.86,
            stale_risk=0.04,
            reason="The fixture source and docs both point at zero-division behavior.",
        ),
        created_at=now,
        updated_at=now,
        target_key="src/calculator.py",
    )
    store = ArtefactStore(repo / ".vaner" / "artefacts.db")
    await store.initialize()
    await store.upsert_work_product(product)


def _codex_env(codex_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    return env


def _vaner_mcp_command(repo: Path) -> list[str]:
    root = _repo_root()
    if shutil.which("uv"):
        return ["uv", "--directory", str(root), "run", "vaner", "mcp", "--path", str(repo)]
    return [sys.executable, "-m", "vaner.cli.main", "mcp", "--path", str(repo)]


def _assert_no_mutation(repo: Path) -> None:
    status = _run(["git", "status", "--short", "--untracked-files=no"], cwd=repo)
    diff = _run(["git", "diff", "--", "README.md", "src/calculator.py", "docs/calculator.md"], cwd=repo)
    if status or diff:
        raise AssertionError(f"Codex MCP smoke mutated the fixture repo:\nstatus:\n{status}\ndiff:\n{diff}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, help="Existing fixture repo to use. If omitted, a temp repo is created.")
    parser.add_argument("--keep", action="store_true", help="Keep temporary CODEX_HOME and fixture after the run.")
    parser.add_argument("--run-agent", action="store_true", help="Run a live Codex agent call after MCP registration.")
    parser.add_argument("--model", default=os.environ.get("VANER_CODEX_SMOKE_MODEL", "gpt-5.4-mini"))
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    if shutil.which("codex") is None:
        raise SystemExit("codex CLI is not installed")

    base = Path(tempfile.mkdtemp(prefix="vaner-codex-smoke-"))
    try:
        repo = args.repo or base / "public-fixture"
        codex_home = base / "codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
        if args.repo is None:
            repo.mkdir()
            _make_fixture(repo)
            asyncio.run(_seed_prepared_work(repo))

        env = _codex_env(codex_home)
        command = _vaner_mcp_command(repo)
        _run(["codex", "mcp", "add", "vaner", "--", *command], env=env)
        listing = _run(["codex", "mcp", "list"], env=env)
        if "vaner" not in listing:
            raise AssertionError(f"Codex MCP listing did not include Vaner:\n{listing}")

        if args.run_agent:
            output_file = base / "codex-last-message.txt"
            prompt = (
                "Use the vaner.prepared_work.dashboard MCP tool. Report the first card title, "
                "its primary action, and whether exporting it silently mutates repo files. "
                "Do not edit files."
            )
            _run(
                [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "-C",
                    str(repo),
                    "-s",
                    "read-only",
                    "-a",
                    "never",
                    "-m",
                    args.model,
                    "-o",
                    str(output_file),
                    prompt,
                ],
                env=env,
                timeout=args.timeout,
            )
            text = output_file.read_text(encoding="utf-8")
            if "Guard divide by zero" not in text:
                raise AssertionError(f"Codex did not report the seeded Prepared Work card:\n{text}")

        _assert_no_mutation(repo)
        sys.stdout.write(f"Codex Prepared Work MCP smoke passed for {repo}\n")
        if args.keep:
            sys.stdout.write(f"Kept temporary state at {base}\n")
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
