from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.slow
def test_install_extras_produce_working_install(tmp_path: Path) -> None:
    if os.environ.get("VANER_RUN_PIPX_INSTALL_TEST", "0").strip() != "1":
        pytest.skip("Set VANER_RUN_PIPX_INSTALL_TEST=1 to run pipx install integration test.")
    if shutil.which("pipx") is None:
        pytest.skip("pipx not found")

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PIPX_HOME"] = str(tmp_path / "pipx-home")
    env["PIPX_BIN_DIR"] = str(tmp_path / "pipx-bin")
    env["PATH"] = f"{env['PIPX_BIN_DIR']}:{env.get('PATH', '')}"

    subprocess.run([sys.executable, "-m", "pip", "install", "build"], cwd=repo_root, check=True, env=env)
    subprocess.run([sys.executable, "-m", "build"], cwd=repo_root, check=True, env=env)

    wheels = sorted((repo_root / "dist").glob("*.whl"))
    assert wheels, "wheel not built"
    wheel = wheels[0]

    subprocess.run(["pipx", "install", "--force", f"{wheel}[all]"], cwd=repo_root, check=True, env=env)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    subprocess.run(["vaner", "init", "--path", str(workspace), "--no-interactive", "--clients", "none"], check=True, env=env)
    subprocess.run(["vaner", "precompute", "--path", str(workspace)], check=True, env=env)
    subprocess.run(["vaner", "query", "quick summary", "--path", str(workspace)], check=True, env=env)
